# Phase 1 research engine

This branch keeps the original analyst/research/trader/risk/portfolio workflow.
Runs are started by the user. It does not add a scheduler, brokerage execution,
or a web server.

## What stays unchanged

OHLCV vendor implementations, price adjustment, indicators, cache freshness,
current-day candles and quote handling are unchanged. The Market Analyst and
Trader price prompts are unchanged. A current-day candle may still be partial;
completed-candle filtering and a separate current quote remain deferred until
the user reviews real original analyses.

The Research Manager's direction, the Trader's action mapping, risk debate,
five-tier PM rating, configured model tiers and portfolio context are retained.
The existing Markdown Memory structure, holding period, return calculation and
checkpoint/resume behavior remain in place.

## Prices must be available before reasoning

The graph's `Price Data` prerequisite uses the configured `get_stock_data`
vendor chain over the preceding 30 calendar days, inclusive of the analysis date.
It requires at least one dated, finite positive Close inside that window.
It understands the existing Yahoo and Alpha Vantage CSV formats. Empty or
unusable replies can fall back only within the configured chain. No successful
price response means the run stops before analysts or Memory reflection call
an LLM, even when the Market Analyst was not selected.

This adds one data request per fresh run, not an LLM request, and does not change
the CSV or agent prompts. It does not require a complete candle, all OHLCV
columns, a minimum indicator history, or a newly invented freshness threshold.
Existing vendor freshness checks still apply. A resumed checkpoint may already
have completed this prerequisite.

News, fundamentals, technical indicators and macro/prediction enrichments can
report unavailable data without aborting the whole analysis. The existing
sentinel text identifies missing data and says not to fabricate values. Internal
programming/configuration errors are not all suppressed by the CLI. Expected
vendor/I/O failures show a safe error notice; Python callers still receive
exceptions. No failed-analysis JSON journal is created.

## Supporting technical review by the PM

The PM receives the full Market Analyst report to check the Trader's price
levels against current price, support/resistance, ATR, volatility and indicators.
It must keep reasonable prices, propose corrections only with clear evidence,
and explain corrections in its Executive Summary. This report is not an
instruction to reopen the Research Manager's direction or thesis. The original
Research/Trader/Risk synthesis determines the final rating.

The existing `TraderProposal.entry_price` and `stop_loss` are reused. The PM's
`PortfolioDecision` adds optional numeric fields of the same names; they mean
**corrections only**, defaulting to null. Unchanged values and corrections without
a Market report are normalized to null. Missing numbers are not forced.

Structured numbers are preserved directly. Free-text fallback accepts only
explicit, unique English price labels. Trader labels are `Entry Price` and
`Stop Loss`; PM labels are `Entry Price Adjustment` and `Stop Loss Adjustment`.
Numbers in general prose, percentages, ambiguous duplicate labels, nonfinite
or nonpositive numbers are not guessed into prices. Thus some fallback reports
may have narrative prices and null machine-readable fields.

## Successful-run JSON

After the graph ends with a nonempty final decision, both CLI and Python paths
write `<results_dir>/runs/<run_id>.json`. The UUID distinguishes repeated and
concurrent runs of the same ticker/date. Records use UTF-8 and `schema_version=1`.
Publication uses a temporary file followed by an atomic no-overwrite hard link
(supported on NTFS and standard local POSIX filesystems); incomplete temporary
files are removed. Existing date-named JSON files are left untouched, and new
runs no longer overwrite them.

The record contains:

- Run ID, status `completed`, timezone-aware timestamps and resumed flag.
- Ticker/date/asset identity, analyst reports, full research/risk debate state,
  research plan, Trader proposal, PM decision and five-tier rating or REVIEW.
- Recorded model/vendor/analyst settings and analysis limits. Credentials and
  authenticated endpoint settings are excluded.
- Supplied portfolio (null when absent), rendered portfolio and actual past
  context seen by the run, plus Memory availability notes.
- `prices.trader_original`, `prices.pm_adjustments` and `prices.effective_reference`.
  Each effective field takes a non-null PM correction; otherwise it retains the
  Trader field, including null. These are reference levels, not executed orders.
- Available callback statistics for calls/tools/input and output tokens.

`api_usage.status` distinguishes complete metadata, partial metadata and no
usage metadata. Missing token reporting is null, not a claim of zero cost.
The counters cover this invocation, including any Memory settlement. Non-LangChain
requests (data APIs or Jev screening), charges, account limits and previous
failed attempts' usage are not measured. A resumed run can therefore have
reports from its prior attempt but usage only from the successful invocation.

A REVIEW with a nonempty final decision is a completed analysis requiring human
review, not a manufactured Hold. An empty final decision is incomplete and is
not archived. Mid-run failure or cancellation does not call the successful
writer. A disk/archive failure is reported as “analysis completed, but saving
its JSON failed”; it does not produce an error JSON. Already published successful
records are not removed if a later Memory/checkpoint operation fails.

Known credential values and authenticated URLs echoed in archived text are
redacted. Never put actual keys in tracked files. Use local environment
variables or an ignored `.env` file, and do not paste keys into report text.

Markdown/HTML reports and original CLI progress logs remain separate. Choosing
`--no-save` disables the optional final report tree, not the successful JSON.
Original progress Markdown/log files or SQLite checkpoints can remain after an
interruption; phase 1 does not turn them into failed-analysis JSON records.

Memory retains its original first-decision-per-ticker/date policy. Re-running the
same date can create several successful JSONs while the Memory entry remains
the first decision. Use the successful JSON archive for run-by-run comparisons.

## Reflection

Reflection still receives the decision, raw/alpha return, benchmark and measured
window. It can explicitly state that no reliable lesson can be drawn. It must
not infer causes from price movement alone, invent missing events, generalize
one result into a universal rule, or judge a long thesis from an inadequate
window. The prompt is a constraint on interpretation, not a proof that all LLM
hallucination has been eliminated. Existing stored lessons are not rewritten.

## Verification without API costs

Install the declared development dependencies and run `pytest -q` and
`ruff check .`. The default test selection excludes external-service integration
tests; existing fixtures block network access and use scripted models/test data.
Use isolated results/cache/Memory paths for manual experiments. Do not run
`main.py`, live analysis, settlement or external integration tests as part of an
unapproved test run: they can invoke a paid model.

For simultaneous analyses use a separate `TradingAgentsGraph` instance per run,
as its checkpointer and invocation metadata are instance state.

## Paid validation remains a separate step

No paid live analysis is part of the automated checks. Before an agent runs one,
obtain explicit user approval for the provider/model, ticker, number of runs and
budget. Configure the key locally; never include its value in a command/report.

For a minimal original-versus-branch comparison, prepare one original run and
one branch run using the same model/vendor, one research/risk round, the same
selected analysts and separate empty Memory files. Empty experiment Memory avoids
unexpected additional reflection calls. Record actual execution times because
live prices and news can change between runs. The user starts analyses directly;
review source prices, the PM's limited price review and JSON provenance before
deciding whether candle/quote handling needs a later change. Exact agreement of
LLM investment ratings is not a regression criterion.
