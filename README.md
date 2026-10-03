# Technology Tracker (daily)

A daily watch on the AI chip stack: company filings, government rules and money, patents, and dated company
promises. Every number is calculated by code from each company's own filings and tested before it is shown.

- **App:** GitHub Pages serves `docs/`. Tabs: Home (every company by layer, with a Strong/Watch/Weak verdict), Today, Layers (a full page per layer), Analyst notes, Last 30 days,
  Promises, Scoreboard, Number tests, What's watched. Each company has a card answering: where is it going, is it growing, is its technology in demand, what is next, and is it strong.
- **Email:** sent after every run: red and amber items, failed sources, and a link to the app.
- **Data run:** `.github/workflows/daily.yml` at 20:00 UTC (7am AEDT). Code in `tracker/`.
- **Analyst:** a Claude Code routine reads each red and amber item and writes plain-English notes (`CLAUDE.md`).
- **Tests:** `pytest -q` runs before every data run. If a test fails, nothing is published that day.

## Movers (candidates to price, never buy signals)
Verdict changes (backtested rules), guidance raised or cut, backlog up 15%+, supply deals naming big customers,
the turn (chips and hardware only, and only when the quarter is also above a year ago: backtested, small sample),
insider buying clusters (Form 4 open-market buys), promises delivered early, and a Strong company's share price
down 20%+ from its 90-day high (prices from Stooq; used for this alert and backtests only, never in the verdict).

## Backtests built in
- Verdict rules: 2014-2021, 15,447 company-years, outcomes 3 and 5 years later (market value via public float).
  Tech: Strong +113% median over 5 years vs +64% for all tech; beat the median in 6 of 6 start years. Weak +11%.
- The turn: 2012-2025 quarterly revenue, every US filer. Only works in chips and hardware with the year-ago guard.
- Not backtested: guidance moves, backlog jumps, supply deals, insider buying, price drawdowns.

## Live sources
SEC filings (all forms, 8-K items coloured by seriousness), company numbers from XBRL (double-pulled against
the filing text), EDGAR full-text search for supply-lock phrases, Form D private raises, Federal Register
(Commerce BIS export rules), USAspending contracts and grants, patents (free key needed), promise ledger.

## Planned sources
Korea DART and chip exports, Taiwan and US trade data, FCC authorisations, grid queues, EPA permits, MLPerf,
standards bodies, ITC import-ban cases, CUDA vs ROCm activity, GPU rental prices, China export controls.
Shown in the app as "planned" so it is always clear what is not covered.

## Honest limits
- The alert scoreboard tests business outcomes (later revenue), not share prices, and its history is mostly
  companies that survived. Small groups are marked "(small)".
- Foreign companies that do not file numbers with the SEC (TSMC, ASML) get filings and alerts but no XBRL numbers.
- A fourth quarter that exists only inside an annual report is derived (full year minus three quarters) and marked.
