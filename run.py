"""MAX JEUNE seat alert: one run = read Telegram commands, check routes, alert, exit."""
import argparse
import logging
import os
import signal
import sys
from datetime import date, datetime, timedelta, timezone

import db
from models import Route, SourceError, Train, format_day
from stations import route_station
from telegram import HELP, Command, CommandError, TelegramClient, TelegramError, parse_command

log = logging.getLogger("run")

WATCHDOG_SECONDS = 600


def today_paris() -> date:
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("Europe/Paris")).date()
    except Exception:  # tzdata missing in a slim image
        return datetime.now(timezone.utc).date()


# --- formatting ------------------------------------------------------------

def format_train(t: Train) -> str:
    times = f"{t.dep}→{t.arr}" if t.arr else t.dep
    seats = f"{t.seats} places MAX dispo" if t.seats and t.seats > 1 else "place MAX dispo"
    return (f"🚄 {t.origin} → {t.dest} · {format_day(t.date)} · "
            f"{times} · TGV {t.train_no.lstrip('0') or t.train_no} — {seats}")


def format_message(groups: list[tuple[Route, list[Train]]], failed: list[Route]) -> str:
    blocks = ["\n".join(format_train(t) for t in trains) for r, trains in groups if trains]
    text = "\n\n".join(blocks)
    if failed:
        warn = "⚠️ Sources indisponibles pour : " + ", ".join(f"#{r.id}" for r in failed)
        text = f"{text}\n\n{warn}" if text else warn
    return text


def format_route_list(routes: list[Route]) -> str:
    """Routes by station pair, then week, then direction. The first route added sets the outward way."""
    pairs: dict[frozenset, list[Route]] = {}
    for r in routes:
        pairs.setdefault(frozenset((r.origin, r.dest)), []).append(r)

    def monday(r: Route) -> date:
        if r.weekday is not None:
            return date.min  # weekly routes come first, under their own title
        d = date.fromisoformat(r.date_from)
        return d - timedelta(days=d.weekday())

    blocks = []
    for group in pairs.values():
        out, back = group[0].origin, group[0].dest
        lines = [f"🔁 {route_station(out)} ↔ {route_station(back)}"]
        for week in sorted({monday(r) for r in group}):
            lines.append("\nChaque semaine" if week == date.min else f"\nSemaine du {week.day:02d}/{week.month:02d}")
            for title, origin in (("Aller", out), ("Retour", back)):
                legs = sorted((r for r in group if monday(r) == week and r.origin == origin),
                              key=lambda r: (r.weekday or 0, r.date_from, r.time_from or ""))
                if legs:
                    lines.append(f"{title} :")
                    lines.extend(f"  {r.slot()}" for r in legs)
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


# --- commands --------------------------------------------------------------

def apply_command(conn, cmd: Command) -> tuple[str, bool]:
    """Apply one command. Returns (reply text, wants_full_availability)."""
    if cmd.name == "help":
        return HELP, False
    if cmd.name == "list":
        routes = db.list_routes(conn)
        if not routes:
            return "Aucune route surveillée. Ajoute-en une avec /add.", False
        return "Routes surveillées :\n\n" + format_route_list(routes), False
    if cmd.name == "del":
        ok = db.delete_route(conn, cmd.route_id)
        return (f"Route #{cmd.route_id} supprimée." if ok else f"Route #{cmd.route_id} introuvable."), False
    if cmd.name == "now":
        return "OK, je vérifie tout de suite et j'envoie les dispos actuelles.", True
    if cmd.name == "add":
        r = db.add_route(conn, cmd.origin, cmd.dest, cmd.date_from, cmd.date_to,
                         cmd.time_from, cmd.time_to, cmd.weekday)
        return f"Route ajoutée : {r.label()}", False
    return HELP, False


def process_updates(conn, tg, chat_id: str, today: date) -> bool:
    """Fetch and apply pending commands. Returns True if /now was requested."""
    want_now = False
    try:
        updates = tg.get_updates(db.get_offset(conn))
    except TelegramError as e:
        log.error("telegram getUpdates failed: %s", e)
        return False
    last_id = None
    for u in updates:
        last_id = u["update_id"]
        msg = u.get("message") or {}
        if str((msg.get("chat") or {}).get("id")) != str(chat_id):
            log.info("ignoring update %s from another chat", last_id)
            continue
        text = msg.get("text") or ""
        try:
            cmd = parse_command(text, today)
            if cmd is None:
                continue
            reply, now = apply_command(conn, cmd)
            want_now = want_now or now
        except CommandError as e:
            reply = str(e)
        try:
            tg.send_message(chat_id, reply)
        except TelegramError as e:
            log.error("telegram reply failed: %s", e)
    if last_id is not None:
        db.set_offset(conn, last_id + 1)
    return want_now


# --- checking routes -------------------------------------------------------

def nkey(t: Train) -> tuple[str, str]:
    return (t.date, t.train_no.lstrip("0") or t.train_no)


def check_route(route: Route, sources, down: set[str]) -> list[Train] | None:
    """Try each source in order. Returns accepted trains, or None if all failed."""
    for name, fetch in sources:
        if name in down:
            continue
        try:
            trains = fetch(route)
        except SourceError as e:
            log.warning("source %s failed for route #%s: %s", name, route.id, e)
            down.add(name)  # don't hammer a blocked source for the other routes
            continue
        except Exception:
            log.exception("source %s crashed for route #%s", name, route.id)
            down.add(name)
            continue
        log.info("route #%s answered by source %s (%d trains)", route.id, name, len(trains))
        seen, out = set(), []
        for t in sorted(trains, key=lambda t: (t.date, t.dep)):
            if route.accepts(t) and nkey(t) not in seen:
                seen.add(nkey(t))
                out.append(t)
        return out
    return None


def run(conn, tg, chat_id: str, sources, today: date, persist: bool = True,
        process_commands: bool = True) -> int:
    want_now = process_commands and process_updates(conn, tg, chat_id, today)
    if persist:
        purged = db.purge_expired(conn, today.isoformat())
        if purged:
            log.info("purged expired routes: %s", purged)

    down: set[str] = set()
    groups, failed, current = [], [], {}
    for route in db.list_routes(conn):
        avail = check_route(route, sources, down)
        if avail is None:
            failed.append(route)
            continue
        current[route.id] = {nkey(t) for t in avail}
        already = db.get_notified(conn, route.id)
        shown = avail if want_now else [t for t in avail if nkey(t) not in already]
        if shown:
            groups.append((route, shown))

    text = format_message(groups, failed)
    if want_now and not groups:
        text = "Aucune place MAX dispo pour le moment." + (f"\n\n{text}" if text else "")
    elif want_now:
        text = "Disponibilités actuelles :\n\n" + text

    if text:
        try:
            tg.send_message(chat_id, text)
        except TelegramError as e:
            log.error("telegram send failed, will retry next run: %s", e)
            return 1  # notified untouched, so the trains are re-alerted next time

    if persist:
        for rid, keys in current.items():
            db.replace_notified(conn, rid, keys)
    return 0


# --- entry point -----------------------------------------------------------

class PrintClient:
    """Dry-run stand-in for Telegram: no network, prints messages."""

    def get_updates(self, offset=0):
        return []

    def send_message(self, chat_id, text):
        print("--- message (dry-run) ---")
        print(text)
        print("-------------------------")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true",
                    help="print instead of sending; no Telegram calls, state is not modified")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)  # its URLs contain the bot token

    if hasattr(signal, "SIGALRM"):
        signal.signal(signal.SIGALRM, lambda *_: sys.exit("watchdog: run took too long"))
        signal.alarm(WATCHDOG_SECONDS)

    if args.dry_run:
        tg, chat_id = PrintClient(), os.environ.get("ALLOWED_CHAT_ID", "")
    else:
        token, chat_id = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("ALLOWED_CHAT_ID")
        if not token or not chat_id:
            print("TELEGRAM_BOT_TOKEN and ALLOWED_CHAT_ID must be set", file=sys.stderr)
            return 2
        tg = TelegramClient(token)

    from sources import opendata, planner
    sources = [("planner", planner.fetch_available), ("opendata", opendata.fetch_available)]

    conn = db.connect()
    try:
        return run(conn, tg, chat_id, sources, today_paris(),
                   persist=not args.dry_run, process_commands=not args.dry_run)
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
