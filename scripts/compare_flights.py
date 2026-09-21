"""Live Google Flights task comparison: Jev vs deepseek-flash (prompt vs json mode).

Runs the same real task on all three decision configurations, interleaving legs within each
round so Google load, proxy, and network conditions hit every leg equally. Every run is
independently verified (route, one-way, date, visible results) — a DONE choice alone proves
nothing. Requires the dedicated dev Chrome (scripts/dev_chrome.sh) and paid API keys in .env.

Raw per-run traces: artifacts/flights-compare/ (gitignored). Summary: docs/flights-compare.json.
"""

import argparse
import json
import os
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from examples.flights import URL, verify  # noqa: E402
from jev_ultrafast import Agent  # noqa: E402
from jev_ultrafast.demo import load_environment  # noqa: E402

LEGS = ("jev", "ds-prompt", "ds-json")


def leg_environment(leg):
    if leg == "jev":
        os.environ["DECISION_PROVIDER"] = "typesafe"
    else:
        os.environ["DECISION_PROVIDER"] = "deepseek"
        os.environ["DEEPSEEK_OUTPUT"] = "prompt" if leg == "ds-prompt" else "json"


def run_once(leg, round_index, date, goal, folder):
    leg_environment(leg)
    run = {"leg": leg, "round": round_index, "started": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    agent = Agent(URL, goal)
    error = None
    try:
        for state in agent.run():
            pass
    except Exception as exc:  # noqa: BLE001 - the error itself is part of the evidence
        error = f"{type(exc).__name__}: {exc}"
    state = agent.snapshot()
    final = agent.browser.observe(screenshot=True)  # fresh observation, outside the task clock
    run.update({
        "elapsed_ms": state["elapsed_ms"],
        "status": state["status"],
        "error": error,
        "verification": verify(final, date),
        "decisions": len(state["decisions"]),
        "decision_latency_ms": [d["latency_ms"] for d in state["decisions"]],
        "decision_model": state["decisions"][0]["model"] if state["decisions"] else None,
        "actions": len(state["history"]),
        "text_calls": [
            {"field": t["field"], "value": t["value"], "latency_ms": t["latency_ms"]}
            for t in state.get("text_calls", [])
        ],
    })
    (folder / f"r{round_index}-{leg}.json").write_text(
        json.dumps({**run, "state": state, "final_page": {k: v for k, v in final.items() if k != "screenshot"}},
                   indent=1)
    )
    agent.close()
    return run


def stats(values):
    return {
        "median": round(statistics.median(values)),
        "mean": round(statistics.fmean(values)),
        "min": min(values),
        "max": max(values),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rounds", type=int, default=3)
    parser.add_argument("--date", default="2026-11-20", help="departure date; must be in the future")
    parser.add_argument("--legs", default=",".join(LEGS))
    parser.add_argument("--out", default="docs/flights-compare.json")
    args = parser.parse_args()

    load_environment()
    day = datetime.strptime(args.date, "%Y-%m-%d")
    spoken = day.strftime("%B ") + f"{day.day}, {day.year}"
    goal = (
        f"Find one-way flights from Zurich to London on {spoken}, for one adult in economy. "
        "Stop when matching flight options are visible. Do not select or book a flight."
    )
    if day.date() <= datetime.now().date():
        raise SystemExit(f"--date {args.date} is not in the future; the date picker cannot reach it")

    legs = [leg for leg in args.legs.split(",") if leg]
    folder = Path("artifacts/flights-compare") / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    folder.mkdir(parents=True, exist_ok=True)
    runs = []
    for round_index in range(1, args.rounds + 1):
        for leg in legs:
            run = run_once(leg, round_index, args.date, goal, folder)
            runs.append(run)
            mark = "VERIFIED" if run["verification"]["passed"] else f"FAILED ({run['error'] or run['status']})"
            print(f"round {round_index} {leg:9s} {run['elapsed_ms']:>7} ms | {run['decisions']:2d} decisions "
                  f"| {run['actions']:2d} actions | {mark}", flush=True)
            if not run["verification"]["passed"]:
                failed = [k for k, ok in run["verification"]["checks"].items() if not ok]
                print(f"           failed checks: {failed}", flush=True)
            time.sleep(1)

    summary = {}
    for leg in legs:
        leg_runs = [r for r in runs if r["leg"] == leg]
        verified = [r for r in leg_runs if r["verification"]["passed"]]
        elapsed = [r["elapsed_ms"] for r in verified]
        decisions = [d for r in verified for d in r["decision_latency_ms"]]
        text = [t["latency_ms"] for r in verified for t in r["text_calls"]]
        summary[leg] = {
            "verified": f"{len(verified)}/{len(leg_runs)}",
            **({"elapsed_ms": stats(elapsed)} if elapsed else {}),
            "decisions_per_run": stats([r["decisions"] for r in verified]) if verified else {},
            "decision_latency_ms": stats(decisions) if decisions else {},
            "text_latency_ms": stats(text) if text else {},
            "errors": [r["error"] for r in leg_runs if r["error"]],
        }

    print("\nleg        verified   task-time(med)  decision(ms, med)  decisions/run  text(ms, med)")
    for leg in legs:
        s = summary[leg]
        if "elapsed_ms" in s:
            print(f"{leg:10s} {s['verified']:>8} {s['elapsed_ms']['median']:>11} ms "
                  f"{s['decision_latency_ms']['median']:>13} ms {s['decisions_per_run']['median']:>12} "
                  f"{s['text_latency_ms']['median']:>12} ms")
        else:
            print(f"{leg:10s} {s['verified']:>8}   no verified run")

    evidence = {
        "date": args.date,
        "goal": goal,
        "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "url": URL,
        "rounds": args.rounds,
        "interleaved": True,
        "summary": summary,
        "runs": [{k: v for k, v in r.items() if k != "state"} for r in runs],
    }
    out = ROOT / args.out
    out.write_text(json.dumps(evidence, indent=1) + "\n")
    print(f"\nevidence: {out.relative_to(ROOT)} | raw traces: {folder}")


if __name__ == "__main__":
    main()
