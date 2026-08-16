from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import quote as urlquote
from urllib.request import Request, urlopen

from .profiler import Profiler


PERIODS = {
    "1d": ("1d", "5m"),
    "1w": ("5d", "30m"),
    "1m": ("1mo", "1d"),
    "3m": ("3mo", "1d"),
    "6m": ("6mo", "1d"),
    "1y": ("1y", "1d"),
    "3y": ("3y", "1wk"),
    "5y": ("5y", "1wk"),
    "ytd": ("ytd", "1d"),
}


class QuoteError(Exception):
    """Market data could not be retrieved."""


@dataclass
class ExtendedHours:
    session: str
    price: float
    change: float
    change_percent: float | None
    timestamp: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


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
    extended_hours: ExtendedHours | None = None

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["latest_price"] = self.latest_price
        value["latest_timestamp"] = self.latest_timestamp
        value["latest_session"] = self.latest_session
        return value

    @property
    def latest_price(self) -> float:
        return self.extended_hours.price if self.extended_hours else self.price

    @property
    def latest_timestamp(self) -> int:
        return self.extended_hours.timestamp if self.extended_hours else self.timestamp

    @property
    def latest_session(self) -> str:
        return self.extended_hours.session if self.extended_hours else self.market_state.lower()

    @property
    def latest_change_percent(self) -> float | None:
        if self.previous_close is None or not self.previous_close:
            return None
        return round((self.latest_price - self.previous_close) / self.previous_close * 100, 6)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "Quote":
        fields = cls.__dataclass_fields__
        filtered = {key: item for key, item in value.items() if key in fields}
        if isinstance(filtered.get("extended_hours"), dict):
            filtered["extended_hours"] = ExtendedHours(**filtered["extended_hours"])
        return cls(**filtered)


@dataclass
class Trend:
    period: str
    points: list[float]
    change_percent: float | None
    minimum: float | None
    maximum: float | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "Trend":
        return cls(
            period=str(value["period"]),
            points=[float(point) for point in value["points"]],
            change_percent=_number(value.get("change_percent")),
            minimum=_number(value.get("minimum")),
            maximum=_number(value.get("maximum")),
        )


class YahooProvider:
    base_url = "https://query1.finance.yahoo.com/v8/finance/chart"

    def __init__(self, opener: Callable[..., Any] = urlopen, profiler: Profiler | None = None):
        self.opener = opener
        self.profiler = profiler or Profiler()

    def get(self, symbol: str) -> Quote:
        quote, _ = self._request(symbol, "1d", "1m")
        return quote

    def get_with_trend(self, symbol: str, period: str) -> tuple[Quote, Trend]:
        if period not in PERIODS:
            raise QuoteError(f"unsupported chart period: {period}")
        data_range, interval = PERIODS[period]
        quote, result = self._request(symbol, data_range, interval)
        trend = self._trend(symbol, period, result, interval, quote)
        if quote.market_state != "REGULAR" and quote.extended_hours is None:
            quote, _ = self._request(symbol, "1d", "1m")
        return quote, trend

    def get_trend(self, symbol: str, period: str) -> Trend:
        """Fetch only trend data, for price-first progressive portfolio loading."""
        if period not in PERIODS:
            raise QuoteError(f"unsupported chart period: {period}")
        data_range, interval = PERIODS[period]
        quote, result = self._request(symbol, data_range, interval)
        return self._trend(symbol, period, result, interval, quote)

    def _trend(
        self,
        symbol: str,
        period: str,
        result: dict[str, Any],
        interval: str,
        quote: Quote,
    ) -> Trend:
        try:
            raw_points = result["indicators"]["quote"][0]["close"]
            timestamps = result["timestamp"]
        except (KeyError, IndexError, TypeError) as error:
            raise QuoteError(f"trend data unavailable: {symbol}") from error
        all_pairs = [
            (int(timestamp), number)
            for timestamp, value in zip(timestamps, raw_points)
            if (number := _number(value)) is not None
        ]
        pairs = _regular_session_points(all_pairs, result.get("meta", {}), interval)
        points = [point for _, point in pairs]
        previous = _previous_session_close(pairs, result.get("meta", {}), interval)
        if previous is not None:
            quote.previous_close = previous
            quote.change = round(quote.price - previous, 6)
            quote.change_percent = round(quote.change / previous * 100, 6) if previous else None
        if len(points) < 2:
            change_percent = None
        else:
            change_percent = round((points[-1] - points[0]) / points[0] * 100, 6) if points[0] else None
        return Trend(
            period=period,
            points=points,
            change_percent=change_percent,
            minimum=min(points) if points else None,
            maximum=max(points) if points else None,
        )

    def _request(self, symbol: str, data_range: str, interval: str) -> tuple[Quote, dict[str, Any]]:
        url = (
            f"{self.base_url}/{urlquote(symbol)}"
            f"?interval={interval}&range={data_range}&includePrePost=true"
        )
        request = Request(url, headers={"User-Agent": "Mozilla/5.0 ticker-cli/0.2"})
        try:
            with self.profiler.span(
                "request", symbol=symbol, range=data_range, interval=interval
            ) as request_info:
                with self.profiler.span(
                    "network", symbol=symbol, range=data_range, interval=interval
                ) as network_info:
                    with self.opener(request, timeout=8) as response:
                        body = response.read()
                        network_info["bytes"] = len(body)
                        network_info["status"] = getattr(response, "status", None)
                with self.profiler.span("parse_json", symbol=symbol, range=data_range):
                    payload = json.loads(body)
                request_info["bytes"] = len(body)
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
            result = chart["result"][0]
            meta = result["meta"]
            price = float(meta["regularMarketPrice"])
        except (KeyError, IndexError, TypeError, ValueError) as error:
            raise QuoteError(f"unknown symbol or incomplete quote: {symbol}") from error

        previous = _number(meta.get("chartPreviousClose") or meta.get("previousClose"))
        change = round(price - previous, 6) if previous is not None else None
        percent = round(change / previous * 100, 6) if change is not None and previous else None
        quote = Quote(
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
        quote.extended_hours = _extended_hours(result, quote)
        return quote, result


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


def _previous_session_close(
    pairs: list[tuple[int, float]], meta: dict[str, Any], interval: str
) -> float | None:
    if len(pairs) < 2:
        return None
    if interval == "1d":
        return pairs[-2][1]
    offset = int(meta.get("gmtoffset", 0))
    latest_day = (pairs[-1][0] + offset) // 86400
    for timestamp, point in reversed(pairs[:-1]):
        if (timestamp + offset) // 86400 < latest_day:
            return point
    return None


def _regular_session_points(
    pairs: list[tuple[int, float]], meta: dict[str, Any], interval: str
) -> list[tuple[int, float]]:
    if interval in {"1d", "1wk"}:
        return pairs
    regular = (meta.get("currentTradingPeriod") or {}).get("regular") or {}
    start, end = int(regular.get("start", 0)), int(regular.get("end", 0))
    if not start or not end:
        return pairs
    offset = int(meta.get("gmtoffset", 0))
    start_second = (start + offset) % 86400
    end_second = (end + offset) % 86400
    return [
        (timestamp, point)
        for timestamp, point in pairs
        if start_second <= (timestamp + offset) % 86400 <= end_second
    ]


def _extended_hours(result: dict[str, Any], quote: Quote) -> ExtendedHours | None:
    meta = result.get("meta") or {}
    if not meta.get("hasPrePostMarketData"):
        return None
    try:
        timestamps = result["timestamp"]
        closes = result["indicators"]["quote"][0]["close"]
    except (KeyError, IndexError, TypeError):
        return None
    pairs = [
        (int(timestamp), number)
        for timestamp, value in zip(timestamps, closes)
        if (number := _number(value)) is not None
    ]
    if not pairs or pairs[-1][0] <= quote.timestamp:
        return None
    timestamp, price = pairs[-1]
    periods = meta.get("currentTradingPeriod") or {}
    pre = periods.get("pre") or {}
    post = periods.get("post") or {}
    if int(post.get("start", 0)) <= timestamp <= int(post.get("end", 0)):
        session = "post"
    elif int(pre.get("start", 0)) <= timestamp <= int(pre.get("end", 0)):
        session = "pre"
    elif timestamp > quote.timestamp:
        # Yahoo may mark the market CLOSED immediately after the extended
        # session while its latest returned point is still a post-market trade.
        session = "post"
    else:
        return None
    change = round(price - quote.price, 6)
    change_percent = round(change / quote.price * 100, 6) if quote.price else None
    return ExtendedHours(session, price, change, change_percent, timestamp)


def get_quote(
    symbol: str,
    provider: YahooProvider,
) -> Quote:
    quote, _ = get_market_data(symbol, provider, None)
    return quote


def get_market_data(
    symbol: str,
    provider: YahooProvider,
    period: str | None,
) -> tuple[Quote, Trend | None]:
    if period:
        return provider.get_with_trend(symbol, period)
    return provider.get(symbol), None
