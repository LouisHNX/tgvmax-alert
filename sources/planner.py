"""PRIMARY source: the MAX JEUNE planner's live API (unofficial, unverified).

Everything that depends on the real API is isolated in the constants and the
two functions marked TODO(verify) below. Open DevTools -> Network on
https://www.maxjeune-tgvinoui.sncf/sncf-connect/max-planner, run a search, and
compare with what is guessed here.
"""
import logging
import random
import time
from datetime import date, datetime, timedelta

import httpx

from models import Route, SourceError, Train
from stations import planner_code

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# TODO(verify): endpoint, method, headers, body shape
# ---------------------------------------------------------------------------
PLANNER_URL = "https://www.maxjeune-tgvinoui.sncf/api/public/refdata/search-freeplaces-proposals"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "fr-FR,fr;q=0.9",
    "Content-Type": "application/json",
    "Origin": "https://www.maxjeune-tgvinoui.sncf",
    "Referer": "https://www.maxjeune-tgvinoui.sncf/sncf-connect/max-planner",
}
SLEEP_BETWEEN_CALLS = (1.0, 2.0)   # seconds, random range
MAX_DAYS = 45                      # longer ranges are left to the open-data fallback
TIMEOUT = httpx.Timeout(15.0, connect=10.0)


def build_body(origin_code: str, dest_code: str, day: str) -> dict:
    """TODO(verify): exact JSON body (key names, datetime format, extra fields)."""
    return {
        "origin": origin_code,
        "destination": dest_code,
        "departureDateTime": f"{day}T00:00:00",
    }


# TODO(verify): key names used in the response. Lists give alternatives tried in order.
PROPOSAL_DEP_KEYS = ("departureDateTime", "departureDate", "departureTime", "departure")
PROPOSAL_ARR_KEYS = ("arrivalDateTime", "arrivalDate", "arrivalTime", "arrival")
PROPOSAL_NO_KEYS = ("trainNumber", "trainNo", "trainNum", "number", "transportNumber")
PROPOSAL_SEATS_KEYS = ("freePlaces", "freeSeats", "availableSeats", "remainingSeats", "seats")


def _first(d: dict, keys):
    for k in keys:
        if k in d and d[k] not in (None, ""):
            return d[k]
    return None


def _find_proposals(node, out: list) -> None:
    """Collect every dict that has a departure key, wherever it sits in the JSON."""
    if isinstance(node, dict):
        if _first(node, PROPOSAL_DEP_KEYS) is not None and not isinstance(
            _first(node, PROPOSAL_DEP_KEYS), (dict, list)
        ):
            out.append(node)
            return
        for v in node.values():
            _find_proposals(v, out)
    elif isinstance(node, list):
        for v in node:
            _find_proposals(v, out)


def _split_dt(value) -> tuple[str, str] | None:
    """'2026-11-15T07:56:00+01:00' -> ('2026-11-15', '07:56')."""
    s = str(value)
    if len(s) < 16 or s[10] not in "T ":
        return None
    try:
        datetime.strptime(s[:16].replace(" ", "T"), "%Y-%m-%dT%H:%M")
    except ValueError:
        return None
    return s[:10], s[11:16]


def parse_response(data, origin_label: str = "", dest_label: str = "") -> list[Train]:
    """TODO(verify): turn the planner JSON into Trains. Parses defensively.

    A proposal is considered available unless a seats field says 0/False.
    """
    found: list[dict] = []
    _find_proposals(data, found)
    if not found and data:
        keys = list(data)[:10] if isinstance(data, dict) else f"list[{len(data)}]"
        log.warning("planner: non-empty response but no proposal recognised (top-level: %s)", keys)

    trains: dict[str, Train] = {}
    for p in found:
        dep = _split_dt(_first(p, PROPOSAL_DEP_KEYS))
        if not dep:
            continue
        arr_raw = _first(p, PROPOSAL_ARR_KEYS)
        arr = _split_dt(arr_raw) if arr_raw is not None else None
        no = _first(p, PROPOSAL_NO_KEYS)
        if no is None and isinstance(p.get("train"), dict):
            no = _first(p["train"], PROPOSAL_NO_KEYS)
        if no is None:
            continue
        seats_raw = _first(p, PROPOSAL_SEATS_KEYS)
        seats = None
        if isinstance(seats_raw, bool):
            if not seats_raw:
                continue
        elif isinstance(seats_raw, (int, float)):
            if seats_raw <= 0:
                continue
            seats = int(seats_raw)
        t = Train(date=dep[0], dep=dep[1], arr=arr[1] if arr else "", train_no=str(no),
                  origin=origin_label, dest=dest_label, seats=seats)
        trains[t.key] = t
    return list(trains.values())


# ---------------------------------------------------------------------------
# Plumbing (should not need changes once the section above is verified)
# ---------------------------------------------------------------------------

def _sleep() -> None:
    time.sleep(random.uniform(*SLEEP_BETWEEN_CALLS))


def _days(route: Route) -> list[str]:
    start = date.fromisoformat(route.date_from)
    end = date.fromisoformat(route.date_to)
    return [(start + timedelta(days=i)).isoformat() for i in range((end - start).days + 1)]


def _query_day(client: httpx.Client, o: str, d: str, day: str):
    try:
        r = client.post(PLANNER_URL, json=build_body(o, d, day), headers=HEADERS)
    except httpx.HTTPError as e:
        raise SourceError(f"planner network error: {type(e).__name__}: {e}") from e
    if r.status_code in (403, 429):
        raise SourceError(f"planner blocked (HTTP {r.status_code})")
    if r.status_code == 204:
        return None
    if r.status_code >= 400:
        raise SourceError(f"planner HTTP {r.status_code}")
    ctype = r.headers.get("content-type", "")
    head = r.text[:2000].lower()
    if "json" not in ctype or "captcha" in head or "datadome" in head:
        raise SourceError(f"planner returned non-JSON/captcha page (content-type {ctype!r})")
    try:
        return r.json()
    except ValueError as e:
        raise SourceError(f"planner JSON parse error: {e}") from e


def fetch_available(route: Route, client: httpx.Client | None = None) -> list[Train]:
    days = _days(route)
    if len(days) > MAX_DAYS:
        raise SourceError(f"date range too long for the planner ({len(days)} days)")
    o, d = planner_code(route.origin), planner_code(route.dest)
    own = client is None
    client = client or httpx.Client(timeout=TIMEOUT)
    trains: dict[str, Train] = {}
    try:
        for i, day in enumerate(days):
            if i:
                _sleep()
            data = _query_day(client, o, d, day)
            if data is None:
                continue
            try:
                for t in parse_response(data, route.origin, route.dest):
                    trains[t.key] = t
            except Exception as e:  # defensive: never let a parser bug crash the run
                raise SourceError(f"planner parse error: {type(e).__name__}: {e}") from e
    finally:
        if own:
            client.close()
    return sorted(trains.values(), key=lambda t: (t.date, t.dep))
