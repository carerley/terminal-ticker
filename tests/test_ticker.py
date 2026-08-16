import curses
import io
import json
import tempfile
import time
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from ticker.core import (
    Quote,
    QuoteError,
    ExtendedHours,
    Trend,
    YahooProvider,
    _extended_hours,
    _previous_session_close,
    _regular_session_points,
    get_market_data,
    get_quote,
)
from ticker.community import DEFAULT_MEMBERS
from ticker.portfolio import Portfolio, Preferences
from ticker.fundamentals import FundamentalsProvider, fundamentals_from_companyfacts
from ticker.profiler import Event, Profiler, _peak_concurrency, _wall_time
from ticker.render import Color, sparkline
from ticker.cli import Result, print_table
from ticker.tui import (
    PortfolioRow,
    calculate_layout,
    footer_bindings,
    market_status,
    move_selection,
    sort_rows,
    view_columns,
)


class Provider:
    def __init__(self, value=None, error=None):
        self.value, self.error, self.calls = value, error, 0

    def get(self, symbol):
        self.calls += 1
        if self.error:
            raise self.error
        return self.value

    def get_with_trend(self, symbol, period):
        self.calls += 1
        if self.error:
            raise self.error
        return self.value, Trend(period, [100.0, 102.0], 2.0, 100.0, 102.0)


def sample():
    return Quote("AAPL", "Apple Inc.", "USD", 231.0, 2.0, 0.87, 229.0, "REGULAR", 100)


class QuoteTests(unittest.TestCase):
    def test_intraday_trend_excludes_extended_hours(self):
        day = 10 * 86400
        meta = {
            "gmtoffset": 0,
            "currentTradingPeriod": {
                "regular": {"start": day + 9 * 3600 + 1800, "end": day + 16 * 3600}
            },
        }
        pairs = [
            (day + 8 * 3600, 9.0),
            (day + 10 * 3600, 10.0),
            (day + 16 * 3600, 11.0),
            (day + 17 * 3600, 12.0),
        ]
        self.assertEqual(
            _regular_session_points(pairs, meta, "5m"),
            [(day + 10 * 3600, 10.0), (day + 16 * 3600, 11.0)],
        )

    def test_weekly_trend_does_not_apply_intraday_session_filter(self):
        pairs = [(100, 10.0), (200, 12.0)]
        meta = {
            "gmtoffset": 0,
            "currentTradingPeriod": {
                "regular": {"start": 9 * 3600 + 1800, "end": 16 * 3600}
            },
        }
        self.assertEqual(_regular_session_points(pairs, meta, "1wk"), pairs)

    def test_extracts_after_hours_price(self):
        now = int(time.time())
        quote = sample()
        quote.timestamp = now - 3600
        quote.price = 100.0
        result = {
            "meta": {
                "hasPrePostMarketData": True,
                "currentTradingPeriod": {
                    "regular": {"start": now - 8 * 3600, "end": now - 3600},
                    "post": {"start": now - 3600, "end": now + 3600},
                },
            },
            "timestamp": [now - 3600, now],
            "indicators": {"quote": [{"close": [100.0, 101.0]}]},
        }
        extended = _extended_hours(result, quote)
        self.assertEqual(extended.session, "post")
        self.assertEqual(extended.price, 101.0)
        self.assertEqual(extended.change_percent, 1.0)

    def test_keeps_latest_after_hours_price_when_market_is_closed(self):
        quote = sample()
        quote.timestamp = 100
        quote.price = 100.0
        result = {
            "meta": {
                "marketState": "CLOSED",
                "hasPrePostMarketData": True,
                "currentTradingPeriod": {
                    "regular": {"start": 50, "end": 100},
                    "post": {"start": 101, "end": 200},
                },
            },
            "timestamp": [100, 150],
            "indicators": {"quote": [{"close": [100.0, 101.0]}]},
        }
        extended = _extended_hours(result, quote)
        self.assertEqual(extended.session, "post")
        self.assertEqual(quote.price, 100.0)
        quote.extended_hours = extended
        self.assertEqual(quote.latest_price, 101.0)
        self.assertEqual(quote.latest_timestamp, 150)

    def test_previous_close_uses_prior_daily_point(self):
        self.assertEqual(
            _previous_session_close([(100, 10.0), (200, 11.0), (300, 12.0)], {}, "1d"),
            11.0,
        )

    def test_previous_close_uses_prior_intraday_session(self):
        day = 86400
        pairs = [(day + 100, 10.0), (day + 200, 11.0), (2 * day + 100, 12.0)]
        self.assertEqual(_previous_session_close(pairs, {"gmtoffset": 0}, "30m"), 11.0)

    def test_every_quote_calls_provider(self):
        provider = Provider(value=sample())
        self.assertEqual(get_quote("AAPL", provider).price, 231.0)
        self.assertEqual(get_quote("AAPL", provider).price, 231.0)
        self.assertEqual(provider.calls, 2)

    def test_trend_calls_provider(self):
        provider = Provider(value=sample())
        quote, trend = get_market_data("AAPL", provider, "1m")
        self.assertEqual(quote.price, 231.0)
        self.assertEqual(trend.change_percent, 2.0)
        self.assertEqual(provider.calls, 1)


class PortfolioTests(unittest.TestCase):
    def test_touch_sorts_by_recent_query_and_deduplicates(self):
        with tempfile.TemporaryDirectory() as directory:
            portfolio = Portfolio(Path(directory) / "portfolio.json")
            portfolio.touch(["AAPL", "MSFT"], now=100)
            portfolio.touch(["NVDA", "AAPL"], now=200)
            self.assertEqual(
                [entry.symbol for entry in portfolio.entries()],
                ["NVDA", "AAPL", "MSFT"],
            )
            self.assertEqual(
                {entry.symbol: entry.added_at for entry in portfolio.entries()},
                {"NVDA": 200, "AAPL": 100, "MSFT": 100},
            )

            portfolio.touch(["MSFT"], now=300)
            self.assertEqual(
                [entry.symbol for entry in portfolio.entries()],
                ["NVDA", "AAPL", "MSFT"],
            )

    def test_forget_and_clear(self):
        with tempfile.TemporaryDirectory() as directory:
            portfolio = Portfolio(Path(directory) / "portfolio.json")
            portfolio.touch(["AAPL", "MSFT"], now=100)
            self.assertEqual(portfolio.forget(["aapl"]), ["AAPL"])
            self.assertEqual(portfolio.clear(), 1)
            self.assertEqual(portfolio.entries(), [])

    def test_add_and_preferences_survive_watchlist_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            portfolio = Portfolio(Path(directory) / "portfolio.json")
            self.assertEqual(portfolio.add(["AAPL", "MSFT"], now=100), ["AAPL", "MSFT"])
            self.assertEqual(portfolio.add(["aapl"], now=200), [])
            expected = Preferences("study", "pe", True, "community")
            portfolio.save_preferences(expected)
            portfolio.forget(["MSFT"])
            self.assertEqual(portfolio.preferences(), expected)

    def test_reads_version_one_portfolio_with_default_preferences(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "portfolio.json"
            path.write_text(json.dumps({
                "version": 1,
                "symbols": [{"symbol": "AAPL", "last_queried": 100}],
            }))
            portfolio = Portfolio(path)
            self.assertEqual(portfolio.entries()[0].symbol, "AAPL")
            self.assertEqual(portfolio.preferences(), Preferences())

    def test_default_preferences_have_no_applied_sort(self):
        self.assertIsNone(Preferences().sort_column)

    def test_migrates_old_implicit_symbol_sort_to_unsorted(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "portfolio.json"
            path.write_text(json.dumps({
                "version": 3,
                "symbols": [],
                "preferences": {"sort_column": "symbol", "sort_descending": False},
            }))
            self.assertIsNone(Portfolio(path).preferences().sort_column)


class RenderTests(unittest.TestCase):
    def test_interactive_selection_stays_in_bounds(self):
        self.assertEqual(move_selection(0, curses.KEY_UP, 3), 0)
        self.assertEqual(move_selection(0, curses.KEY_DOWN, 3), 1)
        self.assertEqual(move_selection(2, curses.KEY_DOWN, 3), 2)
        self.assertEqual(move_selection(1, curses.KEY_HOME, 3), 0)
        self.assertEqual(move_selection(1, curses.KEY_END, 3), 2)

    def test_sparkline_resamples_to_width(self):
        rendered = sparkline([1, 2, 3, 4, 5], width=3)
        self.assertEqual(len(rendered), 3)
        self.assertEqual(rendered[0], "▁")
        self.assertEqual(rendered[-1], "█")

    def test_no_color_mode_has_no_ansi(self):
        self.assertEqual(Color("never").change("+1.00%", 1.0), "+1.00%")

    def test_views_progressively_add_sortable_columns(self):
        self.assertEqual(view_columns("basic"), ["symbol", "price", "today", "added"])
        self.assertEqual(
            view_columns("basic", has_extended=True),
            ["symbol", "price", "extended_price", "extended_change", "today", "added"],
        )
        self.assertIn("one_month", view_columns("extended"))
        self.assertEqual(
            view_columns("extended")[3:8],
            ["one_month", "six_month", "ytd", "one_year", "five_year"],
        )
        self.assertIn("revenue_growth", view_columns("study"))
        self.assertTrue(all(view_columns(view)[-1] == "added" for view in ("basic", "extended", "study")))

    def test_market_status_tracks_loading_and_session(self):
        row = PortfolioRow("AAPL", None)
        self.assertEqual(market_status([row]), "Refreshing prices…")
        row.quote = sample()
        self.assertEqual(market_status([row]), "Market: Open")

    def test_layout_collapses_sidebar_before_main_content(self):
        self.assertEqual(calculate_layout(120, 30).sidebar_width, 24)
        self.assertEqual(calculate_layout(90, 30).sidebar_width, 0)

    def test_footer_exposes_direct_commands_and_feedback_only(self):
        bindings = footer_bindings("watchlist")
        self.assertIn(("a", "Add"), bindings)
        self.assertIn(("/", "Feedback"), bindings)
        self.assertNotIn((":", "Command"), bindings)

    def test_sort_rows_keeps_unavailable_values_last(self):
        low = PortfolioRow("LOW", None, quote=sample())
        high = PortfolioRow("HIGH", None, quote=sample())
        loading = PortfolioRow("WAIT", None)
        low.quote.price, high.quote.price = 10, 20
        rows = [loading, high, low]
        sort_rows(rows, "price", False)
        self.assertEqual([row.symbol for row in rows], ["LOW", "HIGH", "WAIT"])

    def test_added_time_is_sortable_by_recency(self):
        old = PortfolioRow("OLD", None, 100)
        new = PortfolioRow("NEW", None, 200)
        rows = [old, new]
        sort_rows(rows, "added", True)
        self.assertEqual([row.symbol for row in rows], ["NEW", "OLD"])

    def test_portfolio_percentage_is_sortable(self):
        small = PortfolioRow("SMALL", None, portfolio_percent=2.0)
        large = PortfolioRow("LARGE", None, portfolio_percent=20.0)
        rows = [small, large]
        sort_rows(rows, "portfolio_percent", True)
        self.assertEqual([row.symbol for row in rows], ["LARGE", "SMALL"])

    def test_default_community_members_have_fixture_holdings(self):
        self.assertEqual(len(DEFAULT_MEMBERS), 6)
        self.assertTrue(all(member.holdings for member in DEFAULT_MEMBERS))
        self.assertEqual(DEFAULT_MEMBERS[0].name, "Warren Buffett")
        self.assertEqual(DEFAULT_MEMBERS[0].holdings[0].symbol, "AAPL")
        self.assertEqual(DEFAULT_MEMBERS[0].holdings[0].portfolio_percent, 22.04)
        self.assertTrue(all(member.manager and member.report_date for member in DEFAULT_MEMBERS))

    def test_community_portfolio_columns_end_with_disclosure_metadata(self):
        self.assertEqual(
            view_columns("basic", portfolio=True)[-2:],
            ["portfolio_percent", "reported"],
        )
        self.assertIn(("Enter", "Open"), footer_bindings("community"))
        self.assertIn(("Esc", "Back"), footer_bindings("community", True))

    def test_portfolio_adds_conditional_extended_hours_columns(self):
        regular = sample()
        extended = sample()
        extended.extended_hours = ExtendedHours("post", 232.0, 1.0, 0.43, 200)
        trend = Trend("1m", [100.0, 102.0], 2.0, 100.0, 102.0)

        output = io.StringIO()
        with redirect_stdout(output):
            print_table([Result(regular, {"1m": trend}, 100)], "1m", Color("never"), False)
        self.assertIn("PRICE", output.getvalue())
        self.assertNotIn("AFTER HOURS", output.getvalue())

        output = io.StringIO()
        with redirect_stdout(output):
            print_table([Result(extended, {"1m": trend}, 100)], "1m", Color("never"), False)
        self.assertIn("$232.00", output.getvalue())
        self.assertIn("AFTER HOURS", output.getvalue())
        self.assertIn("AFTER %", output.getvalue())


class FundamentalsTests(unittest.TestCase):
    def test_parses_yahoo_fundamentals_fallback(self):
        results = []
        for field, values in {
            "trailingMarketCap": [("2026-08-01", 4_500_000_000_000)],
            "trailingPeRatio": [("2026-08-01", 35.2)],
            "trailingDilutedEPS": [("2026-06-30", 8.68)],
            "quarterlyTotalRevenue": [
                ("2025-06-30", 100),
                ("2025-09-30", 110),
                ("2025-12-31", 120),
                ("2026-03-31", 115),
                ("2026-06-30", 125),
            ],
        }.items():
            results.append({
                "meta": {"type": [field]},
                field: [
                    {"asOfDate": date, "reportedValue": {"raw": value}}
                    for date, value in values
                ],
            })

        class Response(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *args):
                self.close()

        provider = FundamentalsProvider(
            opener=lambda request, timeout: Response(
                json.dumps({"timeseries": {"result": results}}).encode()
            )
        )
        facts = provider._yahoo("AAPL")
        self.assertEqual(facts.market_cap, 4_500_000_000_000)
        self.assertEqual(facts.pe_ratio, 35.2)
        self.assertEqual(facts.trailing_eps, 8.68)
        self.assertEqual(facts.revenue_growth, 25)

    def test_calculates_market_cap_pe_and_quarterly_revenue_growth(self):
        def items(values):
            return [
                {
                    "frame": frame,
                    "val": value,
                    "form": "10-Q",
                    "filed": f"{year}-{quarter * 2 + 1:02d}-01",
                }
                for frame, value in values
                for year, quarter in [(int(frame[2:6]), int(frame[-1]))]
            ]

        payload = {
            "facts": {
                "us-gaap": {
                    "EntityCommonStockSharesOutstanding": {
                        "units": {"shares": [{"val": 10_000_000, "end": "2025-12-31", "filed": "2026-01-20", "form": "10-K"}]}
                    },
                    "EarningsPerShareDiluted": {
                        "units": {"USD/shares": items([("CY2025Q1", 2), ("CY2025Q2", 3), ("CY2025Q3", 4), ("CY2025Q4", 5)])}
                    },
                    "RevenueFromContractWithCustomerExcludingAssessedTax": {
                        "units": {"USD": items([("CY2024Q4", 100), ("CY2025Q4", 120)])}
                    },
                }
            }
        }
        facts = fundamentals_from_companyfacts(payload, 140.0)
        self.assertEqual(facts.market_cap, 1_400_000_000)
        self.assertEqual(facts.trailing_eps, 14)
        self.assertEqual(facts.pe_ratio, 10)
        self.assertAlmostEqual(facts.revenue_growth, 20)


class ProfilerTests(unittest.TestCase):
    def test_calculates_overlapping_request_wall_time(self):
        events = [
            Event("network", 100.0, started_ms=0.0),
            Event("network", 100.0, started_ms=50.0),
            Event("network", 25.0, started_ms=200.0),
        ]
        self.assertEqual(_peak_concurrency(events), 2)
        self.assertEqual(_wall_time(events), 175.0)

    def test_records_request_network_and_parse_timings(self):
        payload = {
            "chart": {
                "error": None,
                "result": [{
                    "meta": {
                        "symbol": "AAPL",
                        "regularMarketPrice": 231.0,
                        "chartPreviousClose": 229.0,
                        "regularMarketTime": 100,
                    },
                    "timestamp": [100],
                    "indicators": {"quote": [{"close": [231.0]}]},
                }],
            }
        }

        class Response(io.BytesIO):
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, *args):
                self.close()

        profiler = Profiler(enabled=True)
        provider = YahooProvider(
            opener=lambda request, timeout: Response(json.dumps(payload).encode()),
            profiler=profiler,
        )
        provider.get("AAPL")

        self.assertEqual(
            [event.name for event in profiler.events],
            ["network", "parse_json", "request"],
        )
        self.assertGreater(profiler.events[0].metadata["bytes"], 0)
        self.assertEqual(profiler.events[0].metadata["status"], 200)


if __name__ == "__main__":
    unittest.main()
