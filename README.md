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

Run tests with `python3 -m unittest discover -s tests -v`.
