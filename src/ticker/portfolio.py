from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass
from pathlib import Path


def state_directory() -> Path:
    root = os.environ.get("XDG_STATE_HOME")
    return Path(root) / "ticker" if root else Path.home() / ".local" / "state" / "ticker"


@dataclass
class PortfolioEntry:
    symbol: str
    last_queried: int
    added_at: int


@dataclass
class Preferences:
    view: str = "basic"
    sort_column: str | None = None
    sort_descending: bool = False
    active_tab: str = "watchlist"


class Portfolio:
    def __init__(self, path: Path | None = None, limit: int = 50):
        self.path = path or state_directory() / "portfolio.json"
        self.limit = limit

    def entries(self) -> list[PortfolioEntry]:
        try:
            payload = json.loads(self.path.read_text())
            entries = [
                PortfolioEntry(
                    str(item["symbol"]).upper(),
                    int(item["last_queried"]),
                    int(item.get("added_at", item["last_queried"])),
                )
                for item in payload["symbols"]
            ]
            return entries[: self.limit]
        except (OSError, ValueError, KeyError, TypeError):
            return []

    def preferences(self) -> Preferences:
        try:
            payload = json.loads(self.path.read_text())
            raw = payload.get("preferences", {})
            view = str(raw.get("view", "basic"))
            active_tab = str(raw.get("active_tab", "watchlist"))
            sort_column = raw.get("sort_column")
            if (
                int(payload.get("version", 1)) <= 3
                and sort_column == "symbol"
                and not bool(raw.get("sort_descending", False))
            ):
                sort_column = None
            return Preferences(
                view=view if view in {"basic", "extended", "study"} else "basic",
                sort_column=(
                    str(sort_column)
                    if sort_column is not None
                    else None
                ),
                sort_descending=bool(raw.get("sort_descending", False)),
                active_tab=active_tab if active_tab in {"watchlist", "community"} else "watchlist",
            )
        except (OSError, ValueError, TypeError):
            return Preferences()

    def save_preferences(self, preferences: Preferences) -> None:
        self._save(self.entries(), preferences)

    def add(self, symbols: list[str], now: int | None = None) -> list[str]:
        existing = {entry.symbol for entry in self.entries()}
        normalized = list(dict.fromkeys(symbol.upper() for symbol in symbols))
        added = [symbol for symbol in normalized if symbol not in existing]
        if added:
            self.touch(added, now)
        return added

    def touch(self, symbols: list[str], now: int | None = None) -> None:
        if not symbols:
            return
        timestamp = int(time.time() if now is None else now)
        normalized = list(dict.fromkeys(symbol.upper() for symbol in symbols))
        current_entries = self.entries()
        current = {entry.symbol: entry for entry in current_entries}
        new_entries = [
            PortfolioEntry(symbol, timestamp, timestamp)
            for symbol in normalized
            if symbol not in current
        ]
        touched = set(normalized)
        existing = [
            PortfolioEntry(
                entry.symbol,
                timestamp if entry.symbol in touched else entry.last_queried,
                entry.added_at,
            )
            for entry in current_entries
        ]
        entries = new_entries + existing
        self._save(entries[: self.limit], self.preferences())

    def forget(self, symbols: list[str]) -> list[str]:
        targets = {symbol.upper() for symbol in symbols}
        existing = self.entries()
        removed = [entry.symbol for entry in existing if entry.symbol in targets]
        self._save([entry for entry in existing if entry.symbol not in targets], self.preferences())
        return removed

    def clear(self) -> int:
        count = len(self.entries())
        self._save([], self.preferences())
        return count

    def _save(self, entries: list[PortfolioEntry], preferences: Preferences | None = None) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(
                {
                    "version": 4,
                    "symbols": [asdict(entry) for entry in entries],
                    "preferences": asdict(preferences or Preferences()),
                },
                indent=2,
            )
            + "\n"
        )
        temporary.replace(self.path)
