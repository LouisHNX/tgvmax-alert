"""SQLite state: Telegram offset, watched routes, already-notified trains."""
import os
import sqlite3

from models import Route

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
CREATE TABLE IF NOT EXISTS routes (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    origin    TEXT NOT NULL,
    dest      TEXT NOT NULL,
    date_from TEXT NOT NULL,
    date_to   TEXT NOT NULL,
    time_from TEXT,
    time_to   TEXT
);
CREATE TABLE IF NOT EXISTS notified (
    route_id INTEGER NOT NULL,
    date     TEXT NOT NULL,
    train_no TEXT NOT NULL,
    PRIMARY KEY (route_id, date, train_no)
);
"""


def db_path() -> str:
    return os.environ.get("DB_PATH", "./alert.db")


def connect(path: str | None = None) -> sqlite3.Connection:
    """Open (and initialise) the database. Use ':memory:' in tests."""
    path = path or db_path()
    if path != ":memory:":
        parent = os.path.dirname(os.path.abspath(path))
        os.makedirs(parent, exist_ok=True)
    conn = sqlite3.connect(path, timeout=10)
    conn.executescript(SCHEMA)
    if "weekday" not in [c[1] for c in conn.execute("PRAGMA table_info(routes)")]:
        conn.execute("ALTER TABLE routes ADD COLUMN weekday INTEGER")  # databases created before weekly routes
    conn.commit()
    return conn


# --- Telegram offset -------------------------------------------------------

def get_offset(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT value FROM meta WHERE key='offset'").fetchone()
    return int(row[0]) if row else 0


def set_offset(conn: sqlite3.Connection, offset: int) -> None:
    with conn:
        conn.execute(
            "INSERT INTO meta(key, value) VALUES('offset', ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (str(offset),),
        )


def get_reminder_day(conn: sqlite3.Connection) -> str | None:
    row = conn.execute("SELECT value FROM meta WHERE key='reminder_day'").fetchone()
    return row[0] if row else None


def set_reminder_day(conn: sqlite3.Connection, day: str) -> None:
    with conn:
        conn.execute(
            "INSERT INTO meta(key, value) VALUES('reminder_day', ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (day,),
        )


# --- routes ----------------------------------------------------------------

def _row_to_route(r) -> Route:
    return Route(id=r[0], origin=r[1], dest=r[2], date_from=r[3], date_to=r[4],
                 time_from=r[5], time_to=r[6], weekday=r[7])


def add_route(conn, origin, dest, date_from, date_to, time_from=None, time_to=None, weekday=None) -> Route:
    with conn:
        cur = conn.execute(
            "INSERT INTO routes(origin, dest, date_from, date_to, time_from, time_to, weekday) "
            "VALUES(?,?,?,?,?,?,?)",
            (origin, dest, date_from, date_to, time_from, time_to, weekday),
        )
    return Route(cur.lastrowid, origin, dest, date_from, date_to, time_from, time_to, weekday)


def list_routes(conn) -> list[Route]:
    rows = conn.execute(
        "SELECT id, origin, dest, date_from, date_to, time_from, time_to, weekday FROM routes ORDER BY id"
    ).fetchall()
    return [_row_to_route(r) for r in rows]


def delete_route(conn, route_id: int) -> bool:
    with conn:
        cur = conn.execute("DELETE FROM routes WHERE id=?", (route_id,))
        conn.execute("DELETE FROM notified WHERE route_id=?", (route_id,))
    return cur.rowcount > 0


def purge_expired(conn, today: str) -> list[int]:
    """Delete routes whose date_to < today (YYYY-MM-DD) and their notified rows."""
    ids = [r[0] for r in conn.execute("SELECT id FROM routes WHERE date_to < ?", (today,))]
    with conn:
        for rid in ids:
            conn.execute("DELETE FROM routes WHERE id=?", (rid,))
            conn.execute("DELETE FROM notified WHERE route_id=?", (rid,))
    return ids


# --- notified --------------------------------------------------------------

def get_notified(conn, route_id: int) -> set[tuple[str, str]]:
    rows = conn.execute("SELECT date, train_no FROM notified WHERE route_id=?", (route_id,))
    return {(d, n) for d, n in rows}


def replace_notified(conn, route_id: int, keys) -> None:
    """Make the notified set for a route exactly `keys` (iterable of (date, train_no))."""
    with conn:
        conn.execute("DELETE FROM notified WHERE route_id=?", (route_id,))
        conn.executemany(
            "INSERT OR IGNORE INTO notified(route_id, date, train_no) VALUES(?,?,?)",
            [(route_id, d, n) for d, n in keys],
        )
