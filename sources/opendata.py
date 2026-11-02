"""FALLBACK source: SNCF open data, dataset `tgvmax` (refreshed ~once a day).

Fields and filter syntax checked against the live API on 2026-11-02. The dataset
covers the next 30 days and has one row per station pair, so a train can appear
twice (e.g. Lyon Part-Dieu and Lyon Perrache).
"""
import logging

import httpx

from models import Route, SourceError, Train
from stations import STATIONS, opendata_label, planner_code

log = logging.getLogger(__name__)

URL = "https://data.sncf.com/api/explore/v2.1/catalog/datasets/tgvmax/records"
PAGE_SIZE = 100          # explore v2.1 maximum for `limit`
MAX_PAGES = 30           # safety net (the API also caps offset+limit at 10000)
TIMEOUT = httpx.Timeout(20.0, connect=10.0)

F_DATE = "date"
F_TRAIN_NO = "train_no"
F_ORIGIN = "origine"
F_DEST = "destination"
F_ORIGIN_CODE = "origine_iata"
F_DEST_CODE = "destination_iata"
F_DEP = "heure_depart"
F_ARR = "heure_arrivee"
F_HAPPY = "od_happy_card"
HAPPY_YES = "OUI"


def _quote(value: str) -> str:
    return value.replace('"', '\\"')


def build_where(origin_label: str, dest_label: str, date_from: str, date_to: str) -> str:
    return (
        f'{F_ORIGIN}="{_quote(origin_label)}" AND {F_DEST}="{_quote(dest_label)}" AND {F_HAPPY}="{HAPPY_YES}" '
        f"AND {F_DATE}>=date'{date_from}' AND {F_DATE}<=date'{date_to}'"
    )


def parse_records(payload: dict) -> list[Train]:
    """Turn one page of the API response into Trains.

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


def _get(client: httpx.Client, params: dict) -> dict:
    try:
        r = client.get(URL, params=params)
    except httpx.HTTPError as e:
        raise SourceError(f"open data network error: {type(e).__name__}: {e}") from e
    if r.status_code >= 400:
        raise SourceError(f"open data HTTP {r.status_code}: {r.text[:200]}")
    try:
        return r.json()
    except ValueError as e:
        raise SourceError(f"open data JSON parse error: {e}") from e


def station_pairs(route: Route, client: httpx.Client) -> list[tuple[str, str]]:
    """Planner codes served between the route's stations, e.g. paris/lyon -> FRPLY/FRLPD and FRPLY/FRLPE."""
    def cond(name: str, label_field: str, code_field: str) -> str:
        code = planner_code(name)
        return f'{code_field}="{_quote(code)}"' if code else f'{label_field}="{_quote(opendata_label(name))}"'

    payload = _get(client, {
        "select": f"{F_ORIGIN_CODE},{F_DEST_CODE}",
        "group_by": f"{F_ORIGIN_CODE},{F_DEST_CODE}",
        "where": f"{cond(route.origin, F_ORIGIN, F_ORIGIN_CODE)} AND {cond(route.dest, F_DEST, F_DEST_CODE)}",
        "limit": PAGE_SIZE,
    })
    results = payload.get("results") if isinstance(payload, dict) else None
    if not isinstance(results, list):
        raise SourceError("open data: unexpected response shape (no 'results' list)")
    return sorted((r[F_ORIGIN_CODE], r[F_DEST_CODE]) for r in results)


def fetch_available(route: Route, client: httpx.Client | None = None) -> list[Train]:
    where = build_where(_label(route.origin), _label(route.dest), route.date_from, route.date_to)
    own = client is None
    client = client or httpx.Client(timeout=TIMEOUT)
    trains: dict[str, Train] = {}
    try:
        for page in range(MAX_PAGES):
            payload = _get(client, {"where": where, "limit": PAGE_SIZE, "offset": page * PAGE_SIZE,
                                    "order_by": f"{F_DATE},{F_DEP},{F_ARR}"})
            results = payload.get("results") if isinstance(payload, dict) else None
            for t in parse_records(payload):
                trains.setdefault(t.key, t)  # same train to several stations: keep the first arrival
            if not results or len(results) < PAGE_SIZE:
                break
        else:
            log.warning("open data: stopped after %d pages", MAX_PAGES)
    finally:
        if own:
            client.close()
    return sorted(trains.values(), key=lambda t: (t.date, t.dep))
