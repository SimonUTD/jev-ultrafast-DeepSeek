"""TypeSafe makes choices; an optional small OpenAI-compatible model writes field values."""

import json
import math
import os
import time

import httpx

from .questions import DECISION_SYSTEM, NEXT_ACTION, TARGET, TEXT_VALUE

CLIENT = httpx.Client(http2=True, timeout=25)


def post_json(url, key, body):
    for attempt in range(3):
        try:
            response = CLIENT.post(url, json=body, headers={"Authorization": f"Bearer {key}"})
        except httpx.HTTPError:
            raise RuntimeError("Model connection failed; no action executed.") from None
        if response.status_code in {429, 529, 503} and attempt < 2:
            time.sleep(0.5 * 2**attempt)
            continue
        if response.is_error:
            raise RuntimeError(f"Model provider returned HTTP {response.status_code}; no action executed.")
        return response.json()
    raise RuntimeError("Model unavailable")


def validate_choice(answer, ids, source="TypeSafe"):
    try:
        probabilities = answer["probabilities"]
        numbers = [*probabilities.values(), answer["confidence"]]
        valid = (
            answer["choice"] in ids
            and set(probabilities) == set(ids)
            and all(type(n) in (int, float) and math.isfinite(n) and 0 <= n <= 1 for n in numbers)
            and abs(sum(probabilities.values()) - 1) < 0.02
            and probabilities[answer["choice"]] >= max(probabilities.values()) - 1e-6
        )
    except (KeyError, TypeError, ValueError):
        valid = False
    if not valid:
        raise ValueError(f"Invalid {source} response; no action executed.")
    return answer


def action_space(actions):
    """One index per observed element; each operation has its own valid target choices."""
    elements, indices, targets, controls = [], {}, {}, {}
    operations = {"click": "CLICK", "fill": "TYPE_TEXT", "select": "SELECT"}
    for action in actions:
        kind = action["kind"]
        if kind not in operations:
            controls[action["id"].upper()] = action
            continue
        node = action["node"]
        if node not in indices:
            index = str(len(elements) + 1)
            indices[node] = index
            element = {k: action[k] for k in ("role", "value", "checked", "selected", "expanded") if k in action}
            element.update(index=index, label=action["label"].split(" → ")[0], operations=[])
            if kind == "select":
                element["value"] = action.get("current_value", "")
                element["options"] = []
            elements.append(element)
        index = indices[node]
        operation = operations[kind]
        group = targets.setdefault(operation, {})
        element = elements[int(index) - 1]
        if operation not in element["operations"]:
            element["operations"].append(operation)
        target = index
        if kind == "select":
            target = f"{index}:{len(element['options']) + 1}"
            element["options"].append({"index": target, "label": action["label"], "value": action["value"]})
        group[target] = action
    return elements, targets, controls


def choose(state, goal, history):
    elements, targets, controls = action_space(state["actions"])
    labels = {
        "CLICK": "Click an element, button, menu option, autocomplete suggestion, or calendar day.",
        "TYPE_TEXT": "Enter or replace text in an editable field. A small LLM will supply the value from the goal.",
        "SELECT": "Select an observed dropdown value.",
    }
    operations = {key: labels[key] for key in targets}
    operations.update({key: value["label"] for key, value in controls.items()})
    operations.update(DONE="Every requirement is visibly satisfied.", BLOCKED="No supported operation can progress.")
    questions = {
        "operation": {"type": "choice", "criteria": operations, "instructions": {"goal": goal, "rules": NEXT_ACTION}}
    }
    for operation, candidates in targets.items():
        questions[operation.lower() + "_target"] = {
            "type": "choice",
            "criteria": {
                index: {
                    "element": f"[{index}] {a['label']}",
                    "current_value": a.get("current_value", a.get("value", "")),
                    **{k: a[k] for k in ("role", "checked", "selected", "expanded") if k in a},
                }
                for index, a in candidates.items()
            },
            "instructions": {"goal": goal, "operation": operation, "rules": [NEXT_ACTION, TARGET]},
        }
    body = {
        "model": os.environ.get("TYPESAFE_MODEL", "jev-latest"),
        "state": {
            "page": {k: state[k] for k in ("url", "title", "text")},
            "elements": elements,
            "recent_actions": [
                {k: h.get(k) for k in ("action", "kind", "text", "page_changed")} for h in history[-10:]
            ],
        },
        "questions": questions,
    }
    started = time.perf_counter()
    if os.environ.get("DECISION_PROVIDER", "typesafe").strip().lower() == "deepseek":
        result = deepseek_decide(body)
        source = "DeepSeek"
    else:
        result = post_json("https://api.typesafe.ai/v1/systemone", os.environ["TYPESAFE_API_KEY"], body)
        source = "TypeSafe"

    def contract_error(error):
        # Keep raw format evidence reachable when the shared validation rejects an answer.
        for extra in ("conditioning", "retry_reasons"):
            if result.get(extra):
                setattr(error, extra, result[extra])
        return error

    try:
        operation_answer = validate_choice(result["answers"].get("operation", {}), operations, source)
    except ValueError as error:
        raise contract_error(error) from None
    operation = operation_answer["choice"]
    target = None
    target_answer = None
    probabilities = {}
    if operation in targets:
        # Unused target heads cannot cause an action. Validate the head selected by the operation.
        head = result["answers"].get(operation.lower() + "_target", {})
        try:
            target_answer = validate_choice(head, targets[operation], source)
        except ValueError as error:
            raise contract_error(error) from None
        target = target_answer["choice"]
        choice = targets[operation][target]["id"]
        probabilities = {a["id"]: target_answer["probabilities"][index] for index, a in targets[operation].items()}
    else:
        choice = controls[operation]["id"] if operation in controls else operation
        probabilities[choice] = operation_answer["probabilities"][operation]
    return {
        "choice": choice,
        "operation": operation,
        "target": target,
        "confidence": operation_answer["confidence"],
        "probabilities": probabilities,
        "operation_probabilities": operation_answer["probabilities"],
        "target_probabilities": target_answer["probabilities"] if target_answer else {},
        "target_confidence": target_answer["confidence"] if target_answer else None,
        "raw_answers": result["answers"],
        "model": result["model"],
        "usage": result.get("usage", {}),
        "latency_ms": round((time.perf_counter() - started) * 1000),
        "conditioning": result.get("conditioning", {}),
        "retry_reasons": result.get("retry_reasons", []),
        "request": body,
    }


def parse_json_object(content):
    """Trim prose or code fences around the object; DeepSeek without json mode can add either."""
    text = content.strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("No json object in content")
    answers = json.loads(text[start : end + 1])
    if not isinstance(answers, dict):
        raise ValueError("Not a json object")
    return answers


def repair_choice(answer, ids):
    """Condition a text model's answer to the choice contract, recording raw format failures.
    A text LLM does not guarantee probability invariants the way Jev's heads do: fill missing
    candidates with 0, clamp, renormalize to exactly 1, and align the maximum with the selected
    choice so display and execution agree. Returns (conditioned answer, format failure reasons);
    an invented candidate is never rescued (answer None)."""
    if not isinstance(answer, dict):
        return None, ["answer_not_object"]
    choice = answer.get("choice")
    if choice not in ids:
        return None, [f"choice_not_offered:{choice!r}"]
    issues = []
    offered = answer.get("probabilities")
    if not isinstance(offered, dict):
        offered, issues = {}, ["probabilities_not_object"]
    unknown = [k for k in offered if k not in ids]
    if unknown:
        issues.append("unknown_candidates")
    missing = [i for i in ids if i not in offered]
    if missing:
        issues.append("missing_candidates")
    def valid(v):
        return type(v) in (int, float) and math.isfinite(v) and 0 <= v <= 1

    if any(not valid(v) for v in offered.values()):
        issues.append("invalid_probability_values")
    scores = {i: offered[i] if valid(offered.get(i)) else 0.0 for i in ids}
    total = sum(scores.values())
    if total <= 0:
        issues.append("no_valid_probability_mass")
        scores = {i: float(i == choice) for i in ids}
    else:
        if abs(total - 1) >= 0.02:
            issues.append(f"probabilities_sum:{total:.2f}")
        scores = {i: v / total for i, v in scores.items()}
    top = max(scores, key=scores.get)
    if top != choice:
        issues.append("choice_not_max")
        scores[top], scores[choice] = scores[choice], scores[top]
    confidence = answer.get("confidence")
    if not valid(confidence):
        issues.append("invalid_confidence")
        confidence = scores[choice]
    return {"choice": choice, "probabilities": scores, "confidence": confidence}, issues


def deepseek_decide(body):
    """deepseek-flash answers every question of one decision cycle in one request, thinking off."""
    key = os.environ.get("DEEPSEEK_API_KEY")
    if not key:
        raise RuntimeError("DECISION_PROVIDER=deepseek needs DEEPSEEK_API_KEY; no action executed.")
    base = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1").rstrip("/")
    request = {
        "model": os.environ.get("DECISION_MODEL", "deepseek-flash"),
        "max_tokens": 4096,
        "messages": [
            {"role": "system", "content": DECISION_SYSTEM},
            {"role": "user", "content": json.dumps({"state": body["state"], "questions": body["questions"]})},
        ],
    }
    if "api.deepseek.com/" in base:
        request["thinking"] = {"type": "disabled"}
    if os.environ.get("DEEPSEEK_OUTPUT", "prompt").strip().lower() == "json":
        request["response_format"] = {"type": "json_object"}
    # Up to two retries for unparsable output or an invented candidate (json mode can return
    # empty content; on large pages a text model slips an off-head id about half the time).
    # Every attempt's failure is recorded. This call only predicts; a browser mutation is never
    # retried.
    retry_reasons = []
    for attempt in range(3):
        result = post_json(base + "/chat/completions", key, request)
        content = ""
        try:
            content = result["choices"][0]["message"]["content"]
            parsed = parse_json_object(content)
        except (KeyError, TypeError, ValueError):
            retry_reasons.append({"reason": "unparsable", "content_head": str(content)[:120]})
            if attempt == 2:
                failure = ValueError("DeepSeek returned no parsable decision json; no action executed.")
                failure.retry_reasons = retry_reasons
                raise failure
            continue
        answers, conditioning = {}, {}
        for qid, answer in parsed.items():
            # Condition every head to the contract before the shared validation consumes it.
            if qid not in body["questions"]:
                answers[qid] = answer
                continue
            repaired, issues = repair_choice(answer, set(body["questions"][qid]["criteria"]))
            answers[qid] = repaired
            if issues:
                conditioning[qid] = issues
            if repaired is None:
                retry_reasons.append({
                    "reason": "choice_not_offered",
                    "question": qid,
                    "choice": str(answer.get("choice"))[:60] if isinstance(answer, dict) else str(answer)[:60],
                })
        if attempt == 2 or (answers and all(a is not None for a in answers.values())):
            return {
                "answers": answers,
                "model": result.get("model", request["model"]),
                "usage": result.get("usage", {}),
                "conditioning": conditioning,
                "retry_reasons": retry_reasons,
            }


def field_context(goal, action, page, history):
    return {
        "goal": goal,
        "field": {k: action.get(k) for k in ("label", "role", "value")},
        "page": {"title": page["title"], "text": page["text"][:6000]},
        "recent_actions": [{k: h.get(k) for k in ("action", "text")} for h in history[-6:]],
    }


def field_text(context):
    key = os.environ.get("TEXT_MODEL_API_KEY") or os.environ.get("DEEPSEEK_API_KEY")
    if not key:
        raise ValueError("TYPE_TEXT needs TEXT_MODEL_API_KEY (or DEEPSEEK_API_KEY); no text is hardcoded.")
    base = os.environ.get("TEXT_MODEL_BASE_URL", "https://api.deepseek.com/v1").rstrip("/")
    model = os.environ.get("TEXT_MODEL", "deepseek-flash")
    if "api.deepseek.com/" in base:
        reasoning = {"thinking": {"type": "disabled"}}
    elif os.environ.get("TEXT_MODEL_REASONING") == "none":
        reasoning = {"reasoning": {"enabled": False}}
    else:
        reasoning = {"reasoning": {"effort": "low"}}
    started = time.perf_counter()
    # Up to two retries: DeepSeek's json mode can return empty content twice in a row.
    for attempt in range(3):
        result = post_json(
            base + "/chat/completions",
            key,
            {
                "model": model,
                "max_tokens": 1024,
                "response_format": {"type": "json_object"},
                **reasoning,
                "messages": [
                    {"role": "system", "content": TEXT_VALUE},
                    {
                        "role": "user",
                        "content": json.dumps(context),
                    },
                ],
            },
        )
        content = ""
        try:
            content = result["choices"][0]["message"]["content"]
            output = json.loads(content)
            value = output["text"]
            if set(output) != {"text"} or not isinstance(value, str) or not value.strip() or len(value) > 2000:
                raise ValueError()
            return value, {
                "model": model,
                "latency_ms": round((time.perf_counter() - started) * 1000),
                "usage": result.get("usage", {}),
            }
        except (ValueError, KeyError, TypeError):
            if attempt == 2:
                raise ValueError(
                    "Text helper returned no valid field value; nothing typed. "
                    f"Last output: {content[:60]!r}"
                ) from None
