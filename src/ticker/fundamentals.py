from __future__ import annotations

import json
import re
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import quote as urlquote
from urllib.request import Request, urlopen

from .portfolio import state_directory
from .profiler import Profiler


SEC_BASE = "https://data.sec.gov"
USER_AGENT = "terminal-ticker/0.2 github.com/carerley/terminal-ticker"
REVENUE_CONCEPTS = (
    "RevenueFromContractWithCustomerExcludingAssessedTax",
    "Revenues",
    "SalesRevenueNet",
)


@dataclass
class Fundamentals:
    market_cap: float | None = None
    pe_ratio: float | None = None
    revenue_growth: float | None = None
    shares_outstanding: float | None = None
    trailing_eps: float | None = None
    fiscal_period: str | None = None
    filed: str | None = None
    source: str = "sec"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class FundamentalsProvider:
    """SEC Company Facts adapter with a daily local filing cache."""

    def __init__(
        self,
        opener: Callable[..., Any] = urlopen,
        cache_dir: Path | None = None,
        profiler: Profiler | None = None,
        ttl: int = 86400,
    ):
        self.opener = opener
        self.cache_dir = cache_dir or state_directory() / "fundamentals"
        self.profiler = profiler or Profiler()
        self.ttl = ttl
        self._mapping: dict[str, int] | None = None
        self._mapping_lock = threading.Lock()

    def get(self, symbol: str, latest_price: float) -> Fundamentals:
        yahoo = self._yahoo(symbol)
        if yahoo.pe_ratio is None and yahoo.trailing_eps is not None and yahoo.trailing_eps > 0:
            yahoo.pe_ratio = latest_price / yahoo.trailing_eps
        # An empty result is normal for ETFs. Do not automatically retry SEC:
        # shared networks are frequently blocked and would add an 8-second
        # penalty. The SEC parser remains available for cached/future adapters.
        return yahoo

    def _yahoo(self, symbol: str) -> Fundamentals:
        now = int(time.time())
        types = "quarterlyTotalRevenue,trailingMarketCap,trailingPeRatio,trailingDilutedEPS"
        url = (
            "https://query1.finance.yahoo.com/ws/fundamentals-timeseries/v1/finance/"
            f"timeseries/{urlquote(symbol)}?symbol={urlquote(symbol)}&type={types}"
            f"&period1={now - 63072000}&period2={now + 86400}"
        )
        request = Request(url, headers={"User-Agent": "Mozilla/5.0 ticker-cli/0.2"})
        try:
            with self.profiler.span("network", symbol=symbol, source="yahoo_fundamentals") as info:
                with self.opener(request, timeout=8) as response:
                    body = response.read()
                    info["bytes"] = len(body)
            payload = json.loads(body)
        except (HTTPError, URLError, TimeoutError, OSError, ValueError):
            return Fundamentals(source="yahoo")
        series = {
            str(result.get("meta", {}).get("type", [""])[0]): result
            for result in payload.get("timeseries", {}).get("result", [])
            if isinstance(result, dict)
        }
        market_cap = _latest_yahoo_value(series.get("trailingMarketCap"), "trailingMarketCap")
        pe = _latest_yahoo_value(series.get("trailingPeRatio"), "trailingPeRatio")
        trailing_eps = _latest_yahoo_value(
            series.get("trailingDilutedEPS"), "trailingDilutedEPS"
        )
        revenues = _yahoo_values(series.get("quarterlyTotalRevenue"), "quarterlyTotalRevenue")
        growth = None
        if len(revenues) >= 5 and revenues[-5][1]:
            growth = round((revenues[-1][1] / revenues[-5][1] - 1) * 100, 6)
        latest_date = revenues[-1][0] if revenues else None
        return Fundamentals(
            market_cap=market_cap,
            pe_ratio=pe,
            revenue_growth=growth,
            trailing_eps=trailing_eps,
            fiscal_period=latest_date,
            filed=latest_date,
            source="yahoo",
        )

    def _cik(self, symbol: str) -> int | None:
        with self._mapping_lock:
            if self._mapping is None:
                payload = self._cached_json(
                    "company_tickers.json",
                    f"{SEC_BASE}/files/company_tickers.json",
                    "mapping",
                )
                self._mapping = {
                    str(item["ticker"]).upper().replace(".", "-"): int(item["cik_str"])
                    for item in payload.values()
                    if isinstance(item, dict) and "ticker" in item and "cik_str" in item
                }
        return self._mapping.get(symbol.upper().replace(".", "-"))

    def _cached_json(self, filename: str, url: str, symbol: str) -> dict[str, Any]:
        path = self.cache_dir / filename
        try:
            if time.time() - path.stat().st_mtime < self.ttl:
                with self.profiler.span("fundamentals_cache", symbol=symbol):
                    return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass
        request = Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with self.profiler.span("network", symbol=symbol, source="sec") as info:
                with self.opener(request, timeout=8) as response:
                    body = response.read()
                    info["bytes"] = len(body)
            payload = json.loads(body)
        except (HTTPError, URLError, TimeoutError, OSError, ValueError):
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                return {}
        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            temporary = path.with_suffix(".tmp")
            temporary.write_text(json.dumps(payload), encoding="utf-8")
            temporary.replace(path)
        except OSError:
            pass
        return payload


def fundamentals_from_companyfacts(payload: dict[str, Any], price: float) -> Fundamentals:
    facts = payload.get("facts", {}).get("us-gaap", {})
    shares_items = _units(facts, "EntityCommonStockSharesOutstanding", "shares")
    shares_item = _latest(shares_items)
    shares = _value(shares_item)

    eps_items = _quarterly(_units(facts, "EarningsPerShareDiluted", "USD/shares"))
    latest_eps = eps_items[-4:]
    trailing_eps = sum(_value(item) or 0 for item in latest_eps) if len(latest_eps) == 4 else None

    revenue_items: list[dict[str, Any]] = []
    for concept in REVENUE_CONCEPTS:
        revenue_items = _quarterly(_units(facts, concept, "USD"))
        if revenue_items:
            break
    growth = None
    fiscal_period = filed = None
    if revenue_items:
        current = revenue_items[-1]
        fiscal_period = str(current.get("frame") or current.get("fy") or "") or None
        filed = str(current.get("filed") or "") or None
        current_frame = _frame(current)
        if current_frame:
            prior = next(
                (item for item in revenue_items if _frame(item) == (current_frame[0] - 1, current_frame[1])),
                None,
            )
            current_value, prior_value = _value(current), _value(prior)
            if current_value is not None and prior_value:
                growth = round((current_value / prior_value - 1) * 100, 6)

    market_cap = price * shares if shares is not None else None
    pe = price / trailing_eps if trailing_eps is not None and trailing_eps > 0 else None
    return Fundamentals(market_cap, pe, growth, shares, trailing_eps, fiscal_period, filed)


def _units(facts: dict[str, Any], concept: str, unit: str) -> list[dict[str, Any]]:
    units = facts.get(concept, {}).get("units", {})
    values = units.get(unit, [])
    return [item for item in values if isinstance(item, dict)]


def _latest(items: list[dict[str, Any]]) -> dict[str, Any] | None:
    valid = [item for item in items if item.get("form") in {"10-Q", "10-K", "20-F", "40-F"}]
    return max(valid, key=lambda item: (str(item.get("end", "")), str(item.get("filed", ""))), default=None)


def _quarterly(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_frame: dict[tuple[int, int], dict[str, Any]] = {}
    for item in items:
        frame = _frame(item)
        if frame is None or item.get("form") not in {"10-Q", "10-K"}:
            continue
        previous = by_frame.get(frame)
        if previous is None or str(item.get("filed", "")) > str(previous.get("filed", "")):
            by_frame[frame] = item
    return [by_frame[key] for key in sorted(by_frame)]


def _frame(item: dict[str, Any] | None) -> tuple[int, int] | None:
    if not item:
        return None
    match = re.fullmatch(r"CY(\d{4})Q([1-4])(?:I)?", str(item.get("frame", "")))
    return (int(match.group(1)), int(match.group(2))) if match else None


def _value(item: dict[str, Any] | None) -> float | None:
    if not item:
        return None
    try:
        return float(item["val"])
    except (KeyError, TypeError, ValueError):
        return None


def _yahoo_values(result: dict[str, Any] | None, field: str) -> list[tuple[str, float]]:
    if not result:
        return []
    values: list[tuple[str, float]] = []
    for item in result.get(field, []):
        try:
            values.append((str(item["asOfDate"]), float(item["reportedValue"]["raw"])))
        except (KeyError, TypeError, ValueError):
            continue
    return sorted(values)


def _latest_yahoo_value(result: dict[str, Any] | None, field: str) -> float | None:
    values = _yahoo_values(result, field)
    return values[-1][1] if values else None
