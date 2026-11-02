from dataclasses import dataclass


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

    def label(self) -> str:
        dates = self.date_from if self.date_from == self.date_to else f"{self.date_from} → {self.date_to}"
        window = f" {self.time_from}-{self.time_to}" if self.time_from else ""
        return f"#{self.id} {self.origin} → {self.dest} · {dates}{window}"

    def accepts(self, t: Train) -> bool:
        if not (self.date_from <= t.date <= self.date_to):
            return False
        if self.time_from and t.dep < self.time_from:
            return False
        if self.time_to and t.dep > self.time_to:
            return False
        return True


class SourceError(Exception):
    """Raised when a data source is unreachable, blocked, or returns garbage."""
