import curses
import base64
import io
import json
import os
import platform
import struct
import tempfile
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError

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
from ticker.portfolio import Portfolio, Preferences, state_directory
from ticker.fundamentals import FundamentalsProvider, fundamentals_from_companyfacts
from ticker.identity import DeviceIdentity, WatchlistVersionMismatch
from ticker.profiler import Event, Profiler, _peak_concurrency, _wall_time
from ticker.render import Color, sparkline
from ticker.cli import Result, parser, print_table
from ticker.tui import (
    PortfolioRow,
    _apply_live_quote,
    calculate_layout,
    footer_bindings,
    market_status,
    move_selection,
    sort_rows,
    view_columns,
)
from ticker.stream import LiveQuote, decode_live_quote
from ticker.update import Update, check_for_update, prompt_for_update


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


class Response:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return None

    def read(self):
        return json.dumps(self.payload).encode()


def sample():
    return Quote("AAPL", "Apple Inc.", "USD", 231.0, 2.0, 0.87, 229.0, "REGULAR", 100)


class IdentityTests(unittest.TestCase):
    def test_registers_and_persists_token_when_missing(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "ticker" / "credentials.json"
            requests = []

            def open_request(request, timeout):
                requests.append((request, timeout))
                return Response({"token": "tkr_live_new", "user": {"id": "user-1"}})

            identity = DeviceIdentity(path, "http://api.example.test", open_request)
            with patch.dict(os.environ, {"TICKER_TOKEN": ""}):
                self.assertEqual(identity.ensure_token(), "tkr_live_new")

            self.assertEqual(len(requests), 1)
            self.assertEqual(requests[0][0].full_url, "http://api.example.test/v1/devices")
            self.assertEqual(
                json.loads(requests[0][0].data),
                {"name": (platform.node() or "")[:80] or None},
            )
            self.assertEqual(json.loads(path.read_text()), {"token": "tkr_live_new"})
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_reuses_persisted_token_without_calling_backend(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "credentials.json"
            path.write_text('{"token": "tkr_live_existing"}')

            def unexpected_request(*_):
                raise AssertionError("backend should not be called")

            identity = DeviceIdentity(path, opener=unexpected_request)
            with patch.dict(os.environ, {"TICKER_TOKEN": ""}):
                self.assertEqual(identity.ensure_token(), "tkr_live_existing")

    def test_empty_persisted_token_requests_a_new_one(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "credentials.json"
            path.write_text('{"token": ""}')
            identity = DeviceIdentity(
                path,
                opener=lambda *_args, **_kwargs: Response({"token": "tkr_live_replacement"}),
            )
            with patch.dict(os.environ, {"TICKER_TOKEN": ""}):
                self.assertEqual(identity.ensure_token(), "tkr_live_replacement")

    def test_syncs_local_symbols_to_default_backend_list(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "credentials.json"
            path.write_text('{"token": "tkr_live_existing"}')
            requests = []

            def open_request(request, timeout):
                requests.append(request)
                if request.get_method() == "GET":
                    return Response({
                        "lists": [
                            {"id": "secondary", "is_default": False, "version": 2},
                            {"id": "main-list", "is_default": True, "version": 7},
                        ]
                    })
                return Response({"version": 8})

            identity = DeviceIdentity(path, "http://api.example.test", open_request)
            with patch.dict(os.environ, {"TICKER_TOKEN": ""}):
                self.assertTrue(identity.sync_watchlist(["aapl", "MSFT"]))

            self.assertEqual([request.get_method() for request in requests], ["GET", "PUT"])
            self.assertEqual(requests[0].full_url, "http://api.example.test/v1/lists")
            self.assertEqual(
                requests[1].full_url,
                "http://api.example.test/v1/lists/main-list/items",
            )
            self.assertEqual(requests[1].get_header("Authorization"), "Bearer tkr_live_existing")
            self.assertEqual(requests[1].get_header("If-match"), 'W/"7"')
            self.assertEqual(
                json.loads(requests[1].data),
                {"items": [
                    {"symbol": "AAPL", "note": None},
                    {"symbol": "MSFT", "note": None},
                ]},
            )

    def test_sync_without_token_does_not_call_backend(self):
        with tempfile.TemporaryDirectory() as directory:
            identity = DeviceIdentity(
                Path(directory) / "missing.json",
                opener=lambda *_args, **_kwargs: self.fail("backend should not be called"),
            )
            with patch.dict(os.environ, {"TICKER_TOKEN": ""}):
                self.assertFalse(identity.sync_watchlist(["AAPL"]))

    def test_community_directory_is_capped_at_fifty(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "credentials.json"
            path.write_text('{"token": "tkr_live_existing"}')
            requests = []

            def open_request(request, timeout):
                requests.append(request)
                return Response({"members": [{"handle": "alex", "display_name": "Alex"}]})

            identity = DeviceIdentity(path, "http://api.example.test", open_request)
            with patch.dict(os.environ, {"TICKER_TOKEN": ""}):
                members = identity.community_members(limit=500)

            self.assertEqual(members[0]["handle"], "alex")
            self.assertEqual(
                requests[0].full_url,
                "http://api.example.test/v1/community/members?limit=50",
            )

    def test_join_community_posts_authenticated_request(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "credentials.json"
            path.write_text('{"token": "tkr_live_existing"}')
            requests = []

            def open_request(request, timeout):
                requests.append(request)
                return Response({"handle": "member-12345678", "joined": True})

            identity = DeviceIdentity(path, "http://api.example.test", open_request)
            with patch.dict(os.environ, {"TICKER_TOKEN": ""}):
                result = identity.join_community("Alex Li")

            self.assertTrue(result["joined"])
            self.assertEqual(requests[0].get_method(), "POST")
            self.assertEqual(json.loads(requests[0].data), {"display_name": "Alex Li"})
            self.assertEqual(
                requests[0].full_url,
                "http://api.example.test/v1/community/join",
            )
            self.assertEqual(requests[0].get_header("Authorization"), "Bearer tkr_live_existing")

    def test_feedback_posts_authenticated_message(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "credentials.json"
            path.write_text('{"token": "tkr_live_existing"}')
            requests = []

            def open_request(request, timeout):
                requests.append(request)
                return Response({"submitted": True})

            identity = DeviceIdentity(path, "http://api.example.test", open_request)
            with patch.dict(os.environ, {"TICKER_TOKEN": ""}):
                result = identity.send_feedback("feedback message.")

            self.assertTrue(result["submitted"])
            self.assertEqual(requests[0].get_method(), "POST")
            self.assertEqual(
                requests[0].full_url,
                "http://api.example.test/v1/feedback",
            )
            self.assertEqual(json.loads(requests[0].data), {"msg": "feedback message."})
            self.assertEqual(
                requests[0].get_header("Authorization"),
                "Bearer tkr_live_existing",
            )

    def test_does_not_overwrite_after_version_conflict(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "credentials.json"
            path.write_text('{"token": "tkr_live_existing"}')
            requests = []

            def open_request(request, timeout):
                requests.append(request)
                if request.get_method() == "GET":
                    return Response({
                        "lists": [{"id": "main", "is_default": True, "version": 3}]
                    })
                raise HTTPError(
                    request.full_url,
                    409,
                    "Conflict",
                    {},
                    io.BytesIO(b'{"error":"stale_version","version":4}'),
                )

            identity = DeviceIdentity(path, "http://api.example.test", open_request)
            with patch.dict(os.environ, {"TICKER_TOKEN": ""}):
                with self.assertRaises(WatchlistVersionMismatch) as raised:
                    identity.sync_watchlist(["AAPL"])

            self.assertEqual(raised.exception.version, 4)
            self.assertEqual(len(requests), 2)
            self.assertEqual(requests[1].get_header("If-match"), 'W/"3"')


class UpdateTests(unittest.TestCase):
    def test_detects_and_caches_new_release(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "update.json"
            update = check_for_update(
                "0.3.0",
                path=path,
                opener=lambda *_args, **_kwargs: Response({
                    "tag_name": "v0.4.0",
                    "html_url": "https://example.test/releases/0.4.0",
                }),
                now=100_000,
            )
            self.assertEqual(update, Update("0.3.0", "0.4.0", "https://example.test/releases/0.4.0"))
            self.assertEqual(json.loads(path.read_text())["latest"], "0.4.0")

    def test_falls_back_to_latest_tag_when_no_release_exists(self):
        with tempfile.TemporaryDirectory() as directory:
            calls = []

            def open_request(request, timeout):
                calls.append(request.full_url)
                if request.full_url.endswith("/releases/latest"):
                    raise HTTPError(request.full_url, 404, "Not Found", {}, io.BytesIO())
                return Response([{"name": "v0.4.0"}])

            update = check_for_update(
                "0.3.0",
                path=Path(directory) / "update.json",
                opener=open_request,
                now=100_000,
            )
            self.assertEqual(update.latest, "0.4.0")
            self.assertEqual(len(calls), 2)
            self.assertEqual(
                update.release_url,
                "https://github.com/carerley/terminal-ticker/compare/v0.3.0...v0.4.0",
            )

    def test_update_choice_runs_homebrew(self):
        commands = []
        with redirect_stdout(io.StringIO()) as output:
            updated = prompt_for_update(
                Update("0.3.0", "0.4.0"),
                read=lambda _: "1",
                run_command=lambda command, **kwargs: (
                    commands.append((command, kwargs))
                    or type("Result", (), {"returncode": 0})()
                ),
            )
        self.assertIn("› 1. Update now", output.getvalue())
        self.assertIn("  2. Skip", output.getvalue())
        self.assertEqual(
            commands,
            [(["brew", "upgrade", "carerley/tap/ticker"], {"check": False})],
        )
        self.assertTrue(updated)
        self.assertIn("Update complete. Run `ticker` again.", output.getvalue())

    def test_enter_uses_default_update_choice(self):
        commands = []
        with redirect_stdout(io.StringIO()):
            updated = prompt_for_update(
                Update("0.3.0", "0.4.0"),
                read=lambda _: "",
                run_command=lambda command, **kwargs: (
                    commands.append(command)
                    or type("Result", (), {"returncode": 0})()
                ),
            )
        self.assertEqual(commands, [["brew", "upgrade", "carerley/tap/ticker"]])
        self.assertTrue(updated)

    def test_failed_update_keeps_current_app_running(self):
        with redirect_stdout(io.StringIO()):
            updated = prompt_for_update(
                Update("0.3.0", "0.4.0"),
                read=lambda _: "1",
                run_command=lambda *_args, **_kwargs: type(
                    "Result", (), {"returncode": 1}
                )(),
            )
        self.assertFalse(updated)

    def test_cached_check_does_not_repeat_prompt_within_interval(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "update.json"
            path.write_text(json.dumps({
                "checked_at": 100,
                "latest": "0.4.0",
                "release_url": "https://example.test/release",
            }))
            update = check_for_update(
                "0.3.0",
                path=path,
                opener=lambda *_args, **_kwargs: self.fail("cache should be used"),
                now=102,
            )
            self.assertIsNone(update)


class QuoteTests(unittest.TestCase):
    def test_decodes_streamed_price_fields(self):
        def varint(value):
            encoded = bytearray()
            while value > 127:
                encoded.append((value & 127) | 128)
                value >>= 7
            encoded.append(value)
            return bytes(encoded)

        timestamp_ms = 1_700_000_000_000
        payload = (
            b"\x0a\x04AAPL"
            + b"\x15" + struct.pack("<f", 231.5)
            + b"\x18" + varint(timestamp_ms << 1)
            + b"\x38\x01"
            + b"\x45" + struct.pack("<f", 1.25)
        )
        message = json.dumps({"message": base64.b64encode(payload).decode()})
        self.assertEqual(
            decode_live_quote(message),
            LiveQuote("AAPL", 231.5, 1.25, 1, 1_700_000_000),
        )

    def test_live_update_changes_only_quote_price_fields(self):
        quote = sample()
        _apply_live_quote(quote, LiveQuote("AAPL", 232.0, 1.31, 1, 200))
        self.assertEqual(quote.price, 232.0)
        self.assertEqual(quote.timestamp, 200)
        self.assertAlmostEqual(quote.latest_change_percent, 1.310044, places=5)

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
    def test_simulated_config_users_have_separate_watchlist_state(self):
        with patch.dict(
            os.environ,
            {"XDG_CONFIG_HOME": "/tmp/ticker-user-a"},
            clear=False,
        ):
            os.environ.pop("XDG_STATE_HOME", None)
            self.assertEqual(
                state_directory(),
                Path("/tmp/ticker-user-a/ticker/state"),
            )

    def test_explicit_state_home_takes_precedence(self):
        with patch.dict(
            os.environ,
            {
                "XDG_CONFIG_HOME": "/tmp/ticker-user-a",
                "XDG_STATE_HOME": "/tmp/custom-state",
            },
        ):
            self.assertEqual(state_directory(), Path("/tmp/custom-state/ticker"))

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
    def test_help_hides_advanced_output_options(self):
        help_text = parser().format_help()
        for option in (
            "--json",
            "--compact",
            "--chart",
            "--no-chart",
            "--ascii",
            "--color",
            "--no-interactive",
        ):
            self.assertNotIn(option, help_text)
        self.assertIn("-p, --print", help_text)

    def test_print_flags_disable_interactive_mode(self):
        self.assertTrue(parser().parse_args(["--print"]).no_interactive)
        self.assertTrue(parser().parse_args(["-p"]).no_interactive)
        self.assertTrue(parser().parse_args(["--no-interactive"]).no_interactive)

    def test_cli_accepts_only_one_optional_ticker(self):
        self.assertEqual(parser().parse_args(["AAPL"]).symbol, "AAPL")
        self.assertIsNone(parser().parse_args([]).symbol)
        with redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                parser().parse_args(["AAPL", "MSFT"])

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
        self.assertEqual(calculate_layout(120, 30, show_sidebar=False).sidebar_width, 0)
        layout = calculate_layout(120, 30)
        self.assertEqual(layout.footer_y - layout.chat_y, 2)

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
