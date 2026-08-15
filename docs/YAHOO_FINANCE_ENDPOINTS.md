# Yahoo Finance endpoint reference

Design reference for Yahoo Finance endpoints observed in `yfinance` and related
experiments. These are unofficial, undocumented interfaces. Availability,
authentication requirements, schemas, and rate limits can change without
notice. Some endpoints require Yahoo cookies and a crumb token even when they
occasionally work without them.

## Base hosts

From `yfinance/const.py`:

```text
query1             https://query1.finance.yahoo.com
query2             https://query2.finance.yahoo.com
finance.yahoo.com  https://finance.yahoo.com
streamer           wss://streamer.finance.yahoo.com
```

## Endpoint inventory

| Host | Endpoint | Method | What it gives |
|---|---|---:|---|
| query2 | `/v8/finance/chart/{symbol}` | GET | OHLCV bars, dividends, splits, capital gains, and metadata such as timezone, exchange, and currency |
| query2 | `/v10/finance/quoteSummary/{symbol}?modules=` | GET | Up to 33 modules covering profile, financials, key statistics, ESG, holders, insiders, earnings trends, and SEC filings |
| query1 | `/v7/finance/quote?symbols=` | GET | Multi-symbol real-time quote snapshot |
| query2 | `/v7/finance/options/{symbol}[?date=]` | GET | Option chain, expiration dates, and underlying quote |
| query2 | `/ws/fundamentals-timeseries/v1/finance/timeseries/{symbol}` | GET | Income statement, balance sheet, cash flow, share count, and valuation time series |
| query2 | `/v1/finance/search?q=` | GET | Quotes, news, lists, research reports, and navigation links |
| query1 | `/v1/finance/lookup?query=&type=` | GET | Symbol lookup by asset class: equity, ETF, index, future, currency, or cryptocurrency |
| query1 | `/v1/finance/screener` | POST | Custom equity, fund, and ETF screening queries |
| query1 | `/v1/finance/screener/predefined/saved?scrIds=` | GET | Predefined screens such as `day_gainers` and `most_actives` |
| query1 | `/v1/finance/visualization` | POST | Earnings, IPO, economic-event, and split calendars |
| query1 | `/v1/finance/sectors/{key}` | GET | Sector overview, industries, and leading companies, ETFs, and funds |
| query1 | `/v1/finance/industries/{key}` | GET | Industry overview and leading performance or growth companies |
| query1 | `/v6/finance/quote/marketSummary?market=` | GET | Regional market-index summary |
| query1 | `/v6/finance/markettime?market=` | GET | Market open/close status; observed to be effectively U.S.-only |
| finance.yahoo.com | `/xhr/ncp?queryRef=&serviceKey=ncp_fin` | GET | News stream such as `latestNews`, `newsAll`, and `pressRelease` |
| query1 | `/ws/obi-integration/v1/subscriptions` | GET | Subscription state and tier |
| query1 or query2 | `/v1/test/getcrumb` | GET | Crumb token used by protected endpoints |
| streamer | `/?version=2` | WebSocket | Streaming prices encoded with protobuf/base64 |
| finance.yahoo.com | `/calendar/earnings?symbol=&offset=&size=` | GET | Ticker earnings dates via HTML; possible fallback when JSON endpoints fail |
| businessinsider | `/ajax/SearchController_Suggest?query=` | GET | Non-Yahoo symbol lookup experiment |

## Current product usage

`ticker` currently uses:

- `query1 /v8/finance/chart/{symbol}` for quotes, sessions, extended-hours
  trades, and price trends. The same chart path is known to work on query1 and
  query2.
- `query1 /ws/fundamentals-timeseries/v1/finance/timeseries/{symbol}` for market
  cap, trailing P/E, trailing diluted EPS, and quarterly revenue.

The implementation deliberately isolates providers and treats missing fields as
normal. It must degrade to `N/A` or `N/M` instead of failing the portfolio when
Yahoo rejects, rate-limits, or changes one of these endpoints.

## Operational cautions

- `query1` and `query2` are not guarantees of different data or capacity; they
  are alternate Yahoo hosts.
- HTTP `401`, `403`, and `429` responses are expected failure modes.
- Quote Summary, quote snapshot, options, screeners, and calendars may require
  a cookie/crumb flow.
- Batch quote support does not imply that it will remain accessible without a
  crumb.
- Streaming is a separate protocol and adds protobuf decoding, reconnect, and
  subscription-management complexity.
- HTML scraping is a last-resort fallback because markup changes are not a
  stable API contract.
- Business Insider is non-Yahoo and experimental; it should not be a default
  market-data dependency.

