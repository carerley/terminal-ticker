# ticker

A small, dependency-free CLI for checking the latest available stock price.
No API key is required.

```bash
python3 -m pip install .
ticker AAPL
ticker AAPL MSFT
ticker AAPL --json
```

For isolated installation, use `pipx install .` or `uv tool install .`.

To expose the included skill to an agent, link or copy `skills/stock-quote` into
that agent's skills directory (for example `~/.codex/skills/stock-quote` or
`~/.claude/skills/stock-quote`). The skill invokes the installed `ticker`
command and consumes its JSON output.

`ticker` uses Yahoo Finance's unofficial chart endpoint. It may be delayed,
rate-limited, or changed without notice, so this tool is intended for quick
informational checks rather than trading decisions.

After creating a stable `vX.Y.Z` tag, run **Update terminal-ticker** from the
tap repository's Actions page. It detects the newest tag, verifies the archive,
updates the checksum, tests the formula, and publishes the change.

Run tests with `python3 -m unittest discover -s tests -v`.
