"""CSV-backed chain provider.

Expected columns: ``ts``, ``expiry``, ``strike``, ``right``, ``bid``, ``ask``.
Timestamps and expiries are parsed with ``datetime.fromisoformat`` and
``date.fromisoformat`` respectively. ``right`` accepts PUT/CALL or P/C.
"""

from __future__ import annotations

import csv
from collections.abc import Iterable, Mapping
from datetime import date, datetime
from os import PathLike
from pathlib import Path
from typing import Literal

from fillsim.providers.base import Quote

REQUIRED_COLUMNS = frozenset({"ts", "expiry", "strike", "right", "bid", "ask"})


class CSVChainProvider:
    """A ChainProvider backed by a tidy option-quote CSV file.

    The file is loaded once at construction time and then queried in memory.
    This keeps the implementation dependency-free and predictable for tests,
    examples, and small research exports.
    """

    def __init__(
        self,
        path: str | PathLike[str],
        spots: Mapping[datetime, float] | None = None,
    ) -> None:
        self.path = Path(path)
        self._quotes = _load_quotes(self.path)
        self._spots = dict(spots) if spots is not None else {}

    def get_quotes(
        self,
        start_ts: datetime,
        end_ts: datetime,
        expiry: date,
        strikes: list[float],
        right: Literal["PUT", "CALL"] = "PUT",
    ) -> Iterable[Quote]:
        sset = set(strikes)
        for q in self._quotes:
            if q.right != right:
                continue
            if q.expiry != expiry:
                continue
            if q.strike not in sset:
                continue
            if not (start_ts <= q.ts <= end_ts):
                continue
            yield q

    def get_spot(self, ts: datetime, symbol: str = "SPY") -> float | None:
        return self._spots.get(ts)


def _load_quotes(path: Path) -> list[Quote]:
    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        fieldnames = set(reader.fieldnames or [])
        missing = REQUIRED_COLUMNS - fieldnames
        if missing:
            missing_cols = ", ".join(sorted(missing))
            raise ValueError(f"CSV quote file is missing required columns: {missing_cols}")
        return [_quote_from_row(row) for row in reader]


def _quote_from_row(row: Mapping[str, str]) -> Quote:
    return Quote(
        ts=_parse_datetime(row["ts"]),
        expiry=date.fromisoformat(row["expiry"].strip()),
        strike=float(row["strike"]),
        right=_parse_right(row["right"]),
        bid=float(row["bid"]),
        ask=float(row["ask"]),
    )


def _parse_datetime(value: str) -> datetime:
    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = f"{normalized[:-1]}+00:00"
    return datetime.fromisoformat(normalized)


def _parse_right(value: str) -> Literal["PUT", "CALL"]:
    normalized = value.strip().upper()
    if normalized in {"P", "PUT"}:
        return "PUT"
    if normalized in {"C", "CALL"}:
        return "CALL"
    raise ValueError(f"right must be PUT/CALL or P/C, got {value!r}")
