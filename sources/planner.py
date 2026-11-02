"""PRIMARY source: the MAX JEUNE planner's live API (unofficial).

Request and response shapes were captured from the max-planner page in a browser
on 2026-11-02. The site is protected by DataDome and the page also sends
x-client-* headers: this module sends a plain request, without them. If the API
refuses it, SourceError makes run.py fall back to the open data.
"""
import logging
import random
import time
from datetime import date, datetime, timedelta

import httpx

from models import Route, SourceError, Train
from sources import opendata
from stations import full_name, planner_code

log = logging.getLogger(__name__)

PLANNER_URL = "https://www.maxjeune-tgvinoui.sncf/api/public/refdata/search-freeplaces-proposals"
HEADERS = {"Accept": "application/json"}
SLEEP_BETWEEN_CALLS = (1.0, 2.0)   # seconds, random range
MAX_DAYS = 45                      # longer ranges are left to the open-data fallback
TIMEOUT = httpx.Timeout(15.0, connect=10.0)


def build_params(origin_code: str, dest_code: str, day: str) -> dict:
    return {
        "origin": origin_code,
        "destination": dest_code,
        "departureDateTime": f"{day}T01:00:00.000Z",  # what the page sends for that day
    }


def _split_dt(value) -> tuple[str, str] | None:
    """'2026-11-07T17:00' -> ('2026-11-07', '17:00')."""
    s = str(value)
    if len(s) < 16 or s[10] not in "T ":
        return None
    try:
        datetime.strptime(s[:16].replace(" ", "T"), "%Y-%m-%dT%H:%M")
    except ValueError:
        return None
    return s[:10], s[11:16]


def parse_response(data, origin_code: str = "", dest_code: str = "") -> list[Train]:
    """Turn the planner JSON into Trains: {"proposals": [{dep, arr, num, count, ...}], ...}."""
    if not isinstance(data, dict) or not isinstance(data.get("proposals"), list):
        raise SourceError("planner: unexpected response shape (no 'proposals' list)")

    trains: dict[str, Train] = {}
    for p in data["proposals"]:
        dep = _split_dt(p.get("dep"))
        no = p.get("num")
        count = p.get("count")
        if not dep or not no or not isinstance(count, int) or count <= 0:
            continue
        arr = _split_dt(p.get("arr"))
        t = Train(date=dep[0], dep=dep[1], arr=arr[1] if arr else "", train_no=str(no),
                  origin=full_name(origin_code, str(p.get("orig") or "")),
                  dest=full_name(dest_code, str(p.get("dest") or "")), seats=count)
        trains[t.key] = t
    return list(trains.values())


# ---------------------------------------------------------------------------
# Plumbing
# ---------------------------------------------------------------------------

def _sleep() -> None:
    time.sleep(random.uniform(*SLEEP_BETWEEN_CALLS))


def _days(route: Route) -> list[str]:
    start = date.fromisoformat(route.date_from)
    end = date.fromisoformat(route.date_to)
    return [(start + timedelta(days=i)).isoformat() for i in range((end - start).days + 1)]


def _query_day(client: httpx.Client, o: str, d: str, day: str):
    try:
        r = client.get(PLANNER_URL, params=build_params(o, d, day), headers=HEADERS)
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
    own = client is None
    client = client or httpx.Client(timeout=TIMEOUT)
    trains: dict[str, Train] = {}
    try:
        o, d = planner_code(route.origin), planner_code(route.dest)
        pairs = [(o, d)] if o and d else opendata.station_pairs(route, client)
        calls = [(po, pd, day) for day in days for po, pd in pairs]
        for i, (po, pd, day) in enumerate(calls):
            if i:
                _sleep()
            data = _query_day(client, po, pd, day)
            if data is None:
                continue
            try:
                for t in parse_response(data, po, pd):
                    trains.setdefault(t.key, t)  # same train to several stations: keep the first pair
            except SourceError:
                raise
            except Exception as e:  # defensive: never let a parser bug crash the run
                raise SourceError(f"planner parse error: {type(e).__name__}: {e}") from e
    finally:
        if own:
            client.close()
    return sorted(trains.values(), key=lambda t: (t.date, t.dep))
