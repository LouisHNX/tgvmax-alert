"""Minimal Telegram Bot API client + command parser (httpx only)."""
import logging
import re
from dataclasses import dataclass
from datetime import date, datetime

import httpx

from stations import STATIONS, is_known

log = logging.getLogger(__name__)
MAX_LEN = 4000  # Telegram limit is 4096 chars per message


class TelegramError(Exception):
    pass


class TelegramClient:
    def __init__(self, token: str, client: httpx.Client | None = None):
        self._base = f"https://api.telegram.org/bot{token}"
        self._client = client or httpx.Client(timeout=httpx.Timeout(15.0, connect=10.0))

    def _call(self, method: str, payload: dict):
        try:
            r = self._client.post(f"{self._base}/{method}", json=payload)
            data = r.json()
        except (httpx.HTTPError, ValueError) as e:
            # Do not chain/print the exception: its text contains the URL, i.e. the token.
            raise TelegramError(f"{method} failed: {type(e).__name__}") from None
        if not data.get("ok"):
            raise TelegramError(f"{method} refused: {data.get('description', r.status_code)}")
        return data["result"]

    def get_updates(self, offset: int = 0) -> list[dict]:
        payload = {"timeout": 0, "allowed_updates": ["message"]}
        if offset:
            payload["offset"] = offset
        return self._call("getUpdates", payload)

    def send_message(self, chat_id, text: str) -> None:
        for chunk in split_text(text):
            self._call("sendMessage", {"chat_id": chat_id, "text": chunk,
                                       "disable_web_page_preview": True})


def split_text(text: str, limit: int = MAX_LEN) -> list[str]:
    chunks, cur = [], ""
    for line in text.split("\n"):
        if cur and len(cur) + len(line) + 1 > limit:
            chunks.append(cur)
            cur = ""
        cur = f"{cur}\n{line}" if cur else line
    if cur:
        chunks.append(cur)
    return chunks or [""]


# --- commands --------------------------------------------------------------

class CommandError(Exception):
    """Bad user input; the message is sent back to the user."""


@dataclass
class Command:
    name: str                      # add | list | del | now | help
    origin: str | None = None
    dest: str | None = None
    date_from: str | None = None
    date_to: str | None = None
    time_from: str | None = None
    time_to: str | None = None
    route_id: int | None = None


HELP = (
    "Commandes (appliquées au prochain passage, dans les 6 h) :\n"
    "/add <départ> <arrivée> <date> [HH:MM-HH:MM]\n"
    "   date : 15/11, 15/11-20/11 ou 2026-11-15\n"
    "   ex : /add paris lyon 15/11-20/11 08:00-12:00\n"
    "/list : routes surveillées\n"
    "/del <id> : supprimer une route\n"
    "/now : envoyer toutes les dispos actuelles\n"
    "/help : cette aide\n"
    "Gares : " + ", ".join(sorted(STATIONS)) + " (ou un code planner, ex. FRPLY)."
)

_DM = re.compile(r"^(\d{1,2})/(\d{1,2})$")
_ISO = re.compile(r"^(\d{4}-\d{2}-\d{2})(?:-(\d{4}-\d{2}-\d{2}))?$")
_TIME = re.compile(r"^(\d{1,2}):(\d{2})-(\d{1,2}):(\d{2})$")


def _next_occurrence(day: int, month: int, not_before: date) -> date:
    for year in range(not_before.year, not_before.year + 5):
        try:
            d = date(year, month, day)
        except ValueError:
            continue
        if d >= not_before:
            return d
    raise CommandError(f"Date invalide : {day:02d}/{month:02d}")


def parse_date_spec(spec: str, today: date) -> tuple[str, str]:
    """Return (date_from, date_to) as ISO strings."""
    m = _ISO.match(spec)
    if m:
        try:
            d1 = date.fromisoformat(m.group(1))
            d2 = date.fromisoformat(m.group(2) or m.group(1))
        except ValueError:
            raise CommandError(f"Date invalide : {spec}") from None
    else:
        parts = spec.split("-")
        if len(parts) > 2 or not all(_DM.match(p) for p in parts):
            raise CommandError(f"Format de date non reconnu : {spec} (essaie 15/11, 15/11-20/11 ou 2026-11-15)")
        try:
            a = _DM.match(parts[0]).groups()
            d1 = _next_occurrence(int(a[0]), int(a[1]), today)
            if len(parts) == 2:
                b = _DM.match(parts[1]).groups()
                d2 = _next_occurrence(int(b[0]), int(b[1]), d1)
            else:
                d2 = d1
        except ValueError:
            raise CommandError(f"Date invalide : {spec}") from None
    if d1 < today:
        raise CommandError(f"La date {d1.isoformat()} est passée.")
    if d2 < d1:
        raise CommandError("La date de fin est avant la date de début.")
    return d1.isoformat(), d2.isoformat()


def parse_time_window(spec: str) -> tuple[str, str]:
    m = _TIME.match(spec)
    if not m:
        raise CommandError(f"Plage horaire non reconnue : {spec} (essaie 08:00-12:00)")
    h1, m1, h2, m2 = (int(x) for x in m.groups())
    if h1 > 23 or h2 > 23 or m1 > 59 or m2 > 59:
        raise CommandError(f"Plage horaire invalide : {spec}")
    t1, t2 = f"{h1:02d}:{m1:02d}", f"{h2:02d}:{m2:02d}"
    if t1 > t2:
        raise CommandError("L'heure de début doit être avant l'heure de fin.")
    return t1, t2


def _check_station(name: str) -> None:
    if not is_known(name):
        raise CommandError(
            f"Gare inconnue : « {name} ».\n"
            "Gares connues : " + ", ".join(sorted(STATIONS)) + "\n"
            "Tu peux aussi donner un code planner (ex. FRPLY). Écris les noms avec des tirets (le-mans)."
        )


def _station_id(name: str) -> str:
    """Canonical form stored in the DB: known short name lower-case, else upper-case code."""
    key = name.strip().lower()
    return key if key in STATIONS else name.strip().upper()


def parse_command(text: str, today: date | None = None) -> Command | None:
    """Parse a Telegram message. Returns None if it is not a command.

    Raises CommandError (with a user-facing message) for bad input.
    """
    today = today or date.today()
    tokens = text.strip().split()
    if not tokens or not tokens[0].startswith("/"):
        return None
    name = tokens[0][1:].split("@")[0].lower()
    args = tokens[1:]

    if name in ("help", "start"):
        return Command("help")
    if name == "list":
        return Command("list")
    if name == "now":
        return Command("now")
    if name == "del":
        if len(args) != 1 or not args[0].lstrip("#").isdigit():
            raise CommandError("Usage : /del <id>  (voir /list)")
        return Command("del", route_id=int(args[0].lstrip("#")))
    if name == "add":
        if len(args) not in (3, 4):
            raise CommandError("Usage : /add <départ> <arrivée> <date> [HH:MM-HH:MM]\nex : /add paris lyon 15/11")
        origin, dest = args[0], args[1]
        _check_station(origin)
        _check_station(dest)
        d1, d2 = parse_date_spec(args[2], today)
        t1 = t2 = None
        if len(args) == 4:
            t1, t2 = parse_time_window(args[3])
        return Command("add", origin=_station_id(origin), dest=_station_id(dest),
                       date_from=d1, date_to=d2, time_from=t1, time_to=t2)
    raise CommandError(f"Commande inconnue : /{name}\n\n{HELP}")
