# Data readiness without an LLM analysis

From the project directory, run:

```powershell
python -m tradingagents.dataflows.preflight --ticker NVDA
```

The command uses the configured vendor chains, with no LLM inference, social
post screening, Memory settlement, trading decision or analysis JSON. It checks
prices, the original Yahoo verification snapshot, an RSI tool read, fundamentals,
statements, insider transactions, news, social feeds, three macro series and the
prediction-market feed. Network reads and normal vendor cache writes do occur.
`--date YYYY-MM-DD` and `--cache-dir PATH` are optional. Use the same cache path
as the later analysis if you want the snapshot to warm that cache.

The JSON printed to the terminal contains statuses rather than full reports.
`core_ready=true` and exit code 0 mean usable core prices were found. Optional
feeds may still be unavailable: inspect every status before choosing to spend on
an analysis. Exit code 1 means core prices are unavailable. This is a snapshot
of current readiness, not a promise that subsequent calls will succeed.

FRED reads the local `FRED_API_KEY` variable. Place it in the Git-ignored local
`.env`, preserving other settings. Never commit the file. A new Python process
loads it automatically; an already exported variable takes precedence. The
preflight rejects missing/malformed FRED credentials without transmitting them.
Use your own free key from https://fred.stlouisfed.org/docs/api/api_key.html .

The original aliases are retained:

| Alias | Series | Meaning |
|---|---|---|
| fed_funds_rate | FEDFUNDS | Monthly effective federal funds rate, percent |
| 10y_treasury | DGS10 | Daily 10-year Treasury constant maturity yield, percent |
| cpi | CPIAUCSL | Monthly seasonally adjusted CPI index, not a year-on-year rate |

The existing FRED implementation pins the data vintage to the analysis date
(clamped to the current date in Chicago), omits missing `.` observations and
reports the observation date, units and frequency. FEDFUNDS is not the FOMC
target range. CPI's displayed window change is not automatically a precisely
aligned year-on-year inflation rate. Consult the actual dates before comparing.
For example, a 365-day request dated 2026-10-17 returned a CPI change from
2025-11-01 to 2026-09-01 of +3.46%; that is not September-on-September inflation.
The existing FRED report still labels this `Change from ...`, with no explicit
YoY warning. A proposed follow-up is a CPI-specific clarification in the data
report, preserving all calculations, vintage bounds and analyst prompts. That
change is not part of this readiness command.

Yahoo indicators and the verification snapshot use the original OHLCV loader.
Concurrent indicator calls on a missing cache can each download the same
history. The explicit preflight checks sequentially and warms the cache without
altering indicator formulas, session policy, TTL or analyst prompts. A later TTL
expiry, new process/cache directory or upstream outage can still cause failures.
The snapshot can supply common latest indicators after an individual tool
failure, but shares Yahoo and the OHLCV loader and is not an independent backup.

Polymarket's public Gamma API requires no API key. HTTP 451 is an access
restriction, not missing credentials. Do not route around a restriction. Treat
prediction probabilities as unavailable. FRED observations and existing news can
still provide context, but are not substitutes for implied future probabilities.
No new provider or alternative model is enabled by this command.

Alpha Vantage is an alternative vendor requiring `ALPHA_VANTAGE_API_KEY`; it is
only called if explicitly in the configured chain. TypeSafe/Jev post screening
may incur LLM charges and is never invoked by this check, even if its key exists.
The configured analysis continues to use its original settings.
