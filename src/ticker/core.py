from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import quote as urlquote
from urllib.request import Request, urlopen


class QuoteError(Exception):
    """A quote could not be retrieved."""


@dataclass
class Quote:
    symbol: str
    name: str
    currency: str
    price: float
    change: float | None
    change_percent: float | None
    previous_close: float | None
    market_state: str
    timestamp: int
    source: str = "yahoo"
    cached: bool = False
    stale: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "Quote":
        fields = cls.__dataclass_fields__
        return cls(**{key: item for key, item in value.items() if key in fields})


def cache_directory() -> Path:
    root = os.environ.get("XDG_CACHE_HOME")
    return Path(root) / "ticker" if root else Path.home() / ".cache" / "ticker"


class Cache:
    def __init__(self, directory: Path | None = None):
        self.directory = directory or cache_directory()

    def path_for(self, symbol: str) -> Path:
        safe = "".join(c if c.isalnum() else "_" for c in symbol.upper())
        return self.directory / f"{safe}.json"

    def load(self, symbol: str) -> tuple[Quote, int] | None:
        try:
            payload = json.loads(self.path_for(symbol).read_text())
            return Quote.from_dict(payload["quote"]), int(payload["saved_at"])
        except (OSError, ValueError, KeyError, TypeError):
            return None

    def save(self, value: Quote, now: int) -> None:
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            path = self.path_for(value.symbol)
            temporary = path.with_suffix(".tmp")
            temporary.write_text(json.dumps({"saved_at": now, "quote": value.to_dict()}))
            temporary.replace(path)
        except OSError:
            pass


class YahooProvider:
    base_url = "https://query1.finance.yahoo.com/v8/finance/chart"

    def __init__(self, opener: Callable[..., Any] = urlopen):
        self.opener = opener

    def get(self, symbol: str) -> Quote:
        url = f"{self.base_url}/{urlquote(symbol)}?interval=1d&range=1d"
        request = Request(url, headers={"User-Agent": "Mozilla/5.0 ticker-cli/0.1"})
        try:
            with self.opener(request, timeout=8) as response:
                payload = json.load(response)
        except HTTPError as error:
            if error.code == 429:
                raise QuoteError("provider rate limit reached") from error
            raise QuoteError(f"provider returned HTTP {error.code}") from error
        except (URLError, TimeoutError, OSError, ValueError) as error:
            raise QuoteError(f"network or provider error: {error}") from error

        try:
            chart = payload["chart"]
            if chart.get("error"):
                raise QuoteError(chart["error"].get("description", "provider error"))
            meta = chart["result"][0]["meta"]
            price = float(meta["regularMarketPrice"])
        except (KeyError, IndexError, TypeError, ValueError) as error:
            raise QuoteError(f"unknown symbol or incomplete quote: {symbol}") from error

        previous = _number(meta.get("chartPreviousClose") or meta.get("previousClose"))
        change = round(price - previous, 6) if previous is not None else None
        percent = round(change / previous * 100, 6) if change is not None and previous else None
        return Quote(
            symbol=str(meta.get("symbol", symbol)).upper(),
            name=str(meta.get("longName") or meta.get("shortName") or symbol.upper()),
            currency=str(meta.get("currency") or ""),
            price=price,
            change=change,
            change_percent=percent,
            previous_close=previous,
            market_state=_market_state(meta),
            timestamp=int(meta.get("regularMarketTime") or time.time()),
        )


def _number(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _market_state(meta: dict[str, Any]) -> str:
    if meta.get("marketState"):
        return str(meta["marketState"])
    now = int(time.time())
    periods = meta.get("currentTradingPeriod") or {}
    for state in ("pre", "regular", "post"):
        period = periods.get(state) or {}
        if int(period.get("start", 0)) <= now < int(period.get("end", 0)):
            return state.upper()
    return "CLOSED"


def get_quote(
    symbol: str,
    provider: YahooProvider,
    cache: Cache,
    ttl: int = 30,
    use_cache: bool = True,
    now: int | None = None,
) -> Quote:
    current = int(time.time() if now is None else now)
    cached = cache.load(symbol) if use_cache else None
    if cached and current - cached[1] <= ttl:
        cached[0].cached = True
        return cached[0]

    try:
        result = provider.get(symbol)
        if use_cache:
            cache.save(result, current)
        return result
    except QuoteError:
        if cached:
            cached[0].cached = True
            cached[0].stale = True
            return cached[0]
        raise
