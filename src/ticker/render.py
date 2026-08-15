from __future__ import annotations

import os
import sys
import time
from dataclasses import dataclass

from .core import Quote, Trend


GREEN = "\033[32m"
RED = "\033[31m"
RESET = "\033[0m"
BLOCKS = "▁▂▃▄▅▆▇█"
ASCII_BLOCKS = "._-~=+*#"


@dataclass
class Color:
    mode: str = "auto"

    @property
    def enabled(self) -> bool:
        if self.mode == "never" or "NO_COLOR" in os.environ:
            return False
        return self.mode == "always" or sys.stdout.isatty()

    def change(self, text: str, value: float | None) -> str:
        if not self.enabled or value is None or value == 0:
            return text
        return f"{GREEN if value > 0 else RED}{text}{RESET}"


def money(value: float, currency: str) -> str:
    prefix = "$" if currency == "USD" else ""
    suffix = "" if currency == "USD" or not currency else f" {currency}"
    return f"{prefix}{value:,.2f}{suffix}"


def percent(value: float | None) -> str:
    return "N/A" if value is None else f"{value:+.2f}%"


def compact_number(value: float | None, currency: str = "") -> str:
    if value is None:
        return "N/A"
    prefix = "$" if currency == "USD" else ""
    for divisor, suffix in ((1e12, "T"), (1e9, "B"), (1e6, "M")):
        if abs(value) >= divisor:
            return f"{prefix}{value / divisor:.1f}{suffix}"
    return f"{prefix}{value:,.0f}"


def pe_text(value: float | None, earnings_available: bool = True) -> str:
    if not earnings_available:
        return "N/A"
    return "N/M" if value is None else f"{value:.1f}x"


def change_text(quote: Quote) -> str:
    if quote.change is None or quote.change_percent is None:
        return "change unavailable"
    return f"{quote.change:+.2f} ({quote.change_percent:+.2f}%)"


def sparkline(points: list[float], width: int = 16, ascii_only: bool = False) -> str:
    if len(points) < 2 or width < 2:
        return "N/A"
    sampled = _sample(points, min(width, len(points)))
    low, high = min(sampled), max(sampled)
    chars = ASCII_BLOCKS if ascii_only else BLOCKS
    if high == low:
        return chars[len(chars) // 2] * len(sampled)
    scale = len(chars) - 1
    return "".join(chars[round((point - low) / (high - low) * scale)] for point in sampled)


def _sample(points: list[float], width: int) -> list[float]:
    if len(points) <= width:
        return points
    if width == 1:
        return [points[-1]]
    return [points[round(index * (len(points) - 1) / (width - 1))] for index in range(width)]


def age_text(timestamp: int, now: int | None = None) -> str:
    age = max(0, int(time.time() if now is None else now) - timestamp)
    if age < 60:
        return f"{age}s"
    if age < 3600:
        return f"{age // 60}m"
    if age < 86400:
        return f"{age // 3600}h"
    return f"{age // 86400}d"


def trend_line(trend: Trend, color: Color, width: int = 24, ascii_only: bool = False) -> str:
    chart = sparkline(trend.points, width, ascii_only)
    chart = color.change(chart, trend.change_percent)
    result = color.change(percent(trend.change_percent), trend.change_percent)
    bounds = ""
    if trend.minimum is not None and trend.maximum is not None:
        bounds = f"  {trend.minimum:,.2f}—{trend.maximum:,.2f}"
    return f"{trend.period.upper():<4} {chart}  {result}{bounds}"
