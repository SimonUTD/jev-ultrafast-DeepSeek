# Decision model latency: Jev vs DeepSeek deepseek-flash

The decision step (one operation + every operation-specific target head, one request) can run on
TypeSafe Jev or on DeepSeek's `deepseek-flash` (DeepSeek-V4.1-Flash) with thinking disabled. All
three legs below were measured live **from the same machine and network, legs interleaved within
every round**, on 2026-09-21 13:14 UTC — including Jev, so network conditions hit every leg
equally. Raw per-call evidence: [benchmark-models.json](benchmark-models.json).

| Leg | Backend | Output enforcement |
| --- | --- | --- |
| `jev` | TypeSafe `systemone`, `jev-1.13.0` | provider-native choice heads |
| `ds-prompt` | `deepseek-flash`, thinking off | system prompt only |
| `ds-json` | `deepseek-flash`, thinking off | `response_format` json mode |

## Results (54 measured calls per leg: 6 states × 8 rounds, after 1 warmup round)

| Leg | Median | Mean | p95 | Min | Max | Valid | In/Out tokens (median) | Cache hit | Calls with raw format issues |
| --- | ---: | ---: | ---: | ---: | ---: | --- | --- | ---: | ---: |
| `jev` | **296 ms** | 419 ms | 895 ms | 250 | 1,303 | 54/54 | 3,256 / 206 | — | 0 |
| `ds-prompt` | 1,213 ms | 1,235 ms | 1,846 ms | 772 | 2,700 | 54/54 | 2,874 / 240 | 92.5% | 16 |
| `ds-json` | 1,224 ms | 1,394 ms | 2,378 ms | 959 | 2,644 | 54/54 | 2,896 / 242 | 93.3% | 17 |

**Jev is ~4× faster on this network** (296 ms vs ~1.2 s median). It is a small purpose-built
decision model whose heads natively satisfy the choice contract; `deepseek-flash` is a general
1M-context model writing the same answer as text. Note the network factor: the repository's own
recorded Jev evidence ([flights-measurement.json](flights-measurement.json), real task, different
network) shows a 178 ms median — measuring all legs together, as here, is the only fair basis.

**Prompt-enforced vs json mode: no meaningful latency difference.** Medians are 1 ms-level apart
(1,213 vs 1,224 ms); across the four runs on 2026-09-21 the two modes traded places repeatedly.
This run's tail was heavier for json mode (p95 2,378 vs 1,846 ms), earlier runs the opposite.
Json mode buys parse reliability, not speed.

## Recorded output-format failures (the text model's raw answers)

A text model does not guarantee the choice contract the way Jev's heads do. Every call records
what the raw answer got wrong, even when conditioning repaired it. Counts below cover all calls
of the final run (warmup + measured); the same categories appear on both DeepSeek legs:

| Failure category | `ds-prompt` | `ds-json` | Meaning / handling |
| --- | ---: | ---: | --- |
| `probabilities_sum` | 8 | 7 | probabilities sum ≠ 1 within ±0.02 (e.g. 0.95) — renormalized |
| `missing_candidates` | 6 | 6 | probabilities omitted some offered candidates — filled with 0 |
| `choice_not_offered` | 6 | 12 | answer invented a candidate id — **not repairable**: one retry, then the run stops |
| `choice_not_max` | 2 | 2 | selected choice was not the probability maximum — realigned |
| `unknown_candidates` | 0 | 1 | probabilities contained ids outside the offered set — dropped |
| `unparsable` | 0 | 0 | content was not a json object (json mode can return empty content) — one retry |
| `contract_rejected` | 0 | 0 | validation still rejected after retry — would stop the run |

All 108 DeepSeek calls ended valid (54/54 per leg): the 18 `choice_not_offered` occurrences were
recovered by the single retry; everything else was repaired by conditioning before the shared
validation. Jev returned contract-valid answers on all 54 calls with no conditioning.

## How to reproduce

```bash
uv run --env-file .env python scripts/benchmark_models.py --legs jev,ds-prompt,ds-json --rounds 8
```

Legs without their API key (`TYPESAFE_API_KEY` / `DEEPSEEK_API_KEY`) are skipped with a notice.
The evidence JSON records per-call latency, usage, decisions, retries, and every raw format
failure; no credentials are written. Decision sanity across legs: `results` chose DONE on every
call; suggestion/search/date states chose CLICK (a few TYPE_TEXT first moves on the DeepSeek
legs, also valid).

Environment: 2026-09-21 13:14 UTC (off-peak), direct `https` to `api.typesafe.ai` and
`api.deepseek.com`, httpx HTTP/2, macOS arm64, ~2.9–3.3K prompt tokens per call on identical
fixed states. Total live-comparison cost today: about $0.10 of DeepSeek credit.

## End-to-end check (real browser, full loop)

After the latency runs, all three configurations completed the repo's live smoke check
(`uv run python scripts/smoke.py`, not pytest): a real Chrome tab, the local travel fixture,
dynamic decisions, TYPE_TEXT text generated live by `deepseek-flash`, and independent outcome
verification (final URL `#casa-flora`, "Design · Free cancellation enabled · Destination Lisbon"
visible). Chrome connected through a dedicated automation instance
(`BU_CDP_URL=http://127.0.0.1:9333 --user-data-dir=<dedicated>`), which sidesteps the macOS
permission block on reading the default profile's `DevToolsActivePort` and Chrome 147+'s
default-profile CDP lockdown.

| Configuration | Verified | Total time | Decisions / actions |
| --- | --- | ---: | --- |
| `jev` decisions + `deepseek-flash` text | ✓ | 3,819 ms | 6 / 5 |
| `ds-prompt` decisions + `deepseek-flash` text | ✓ | 7,997 ms | 6 / 5 |
| `ds-json` decisions + `deepseek-flash` text | ✓ | 9,426 ms | 6 / 5 |

Traces: `artifacts/dynamic/fixture/*` (gitignored). Task times track the decision-step
latencies above. The two DeepSeek legs chose filters before submitting the search (a valid
order); all legs ended DONE with the verified outcome.

## Real-task comparison (Google Flights, three legs, interleaved)

Same machine, network, and proxy as above; every leg ran the identical live task three times,
legs interleaved within each round. The task date is **2026-11-20**: the repository's original
demo date (2026-09-20) has passed, and a past date is unreachable in the date picker — an
earlier attempt with it blocked every leg until the date was moved forward. Every run is
independently verified (route, one-way, date, visible results); a DONE choice alone counts as
failure. Evidence: [flights-compare.json](flights-compare.json); raw traces under
`artifacts/flights-compare/` (gitignored). The proxy's exit IP serves a UK Flights variant
(multi-airport panels, priced date grid), harder than the recorded US demo — all legs face it
equally.

| Leg | Verified | Task time (median, verified runs) | Decision latency (median) | Decisions/run (median) | Text latency (median) |
| --- | --- | ---: | ---: | ---: | ---: |
| `jev` | 2/3 | **15,098 ms** | 310 ms | 22 | 800 ms |
| `ds-prompt` | 2/3 | 64,766 ms | 1,163 ms | 43 | 667 ms |
| `ds-json` | 2/3 | 39,178 ms | 1,155 ms | 28 | 574 ms |

**Jev finished the real task ~2.6–4.3× faster** with roughly half the decisions per run; the
DeepSeek legs' extra decisions (stale re-predicts, wander, premature DONE attempts) dominate
the gap beyond raw decision latency (~3.7×). Reliability was 2/3 for every leg on this UI
variant — the three failures differ in kind:

- `jev` r1: claimed DONE on a page missing one-way/origin/date — a decision error, no format
  issue (Jev's answers never needed conditioning).
- `ds-prompt` r1: the **text helper** (json mode empty-content caveat) returned invalid output
  three times in a row at the first field; a later identical probe returned valid output 3/3,
  so it was transient. The failure now records the last raw output in its error message.
- `ds-json` r3: premature DONE before results loaded (decision error).

Recorded DeepSeek format failures across the six runs: `missing_candidates` 99 (repaired by
conditioning on essentially every decision — large pages rarely emit a full probability
vector), `probabilities_sum` 14, `choice_not_max` 5, `unknown_candidates` 1, and
`choice_not_offered` 12 — all twelve recovered by retry. Before the reliability hardening
below, the DeepSeek legs failed 5/6 runs: a text model on a 70+-element page slips an
off-head element id (e.g. a global index into the wrong question's candidate set) about half
the time, and the then-budget of one retry could not absorb two consecutive slips. The fix —
an explicit "only that question's own ids" rule in the system prompt plus a two-retry budget,
every attempt recorded — took the same matrix to 2/3 per leg. Retry and conditioning counts
are per decision in the raw traces.

## Limits

Fixed synthetic Flights-like states, one machine, one hour, one task family. This compares
decision-step latency and output-format reliability, not end-to-end agent success; DeepSeek-leg
decision quality on real pages was not independently verified end to end. Jev latency on other
networks will differ (178 ms median in the repository's own recording).
