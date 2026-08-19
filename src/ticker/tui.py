from __future__ import annotations

import curses
from datetime import datetime
import queue
import time
from concurrent.futures import CancelledError, Future, ThreadPoolExecutor
from dataclasses import dataclass

from .community import DEFAULT_MEMBERS, CommunityHolding, CommunityMember
from .core import ExtendedHours, Quote, QuoteError, Trend, YahooProvider
from .fundamentals import Fundamentals, FundamentalsProvider
from .identity import DeviceIdentity, IdentityError
from .portfolio import Portfolio, PortfolioEntry, Preferences
from .render import compact_number, money, pe_text, percent, sparkline
from .stream import LiveQuote, QuoteStream


@dataclass
class PortfolioRow:
    symbol: str
    last_queried: int | None
    added_at: int | None = None
    quote: Quote | None = None
    trend: Trend | None = None
    error: str | None = None
    trend_error: bool = False
    ytd: Trend | None = None
    ytd_error: bool = False
    six_month: Trend | None = None
    six_month_error: bool = False
    one_year: Trend | None = None
    one_year_error: bool = False
    five_year: Trend | None = None
    five_year_error: bool = False
    fundamentals: Fundamentals | None = None
    fundamentals_done: bool = False
    portfolio_percent: float | None = None
    reported_at: str | None = None


@dataclass
class Cell:
    text: str
    width: int
    align: str = "left"
    change: float | None = None
    key: str | None = None

    def formatted(self) -> str:
        return f"{self.text:>{self.width}}" if self.align == "right" else f"{self.text:<{self.width}}"


@dataclass(frozen=True)
class AppLayout:
    width: int
    height: int
    tabs_y: int
    body_y: int
    body_height: int
    main_width: int
    sidebar_width: int
    chat_y: int
    footer_y: int


def calculate_layout(width: int, height: int, show_sidebar: bool = True) -> AppLayout:
    sidebar_width = 24 if show_sidebar and width >= 100 else 0
    chat_y = max(4, height - 3)
    return AppLayout(
        width=width,
        height=height,
        tabs_y=0,
        body_y=1,
        body_height=max(3, chat_y - 2),
        main_width=max(4, width - sidebar_width),
        sidebar_width=sidebar_width,
        chat_y=chat_y,
        footer_y=max(5, height - 1),
    )


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
    curses.wrapper(
        _run, rows, period, ascii_only, None, Preferences(), (), False,
        None, None, None, None, None, None,
    )


def run_progressive_portfolio(
    entries: list[PortfolioEntry],
    period: str | None,
    ascii_only: bool,
    provider: YahooProvider,
    fundamentals_provider: FundamentalsProvider,
    portfolio: Portfolio,
    preferences: Preferences,
    workers: int = 4,
) -> None:
    rows = [PortfolioRow(entry.symbol, entry.last_queried, entry.added_at) for entry in entries]
    updates: queue.SimpleQueue[tuple[str, str, object]] = queue.SimpleQueue()
    executor = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="ticker")
    stream = QuoteStream(
        [entry.symbol for entry in entries],
        lambda quote: updates.put((quote.symbol, "live_quote", quote)),
    )
    stream.start()
    identity = DeviceIdentity()
    try:
        online_members = tuple(
            CommunityMember.from_directory(value)
            for value in identity.community_members(limit=50)
        )
        members = online_members or DEFAULT_MEMBERS
        community_joined = True
    except IdentityError:
        members = DEFAULT_MEMBERS
        community_joined = False

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
                for trend_range, kind in (
                    ("6m", "six_month"),
                    ("1y", "one_year"),
                    ("5y", "five_year"),
                ):
                    range_future = executor.submit(provider.get_trend, symbol, trend_range)
                    range_future.add_done_callback(
                        lambda done, item=symbol, field=kind: trend_done(item, field, done)
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

    def submit_symbol(symbol: str) -> None:
        future = executor.submit(provider.get, symbol)
        future.add_done_callback(lambda done, item=symbol: quote_done(item, done))

    for entry in entries:
        submit_symbol(entry.symbol)

    def add_symbol(symbol: str) -> bool:
        if not portfolio.add([symbol]):
            return False
        now = int(time.time())
        rows.insert(0, PortfolioRow(symbol, now, now))
        stream.subscribe(symbol)
        submit_symbol(symbol)
        return True

    def remove_symbol(symbol: str) -> None:
        portfolio.forget([symbol])
        stream.unsubscribe(symbol)

    def load_member(member: CommunityMember) -> list[PortfolioRow]:
        if not member.holdings:
            profile = identity.community_profile(member.handle)
            user = profile.get("user", {})
            watchlist = profile.get("list", {})
            items = watchlist.get("items", []) if isinstance(watchlist, dict) else []
            member.name = str(user.get("display_name") or member.handle) if isinstance(user, dict) else member.handle
            member.manager = str(user.get("bio") or "Community member") if isinstance(user, dict) else "Community member"
            member.report_date = str(watchlist.get("updated_at") or "")[:10] if isinstance(watchlist, dict) else ""
            member.holdings = tuple(
                CommunityHolding(str(item["symbol"]).upper())
                for item in items
                if isinstance(item, dict) and item.get("symbol")
            )
        member_rows = [
            PortfolioRow(
                holding.symbol,
                None,
                portfolio_percent=holding.portfolio_percent,
                reported_at=member.report_date,
            )
            for holding in member.holdings
        ]
        for row in member_rows:
            submit_symbol(row.symbol)
        return member_rows

    def join_community(display_name: str) -> tuple[CommunityMember, ...]:
        identity.join_community(display_name)
        return tuple(
            CommunityMember.from_directory(value)
            for value in identity.community_members(limit=50)
        )

    def send_feedback(message: str) -> None:
        identity.send_feedback(message)

    try:
        curses.wrapper(
            _run, rows, period, ascii_only, updates, preferences,
            members, community_joined, portfolio.save_preferences,
            add_symbol, remove_symbol, load_member, join_community, send_feedback,
        )
    finally:
        stream.stop()
        executor.shutdown(wait=False, cancel_futures=True)


def _run(
    screen: curses.window,
    rows: list[PortfolioRow],
    period: str | None,
    ascii_only: bool,
    updates: queue.SimpleQueue[tuple[str, str, object]] | None,
    preferences: Preferences,
    members: tuple[CommunityMember, ...],
    community_joined: bool,
    save_preferences: object | None,
    add_symbol: object | None,
    remove_symbol: object | None,
    load_member: object | None,
    join_community: object | None,
    send_feedback: object | None,
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
            curses.init_pair(8, curses.COLOR_WHITE, 235)
            screen.bkgd(" ", curses.color_pair(8))
        curses.init_pair(6, curses.COLOR_YELLOW, -1)

    selected: int | None = None
    selected_member: int | None = None
    opened_member: int | None = None
    member_rows: dict[int, list[PortfolioRow]] = {}
    member_sort_column: str | None = None
    member_sort_descending = False
    focused_column: str | None = None
    input_message = (
        "Join Community to view others' lists and share your watchlist? y/n"
        if preferences.active_tab == "community"
        and not community_joined
        else "Press / to send feedback"
    )
    offset = 0
    while True:
        active_rows = member_rows.get(opened_member, []) if opened_member is not None else rows
        active_sort_column = member_sort_column if opened_member is not None else preferences.sort_column
        active_sort_descending = member_sort_descending if opened_member is not None else preferences.sort_descending
        selected_symbol = (
            active_rows[selected].symbol
            if selected is not None and active_rows and selected < len(active_rows)
            else None
        )
        if updates is not None:
            all_rows = rows + [row for cached in member_rows.values() for row in cached]
            _apply_updates(all_rows, updates)
        if active_sort_column is not None:
            sort_rows(active_rows, active_sort_column, active_sort_descending)
        if selected_symbol:
            selected = next(
                (index for index, row in enumerate(active_rows) if row.symbol == selected_symbol),
                min(selected or 0, max(0, len(active_rows) - 1)),
            )
        height, width = screen.getmaxyx()
        layout = calculate_layout(
            width, height, show_sidebar=preferences.active_tab == "community"
        )
        visible = max(1, layout.body_height - (4 if opened_member is not None else 3))
        if selected is not None and selected < offset:
            offset = selected
        elif selected is not None and selected >= offset + visible:
            offset = selected - visible + 1
        _draw(
            screen,
            active_rows,
            period,
            ascii_only,
            selected,
            offset,
            height,
            width,
            subtle_background,
            preferences,
            focused_column,
            selected_member,
            members,
            members[opened_member] if opened_member is not None else None,
            active_sort_column,
            active_sort_descending,
            input_message,
        )
        key = screen.getch()
        if key == -1:
            continue
        if key == ord("q"):
            return
        if key == 27:
            if opened_member is not None:
                opened_member = None
                selected = None
                focused_column = None
                offset = 0
                continue
            return
        if key == 9:
            preferences.active_tab = (
                "community" if preferences.active_tab == "watchlist" else "watchlist"
            )
            selected = None
            selected_member = None
            opened_member = None
            focused_column = None
            if callable(save_preferences):
                save_preferences(preferences)
            input_message = (
                "Join Community to view others' lists and share your watchlist? y/n"
                if preferences.active_tab == "community" and not community_joined
                else "Press / to send feedback"
            )
            continue
        if key == ord("/"):
            entered = _prompt_input(screen, layout.chat_y, width, "/")
            if entered:
                if callable(send_feedback):
                    try:
                        send_feedback(entered.removeprefix("/"))
                        input_message = "Feedback sent — thank you"
                    except IdentityError:
                        input_message = "Unable to send feedback"
            continue
        if preferences.active_tab == "community":
            if not community_joined and key in (ord("y"), ord("Y")):
                if callable(join_community):
                    display_name = _prompt_name(screen, layout.chat_y)
                    if not display_name:
                        input_message = "Community join cancelled"
                        continue
                    try:
                        refreshed = join_community(display_name)
                        members = refreshed or members
                        community_joined = True
                        selected_member = None
                        input_message = "Joined Community — your watchlist is now shared"
                    except IdentityError:
                        input_message = "Unable to join Community"
                continue
            if not community_joined and key in (ord("n"), ord("N")):
                input_message = "Community join dismissed"
                continue
            if not members:
                input_message = "No community members available"
                continue
            if opened_member is None and key in (curses.KEY_UP, ord("k")):
                selected_member = (
                    len(members) - 1
                    if selected_member is None
                    else (selected_member - 1) % len(members)
                )
            elif opened_member is None and key in (curses.KEY_DOWN, ord("j")):
                selected_member = (
                    0 if selected_member is None else (selected_member + 1) % len(members)
                )
            elif opened_member is None and key in (curses.KEY_ENTER, 10, 13) and selected_member is not None:
                if selected_member not in member_rows and callable(load_member):
                    try:
                        member_rows[selected_member] = load_member(members[selected_member])
                    except IdentityError:
                        input_message = "Unable to load community watchlist"
                        continue
                opened_member = selected_member
                selected = None
                focused_column = None
                offset = 0
            if opened_member is None:
                continue
        is_member_view = opened_member is not None
        is_demo_portfolio = (
            opened_member is not None and bool(members[opened_member].reporting_period)
        )
        columns = view_columns(
            preferences.view,
            any(row.quote and row.quote.extended_hours for row in active_rows),
            is_demo_portfolio,
        )
        if key == ord("v"):
            views = ["basic", "extended", "study"]
            preferences.view = views[(views.index(preferences.view) + 1) % len(views)]
            if preferences.sort_column not in view_columns(preferences.view, False):
                preferences.sort_column = None
            if member_sort_column not in view_columns(
                preferences.view, False, is_demo_portfolio
            ):
                member_sort_column = None
            if focused_column not in view_columns(
                preferences.view, False, is_demo_portfolio
            ):
                focused_column = None
            if callable(save_preferences):
                save_preferences(preferences)
            continue
        if key in (curses.KEY_LEFT, curses.KEY_RIGHT):
            step = -1 if key == curses.KEY_LEFT else 1
            if focused_column not in columns:
                focused_column = columns[-1] if step < 0 else columns[0]
            else:
                focused_column = columns[(columns.index(focused_column) + step) % len(columns)]
            continue
        if key == ord("s"):
            target = focused_column or active_sort_column
            if target is None:
                continue
            if is_member_view:
                if target == member_sort_column:
                    member_sort_descending = not member_sort_descending
                else:
                    member_sort_column = target
                    member_sort_descending = False
            elif target == preferences.sort_column:
                preferences.sort_descending = not preferences.sort_descending
            else:
                preferences.sort_column = target
                preferences.sort_descending = False
            if not is_member_view and callable(save_preferences):
                save_preferences(preferences)
            continue
        if not is_member_view and key == ord("d") and selected is not None and rows and callable(remove_symbol):
            remove_symbol(rows[selected].symbol)
            rows.pop(selected)
            selected = min(selected, max(0, len(rows) - 1))
            if not rows:
                selected = None
            continue
        if not is_member_view and key == ord("a") and callable(add_symbol):
            symbol = _prompt_symbol(screen, layout.chat_y)
            if symbol and symbol not in {row.symbol for row in rows}:
                add_symbol(symbol)
            continue
        if key in (curses.KEY_UP, curses.KEY_DOWN, curses.KEY_HOME, curses.KEY_END, ord("j"), ord("k")):
            if selected is None:
                selected = len(active_rows) - 1 if key in (curses.KEY_UP, curses.KEY_END, ord("k")) else 0
            else:
                selected = move_selection(selected, key, len(active_rows))


def view_columns(view: str, has_extended: bool = False, portfolio: bool = False) -> list[str]:
    columns = ["symbol", "price"]
    if has_extended:
        columns.extend(["extended_price", "extended_change"])
    columns.append("today")
    if view in {"extended", "study"}:
        columns.extend(["one_month", "six_month", "ytd", "one_year", "five_year"])
    if view == "study":
        columns.extend(["market_cap", "pe", "revenue_growth"])
    columns.extend(["portfolio_percent", "reported"] if portfolio else ["added"])
    return columns


def sort_rows(rows: list[PortfolioRow], column: str, descending: bool) -> None:
    def value(row: PortfolioRow) -> object | None:
        quote, facts = row.quote, row.fundamentals
        values = {
            "symbol": row.symbol,
            "added": row.added_at,
            "price": quote.price if quote else None,
            "extended_price": quote.extended_hours.price if quote and quote.extended_hours else None,
            "extended_change": quote.extended_hours.change_percent if quote and quote.extended_hours else None,
            "today": quote.latest_change_percent if quote else None,
            "one_month": row.trend.change_percent if row.trend else None,
            "six_month": row.six_month.change_percent if row.six_month else None,
            "one_year": row.one_year.change_percent if row.one_year else None,
            "five_year": row.five_year.change_percent if row.five_year else None,
            "ytd": row.ytd.change_percent if row.ytd else None,
            "market_cap": facts.market_cap if facts else None,
            "pe": facts.pe_ratio if facts else None,
            "revenue_growth": facts.revenue_growth if facts else None,
            "portfolio_percent": row.portfolio_percent,
            "reported": row.reported_at,
        }
        return values.get(column)

    present = [row for row in rows if value(row) is not None]
    missing = [row for row in rows if value(row) is None]
    present.sort(key=value, reverse=descending)  # type: ignore[arg-type]
    rows[:] = present + missing


def _prompt_symbol(screen: curses.window, y: int) -> str | None:
    prompt = " Add symbol: "
    try:
        curses.echo()
        curses.curs_set(1)
        screen.timeout(-1)
        screen.move(y, 0)
        screen.clrtoeol()
        screen.addstr(y, 0, prompt)
        raw = screen.getstr(y, len(prompt), 32).decode("utf-8").strip().upper()
        if raw and not any(character.isspace() for character in raw):
            return raw
    except (curses.error, UnicodeDecodeError):
        return None
    finally:
        curses.noecho()
        try:
            curses.curs_set(0)
        except curses.error:
            pass
        screen.timeout(50)
    return None


def _prompt_name(screen: curses.window, y: int) -> str | None:
    prompt = " Display name: "
    try:
        curses.echo()
        curses.curs_set(1)
        screen.timeout(-1)
        screen.move(y, 0)
        screen.clrtoeol()
        screen.addstr(y, 0, prompt)
        value = screen.getstr(y, len(prompt), 80).decode("utf-8").strip()
        return value or None
    except (curses.error, UnicodeDecodeError):
        return None
    finally:
        curses.noecho()
        try:
            curses.curs_set(0)
        except curses.error:
            pass
        screen.timeout(50)


def _prompt_input(screen: curses.window, y: int, width: int, prefix: str) -> str | None:
    prompt = f" {prefix} "
    try:
        curses.echo()
        curses.curs_set(1)
        screen.timeout(-1)
        screen.move(y, 0)
        screen.clrtoeol()
        screen.addstr(y, 0, prompt, curses.A_BOLD)
        raw = screen.getstr(
            y, len(prompt), min(200, max(1, width - len(prompt) - 1))
        )
        value = raw.decode("utf-8").strip()
        return f"{prefix}{value}" if value else None
    except (curses.error, UnicodeDecodeError):
        return None
    finally:
        curses.noecho()
        try:
            curses.curs_set(0)
        except curses.error:
            pass
        screen.timeout(50)


def _apply_updates(
    rows: list[PortfolioRow], updates: queue.SimpleQueue[tuple[str, str, object]]
) -> None:
    by_symbol: dict[str, list[PortfolioRow]] = {}
    for row in rows:
        by_symbol.setdefault(row.symbol, []).append(row)
    while True:
        try:
            symbol, kind, value = updates.get_nowait()
        except queue.Empty:
            return
        matching_rows = by_symbol.get(symbol, [])
        if not matching_rows:
            continue
        for row in matching_rows:
            if kind == "quote":
                row.quote = value  # type: ignore[assignment]
            elif kind == "live_quote" and row.quote is not None:
                _apply_live_quote(row.quote, value)  # type: ignore[arg-type]
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
            elif kind in {"six_month", "one_year", "five_year"}:
                setattr(row, kind, value)
            elif kind in {"six_month_error", "one_year_error", "five_year_error"}:
                setattr(row, kind, True)
            elif kind == "fundamentals":
                row.fundamentals = value  # type: ignore[assignment]
                row.fundamentals_done = True
            elif kind == "fundamentals_error":
                row.fundamentals_done = True


def _apply_live_quote(quote: Quote, live: LiveQuote) -> None:
    # Yahoo MarketHoursType values: 0 pre, 1 regular, 2 post, 3 extended.
    if live.market_hours == 1:
        quote.price = live.price
        quote.timestamp = live.timestamp
        quote.change_percent = live.change_percent
        if quote.previous_close is not None:
            quote.change = round(live.price - quote.previous_close, 6)
        quote.extended_hours = None
        quote.market_state = "REGULAR"
        return

    session = "pre" if live.market_hours == 0 else "post"
    if live.market_hours == 3 and quote.extended_hours is not None:
        session = quote.extended_hours.session
    change = round(live.price - quote.price, 6)
    change_percent = round(change / quote.price * 100, 6) if quote.price else None
    quote.extended_hours = ExtendedHours(
        session, live.price, change, change_percent, live.timestamp
    )


def _draw(
    screen: curses.window,
    rows: list[PortfolioRow],
    period: str | None,
    ascii_only: bool,
    selected: int | None,
    offset: int,
    height: int,
    width: int,
    subtle_background: bool,
    preferences: Preferences,
    focused_column: str | None,
    selected_member: int | None,
    members: tuple[CommunityMember, ...],
    portfolio_member: CommunityMember | None,
    sort_column: str | None,
    sort_descending: bool,
    input_message: str,
) -> None:
    screen.erase()
    layout = calculate_layout(
        width, height, show_sidebar=preferences.active_tab == "community"
    )
    _draw_tabs(screen, layout, preferences.active_tab)
    _draw_box(
        screen, 0, layout.body_y, layout.main_width, layout.body_height,
    )
    if layout.sidebar_width:
        _draw_community_sidebar(screen, layout, members, selected_member, subtle_background)
    if preferences.active_tab == "community" and portfolio_member is None:
        _draw_community_page(screen, layout, members, selected_member)
        _draw_chat_bar(screen, layout, input_message)
        _draw_footer(screen, layout, preferences, False)
        screen.refresh()
        return

    content_width = max(18, layout.main_width - 2)
    has_extended = any(row.quote and row.quote.extended_hours for row in rows)
    chart_width = max(8, min(14, content_width - (98 if has_extended else 71)))
    show_trends = preferences.view in {"extended", "study"}
    show_cap = preferences.view == "study" and content_width >= (157 if has_extended else 130)
    show_pe = preferences.view == "study" and content_width >= (167 if has_extended else 140)
    show_growth = preferences.view == "study" and content_width >= (178 if has_extended else 152)
    table_y = layout.body_y + (2 if portfolio_member else 1)
    if portfolio_member:
        is_demo_portfolio = bool(portfolio_member.reporting_period)
        title = "Portfolio" if is_demo_portfolio else "Watchlist"
        metadata = (
            f"  {portfolio_member.manager}  "
            f"{portfolio_member.reporting_period} · historical disclosure"
            if is_demo_portfolio
            else f"  {portfolio_member.name}"
        )
        try:
            screen.addnstr(
                layout.body_y + 1, 2, title, max(0, content_width - 2),
                curses.color_pair(6) | curses.A_BOLD if curses.has_colors() else curses.A_BOLD,
            )
            metadata_x = 2 + len(title)
            screen.addnstr(
                layout.body_y + 1,
                metadata_x,
                metadata,
                max(0, content_width - metadata_x),
                curses.A_DIM,
            )
        except curses.error:
            pass
    header = _header_cells(
        rows, chart_width, has_extended, show_trends, show_cap, show_pe, show_growth,
        sort_column, sort_descending,
        bool(portfolio_member and portfolio_member.reporting_period),
    )
    _draw_table_header(
        screen, table_y, header, content_width,
        subtle_background, focused_column, 1,
    )

    visible = max(1, layout.body_height - (4 if portfolio_member else 3))
    for screen_index, row in enumerate(
        rows[offset : offset + visible], start=table_y + 1
    ):
        row_index = offset + screen_index - table_y - 1
        highlighted = selected is not None and row_index == selected
        if highlighted:
            highlight_attribute = curses.color_pair(3) if subtle_background else curses.A_BOLD
            try:
                screen.addnstr(
                    screen_index,
                    1,
                    " " * max(0, content_width),
                    max(0, content_width),
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
            show_trends,
            ascii_only,
            highlighted,
            bool(portfolio_member and portfolio_member.reporting_period),
        )
        _draw_table_row(
            screen,
            screen_index,
            cells,
            content_width,
            highlighted,
            subtle_background,
            focused_column,
            1,
        )
    _draw_chat_bar(screen, layout, input_message)
    _draw_footer(screen, layout, preferences, portfolio_member is not None)
    screen.refresh()


def market_status(rows: list[PortfolioRow]) -> str:
    if not rows:
        return "Watchlist empty"
    if any(row.quote is None and row.error is None for row in rows):
        return "Refreshing prices…"
    quotes = [row.quote for row in rows if row.quote is not None]
    if not quotes:
        return "Unable to refresh prices"
    states = set()
    for quote in quotes:
        if quote.extended_hours:
            states.add("Pre-market" if quote.extended_hours.session == "pre" else "After hours")
        else:
            states.add({
                "REGULAR": "Market: Open",
                "PRE": "Pre-market",
                "POST": "After hours",
                "CLOSED": "Market: Closed",
            }.get(quote.market_state.upper(), "Market status unavailable"))
    return next(iter(states)) if len(states) == 1 else "Mixed markets"


def _draw_tabs(screen: curses.window, layout: AppLayout, active_tab: str) -> None:
    x = 2
    for key, label in (("watchlist", "Watchlist"), ("community", "Community")):
        text = f" {label} "
        attribute = (
            curses.color_pair(6) | curses.A_BOLD
            if key == active_tab and curses.has_colors()
            else curses.A_REVERSE | curses.A_BOLD
            if key == active_tab
            else curses.A_DIM
        )
        try:
            screen.addnstr(layout.tabs_y, x, text, len(text), attribute)
        except curses.error:
            return
        x += len(text) + 1


def _draw_box(
    screen: curses.window,
    x: int,
    y: int,
    width: int,
    height: int,
    title: str | None = None,
) -> None:
    if width < 4 or height < 3:
        return
    horizontal = "─" * max(0, width - 2)
    try:
        screen.addnstr(y, x, f"╭{horizontal}╮", width)
        for line in range(y + 1, y + height - 1):
            screen.addstr(line, x, "│")
            screen.addstr(line, x + width - 1, "│")
        screen.addnstr(y + height - 1, x, f"╰{horizontal}╯", width)
        if title:
            title_style = curses.color_pair(6) | curses.A_BOLD if curses.has_colors() else curses.A_BOLD
            screen.addnstr(y, x + 2, f" {title} ", max(0, width - 4), title_style)
    except curses.error:
        pass


def _draw_community_sidebar(
    screen: curses.window,
    layout: AppLayout,
    members: tuple[CommunityMember, ...],
    selected_member: int | None,
    subtle_background: bool,
) -> None:
    x = layout.main_width
    _draw_box(screen, x, layout.body_y, layout.sidebar_width, layout.body_height)
    for index, member in enumerate(members[: max(0, layout.body_height - 3)]):
        is_selected = selected_member == index
        attribute = (
            curses.color_pair(6) | curses.A_BOLD
            if is_selected and curses.has_colors()
            else curses.A_BOLD
            if is_selected
            else 0
        )
        try:
            label = f"{member.name} · demo" if member.reporting_period else member.name
            screen.addnstr(
                layout.body_y + 2 + index, x + 2, label,
                max(0, layout.sidebar_width - 4), attribute,
            )
        except curses.error:
            return


def _draw_community_page(
    screen: curses.window,
    layout: AppLayout,
    members: tuple[CommunityMember, ...],
    selected_member: int | None,
) -> None:
    if selected_member is None:
        lines = [
            "Select a community member",
            "",
            "Use ↑/↓ to browse shared watchlists.",
        ]
    else:
        member = members[selected_member]
        lines = [
            f"@{member.handle}  {member.name}",
            member.manager,
            "",
            (
                "Demo based on a reported manager portfolio"
                if member.reporting_period
                else "Press Enter to load this member's watchlist"
            ),
        ]
    for index, line in enumerate(lines, start=layout.body_y + 2):
        try:
            screen.addnstr(index, 2, line, max(0, layout.main_width - 4), curses.A_DIM)
        except curses.error:
            return


def _draw_chat_bar(screen: curses.window, layout: AppLayout, message: str) -> None:
    is_join_prompt = message.startswith("Join Community")
    prefix = "  › "
    text = f"{prefix}{message}  "
    try:
        screen.addnstr(
            layout.chat_y, 0, text.ljust(max(0, layout.width - 1)),
            max(0, layout.width - 1), curses.A_DIM,
        )
        if is_join_prompt and (choice_at := message.rfind("y/n")) >= 0:
            choice_style = curses.A_BOLD
            if curses.has_colors():
                choice_style |= curses.color_pair(6)
            screen.addch(layout.chat_y, len(prefix) + choice_at, "y", choice_style)
            screen.addch(layout.chat_y, len(prefix) + choice_at + 2, "n", choice_style)
    except curses.error:
        pass


def _draw_footer(
    screen: curses.window,
    layout: AppLayout,
    preferences: Preferences,
    portfolio_open: bool = False,
) -> None:
    bindings = footer_bindings(preferences.active_tab, portfolio_open)
    try:
        screen.addnstr(layout.footer_y, 0, " " * max(0, layout.width - 1), max(0, layout.width - 1), curses.A_DIM)
        x = 1
        for key, description in bindings:
            key_text = f"{key} "
            key_style = curses.color_pair(6) | curses.A_BOLD if curses.has_colors() else curses.A_BOLD
            screen.addnstr(layout.footer_y, x, key_text, max(0, layout.width - x - 1), key_style)
            x += len(key_text)
            label = f"{description}   "
            screen.addnstr(layout.footer_y, x, label, max(0, layout.width - x - 1), curses.A_DIM)
            x += len(label)
            if x >= layout.width - 1:
                break
    except curses.error:
        pass


def footer_bindings(active_tab: str, portfolio_open: bool = False) -> list[tuple[str, str]]:
    if active_tab == "community" and portfolio_open:
        return [
            ("↑/↓", "Row"), ("←/→", "Column"), ("s", "Sort"),
            ("v", "View"), ("Esc", "Back"), ("Tab", "Watchlist"),
            ("q", "Quit"),
        ]
    if active_tab == "community":
        return [
            ("↑/↓", "Member"), ("Enter", "Open"), ("Tab", "Watchlist"),
            ("/", "Feedback"), ("q", "Quit"),
        ]
    return [
        ("a", "Add"), ("d", "Remove"), ("v", "View"),
        ("←/→", "Column"), ("s", "Sort"), ("Tab", "Community"),
        ("/", "Feedback"), ("q", "Quit"),
    ]


def _header_cells(
    rows: list[PortfolioRow],
    chart_width: int,
    has_extended: bool,
    show_trends: bool,
    show_cap: bool,
    show_pe: bool,
    show_growth: bool,
    sort_column: str | None,
    sort_descending: bool,
    portfolio: bool = False,
) -> list[Cell]:
    marker = "↓" if sort_descending else "↑"
    def label(text: str, key: str) -> str:
        return f"{text}{marker}" if sort_column == key else text

    cells = [
        Cell("", 1),
        Cell(label("SYMBOL", "symbol"), 8, key="symbol"),
    ]
    if has_extended:
        sessions = {
            row.quote.extended_hours.session
            for row in rows
            if row.quote and row.quote.extended_hours
        }
        cells.extend([
            Cell(label("CLOSE", "price"), 12, "right", key="price"),
            Cell(label("PRE-MARKET" if sessions == {"pre"} else "AFTER HOURS", "extended_price"), 12, "right", key="extended_price"),
            Cell(label("PRE %" if sessions == {"pre"} else "AFTER %", "extended_change"), 9, "right", key="extended_change"),
        ])
    else:
        cells.append(Cell(label("PRICE", "price"), 12, "right", key="price"))
    cells.append(Cell(label("TODAY", "today"), 9, "right", key="today"))
    if show_trends:
        cells.extend([
            Cell("1M TREND", chart_width, key="one_month"),
            Cell(label("1M", "one_month"), 8, "right", key="one_month"),
            Cell(label("6M", "six_month"), 8, "right", key="six_month"),
            Cell(label("YTD", "ytd"), 8, "right", key="ytd"),
            Cell(label("1Y", "one_year"), 8, "right", key="one_year"),
            Cell(label("5Y", "five_year"), 8, "right", key="five_year"),
        ])
    if show_cap:
        cells.append(Cell(label("MKT CAP", "market_cap"), 10, "right", key="market_cap"))
    if show_pe:
        cells.append(Cell(label("P/E", "pe"), 8, "right", key="pe"))
    if show_growth:
        cells.append(Cell(label("REV YOY", "revenue_growth"), 9, "right", key="revenue_growth"))
    if portfolio:
        cells.extend([
            Cell(label("% OF PORTFOLIO", "portfolio_percent"), 16, "right", key="portfolio_percent"),
            Cell(label("REPORTED", "reported"), 10, key="reported"),
        ])
    else:
        cells.append(Cell(label("ADDED", "added"), 10, key="added"))
    return cells


def _added_text(timestamp: int | None) -> str:
    return datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d") if timestamp else "N/A"


def _row_cells(
    row: PortfolioRow,
    chart_width: int,
    has_extended: bool,
    show_cap: bool,
    show_pe: bool,
    show_growth: bool,
    show_trends: bool,
    ascii_only: bool,
    highlighted: bool,
    portfolio: bool = False,
) -> list[Cell]:
    quote = row.quote
    marker = "›" if highlighted and not ascii_only else ">" if highlighted else ""
    if quote is None:
        status = "error" if row.error else "loading…"
        cells = [
            Cell(marker, 1),
            Cell(row.symbol, 8, key="symbol"),
            Cell(status, 12, "right", key="price"),
        ]
        if has_extended:
            cells.extend([
                Cell("—", 12, "right", key="extended_price"),
                Cell("—", 9, "right", key="extended_change"),
            ])
        cells.append(Cell("—", 9, "right", key="today"))
        if show_trends:
            cells.extend([
                Cell("loading…", chart_width),
                Cell("—", 8, "right", key="one_month"),
                Cell("—", 8, "right", key="six_month"),
                Cell("—", 8, "right", key="ytd"),
                Cell("—", 8, "right", key="one_year"),
                Cell("—", 8, "right", key="five_year"),
            ])
        if show_cap:
            cells.append(Cell("loading…", 10, "right", key="market_cap"))
        if show_pe:
            cells.append(Cell("loading…", 8, "right", key="pe"))
        if show_growth:
            cells.append(Cell("loading…", 9, "right", key="revenue_growth"))
        if portfolio:
            cells.extend(_portfolio_metadata_cells(row))
        else:
            cells.append(Cell(_added_text(row.added_at), 10, key="added"))
        return cells
    cells = [
        Cell(marker, 1),
        Cell(row.symbol, 8, key="symbol"),
    ]
    if has_extended:
        extended = quote.extended_hours
        cells.extend([
            Cell(money(quote.price, quote.currency), 12, "right", key="price"),
            Cell(money(extended.price, quote.currency) if extended else "—", 12, "right", key="extended_price"),
            Cell(percent(extended.change_percent if extended else None), 9, "right", extended.change_percent if extended else None, key="extended_change"),
        ])
    else:
        cells.append(Cell(money(quote.latest_price, quote.currency), 12, "right", key="price"))
    cells.append(Cell(percent(quote.latest_change_percent), 9, "right", quote.latest_change_percent, key="today"))
    trend = row.trend
    if show_trends:
        cells.extend([
            Cell(sparkline(trend.points, chart_width, ascii_only) if trend else "N/A" if row.trend_error else "loading…", chart_width, change=trend.change_percent if trend else None, key="one_month"),
            _return_cell("one_month", trend, row.trend_error),
            _return_cell("six_month", row.six_month, row.six_month_error),
            _return_cell("ytd", row.ytd, row.ytd_error),
            _return_cell("one_year", row.one_year, row.one_year_error),
            _return_cell("five_year", row.five_year, row.five_year_error),
        ])
    facts = row.fundamentals
    waiting = not row.fundamentals_done
    if show_cap:
        cells.append(Cell("loading…" if waiting else compact_number(facts.market_cap if facts else None, quote.currency), 10, "right", key="market_cap"))
    if show_pe:
        earnings_available = facts is not None and (
            facts.pe_ratio is not None or facts.trailing_eps is not None
        )
        cells.append(Cell("loading…" if waiting else pe_text(facts.pe_ratio if facts else None, earnings_available), 8, "right", key="pe"))
    if show_growth:
        growth = facts.revenue_growth if facts else None
        cells.append(Cell("loading…" if waiting else percent(growth), 9, "right", growth, key="revenue_growth"))
    if portfolio:
        cells.extend(_portfolio_metadata_cells(row))
    else:
        cells.append(Cell(_added_text(row.added_at), 10, key="added"))
    return cells


def _return_cell(key: str, trend: Trend | None, failed: bool) -> Cell:
    value = trend.change_percent if trend else None
    text = percent(value) if trend or failed else "loading…"
    return Cell(text, 8, "right", value, key=key)


def _portfolio_metadata_cells(row: PortfolioRow) -> list[Cell]:
    allocation = "N/A" if row.portfolio_percent is None else f"{row.portfolio_percent:.2f}%"
    return [
        Cell(allocation, 16, "right", key="portfolio_percent"),
        Cell(row.reported_at or "N/A", 10, key="reported"),
    ]


def _draw_table_header(
    screen: curses.window,
    y: int,
    cells: list[Cell],
    width: int,
    subtle_background: bool,
    focused_column: str | None,
    origin_x: int,
) -> None:
    _draw_cells(
        screen,
        y,
        cells,
        width,
        curses.color_pair(6) | curses.A_BOLD if curses.has_colors() else curses.A_BOLD,
        False,
        subtle_background,
        focused_column,
        origin_x,
    )


def _draw_table_row(
    screen: curses.window,
    y: int,
    cells: list[Cell],
    width: int,
    highlighted: bool,
    subtle_background: bool,
    focused_column: str | None,
    origin_x: int,
) -> None:
    _draw_cells(
        screen,
        y,
        cells,
        width,
        0,
        highlighted,
        subtle_background,
        focused_column,
        origin_x,
    )


def _draw_cells(
    screen: curses.window,
    y: int,
    cells: list[Cell],
    terminal_width: int,
    base_attribute: int,
    highlighted: bool,
    subtle_background: bool = False,
    focused_column: str | None = None,
    origin_x: int = 0,
) -> None:
    x = origin_x
    for cell in cells:
        text = cell.formatted() + " "
        attribute = base_attribute
        column_focused = cell.key == focused_column
        if (highlighted or column_focused) and subtle_background:
            attribute = _with_color_pair(attribute, 3)
        elif highlighted or column_focused:
            attribute |= curses.A_BOLD
        if cell.change and curses.has_colors():
            if (highlighted or column_focused) and subtle_background:
                attribute = _with_color_pair(base_attribute, 4 if cell.change > 0 else 5)
            else:
                attribute |= curses.color_pair(1 if cell.change > 0 else 2)
        if len(cells) > 1 and cell is cells[1]:
            attribute |= curses.A_BOLD
        try:
            remaining = terminal_width - (x - origin_x)
            screen.addnstr(y, x, text, max(0, remaining), attribute)
        except curses.error:
            return
        x += len(text)
        if x - origin_x >= terminal_width:
            return


def _with_color_pair(attribute: int, pair: int) -> int:
    """Replace a curses color pair without discarding weight attributes."""
    return (attribute & ~curses.A_COLOR) | curses.color_pair(pair)
