<img src="docs/banner.svg" alt="Jev Ultrafast · Browser Use × TypeSafe" width="100%" />

# Jev Ultrafast ⚡

> [!IMPORTANT]
> **This fork adds DeepSeek as the decision backend.** Set `DECISION_PROVIDER=deepseek` and `deepseek-flash` (DeepSeek-V4.1-Flash, thinking disabled) answers the operation + target questions in one request — enforced by system prompt or by json mode (`DEEPSEEK_OUTPUT=prompt|json`). The TYPE_TEXT helper also defaults to `deepseek-flash`.

**Same agent, three decision backends, same machine and network.** Decision-step latency: 54 replayed calls per leg. Real task: Google Flights, 3 interleaved runs per leg, independently verified results (route / one-way / date / visible flights; DONE alone is not success).

| Decision backend | Decision latency (median) | Real-task time (median) | Decisions/run | Verified |
| --- | ---: | ---: | ---: | ---: |
| Jev (`jev-1.13.0`) | **296 ms** | **15.1 s** | 22 | 2/3 |
| `deepseek-flash` + prompt | 1,213 ms | 64.8 s | 43 | 2/3 |
| `deepseek-flash` + json mode | 1,224 ms | 39.2 s | 28 | 2/3 |

Jev is ~4× faster per decision and 2.6–4.3× faster end to end; prompt vs json mode is a wash. A general LLM also breaks the choice contract on its own: probability sums drift off 1, candidates go missing, and on 70+-element pages it slips an off-head element id about half the time — every such raw failure is recorded per call, conditioned or retried, and an invented candidate is never silently accepted. Full evidence and failure taxonomy: [benchmark-models.md](docs/benchmark-models.md) · reproduce: `uv run --env-file .env python scripts/benchmark_models.py` and `BU_CDP_URL=http://127.0.0.1:9333 uv run python scripts/compare_flights.py --rounds 3`.

> [!NOTE]
> **The Browser Use Cloud waitlist is open.** Get early access to ultrafast browser agents in the cloud.
> **[Join the waitlist →](https://browser-use.com/ultrafast?utm_source=github&utm_medium=readme&utm_campaign=jev-ultrafast)**

**A browser agent with a dynamic, indexed action space.**

Give it one goal. [TypeSafe's Jev](https://docs.typesafe.ai/introduction) picks an operation and an element. A small LLM writes text only when the operation is `TYPE_TEXT`.

**Zürich → London on Google Flights in 7.1 seconds.** One natural-language goal, actual text generation, and loading waits included.

<a href="docs/demo.mp4"><img src="docs/demo.gif" alt="A real Google Flights search at 1× speed, with generated city names and dynamic operation/target decisions" width="100%" /></a>

[Watch the MP4](docs/demo.mp4) · [Measurements](docs/performance.md) · [Read the loop](jev_ultrafast/agent.py)

## The action space

Every observation produces a new element table:

```text
[1] button    Change ticket type · Round trip
[2] combobox  Where from?        · San Francisco
[3] combobox  Where to?          · empty
[4] textbox   Departure          · empty
...
```

The operations are `CLICK`, `TYPE_TEXT`, `SELECT`, `SCROLL_UP`, `SCROLL_DOWN`, `WAIT`, `DONE`, and `BLOCKED`. Only supported operations and targets are offered.

```text
                      one TypeSafe request
                     ┌───────────────────────────┐
page → element table → operation                 │
                     │ click_target              │
                     │ type_text_target          │
                     │ select_target, if present │
                     └─────────────┬─────────────┘
                         use the matching target
                                   │
                    CLICK [7] ─────┤──→ browser
                TYPE_TEXT [3] ─────┘
                          ↓
                   small LLM → text → browser
```

Target questions are speculative. If the operation is `CLICK`, only `click_target` can execute. Two decisions, **one network round trip**. Each target head contains only compatible elements. Native dropdown choices carry an observed element/option index.

There are no site-specific action scripts or prepared field strings in the policy. The Flights example supplies a goal and independently verifies the outcome. The screenshot renderer adds labels afterward; it does not drive the browser.

## Try it

```bash
git clone https://github.com/browser-use/jev-ultrafast.git
cd jev-ultrafast
uv sync
cp .env.example .env
# Add TYPESAFE_API_KEY; DEEPSEEK_API_KEY powers the deepseek provider and the text helper.
uv run jev
```

Open **http://127.0.0.1:8766** and click **Start demo → Run automatically**. The inspector shows numbered elements, operation probabilities, target probabilities, and executed actions. **Choose next** pauses before execution.

Chrome connects through [Browser Harness](https://github.com/browser-use/browser-harness), installed by `uv sync`. Run `uv run browser-harness --doctor` if it needs connecting. Allow remote debugging in Chrome when prompted.

The text helper defaults to DeepSeek's `deepseek-flash` with thinking disabled — `DEEPSEEK_API_KEY` powers it unless `TEXT_MODEL_API_KEY` is set. Any OpenAI-compatible endpoint works: the recorded demo video used OpenRouter's `inception/mercury-2.5` with reasoning disabled, and Gemini or GLM need only the model, endpoint, and reasoning setting.

## Decision model: Jev or DeepSeek

The decision step also runs on DeepSeek's official `deepseek-flash` (DeepSeek-V4.1-Flash) with thinking disabled, in one `.env` line:

```bash
DECISION_PROVIDER=deepseek   # default: typesafe
DEEPSEEK_API_KEY=sk-...
DEEPSEEK_OUTPUT=prompt       # prompt | json (response_format json mode)
```

The contract is unchanged: one request answers the operation question and every operation-specific target question, and every choice must map to an observed element. DeepSeek answers through a System One decision prompt; text-model probabilities are conditioned (missing candidates filled, renormalized, maximum aligned with the selection) before the same validation Jev output passes, and **every raw output-format failure is recorded** (probability sums drifting off 1, missing candidates, invented ids — the last retried once, then the run stops). Use `DEEPSEEK_OUTPUT=json` for json-mode enforcement instead of prompt discipline.

Measured on the decision step, all legs from the same machine and network: **Jev is ~4× faster (296 ms vs ~1.2 s median)**; prompt vs json mode on `deepseek-flash` is a wash (1,213 vs 1,224 ms median). On the **real Google Flights task** (three interleaved runs per leg, independently verified): **Jev finished in ~15 s vs ~39–65 s for DeepSeek**, with roughly half the decisions per run; each leg verified 2/3 on this network's Flights UI variant. Format-failure counts, validity, tokens, and limits are in [benchmark-models.md](docs/benchmark-models.md); reproduce the decision-step benchmark with `uv run --env-file .env python scripts/benchmark_models.py` and the real-task comparison with `scripts/dev_chrome.sh <proxy-port>` plus `BU_CDP_URL=http://127.0.0.1:9333 uv run python scripts/compare_flights.py --rounds 3`.

## Use the library

```python
from jev_ultrafast import Agent

with Agent(
    "https://www.google.com/travel/flights?hl=en",
    "Find one-way flights from Zurich to London on September 20, 2026, "
    "for one adult in economy. Stop when matching flight options are visible.",
) as agent:
    for state in agent.run():
        print(state["elapsed_ms"], state["status"])
```

Run with `uv run --env-file .env python your_script.py`. The same policy can run a different task:

```bash
uv run --env-file .env python examples/run.py \
  --url https://en.wikipedia.org/wiki/Main_Page \
  --goal 'Find and open the Wikipedia article about Gödel’s incompleteness theorems.'
```

`uv run --env-file .env python examples/flights.py --keep-open` performs the flight search, checks the actual route/date/results, and saves its trace. It does not select or book a flight.

## Why it moves

- **One request per decision cycle.** Operation and target heads share the same observed state.
- **No screenshots in the default agent loop.** Jev consumes structured state. The inspector opts into screenshots; the video uses a separate continuous screencast.
- **One browser call per snapshot.** Read visible controls, their names, values, and text atomically. Keep references to the actual DOM nodes.
- **Validate the selected target.** Clicks check the document, form values, target, and nearby context. Animation alone does not force another prediction. Resolve current geometry and reject covered controls before input.
- **Wait for useful state.** After typing into a combobox, wait for visible suggestions, capped at 200 ms. Other interactions get at most two animation frames or 50 ms. These reads happen after execution is logged.
- **Keep hidden tabs rendering.** Focus emulation prevents background animation throttling without switching Chrome's visible tab.
- **Send visible text.** Offscreen article bodies and footers do not fill the model context.
- **Reuse an interrupted text request.** A generated value survives a stale-page retry only if the entire text-helper input is unchanged.

Every executed target is resolved from an observed node. The executor rechecks page freshness and click occlusion. Model output never becomes selectors, coordinates, shell commands, or executable JavaScript. Text-helper output must parse as a small JSON object before typing.

## Small enough to read

| File | Job |
| --- | --- |
| [agent.py](jev_ultrafast/agent.py) | The complete loop and text-helper handoff |
| [snapshot.js](jev_ultrafast/snapshot.js) | Atomic DOM snapshot, indexed controls, freshness guards |
| [browser.py](jev_ultrafast/browser.py) | Browser connection, current geometry, execution |
| [model.py](jev_ultrafast/model.py) | Decision request (Jev or DeepSeek) and text generation |
| [questions.py](jev_ultrafast/questions.py) | Model instructions |
| [demo.py](jev_ultrafast/demo.py) | Local inspector |

## Evidence and limits

The current video is a **7,073 ms** Google Flights run. Timing starts after initial page observation and includes model calls, generated text, browser work, stale decisions, and loading waits. A fresh independent check verifies the one-way setting, Zürich, London, September 20, 2026, and visible flight options. The video plays at 1×, with no opening hold and a 0.5-second final hold.

In six alternating runs with identical models and settings, both versions passed **3/3**. Median task time went from **9.450 s → 7.092 s**, a **25% reduction**; median browser protocol calls went from **1,092 → 101**. This is three repeats of one task on one browser profile, not a general reliability benchmark.

The same policy opened the requested Wikipedia article in **2.798 s** and passed a local hotel search/filter task in **1.896 s**. Runs, failures, source hashes, and measurement boundaries are in [performance.md](docs/performance.md).

A `DONE` choice still requires independent outcome verification. The DOM reader handles common HTML and ARIA controls, not the full accessible-name specification. Shadow roots, frames, canvas, uploads, pop-up tabs, nested scrolling, and arbitrary keyboard widgets remain outside this MVP. Owned tabs share the existing Chrome profile.

## Development

```bash
uv run ruff check .
uv run pytest
node --check jev_ultrafast/static/app.js
node --check jev_ultrafast/snapshot.js
uv build
```

Tests are offline. `uv run python scripts/check_guards.py` checks real controls in a local browser without model calls. Live examples and recording scripts make paid API calls; `scripts/benchmark_models.py` compares decision-model latency (Jev vs `deepseek-flash`, prompt vs json mode) and writes its raw evidence to `docs/benchmark-models.json`. `scripts/record_flights.py <new-folder>` captures original browser timestamps; `scripts/render_demo.py <recording-folder>` renders that verified run at 1× and crops out the Google account strip. Credentials and raw traces stay ignored.

On macOS, run `scripts/dev_chrome.sh [proxy-port]` first: it launches the dedicated automation Chrome the harness needs (the default profile's CDP is permission-blocked) and, given a local proxy port, routes Google through it plus skips the consent redirect for the Flights scenario. Then `BU_CDP_URL=http://127.0.0.1:9333 uv run jev`.

---

[Browser Use](https://github.com/browser-use/browser-use) · [Browser Harness](https://github.com/browser-use/browser-harness) · [TypeSafe speculative fan-out](https://docs.typesafe.ai/patterns/fan-out)
