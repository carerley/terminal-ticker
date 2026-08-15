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


class Portfolio:
    def __init__(self, path: Path | None = None, limit: int = 50):
        self.path = path or state_directory() / "portfolio.json"
        self.limit = limit

    def entries(self) -> list[PortfolioEntry]:
        try:
            payload = json.loads(self.path.read_text())
            entries = [
                PortfolioEntry(str(item["symbol"]).upper(), int(item["last_queried"]))
                for item in payload["symbols"]
            ]
            return entries[: self.limit]
        except (OSError, ValueError, KeyError, TypeError):
            return []

    def touch(self, symbols: list[str], now: int | None = None) -> None:
        if not symbols:
            return
        timestamp = int(time.time() if now is None else now)
        normalized = list(dict.fromkeys(symbol.upper() for symbol in symbols))
        touched = set(normalized)
        existing = [entry for entry in self.entries() if entry.symbol not in touched]
        entries = [PortfolioEntry(symbol, timestamp) for symbol in normalized] + existing
        self._save(entries[: self.limit])

    def forget(self, symbols: list[str]) -> list[str]:
        targets = {symbol.upper() for symbol in symbols}
        existing = self.entries()
        removed = [entry.symbol for entry in existing if entry.symbol in targets]
        self._save([entry for entry in existing if entry.symbol not in targets])
        return removed

    def clear(self) -> int:
        count = len(self.entries())
        self._save([])
        return count

    def _save(self, entries: list[PortfolioEntry]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(
                {"version": 1, "symbols": [asdict(entry) for entry in entries]},
                indent=2,
            )
            + "\n"
        )
        temporary.replace(self.path)
