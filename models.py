from dataclasses import dataclass
from datetime import date

from stations import route_station

RECURRING_END = "2099-12-31"  # date_to of a weekly route, so it never expires
WEEKDAYS = ("lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche")


def format_day(iso: str) -> str:
    d = date.fromisoformat(iso)
    return f"{WEEKDAYS[d.weekday()]} {d.day:02d}/{d.month:02d}"


@dataclass(frozen=True)
class Train:
    date: str          # YYYY-MM-DD
    dep: str           # HH:MM
    arr: str           # HH:MM ("" if unknown)
    train_no: str
    origin: str        # human-readable station label
    dest: str
    seats: int | None = None  # free MAX seats, if the source tells us

    @property
    def key(self) -> str:
        return f"{self.date}|{self.train_no.lstrip('0')}|{self.dep}"


@dataclass
class Route:
    id: int
    origin: str        # short name or code, as typed by the user
    dest: str
    date_from: str     # YYYY-MM-DD
    date_to: str       # YYYY-MM-DD
    time_from: str | None = None  # HH:MM
    time_to: str | None = None
    weekday: int | None = None    # 0 = Monday; set on a route that repeats every week

    def slot(self) -> str:
        dates = format_day(self.date_from)
        if self.weekday is not None:
            dates = f"chaque {WEEKDAYS[self.weekday]}"
        elif self.date_to != self.date_from:
            dates += f" → {format_day(self.date_to)}"
        window = f" · {self.time_from}-{self.time_to}" if self.time_from else ""
        return f"#{self.id} · {dates}{window}"

    def label(self) -> str:
        return f"{route_station(self.origin)} → {route_station(self.dest)} {self.slot()}"

    def accepts(self, t: Train) -> bool:
        if not (self.date_from <= t.date <= self.date_to):
            return False
        if self.weekday is not None and date.fromisoformat(t.date).weekday() != self.weekday:
            return False
        if self.time_from and t.dep < self.time_from:
            return False
        if self.time_to and t.dep > self.time_to:
            return False
        return True


class SourceError(Exception):
    """Raised when a data source is unreachable, blocked, or returns garbage."""
