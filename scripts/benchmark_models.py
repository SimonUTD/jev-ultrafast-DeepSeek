"""Latency comparison for the decision step: Jev vs deepseek-flash (prompt vs json mode).

Replays the same fixed states through every configured backend, interleaving backends within
each round so network and provider load hit all legs equally. Round 0 is a warmup (DeepSeek's
context cache fills; excluded from statistics). Live paid calls: each leg needs its API key,
and legs without one are skipped with a notice. Raw per-call evidence is written to
docs/benchmark-models.json; no credentials are recorded.

Usage:
  uv run --env-file .env python scripts/benchmark_models.py --rounds 5
"""

import argparse
import json
import os
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from jev_ultrafast import model  # noqa: E402

GOAL = (
    "Find one-way flights from Zurich to London on September 20, 2026, for one adult in "
    "economy. Stop when matching flight options are visible. Do not select or book a flight."
)

LEGS = {
    "jev": {"DECISION_PROVIDER": "typesafe"},
    "ds-prompt": {"DECISION_PROVIDER": "deepseek", "DEEPSEEK_OUTPUT": "prompt"},
    "ds-json": {"DECISION_PROVIDER": "deepseek", "DEEPSEEK_OUTPUT": "json"},
}
KEYS = {"jev": "TYPESAFE_API_KEY", "ds-prompt": "DEEPSEEK_API_KEY", "ds-json": "DEEPSEEK_API_KEY"}


def load_environment():
    path = Path.cwd() / ".env"
    if path.exists():
        for line in path.read_text().splitlines():
            if "=" in line and not line.startswith("#"):
                key, value = line.split("=", 1)
                os.environ.setdefault(key, value)


def n():
    """Next unique DOM node id."""
    n.counter += 1
    return n.counter * 10


n.counter = 0


def combobox(label, value="", suggestions=None):
    actions = [
        {"id": f"f{n()}", "kind": "fill", "label": label, "role": "combobox", "value": value, "node": n()},
        {"id": f"c{n()}", "kind": "click", "label": f"Open {label}", "role": "combobox", "value": value, "node": None},
    ]
    actions[1]["node"] = actions[0]["node"]
    if suggestions:
        for text in suggestions:
            actions.append(
                {"id": f"s{n()}", "kind": "click", "label": text, "role": "menuitem", "value": text, "node": n()}
            )
    return actions


def native_select(label, current, options):
    node = n()
    actions = [
        {"id": f"o{n()}", "kind": "select", "label": f"{label} → {value}", "role": "combobox",
         "value": value, "current_value": current, "node": node}
        for value in options
    ]
    return actions


def button(label, role="button", **extra):
    return {"id": f"b{n()}", "kind": "click", "label": label, "role": role, "value": "", "node": n(), **extra}


def link(label):
    return button(label, role="link")


def checkbox(label, checked="false"):
    return button(label, role="checkbox", checked=checked)


def page(state_id, url, title, text, actions, history):
    return {"id": state_id, "url": url, "title": title, "text": text, "actions": actions, "history": history}


def states():
    home_text = (
        "Google Flights . Flights . Hotels . Shortlist . Tracked flights . Sign in . Round trip . "
        "One way . Multi-city . Where from? . Where to? . Departure . Return . Add destination . "
        "Add passengers and cabins . 1 economy passenger . Search . Explore destinations . "
        "Popular flights from Zurich . London . Paris . Barcelona . Amsterdam . Departures rising in price . "
        "Price insights and tracking . Best time to book . Cheapest . Best . Fastest . Other airlines . "
        "Carbon emissions estimates . Help . Privacy . Terms"
    )
    s1_actions = [
        button("Change ticket type. Round trip"),
        *combobox("Where from?"),
        *combobox("Where to?"),
        button("Departure", role="textbox"),
        button("Return", role="textbox"),
        *native_select("Passengers", "1 economy passenger", [
            "1 economy passenger", "2 economy passengers", "1 business passenger", "2 business passengers"]),
        button("Search"),
        link("Explore destinations"),
        link("Popular flights from Zurich"),
        link("London"),
        link("Shortlist"),
    ]
    h1 = []
    s1 = page("home", "https://www.google.com/travel/flights?hl=en", "Google Flights", home_text, s1_actions, h1)

    origin_text = (
        "Enter origin or airport . Zurich Airport (ZRH) . Zurich, Switzerland . Zurich Hauptbahnhof . "
        "Zurich Kreis 4 . Recent searches . Zurich to London . Zurich to Barcelona . Zurich Airport (ZRH), "
        "Switzerland . Nearby airports . Friedrichshafen . Basel (BSL) . Mulhouse (MLH) . Close . "
        "Where from? Zurich Airport (ZRH) . Where to? . Departure . Search"
    )
    s2_actions = [
        button("Change ticket type. Round trip"),
        *combobox("Where from?", "Zurich Airport (ZRH)", [
            "Zurich Airport (ZRH), Switzerland",
            "Zurich Hauptbahnhof (ZRH), Switzerland",
            "Zurich Kreis 4, Switzerland",
            "Friedrichshafen (FDH), Germany",
        ]),
        *combobox("Where to?"),
        button("Departure", role="textbox"),
        *native_select("Passengers", "1 economy passenger", ["1 economy passenger", "2 economy passengers"]),
        button("Search"),
        link("Explore destinations"),
    ]
    h2 = [{"action": "Where from?", "kind": "fill", "text": "Zurich", "page_changed": True}]
    s2 = page("origin-suggest", "https://www.google.com/travel/flights?hl=en&origin=zh", "Google Flights",
              origin_text, s2_actions, h2)

    dest_text = (
        "Enter destination or airport . London (LON) . All London airports . London Heathrow (LHR) . "
        "London Gatwick (LGW) . London City (LCW) . London Stansted (STN) . London Luton (LTN) . "
        "London Southend (SEN) . Where from? Zurich Airport (ZRH) . Where to? . Departure . Search"
    )
    s3_actions = [
        button("Change ticket type. Round trip"),
        *combobox("Where from?", "Zurich Airport (ZRH)"),
        *combobox("Where to?", "", [
            "All London airports (LON)",
            "London Heathrow (LHR)",
            "London Gatwick (LGW)",
            "London City (LCW)",
            "London Stansted (STN)",
            "London Luton (LTN)",
        ]),
        button("Departure", role="textbox"),
        button("Search"),
        link("Explore destinations"),
    ]
    h3 = h2 + [
        {"action": "Zurich Airport (ZRH), Switzerland", "kind": "click", "text": None, "page_changed": True},
        {"action": "Where to?", "kind": "fill", "text": "London", "page_changed": True},
    ]
    s3 = page("dest-suggest", "https://www.google.com/travel/flights?hl=en&dest=lon", "Google Flights",
              dest_text, s3_actions, h3)

    calendar_text = (
        "Departure . September 2026 . Su Mo Tu We Th Fr Sa . 1 2 3 4 5 . Sun, Sep 20 . 6 7 8 9 10 11 12 . "
        "13 14 15 16 17 18 19 . 20 21 22 23 24 25 26 . 27 28 29 30 . October 2026 . Previous month . "
        "Next month . Done . Where from? Zurich Airport (ZRH) . Where to? All London airports . Search"
    )
    s4_actions = [
        button("Change ticket type. Round trip"),
        *combobox("Where from?", "Zurich Airport (ZRH)"),
        *combobox("Where to?", "All London airports (LON)"),
        button("Previous month"),
        button("Next month"),
        *[button(f"Sun, Sep {d}") for d in (6, 13, 20, 27)],
        *[button(f"Mon, Sep {d}") for d in (7, 14, 21, 28)],
        button("Done"),
        button("Search"),
    ]
    h4 = h3 + [{"action": "All London airports (LON)", "kind": "click", "text": None, "page_changed": True},
               {"action": "Departure", "kind": "click", "text": None, "page_changed": True}]
    s4 = page("calendar", "https://www.google.com/travel/flights?hl=en&calendar=1", "Google Flights · dates",
              calendar_text, s4_actions, h4)

    ready_text = (
        "Google Flights . One way . Zurich Airport (ZRH) . All London airports (LON) . Sun, Sep 20 . "
        "1 economy passenger . Search . Filters . Stops . Price . Airlines . Times . Emissions . "
        "Sort by: Best . Cheapest . Fastest . Connecting airports . Help . Privacy . Terms"
    )
    s5_actions = [
        button("Change ticket type. One way"),
        *combobox("Where from?", "Zurich Airport (ZRH)"),
        *combobox("Where to?", "All London airports (LON)"),
        button("Departure", role="textbox", value="Sun, Sep 20"),
        *native_select("Passengers", "1 economy passenger", ["1 economy passenger", "2 economy passengers"]),
        button("Search"),
        checkbox("Stops"),
        checkbox("Price"),
        link("Shortlist"),
    ]
    h5 = h4 + [{"action": "Sun, Sep 20", "kind": "click", "text": None, "page_changed": True},
               {"action": "Done", "kind": "click", "text": None, "page_changed": True}]
    s5 = page("form-ready", "https://www.google.com/travel/flights?hl=en&ready=1", "Google Flights",
              ready_text, s5_actions, h5)

    results_text = (
        "Flights from Zurich to London on Sun, Sep 20 . One way . 1 economy passenger . Sort by: Best . "
        "Filters . Stops . Nonstop . 1 stop or fewer . Price . Any price . Airlines . Swiss . British Airways . "
        "easyJet . Times . Emissions . Swiss 348 . Nonstop . ZRH 07:00 → LGW 07:55 . 1h 55m . CHF 96 . "
        "British Airways 717 . Nonstop . ZRH 08:30 → LHR 09:20 . 1h 50m . CHF 112 . easyJet 55 . "
        "Nonstop . ZRH 13:10 → LTN 13:55 . 1h 45m . CHF 61 . Swiss 352 . Nonstop . ZRH 16:20 → LHR 17:10 . "
        "CHF 89 . Show more flights . Track prices . Help . Privacy . Terms"
    )
    s6_actions = [
        button("Change ticket type. One way"),
        *combobox("Where from?", "Zurich Airport (ZRH)"),
        *combobox("Where to?", "All London airports (LON)"),
        button("Departure", role="textbox", value="Sun, Sep 20"),
        checkbox("Nonstop", "false"),
        checkbox("1 stop or fewer", "false"),
        *native_select("Sort by", "Best", ["Best", "Cheapest", "Fastest"]),
        *[button(f"Select flight Swiss {num} · ZRH → LGW/LHR/LTN") for num in (348, 717, 55, 352)],
        button("Show more flights"),
        {"id": "scroll_down", "kind": "scroll", "label": "Scroll down", "delta": 560},
        link("Track prices"),
    ]
    h6 = h5 + [{"action": "Search", "kind": "click", "text": None, "page_changed": True}]
    s6 = page("results", "https://www.google.com/travel/flights/search?tfs=zh-lon-20260920",
              "Flights Zürich to London", results_text, s6_actions, h6)
    return [s1, s2, s3, s4, s5, s6]


def stats(latencies):
    ordered = sorted(latencies)

    def percentile(p):
        return ordered[min(len(ordered) - 1, round(p * (len(ordered) - 1)))]

    return {
        "n": len(latencies),
        "median_ms": round(statistics.median(latencies)),
        "mean_ms": round(statistics.fmean(latencies)),
        "min_ms": min(latencies),
        "p95_ms": percentile(0.95),
        "max_ms": max(latencies),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rounds", type=int, default=5, help="measured rounds per leg (after warmup)")
    parser.add_argument("--warmup", type=int, default=1, help="warmup rounds excluded from statistics")
    parser.add_argument("--legs", default="jev,ds-prompt,ds-json", help="comma-separated legs to run")
    parser.add_argument("--out", default="docs/benchmark-models.json", help="raw evidence file")
    args = parser.parse_args()

    load_environment()
    wanted = [leg for leg in args.legs.split(",") if leg]
    unknown = [leg for leg in wanted if leg not in LEGS]
    if unknown:
        raise SystemExit(f"Unknown legs: {unknown}")
    active = [leg for leg in wanted if os.environ.get(KEYS[leg])]
    skipped = [leg for leg in wanted if leg not in active]
    for leg in skipped:
        print(f"skipping {leg}: {KEYS[leg]} is not set")
    if not active:
        raise SystemExit("No leg has its API key; nothing to measure.")

    scenarios = states()
    rows = []
    for round_index in range(args.warmup + args.rounds):
        for leg in active:
            os.environ.update(LEGS[leg])
            for scenario in scenarios:
                conditioning, retry_reasons = [], []
                try:
                    decision = model.choose(scenario, GOAL, scenario["history"])
                    conditioning = decision.get("conditioning", {})
                    retry_reasons = decision.get("retry_reasons", [])
                    rows.append({
                        "leg": leg, "round": round_index, "measured": round_index >= args.warmup,
                        "state": scenario["id"], "valid": True, "error": None,
                        "operation": decision["operation"], "target": decision["target"],
                        "choice": decision["choice"], "confidence": decision["confidence"],
                        "model": decision["model"], "latency_ms": decision["latency_ms"],
                        "usage": decision["usage"],
                        "conditioning": conditioning, "retry_reasons": retry_reasons,
                    })
                except (ValueError, RuntimeError) as error:
                    # Raw-format evidence survives on the exception when validation rejects an answer.
                    conditioning = getattr(error, "conditioning", {})
                    retry_reasons = getattr(error, "retry_reasons", [])
                    rows.append({
                        "leg": leg, "round": round_index, "measured": round_index >= args.warmup,
                        "state": scenario["id"], "valid": False, "error": str(error),
                        "operation": None, "target": None, "choice": None, "confidence": None,
                        "model": None, "latency_ms": None, "usage": {},
                        "conditioning": conditioning, "retry_reasons": retry_reasons,
                    })
                row = rows[-1]
                issues = sorted({i.split(":")[0] for issues in conditioning.values() for i in issues})
                note = f"  format: {','.join(issues)}" if issues else ""
                note += f"  retried: {len(retry_reasons)}" if retry_reasons else ""
                note += f"  INVALID: {row['error']}" if not row["valid"] else ""
                latency = row["latency_ms"] if row["latency_ms"] is not None else "—"
                print(f"round {round_index} {leg:9s} {scenario['id']:24s} {latency:>6} ms{note}")

    summary = {}
    for leg in active:
        leg_rows = [r for r in rows if r["leg"] == leg]
        measured = [r for r in leg_rows if r["measured"] and r["valid"]]
        valid_all = [r for r in leg_rows if r["valid"]]
        usages = [r["usage"] for r in measured]

        def pick(key, alt):
            # TypeSafe reports input_tokens/output_tokens; OpenAI-compatible APIs prompt/completion.
            return [u.get(key, u.get(alt, 0)) or 0 for u in usages]

        prompt = pick("prompt_tokens", "input_tokens")
        completion = pick("completion_tokens", "output_tokens")
        hit = [u.get("prompt_cache_hit_tokens", 0) for u in usages]
        failure_counts = {}
        for r in leg_rows:
            for issues in r["conditioning"].values():
                for issue in issues:
                    category = issue.split(":")[0]
                    failure_counts[category] = failure_counts.get(category, 0) + 1
            for reason in r["retry_reasons"]:
                category = reason["reason"]
                failure_counts[category] = failure_counts.get(category, 0) + 1
            if not r["valid"]:
                failure_counts["contract_rejected"] = failure_counts.get("contract_rejected", 0) + 1
        leg_stats = stats([r["latency_ms"] for r in measured]) if measured else {"n": 0}
        summary[leg] = {
            **leg_stats,
            "valid_calls": len(valid_all),
            "total_calls": len(leg_rows),
            "calls_with_raw_format_issues": sum(1 for r in leg_rows if r["conditioning"] or r["retry_reasons"]),
            "raw_format_failure_counts": dict(sorted(failure_counts.items())),
            "median_prompt_tokens": round(statistics.median(prompt)) if prompt else None,
            "median_completion_tokens": round(statistics.median(completion)) if completion else None,
            "cache_hit_share": round(sum(hit) / sum(prompt), 3) if sum(prompt) else None,
        }

    print("\nleg        median   mean    p95    min    max   calls  in/out (median)  format-issues")
    for leg in active:
        s = summary[leg]
        if s["n"]:
            print(f"{leg:10s} {s['median_ms']:6} {s['mean_ms']:6} {s['p95_ms']:6} {s['min_ms']:6} "
                  f"{s['max_ms']:6} {s['valid_calls']:3}/{s['total_calls']:3}  "
                  f"{s['median_prompt_tokens']}/{s['median_completion_tokens']}  {s['calls_with_raw_format_issues']}")
        else:
            print(f"{leg:10s} no valid measured calls")
    if any(s["raw_format_failure_counts"] for s in summary.values()):
        print("\nraw format failures (all calls, per-category counts):")
        for leg in active:
            print(f"  {leg}: {summary[leg]['raw_format_failure_counts'] or '{}'}")

    evidence = {
        "date": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "goal": GOAL,
        "scope": "decision-step latency on fixed replayed states; interleaved legs per round; "
                 f"{args.warmup} warmup round(s) excluded; network and provider load are live. "
                 "Every call records raw output-format failures (repaired by conditioning or not)",
        "rounds": args.rounds,
        "warmup": args.warmup,
        "states": [
            {"id": s["id"], "url": s["url"], "actions": len(s["actions"]), "history": len(s["history"])}
            for s in scenarios
        ],
        "models": {leg: next((r["model"] for r in rows if r["leg"] == leg and r["model"]), None) for leg in active},
        "summary": summary,
        "calls": rows,
    }
    out = ROOT / args.out
    out.write_text(json.dumps(evidence, indent=1) + "\n")
    print(f"\nevidence: {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
