from __future__ import annotations

import curses
import queue
from concurrent.futures import CancelledError, Future, ThreadPoolExecutor
from dataclasses import dataclass

from .core import Quote, QuoteError, Trend, YahooProvider
from .fundamentals import Fundamentals, FundamentalsProvider
from .portfolio import PortfolioEntry
from .render import compact_number, money, pe_text, percent, sparkline


@dataclass
class PortfolioRow:
    symbol: str
    last_queried: int | None
    quote: Quote | None = None
    trend: Trend | None = None
    error: str | None = None
    trend_error: bool = False
    ytd: Trend | None = None
    ytd_error: bool = False
    fundamentals: Fundamentals | None = None
    fundamentals_done: bool = False


@dataclass
class Cell:
    text: str
    width: int
    align: str = "left"
    change: float | None = None

    def formatted(self) -> str:
        return f"{self.text:>{self.width}}" if self.align == "right" else f"{self.text:<{self.width}}"


def move_selection(selected: int, key: int, count: int) -> int:
    if count <= 0:
        return 0
    if key in (curses.KEY_UP, ord("k")):
        return max(0, selected - 1)
    if key in (curses.KEY_DOWN, ord("j")):
        return min(count - 1, selected + 1)
    if key == curses.KEY_HOME:
        return 0
    if key == curses.KEY_END:
        return count - 1
    return selected


def run_portfolio(rows: list[PortfolioRow], period: str | None, ascii_only: bool) -> None:
    curses.wrapper(_run, rows, period, ascii_only, None)


def run_progressive_portfolio(
    entries: list[PortfolioEntry],
    period: str | None,
    ascii_only: bool,
    provider: YahooProvider,
    fundamentals_provider: FundamentalsProvider,
    workers: int = 4,
) -> None:
    rows = [PortfolioRow(entry.symbol, entry.last_queried) for entry in entries]
    updates: queue.SimpleQueue[tuple[str, str, object]] = queue.SimpleQueue()
    executor = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="ticker")

    def trend_done(symbol: str, kind: str, future: Future[Trend]) -> None:
        try:
            updates.put((symbol, kind, future.result()))
        except CancelledError:
            return
        except QuoteError as error:
            updates.put((symbol, f"{kind}_error", str(error)))

    def fundamentals_done(symbol: str, future: Future[Fundamentals]) -> None:
        try:
            updates.put((symbol, "fundamentals", future.result()))
        except CancelledError:
            return
        except (OSError, ValueError):
            updates.put((symbol, "fundamentals_error", "unavailable"))

    def quote_done(symbol: str, future: Future[Quote]) -> None:
        try:
            updates.put((symbol, "quote", future.result()))
            try:
                trend_period = period or "1m"
                trend_future = executor.submit(provider.get_trend, symbol, trend_period)
                trend_future.add_done_callback(
                    lambda done, item=symbol: trend_done(item, "trend", done)
                )
                if trend_period != "ytd":
                    ytd_future = executor.submit(provider.get_trend, symbol, "ytd")
                    ytd_future.add_done_callback(
                        lambda done, item=symbol: trend_done(item, "ytd", done)
                    )
                fundamental_future = executor.submit(
                    fundamentals_provider.get, symbol, future.result().latest_price
                )
                fundamental_future.add_done_callback(
                    lambda done, item=symbol: fundamentals_done(item, done)
                )
            except RuntimeError:
                pass
        except CancelledError:
            return
        except QuoteError as error:
            updates.put((symbol, "error", str(error)))

    for entry in entries:
        future = executor.submit(provider.get, entry.symbol)
        future.add_done_callback(lambda done, symbol=entry.symbol: quote_done(symbol, done))
    try:
        curses.wrapper(_run, rows, period, ascii_only, updates)
    finally:
        executor.shutdown(wait=False, cancel_futures=True)


def _run(
    screen: curses.window,
    rows: list[PortfolioRow],
    period: str | None,
    ascii_only: bool,
    updates: queue.SimpleQueue[tuple[str, str, object]] | None,
) -> None:
    try:
        curses.curs_set(0)
    except curses.error:
        pass
    screen.keypad(True)
    if updates is not None:
        screen.timeout(50)
    subtle_background = False
    if curses.has_colors():
        curses.start_color()
        curses.use_default_colors()
        curses.init_pair(1, curses.COLOR_GREEN, -1)
        curses.init_pair(2, curses.COLOR_RED, -1)
        if curses.COLORS >= 256:
            subtle_background = True
            curses.init_pair(3, -1, 236)
            curses.init_pair(4, curses.COLOR_GREEN, 236)
            curses.init_pair(5, curses.COLOR_RED, 236)

    selected = 0
    offset = 0
    while True:
        if updates is not None:
            _apply_updates(rows, updates)
        height, width = screen.getmaxyx()
        visible = max(1, height - 3)
        if selected < offset:
            offset = selected
        elif selected >= offset + visible:
            offset = selected - visible + 1
        _draw(
            screen,
            rows,
            period,
            ascii_only,
            selected,
            offset,
            height,
            width,
            subtle_background,
        )
        key = screen.getch()
        if key == -1:
            continue
        if key in (ord("q"), 27):
            return
        selected = move_selection(selected, key, len(rows))


def _apply_updates(
    rows: list[PortfolioRow], updates: queue.SimpleQueue[tuple[str, str, object]]
) -> None:
    by_symbol = {row.symbol: row for row in rows}
    while True:
        try:
            symbol, kind, value = updates.get_nowait()
        except queue.Empty:
            return
        row = by_symbol[symbol]
        if kind == "quote":
            row.quote = value  # type: ignore[assignment]
        elif kind == "trend":
            row.trend = value  # type: ignore[assignment]
        elif kind == "error":
            row.error = str(value)
        elif kind == "trend_error":
            row.trend_error = True
        elif kind == "ytd":
            row.ytd = value  # type: ignore[assignment]
        elif kind == "ytd_error":
            row.ytd_error = True
        elif kind == "fundamentals":
            row.fundamentals = value  # type: ignore[assignment]
            row.fundamentals_done = True
        elif kind == "fundamentals_error":
            row.fundamentals_done = True


def _draw(
    screen: curses.window,
    rows: list[PortfolioRow],
    period: str | None,
    ascii_only: bool,
    selected: int,
    offset: int,
    height: int,
    width: int,
    subtle_background: bool,
) -> None:
    screen.erase()
    has_extended = any(row.quote and row.quote.extended_hours for row in rows)
    chart_width = max(8, min(14, width - (87 if has_extended else 60)))
    show_cap = width >= (119 if has_extended else 92)
    show_pe = width >= (129 if has_extended else 102)
    show_growth = width >= (140 if has_extended else 114)
    header = _header_cells(rows, chart_width, has_extended, show_cap, show_pe, show_growth)
    _draw_cells(screen, 0, header, width, curses.A_BOLD, False)

    visible = max(1, height - 3)
    for screen_index, row in enumerate(rows[offset : offset + visible], start=1):
        row_index = offset + screen_index - 1
        highlighted = row_index == selected
        if highlighted:
            highlight_attribute = curses.color_pair(3) if subtle_background else curses.A_BOLD
            try:
                screen.addnstr(
                    screen_index,
                    0,
                    " " * max(0, width - 1),
                    max(0, width - 1),
                    highlight_attribute,
                )
            except curses.error:
                pass
        cells = _row_cells(
            row,
            chart_width,
            has_extended,
            show_cap,
            show_pe,
            show_growth,
            ascii_only,
            highlighted,
        )
        _draw_cells(
            screen,
            screen_index,
            cells,
            width,
            0,
            highlighted,
            subtle_background,
        )

    loaded = sum(row.quote is not None or row.error is not None for row in rows)
    charted = sum(row.trend is not None or row.trend_error for row in rows)
    enriched = sum(row.fundamentals_done for row in rows)
    progress = f"Loaded {loaded}/{len(rows)}"
    progress += f" · charts {charted}/{len(rows)}"
    if show_cap:
        progress += f" · facts {enriched}/{len(rows)}"
    footer = f" {progress}  •  ↑/↓ or j/k select  •  Home/End jump  •  q or Esc quit "
    try:
        screen.addnstr(
            height - 1,
            0,
            footer.ljust(max(0, width - 1)),
            max(0, width - 1),
            curses.A_DIM,
        )
    except curses.error:
        pass
    screen.refresh()


def _header_cells(
    rows: list[PortfolioRow],
    chart_width: int,
    has_extended: bool,
    show_cap: bool,
    show_pe: bool,
    show_growth: bool,
) -> list[Cell]:
    cells = [Cell("", 1), Cell("SYMBOL", 8), Cell("│", 1)]
    if has_extended:
        sessions = {
            row.quote.extended_hours.session
            for row in rows
            if row.quote and row.quote.extended_hours
        }
        cells.extend([
            Cell("CLOSE", 12, "right"),
            Cell("PRE-MARKET" if sessions == {"pre"} else "AFTER HOURS", 12, "right"),
            Cell("PRE %" if sessions == {"pre"} else "AFTER %", 9, "right"),
        ])
    else:
        cells.append(Cell("PRICE", 12, "right"))
    cells.extend([
        Cell("TODAY", 9, "right"), Cell("│", 1), Cell("1M TREND", chart_width),
        Cell("1M", 8, "right"), Cell("YTD", 8, "right"),
    ])
    if show_cap:
        cells.append(Cell("MKT CAP", 10, "right"))
    if show_pe:
        cells.append(Cell("P/E", 8, "right"))
    if show_growth:
        cells.append(Cell("REV YOY", 9, "right"))
    return cells


def _row_cells(
    row: PortfolioRow,
    chart_width: int,
    has_extended: bool,
    show_cap: bool,
    show_pe: bool,
    show_growth: bool,
    ascii_only: bool,
    highlighted: bool,
) -> list[Cell]:
    quote = row.quote
    marker = "›" if highlighted and not ascii_only else ">" if highlighted else ""
    if quote is None:
        status = "error" if row.error else "loading…"
        cells = [Cell(marker, 1), Cell(row.symbol, 8), Cell("│", 1), Cell(status, 12, "right")]
        if has_extended:
            cells.extend([Cell("—", 12, "right"), Cell("—", 9, "right")])
        cells.extend([
            Cell("—", 9, "right"), Cell("│", 1), Cell("loading…", chart_width),
            Cell("—", 8, "right"), Cell("—", 8, "right"),
        ])
        if show_cap:
            cells.append(Cell("loading…", 10, "right"))
        if show_pe:
            cells.append(Cell("loading…", 8, "right"))
        if show_growth:
            cells.append(Cell("loading…", 9, "right"))
        return cells
    cells = [Cell(marker, 1), Cell(row.symbol, 8), Cell("│", 1)]
    if has_extended:
        extended = quote.extended_hours
        cells.extend([
            Cell(money(quote.price, quote.currency), 12, "right"),
            Cell(money(extended.price, quote.currency) if extended else "—", 12, "right"),
            Cell(percent(extended.change_percent if extended else None), 9, "right", extended.change_percent if extended else None),
        ])
    else:
        cells.append(Cell(money(quote.latest_price, quote.currency), 12, "right"))
    cells.extend([
        Cell(percent(quote.latest_change_percent), 9, "right", quote.latest_change_percent),
        Cell("│", 1),
    ])
    trend = row.trend
    cells.extend([
        Cell(sparkline(trend.points, chart_width, ascii_only) if trend else "N/A" if row.trend_error else "loading…", chart_width, change=trend.change_percent if trend else None),
        Cell(percent(trend.change_percent if trend else None) if trend or row.trend_error else "loading…", 8, "right", trend.change_percent if trend else None),
        Cell(percent(row.ytd.change_percent if row.ytd else None) if row.ytd or row.ytd_error else "loading…", 8, "right", row.ytd.change_percent if row.ytd else None),
    ])
    facts = row.fundamentals
    waiting = not row.fundamentals_done
    if show_cap:
        cells.append(Cell("loading…" if waiting else compact_number(facts.market_cap if facts else None, quote.currency), 10, "right"))
    if show_pe:
        earnings_available = facts is not None and (
            facts.pe_ratio is not None or facts.trailing_eps is not None
        )
        cells.append(Cell("loading…" if waiting else pe_text(facts.pe_ratio if facts else None, earnings_available), 8, "right"))
    if show_growth:
        growth = facts.revenue_growth if facts else None
        cells.append(Cell("loading…" if waiting else percent(growth), 9, "right", growth))
    return cells


def _draw_cells(
    screen: curses.window,
    y: int,
    cells: list[Cell],
    terminal_width: int,
    base_attribute: int,
    highlighted: bool,
    subtle_background: bool = False,
) -> None:
    x = 0
    for cell in cells:
        text = cell.formatted() + " "
        attribute = base_attribute
        if highlighted:
            attribute |= curses.color_pair(3) if subtle_background else curses.A_BOLD
        if cell.change and curses.has_colors():
            if highlighted and subtle_background:
                attribute = base_attribute | curses.color_pair(4 if cell.change > 0 else 5)
            else:
                attribute |= curses.color_pair(1 if cell.change > 0 else 2)
        if len(cells) > 1 and cell is cells[1]:
            attribute |= curses.A_BOLD
        try:
            screen.addnstr(y, x, text, max(0, terminal_width - x - 1), attribute)
        except curses.error:
            return
        x += len(text)
        if x >= terminal_width - 1:
            return
