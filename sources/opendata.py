"""FALLBACK source: SNCF open data, dataset `tgvmax` (refreshed ~once a day).

The field names and the ODSQL syntax below come from memory and were NOT
verified. Check them on https://data.sncf.com/explore/dataset/tgvmax/ (tab
"API" or "Table"), then fix the constants/functions marked TODO(verify).
"""
import logging

import httpx

from models import Route, SourceError, Train
from stations import STATIONS, opendata_label

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# TODO(verify): endpoint, field names, filter syntax
# ---------------------------------------------------------------------------
URL = "https://data.sncf.com/api/explore/v2.1/catalog/datasets/tgvmax/records"
PAGE_SIZE = 100          # explore v2.1 maximum for `limit`
MAX_PAGES = 30           # safety net (the API also caps offset+limit at 10000)
TIMEOUT = httpx.Timeout(20.0, connect=10.0)

F_DATE = "date"
F_TRAIN_NO = "train_no"
F_ORIGIN = "origine"
F_DEST = "destination"
F_DEP = "heure_depart"
F_ARR = "heure_arrivee"
F_HAPPY = "od_happy_card"
HAPPY_YES = "OUI"


def build_where(origin_label: str, dest_label: str, date_from: str, date_to: str) -> str:
    """TODO(verify): ODSQL filter. `date'YYYY-MM-DD'` is the ODSQL date literal."""
    o = origin_label.replace('"', '\\"')
    d = dest_label.replace('"', '\\"')
    return (
        f'{F_ORIGIN}="{o}" AND {F_DEST}="{d}" AND {F_HAPPY}="{HAPPY_YES}" '
        f"AND {F_DATE}>=date'{date_from}' AND {F_DATE}<=date'{date_to}'"
    )


def parse_records(payload: dict) -> list[Train]:
    """TODO(verify): turn one page of the API response into Trains.

    Expects {"total_count": n, "results": [ {field: value, ...}, ... ]}.
    """
    if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
        raise SourceError("open data: unexpected response shape (no 'results' list)")
    out: list[Train] = []
    for r in payload["results"]:
        if not isinstance(r, dict):
            continue
        if str(r.get(F_HAPPY, "")).upper() != HAPPY_YES:
            continue
        day = str(r.get(F_DATE) or "")[:10]
        no = r.get(F_TRAIN_NO)
        dep = str(r.get(F_DEP) or "")[:5]
        arr = str(r.get(F_ARR) or "")[:5]
        if len(day) != 10 or no in (None, "") or len(dep) != 5:
            continue
        out.append(Train(date=day, dep=dep, arr=arr, train_no=str(no),
                         origin=str(r.get(F_ORIGIN) or ""), dest=str(r.get(F_DEST) or "")))
    return out


# ---------------------------------------------------------------------------
# Plumbing
# ---------------------------------------------------------------------------

def _label(name: str) -> str:
    key = name.strip().lower().replace(" ", "-")
    if key not in STATIONS and len(name.strip()) == 5 and name.strip()[:2].upper() == "FR":
        raise SourceError(f"no open-data label for planner code {name!r}")
    return opendata_label(name)


def fetch_available(route: Route, client: httpx.Client | None = None) -> list[Train]:
    where = build_where(_label(route.origin), _label(route.dest), route.date_from, route.date_to)
    own = client is None
    client = client or httpx.Client(timeout=TIMEOUT)
    trains: dict[str, Train] = {}
    try:
        for page in range(MAX_PAGES):
            params = {"where": where, "limit": PAGE_SIZE, "offset": page * PAGE_SIZE,
                      "order_by": f"{F_DATE},{F_DEP}"}
            try:
                r = client.get(URL, params=params)
            except httpx.HTTPError as e:
                raise SourceError(f"open data network error: {type(e).__name__}: {e}") from e
            if r.status_code >= 400:
                raise SourceError(f"open data HTTP {r.status_code}: {r.text[:200]}")
            try:
                payload = r.json()
            except ValueError as e:
                raise SourceError(f"open data JSON parse error: {e}") from e
            results = payload.get("results") if isinstance(payload, dict) else None
            for t in parse_records(payload):
                trains[t.key] = t
            if not results or len(results) < PAGE_SIZE:
                break
        else:
            log.warning("open data: stopped after %d pages", MAX_PAGES)
    finally:
        if own:
            client.close()
    return sorted(trains.values(), key=lambda t: (t.date, t.dep))
