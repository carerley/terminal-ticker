# ticker

A terminal app for raising financial awareness without making developers
switch context. It provides quick, glanceable market checks with no account,
API key, or runtime dependency.

```bash
python3 -m pip install .
ticker AAPL                    # add and quote one symbol
ticker                         # recent-symbol portfolio with 1M trends
ticker -p                      # print the portfolio without interactive mode
ticker --profile                # diagnose portfolio latency
ticker AAPL --profile-json profile.json
```

Successful queries are added to the watchlist automatically. Only one symbol
can be added at a time.
Human output uses green for gains and red for losses when connected to a
terminal; `NO_COLOR` and `--color never` disable ANSI colors.

In an interactive terminal, `ticker` highlights one full portfolio row. Use
Up/Down or `j`/`k` to move and Home/End to jump. Use `a` to add, `d` to
remove, `v` to cycle basic/extended/study views, and Left/Right
to highlight a column without reordering rows. Press `s` to apply that sort or
reverse its direction. No row or column is highlighted until you navigate.
The watchlist starts unsorted with newly added symbols first. An explicitly
chosen view and sort are restored on the next launch; `q` or Esc exits. Use
`--print` (or `-p`) or piped output for a regular printed table.
Press Tab to switch between Watchlist and Community. The app loads up to 50
community members from the backend and shows their display names in the sidebar.
Select a member with Up/Down and press Enter to fetch and open that member's
shared watchlist with live market columns. Use the normal view and sort controls
inside a watchlist, and press Esc to go back.
When no community directory is available, clearly labelled demo investors such
as Warren Buffett and Ray Dalio remain available using reported manager holdings.
The community chat bar invites users to join in order to view others' lists and
share their own watchlist. Press `y` at the `y/n` prompt to join; the
app asks for a display name, then the backend assigns the anonymous account a
handle and marks its default watchlist as community-visible. Press `n` to dismiss
the prompt. The community sidebar is hidden on the Watchlist tab so the portfolio
uses the full terminal width.
On wide terminals, the asynchronous member directory appears in a right
sidebar without online status. Press `/` to open the feedback bar; application
commands remain available as direct shortcuts in the footer.
The portfolio appears immediately with placeholder rows, fetches up to four
symbols concurrently, and fills in prices before loading trend charts.
While the interactive watchlist is open, one WebSocket connection subscribes
to its symbols and updates only live prices and percentages. Charts and
fundamentals retain their initial snapshot values. If streaming disconnects,
the last displayed prices remain available while the connection retries.

Market data is fetched on every invocation and is never cached. The portfolio
stores only ticker symbols, their last-query times, and display preferences.

On first startup, `ticker` registers the device with the backend and stores the
returned token in the XDG-aware `~/.config/ticker/credentials.json`. Later runs
reuse that token. Set `TICKER_API_URL` to select the backend (the local default is
`http://127.0.0.1:8000`) or `TICKER_TOKEN` to supply a token without writing one.
If registration is unavailable, quote and portfolio features continue locally.
Setting `XDG_CONFIG_HOME` also isolates watchlist state unless `XDG_STATE_HOME`
is set explicitly, which makes it convenient to simulate multiple local users.
When `ticker` exits, it uploads the final local symbols to the authenticated
user's default backend list. If the backend is unavailable, exit still completes
normally and the local watchlist remains intact. After a stale-version conflict,
the next sync starts by fetching the backend's latest version.

The portfolio's progressive views keep a one-row-per-symbol comparison of
price, the date each symbol was added, today, a 1M sparkline, 1M, 6M, YTD, 1Y,
and 5Y returns, market cap, trailing P/E, and
quarterly revenue growth. During extended hours it adds the regular close,
latest pre- or post-market price, and extended-hours percentage. Price and
charts load first; fundamentals populate afterward. Company fundamentals use
Yahoo's no-key time-series endpoint and display `N/A` when they do not apply,
such as for many ETFs.

Use `--profile` to print request, network, parsing, and rendering timings to
standard error. Profiling disables the interactive portfolio for that run so
waiting for keyboard input does not distort the result. `--profile-json PATH`
writes the same measurements for automated comparisons.

During pre-market or after-hours sessions, output keeps the regular close as a
reference and adds the latest extended-hours price and its change. Those
columns remain visible after the extended session closes, so the displayed
value is still the latest available trade. They are omitted during the regular
session, when the regular price is already the latest price.

For isolated installation, use `pipx install .` or `uv tool install .`.

To expose the included skill to an agent, link or copy `skills/stock-quote` into
that agent's skills directory (for example `~/.codex/skills/stock-quote` or
`~/.claude/skills/stock-quote`). The skill invokes the installed `ticker`
command and consumes its JSON output.

`ticker` uses Yahoo Finance's unofficial chart endpoint. It may be delayed,
rate-limited, or changed without notice, so this tool is intended for quick
informational checks rather than trading decisions.

After creating a stable `vX.Y.Z` tag, update `Formula/ticker.rb` in the
`carerley/homebrew-tap` repository with the tag URL and archive checksum, then
commit it to publish the Homebrew release.

Run tests with `python3 -m unittest discover -s tests -v`.
