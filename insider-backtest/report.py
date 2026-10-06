"""Stage 6: results/summary.md, events.csv, groups.csv, insider_backtest.xlsx, today.md."""
import datetime as dt

import numpy as np
import pandas as pd
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from backtest import HORIZONS, MAIN, YEARS
from common import RESULTS

BG, FG = "1E1E1E", "FFFFFF"
COLOURS = {"PASS": "1E7B34", "MIXED": "B7791F", "FAIL": "9B2C2C"}


def pct(x, dp=1):
    return "n/a" if x is None or pd.isna(x) else f"{x * 100:+.{dp}f}%"


def plain_pct(x):
    return "n/a" if x is None or pd.isna(x) else f"{x * 100:.0f}%"


# ---------- workbook ----------

def _sheet(wb, title, df, verdict_col=None, first=False):
    ws = wb.active if first else wb.create_sheet(title)
    ws.title = title
    ws.sheet_view.showGridLines = False
    fill, font = PatternFill("solid", fgColor=BG), Font(color=FG, name="Calibri", size=10)
    bold = Font(color=FG, name="Calibri", size=10, bold=True)
    cols = list(df.columns)
    for j, c in enumerate(cols, 1):
        cell = ws.cell(row=1, column=j, value=str(c))
        cell.fill, cell.font = PatternFill("solid", fgColor="2D2D2D"), bold
    for i, row in enumerate(df.itertuples(index=False), 2):
        colour = COLOURS.get(getattr(row, verdict_col)) if verdict_col else None
        for j, v in enumerate(row, 1):
            if isinstance(v, (float, np.floating)) and not np.isfinite(v):
                v = None
            if isinstance(v, (pd.Timestamp, dt.date)):
                v = pd.Timestamp(v).date()
            cell = ws.cell(row=i, column=j, value=v.item() if hasattr(v, "item") else v)
            cell.font = font
            cell.fill = PatternFill("solid", fgColor=colour) if colour and cols[j - 1] == verdict_col else fill
            if isinstance(v, float):
                cell.number_format = "0.0%" if any(k in cols[j - 1] for k in
                                                   ("median", "mean", "pct", "ci_", "share", "xs")) else "0.00"
    for j, c in enumerate(cols, 1):
        width = max([len(str(c))] + [len(str(x)) for x in df.iloc[:200, j - 1].tolist()]) + 2
        ws.column_dimensions[get_column_letter(j)].width = min(width, 60)
    # dark background beyond the table too
    for r in range(1, max(len(df) + 2, 40)):
        for c in range(1, max(len(cols) + 1, 15)):
            cell = ws.cell(row=r, column=c)
            if cell.value is None and cell.fill.fgColor.rgb in (None, "00000000"):
                cell.fill = fill
    ws.freeze_panes = "A2"
    return ws


def workbook(summary_rows, verdicts, groups_df, years_df, robust_df, missing_df):
    wb = Workbook()
    s = pd.DataFrame(summary_rows, columns=["Finding", "Answer", "verdict"])
    ws = _sheet(wb, "Summary", s, "verdict", first=True)
    for row in ws.iter_rows(min_row=2):
        for c in row:
            c.alignment = Alignment(wrap_text=True, vertical="top")
    ws.column_dimensions["B"].width = 100
    tag = groups_df[groups_df.survivorship == MAIN].merge(verdicts[["group", "verdict"]], on="group", how="left")
    _sheet(wb, "By tag", tag, "verdict")
    _sheet(wb, "By start year", years_df)
    _sheet(wb, "Robustness", robust_df, "verdict" if "verdict" in robust_df else None)
    _sheet(wb, "Missing prices", missing_df)
    wb.save(RESULTS / "insider_backtest.xlsx")


# ---------- markdown ----------

def summary_md(ctx):
    v, g, ev = ctx["verdicts"], ctx["groups"], ctx["events"]
    allv = v[v.group == "All insider buys"].iloc[0]
    main = g[(g.survivorship == MAIN)]
    allg = main[main.group == "All insider buys"].set_index("horizon")
    passed = v[(v.verdict == "PASS") & (v.group != "All insider buys")]
    mixed = v[v.verdict == "MIXED"]
    failed = v[v.verdict == "FAIL"]
    ins = ev[ev.kind == "insider"]
    L = []
    w = L.append
    w("# Insider buying backtest: results\n")
    w(f"Run {dt.date.today().isoformat()}. Universe: profitable, free-cash-positive US 10-K filers with return on "
      f"capital of at least 15% and a market cap of at least $2bn, outside banks, insurers and REITs. All data is "
      f"filed SEC data (Form 4/5, XBRL) plus Yahoo adjusted closes.\n")
    w("## Findings\n")
    w("**1. Does insider buying work in this universe at all?**  ")
    w(f"All insider buys: **{allv.verdict}**. {int(allv.n_3y)} events with a full 3-year record. Median 3-year return "
      f"minus the median of matched companies with no insider buying: {pct(allv.median_xs_3y)} "
      f"(95% interval {pct(allv.ci_lo)} to {pct(allv.ci_hi)}). They beat the control in "
      f"{plain_pct(allv.share_years_beating)} of start years. Medians by half: 2009-16 {pct(allv.half1_median)}, "
      f"2017-21 {pct(allv.half2_median)}.\n")
    w("**2. Which tags pass and which fail?**  ")
    if len(passed):
        w("Passing every test in Section 7 (at least 100 events, median 3-year excess above 0 with its 95% interval "
          "above 0, beating the control in at least 2 of 3 start years, holding in both halves):\n")
        for r in passed.itertuples():
            w(f"- **{r.group}**: n={int(r.n_3y)}, median 3-year excess {pct(r.median_xs_3y)} "
              f"({pct(r.ci_lo)} to {pct(r.ci_hi)})")
    else:
        w("No tag passed every test. In plain words: **no reliable edge** in any kind of buy.\n")
    if len(mixed):
        w("\nMixed (median above control but failing at least one test, so **no reliable edge**): " +
          ", ".join(f"{r.group} ({pct(r.median_xs_3y)}, n={int(r.n_3y)})" for r in mixed.itertuples()) + ".")
    if len(failed):
        w("\nFail (median at or below control): " + ", ".join(f"{r.group} ({pct(r.median_xs_3y)}, n={int(r.n_3y)})"
                                                               for r in failed.itertuples()) + ".")
    w("\n**3. Is the effect bigger at 1 year than at 5 years?** (all insider buys, median excess over control)\n")
    w("| Horizon | Entry years | Events | Median return | Median excess vs control | 95% interval | Excess vs SPY | % beating control |")
    w("|---|---|---|---|---|---|---|---|")
    for h in HORIZONS:
        if h in allg.index:
            r = allg.loc[h]
            w(f"| {h} | {YEARS[h][0]}-{YEARS[h][1]} | {int(r['count'])} | {pct(r['median'])} | "
              f"{pct(r.median_xs_control)} | {pct(r.ci_lo)} to {pct(r.ci_hi)} | {pct(r.median_xs_spy)} | "
              f"{plain_pct(r.pct_beat_control)} |")
    w("")
    w(ctx["horizon_sentence"] + "\n")
    w("**4. Noise or real?**  ")
    w(ctx["noise_sentence"] + "\n")

    w("## Robustness\n")
    w(ctx["robust_md"])
    w("## How the test was built\n")
    w(f"- Events: {len(ins)} insider-buy events ({int(ins.missing_price.sum())} without an entry price), "
      f"{int((ev.kind == 'entity').sum())} fund/company buy events reported separately.")
    w("- Entry is the next trading day's close after the Form 4 **filing** date, never the transaction date.")
    w("- One event per company per 90 days. Buys by other insiders within 30 days of the first filing fold into it.")
    w("- Control: every other company IN the universe on the filing date with no insider purchase in the prior "
      "12 months, measured over the same dates. The headline is event return minus control median.")
    w(f"- The main survivorship version is '{MAIN}' (a delisted stock is held at its last price). 'Exclude' and "
      "'zero' are in the Robustness tab and groups.csv.")
    w("- Event role = the most senior insider in the event. Routine/opportunistic: an event counts as Opportunistic "
      "if any of its insiders is opportunistic. Routine and Opportunistic are judged on the insider's open-market "
      "trades in the same company. 'History' means at least one open-market buy or sell in each of the 3 prior "
      "calendar years.")
    w("- Each pass test uses a fixed seed for its 2,000 resamples.\n")
    w("## Data notes\n")
    for line in ctx["data_notes"]:
        w(f"- {line}")
    w("\n## Known limits\n")
    w("- No trading costs or taxes are modelled. There is no AUD/USD currency effect and no US withholding tax.")
    w("- Yahoo's coverage of dead tickers is incomplete. Companies with no Yahoo prices at all cannot be valued, so "
      "they never enter the universe (see missing_prices.csv). The three-way survivorship test covers stocks whose "
      "price history stops early.")
    w("- XBRL starts around 2009-2010 for most companies, so the universe is thin before 2010.")
    w("- Return on capital and free cash come from tagged XBRL, not hand-stripped books. That is coarser than Our "
      "Company Values. Where a 10-K has no OperatingIncomeLoss tag, pre-tax income plus interest expense stands in "
      "(counted in Data notes).")
    w("- The cluster tag is only known once the later insiders have filed, up to 30 days after entry. The spec "
      "measures clusters this way, so it carries a little hindsight. The threshold check also re-runs clusters "
      "entering only once the k-th insider has filed.")
    (RESULTS / "summary.md").write_text("\n".join(L) + "\n")


def today_md(passed, recent, names, tickers):
    L = [f"# Insider buys since 1 Jan 2026 that meet the passing tags\n",
         f"Built {dt.date.today().isoformat()}. Only tags that passed every test in Section 7 are applied. "
         "This is a list of names, not a price call.\n"]
    if passed.empty:
        L.append("**No tag passed.** The backtest found no reliable edge, so no current names are listed.")
        L.append(f"\n(For reference only: {len(recent)} insider-buy events have been filed since 1 Jan 2026 by "
                 f"companies in the current universe. They are in events.csv with entry dates in 2026.)")
    else:
        L.append("Passing tags: " + ", ".join(passed.group) + "\n")
        mask = np.zeros(len(recent), bool)
        for r in passed.itertuples():
            mask |= True if r.tag == "all" else (recent[r.tag] == r.value).to_numpy()
        hits = recent[mask]
        if hits.empty:
            L.append("No insider buys filed since 1 Jan 2026 meet any passing tag.")
        else:
            L.append("| Company | Ticker | Filed | Tags met | Role | Insiders | $ bought | Stake | Off 52w high |")
            L.append("|---|---|---|---|---|---|---|---|---|")
            for r in hits.sort_values("filing_date").itertuples():
                met = [p.group for p in passed.itertuples() if p.tag == "all" or getattr(r, p.tag) == p.value]
                L.append(f"| {names.get(r.issuer_cik, r.issuer_cik)} | {tickers.get(r.issuer_cik, '')} | "
                         f"{pd.Timestamp(r.filing_date).date()} | {'; '.join(met)} | {r.role} | {int(r.n_insiders)} | "
                         f"${r.dollars:,.0f} | {plain_pct(r.stake) if np.isfinite(r.stake) else 'new holder'} | "
                         f"{plain_pct(r.drawdown)} |")
    (RESULTS / "today.md").write_text("\n".join(L) + "\n")
