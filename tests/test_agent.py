"""Offline contracts for a dynamic operation/target policy. No paid APIs."""

import json
import time
from copy import deepcopy
from unittest.mock import Mock

import pytest

from jev_ultrafast import agent as loop
from jev_ultrafast import model
from jev_ultrafast.browser import StalePage, browser_operation, fingerprint


def page():
    state = {
        "url": "https://example.test/",
        "title": "Search",
        "text": "Search",
        "scroll": {"y": 0},
        "actions": [
            {"id": "e1", "kind": "fill", "label": "Search", "role": "textbox", "value": "", "node": 10},
            {"id": "e2", "kind": "click", "label": "Open Search", "role": "textbox", "value": "", "node": 10},
            {"id": "e3", "kind": "click", "label": "Go", "role": "button", "value": "", "node": 20},
            {"id": "wait", "kind": "wait", "label": "Wait"},
        ],
    }
    state["fingerprint"] = fingerprint(state)
    return state


def choice(ids, selected):
    return {"choice": selected, "confidence": 1.0, "probabilities": {i: float(i == selected) for i in ids}}


def decision(action="e1"):
    return {
        "choice": action,
        "operation": "TYPE_TEXT",
        "target": "1",
        "confidence": 1.0,
        "probabilities": {action: 1.0},
        "latency_ms": 10,
        "usage": {},
    }


@pytest.mark.parametrize("mutation", ["unknown", "nan", "missing", "negative", "non_max", "confidence"])
def test_invalid_choice_is_rejected(mutation):
    a = choice(["a", "b"], "a")
    if mutation == "unknown":
        a["choice"] = "invented"
    elif mutation == "nan":
        a["probabilities"]["a"] = float("nan")
    elif mutation == "missing":
        del a["probabilities"]["b"]
    elif mutation == "negative":
        a["probabilities"]["b"] = -1
    elif mutation == "non_max":
        a["choice"] = "b"
    else:
        a["confidence"] = 5
    with pytest.raises(ValueError, match="Invalid TypeSafe"):
        model.validate_choice(a, {"a", "b"})


def test_one_index_per_node_with_operation_specific_targets():
    elements, targets, controls = model.action_space(page()["actions"])
    assert len(elements) == 2
    assert elements[0]["operations"] == ["TYPE_TEXT", "CLICK"]
    assert targets["TYPE_TEXT"]["1"]["id"] == "e1"
    assert targets["CLICK"]["1"]["id"] == "e2"
    assert targets["CLICK"]["2"]["id"] == "e3"
    assert "WAIT" in controls


def test_all_heads_are_one_request_and_only_matching_head_executes(monkeypatch):
    calls = []

    def post(_url, _key, body):
        calls.append(body)
        return {
            "model": "test",
            "answers": {
                "operation": choice(body["questions"]["operation"]["criteria"], "TYPE_TEXT"),
                "type_text_target": choice(["1"], "1"),
                "click_target": {"choice": "invented"},
            },
        }

    monkeypatch.setenv("TYPESAFE_API_KEY", "test")
    monkeypatch.setattr(model, "post_json", post)
    d = model.choose(page(), "Find a book", [])
    assert len(calls) == 1
    assert d["operation"] == "TYPE_TEXT" and d["target"] == "1" and d["choice"] == "e1"
    assert set(calls[0]["questions"]) == {"operation", "click_target", "type_text_target"}


def test_click_cannot_consume_a_text_target(monkeypatch):
    def post(_url, _key, body):
        return {
            "model": "test",
            "answers": {
                "operation": choice(body["questions"]["operation"]["criteria"], "CLICK"),
                "type_text_target": choice(["1"], "1"),
                "click_target": choice(["1", "2", "999"], "999"),
            },
        }

    monkeypatch.setenv("TYPESAFE_API_KEY", "test")
    monkeypatch.setattr(model, "post_json", post)
    with pytest.raises(ValueError, match="Invalid TypeSafe"):
        model.choose(page(), "Find a book", [])


def test_target_head_receives_control_state_and_full_next_step_rules(monkeypatch):
    p = page()
    p["actions"].insert(0, {
        "id": "toggle", "kind": "click", "label": "Free cancellation", "node": 30,
        "role": "checkbox", "checked": "true", "selected": False,
    })

    def post(_url, _key, body):
        questions = body["questions"]
        target = questions["click_target"]
        assert target["criteria"]["1"]["checked"] == "true"
        assert target["criteria"]["1"]["selected"] is False
        assert questions["operation"]["instructions"]["rules"] in target["instructions"]["rules"]
        return {
            "model": "test",
            "answers": {
                "operation": choice(questions["operation"]["criteria"], "CLICK"),
                "click_target": choice(target["criteria"], "3"),
            },
        }

    monkeypatch.setenv("TYPESAFE_API_KEY", "test")
    monkeypatch.setattr(model, "post_json", post)
    d = model.choose(p, "Search with free cancellation", [])
    assert d["choice"] == "e3"


def test_quoted_task_text_still_uses_the_llm(monkeypatch):
    monkeypatch.setenv("TEXT_MODEL_API_KEY", "test")
    post = Mock(return_value={"choices": [{"message": {"content": '{"text":"Zurich"}'}}]})
    monkeypatch.setattr(model, "post_json", post)
    context = model.field_context('Fly from "Zurich" to London', page()["actions"][0], page(), [])
    assert model.field_text(context)[0] == "Zurich"
    assert post.call_count == 1
    sent = json.loads(post.call_args.args[2]["messages"][1]["content"])
    assert sent["goal"] == 'Fly from "Zurich" to London'


def test_missing_text_credential_stops_before_guessing(monkeypatch):
    monkeypatch.delenv("TEXT_MODEL_API_KEY", raising=False)
    with pytest.raises(ValueError, match="TEXT_MODEL_API_KEY"):
        model.field_text({"goal": 'Enter "Zurich"'})


@pytest.fixture
def runner():
    a = loop.Agent.__new__(loop.Agent)
    a.screenshots = False
    a.pending_text = None
    p = page()
    a.state = {
        "browser": Mock(fresh=Mock(return_value=True), observe=Mock(return_value=p)),
        "page": p,
        "decision": decision(),
        "goal": "Find a book",
        "history": [],
        "decisions": [],
        "status": "predicted",
        "started_at": time.perf_counter(),
        "record": False,
        "text_calls": [],
    }
    return a


def test_stale_decision_is_consumed_before_any_mutation(runner):
    runner.state["browser"].fresh.return_value = False
    with pytest.raises(StalePage):
        runner.command("act", {"fingerprint": runner.state["page"]["fingerprint"]})
    runner.state["browser"].act.assert_not_called()
    assert runner.state["decision"] is None


def test_generated_text_reused_only_for_identical_retry_context(runner, monkeypatch):
    helper = Mock(return_value=("book", {"model": "test", "latency_ms": 10}))
    monkeypatch.setattr(loop, "field_text", helper)
    runner.state["browser"].act.side_effect = [StalePage("Changed before input"), None]
    with pytest.raises(StalePage):
        runner.command("act", {"fingerprint": runner.state["page"]["fingerprint"]})
    runner.state["decision"] = decision()
    runner.command("act", {"fingerprint": runner.state["page"]["fingerprint"]})
    assert helper.call_count == 1
    assert runner.state["browser"].act.call_count == 2  # The first call rejects before any browser input.
    assert runner.pending_text is None


def test_changed_field_context_does_not_reuse_generated_text(runner, monkeypatch):
    helper = Mock(return_value=("book", {"model": "test", "latency_ms": 10}))
    monkeypatch.setattr(loop, "field_text", helper)
    runner.state["browser"].act.side_effect = [StalePage("Changed before input"), None]
    with pytest.raises(StalePage):
        runner.command("act", {"fingerprint": runner.state["page"]["fingerprint"]})
    runner.state["page"]["text"] = "Different page context"
    runner.state["decision"] = decision()
    runner.command("act", {"fingerprint": runner.state["page"]["fingerprint"]})
    assert helper.call_count == 2


def test_loading_waits_do_not_trigger_no_progress_stop(runner):
    for _ in range(5):
        runner.state["decision"] = decision("wait")
        runner.command("act", {"fingerprint": runner.state["page"]["fingerprint"]})
    assert len(runner.state["history"]) == 5 and runner.state["status"] == "ready"


def test_stale_observation_preserves_executed_action(runner):
    runner.state["decision"] = decision("e3")
    runner.state["browser"].observe.side_effect = StalePage("changed")
    with pytest.raises(StalePage):
        runner.command("act", {"fingerprint": runner.state["page"]["fingerprint"]})
    assert runner.state["history"][-1]["action"] == "Go"
    runner.state["browser"].act.assert_called_once()


def test_observation_is_one_atomic_browser_read(monkeypatch):
    import jev_ultrafast.browser as browser

    p = page()
    cdp = Mock(return_value={"result": {"value": p}})
    monkeypatch.setattr(browser, "cdp", cdp)
    actual = browser_operation({"operation": "observe", "session": "test", "screenshot": False})
    assert actual["actions"] == p["actions"]
    assert cdp.call_count == 1
    assert cdp.call_args.args[0] == "Runtime.evaluate"


def test_executor_rejects_a_stale_page_before_browser_input(monkeypatch):
    import jev_ultrafast.browser as browser

    b = browser.Browser.__new__(browser.Browser)
    b.fresh = Mock(return_value=False)
    operation = Mock()
    monkeypatch.setattr(browser, "browser_operation", operation)
    with pytest.raises(StalePage):
        b.act(page()["actions"][0], page(), "book")
    operation.assert_not_called()


@pytest.mark.parametrize("response", [{"exceptionDetails": {}}, {"result": {}}])
def test_interrupted_dropdown_mutation_cannot_be_retried_as_stale(monkeypatch, response):
    import jev_ultrafast.browser as browser

    # A navigation can destroy the evaluation result after the change event already fired.
    if "exceptionDetails" in response:
        response["exceptionDetails"] = {"text": "Execution context destroyed"}
    cdp = Mock(return_value=response)
    monkeypatch.setattr(browser, "cdp", cdp)
    with pytest.raises(RuntimeError, match="Dropdown execution"):
        browser_operation({"operation": "act", "session": "test", "action": {
            "id": "e1", "kind": "select", "node": 1, "value": "Design",
        }})
    assert cdp.call_count == 1


def test_fingerprint_tracks_values_and_identity_not_screenshots():
    p = page()
    other = deepcopy(p)
    other["screenshot"] = "changed"
    assert fingerprint(p) == fingerprint(other)
    other["actions"][0]["node"] = 99
    assert fingerprint(p) != fingerprint(other)


@pytest.mark.parametrize("changed", ["Departure", "Where from?", "Where to?", "year"])
def test_flight_verification_rejects_wrong_trip(changed):
    from examples.flights import verify

    actual = {
        "url": "https://www.google.com/travel/flights/search?tfs=example",
        "text": "Track prices from Zürich to London departing 2026-09-20",
        "actions": [
            {"label": k, "value": v}
            for k, v in [
                ("Change ticket type. One way", "One way"),
                ("Where from?", "Zürich"),
                ("Where to?", "London"),
                ("Departure", "Sun, Sep 20"),
                ("Nonstop flight on Sunday, September 20. Select flight", ""),
            ]
        ],
    }
    assert verify(actual)["passed"]
    if changed == "year":
        actual["text"] = actual["text"].replace("2026", "2027")
    else:
        next(a for a in actual["actions"] if a["label"] == changed)["value"] = "wrong"
    assert not verify(actual)["passed"]


@pytest.mark.parametrize(
    "content", ["Thinking: Zurich", '{"text":null}', '{"text":"Zurich","extra":true}', '{"text":123}']
)
def test_text_helper_rejects_invalid_values(monkeypatch, content):
    monkeypatch.setenv("TEXT_MODEL_API_KEY", "test")
    monkeypatch.setattr(model, "post_json", Mock(return_value={"choices": [{"message": {"content": content}}]}))
    with pytest.raises(ValueError, match="nothing typed"):
        model.field_text({"goal": "Find a flight"})
    # Two retries for the invalid output (DeepSeek json mode can return empty content).
    assert model.post_json.call_count == 3


def test_text_helper_recovers_on_retry(monkeypatch):
    monkeypatch.setenv("TEXT_MODEL_API_KEY", "test")
    responses = [
        {"choices": [{"message": {"content": ""}}]},  # json mode empty-content caveat
        {"choices": [{"message": {"content": '{"text":"Zurich"}'}}]},
    ]
    monkeypatch.setattr(model, "post_json", Mock(side_effect=responses))
    assert model.field_text({"goal": 'Enter "Zurich"'})[0] == "Zurich"
    assert model.post_json.call_count == 2


def test_navigation_during_prediction_reobserves_without_action(runner):
    runner.state["browser"].fresh.side_effect = StalePage("Document navigating")
    runner.command("tick")
    assert runner.state["status"] == "ready"
    assert runner.state["decision"] is None
    runner.state["browser"].act.assert_not_called()


def deepseek_post(capture, content_fn):
    def post(_url, _key, body):
        capture.append((_url, body))
        questions = json.loads(body["messages"][1]["content"])["questions"]
        return {
            "model": "deepseek-flash",
            "choices": [{"message": {"content": content_fn(questions)}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        }

    return post


def test_deepseek_decides_in_one_request_with_thinking_off(monkeypatch):
    capture = []

    def content(questions):
        return json.dumps({qid: choice(list(q["criteria"]), list(q["criteria"])[0]) for qid, q in questions.items()})

    monkeypatch.setenv("DECISION_PROVIDER", "deepseek")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test")
    monkeypatch.setattr(model, "post_json", deepseek_post(capture, content))
    d = model.choose(page(), "Find a book", [])
    url, request = capture[0]
    assert len(capture) == 1 and url == "https://api.deepseek.com/v1/chat/completions"
    assert request["model"] == "deepseek-flash" and request["thinking"] == {"type": "disabled"}
    assert "response_format" not in request  # prompt mode relies on the system prompt only
    assert request["messages"][0]["role"] == "system" and "json" in request["messages"][0]["content"]
    assert set(json.loads(request["messages"][1]["content"])["questions"]) == {
        "operation", "click_target", "type_text_target"
    }
    assert d["operation"] == "TYPE_TEXT" and d["choice"] == "e1"
    assert d["target"] == "1" and d["model"] == "deepseek-flash"


def test_deepseek_json_mode_sets_response_format(monkeypatch):
    capture = []

    def content(questions):
        return json.dumps({qid: choice(list(q["criteria"]), list(q["criteria"])[0]) for qid, q in questions.items()})

    monkeypatch.setenv("DECISION_PROVIDER", "deepseek")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test")
    monkeypatch.setenv("DEEPSEEK_OUTPUT", "json")
    monkeypatch.setattr(model, "post_json", deepseek_post(capture, content))
    model.choose(page(), "Find a book", [])
    request = capture[0][1]
    assert request["response_format"] == {"type": "json_object"}
    assert "json" in request["messages"][0]["content"]  # DeepSeek json mode requires the word json in the prompt


@pytest.mark.parametrize(
    "content", ['```json\n{"operation": 1}\n```', 'Sure!\n{"operation": 2}\nDone.']
)
def test_parse_json_object_tolerates_fences_and_prose(content):
    assert model.parse_json_object(content)["operation"] in (1, 2)


def test_deepseek_invalid_candidate_is_rejected(monkeypatch):
    def content(questions):
        answers = {qid: choice(list(q["criteria"]), list(q["criteria"])[0]) for qid, q in questions.items()}
        answers["operation"]["choice"] = "invented"
        return json.dumps(answers)

    monkeypatch.setenv("DECISION_PROVIDER", "deepseek")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test")
    monkeypatch.setattr(model, "post_json", deepseek_post([], content))
    with pytest.raises(ValueError, match="Invalid DeepSeek"):
        model.choose(page(), "Find a book", [])


def test_deepseek_invented_candidate_is_retried_once(monkeypatch):
    calls = []

    def content(questions):
        answers = {qid: choice(list(q["criteria"]), list(q["criteria"])[0]) for qid, q in questions.items()}
        if not calls:
            answers["operation"]["choice"] = "invented"
        calls.append(1)
        return json.dumps(answers)

    def post(_url, _key, body):
        questions = json.loads(body["messages"][1]["content"])["questions"]
        return {
            "model": "deepseek-flash",
            "choices": [{"message": {"content": content(questions)}}],
            "usage": {},
        }

    monkeypatch.setenv("DECISION_PROVIDER", "deepseek")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test")
    monkeypatch.setattr(model, "post_json", post)
    d = model.choose(page(), "Find a book", [])
    assert len(calls) == 2 and d["choice"] == "e1"
    assert d["retry_reasons"][0]["reason"] == "choice_not_offered"  # failures stay visible


def test_deepseek_format_failures_are_reported_not_hidden(monkeypatch):
    def content(questions):
        answers = {}
        for qid, q in questions.items():
            ids = list(q["criteria"])
            answers[qid] = {
                "choice": ids[0],
                "probabilities": {ids[0]: 0.7},  # missing candidates, sum drifts to 0.70
                "confidence": 0.9,
            }
        return json.dumps(answers)

    monkeypatch.setenv("DECISION_PROVIDER", "deepseek")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test")
    monkeypatch.setattr(model, "post_json", deepseek_post([], content))
    d = model.choose(page(), "Find a book", [])
    assert d["choice"] == "e1" and d["conditioning"]["operation"] == [
        "missing_candidates", "probabilities_sum:0.70"
    ]


def test_text_helper_falls_back_to_the_deepseek_key_and_keeps_thinking_off(monkeypatch):
    monkeypatch.delenv("TEXT_MODEL_API_KEY", raising=False)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "shared")
    monkeypatch.setenv("TEXT_MODEL_REASONING", "none")  # OpenRouter-only knob must not leak to DeepSeek
    monkeypatch.setenv("TEXT_MODEL_BASE_URL", "https://api.deepseek.com/v1")
    post = Mock(return_value={"choices": [{"message": {"content": '{"text":"Zurich"}'}}]})
    monkeypatch.setattr(model, "post_json", post)
    assert model.field_text({"goal": 'Enter "Zurich"'})[0] == "Zurich"
    body = post.call_args.args[2]
    assert post.call_args.args[1] == "shared"
    assert body["model"] == "deepseek-flash" and body["thinking"] == {"type": "disabled"}


def test_deepseek_unparsable_output_is_retried_then_fails(monkeypatch):
    capture = []

    def content(questions):
        return "not json at all"

    monkeypatch.setenv("DECISION_PROVIDER", "deepseek")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test")
    monkeypatch.setattr(model, "post_json", deepseek_post(capture, content))
    with pytest.raises(ValueError, match="no parsable decision json"):
        model.choose(page(), "Find a book", [])
    assert len(capture) == 3  # two retries for unparsable output, then a hard stop


def test_deepseek_needs_a_key(monkeypatch):
    monkeypatch.setenv("DECISION_PROVIDER", "deepseek")
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    post = Mock()
    monkeypatch.setattr(model, "post_json", post)
    with pytest.raises(RuntimeError, match="DEEPSEEK_API_KEY"):
        model.choose(page(), "Find a book", [])
    post.assert_not_called()


def test_repair_choice_normalizes_a_drifting_partial_distribution():
    answer, issues = model.repair_choice(
        {"choice": "b", "probabilities": {"a": 0.6, "b": 0.35}, "confidence": 5}, {"a", "b", "c"}
    )
    assert set(answer["probabilities"]) == {"a", "b", "c"}  # missing candidate filled with 0
    assert abs(sum(answer["probabilities"].values()) - 1) < 1e-9  # renormalized to exactly 1
    assert answer["probabilities"]["b"] > answer["probabilities"]["a"]  # max aligned with the choice
    assert answer["confidence"] == answer["probabilities"]["b"]  # invalid confidence falls back
    assert "missing_candidates" in issues  # every raw format failure is recorded
    assert any(i.startswith("probabilities_sum:") for i in issues)
    assert "invalid_confidence" in issues


def test_repair_choice_falls_back_to_one_hot_without_numbers():
    answer, issues = model.repair_choice({"choice": "a", "probabilities": {"a": "high"}}, {"a", "b"})
    assert answer["probabilities"] == {"a": 1.0, "b": 0.0}
    assert "no_valid_probability_mass" in issues and "invalid_probability_values" in issues
    assert "missing_candidates" in issues  # absent candidates are reported as missing, not invalid


def test_repair_choice_never_converts_an_invented_candidate():
    answer, issues = model.repair_choice({"choice": "zz", "probabilities": {}}, {"a"})
    assert answer is None and issues == ["choice_not_offered:'zz'"]
    assert model.repair_choice("not a dict", {"a"})[0] is None
