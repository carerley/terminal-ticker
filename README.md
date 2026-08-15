# ticker

A small, dependency-free CLI for checking the latest available stock price.
No API key is required.

```bash
python3 -m pip install .
ticker AAPL                    # remember and quote a symbol
ticker                         # recent-symbol portfolio with 1M trends
ticker AAPL --chart 1w         # 1D, 1W, 1M, 3M, or YTD
ticker AAPL --chart all
ticker AAPL MSFT --json
ticker --profile                # diagnose portfolio latency
ticker AAPL --profile-json profile.json
```

Successful queries are remembered automatically, newest first. Manage them
with `ticker list`, `ticker forget AAPL`, and `ticker clear`. Human output uses
green for gains and red for losses when connected to a terminal; `NO_COLOR`
and `--color never` disable ANSI colors.

In an interactive terminal, `ticker` highlights one full portfolio row. Use
Up/Down or `j`/`k` to move, Home/End to jump, and `q` or Esc to exit. Use
`--no-interactive`, `ticker list`, or piped output for a regular printed table.
The portfolio appears immediately with placeholder rows, fetches up to four
symbols concurrently, and fills in prices before loading trend charts.

Market data is fetched on every invocation and is never cached. The portfolio
stores only ticker symbols and their last-query times.

The portfolio's Set 1 view keeps a one-row-per-symbol comparison of price,
today, a 1M sparkline and return, YTD return, market cap, trailing P/E, and
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

After creating a stable `vX.Y.Z` tag, manually run **brew bump** from the tap
repository's Actions page. Homebrew detects the new version, updates the URL
and checksum, and opens a pull request. Merge that pull request to publish it.

Run tests with `python3 -m unittest discover -s tests -v`.
