from __future__ import annotations

from unittest.mock import MagicMock, patch
from fastapi.testclient import TestClient
from pydantic import JsonValue, TypeAdapter
import pytest

from providers.decision_provider import (
    ChoiceAnswer,
    DecisionProvider,
    DecisionRequest,
    DecisionResult,
    PredicateAnswer,
)
from testlab.decision_lab import (
    DECISION_SCENARIOS,
    compute_benchmark_summary,
    decision_scenario_catalog,
    get_decision_scenario,
)
from testlab.server import app


def test_decision_scenario_catalog() -> None:
    catalog = decision_scenario_catalog()
    assert len(catalog) == 10
    scenario = get_decision_scenario("hybrid-image-router-complex")
    assert scenario.id == "hybrid-image-router-complex"
    assert len(scenario.questions) == 4


def test_decision_lab_page_and_catalog_routes() -> None:
    client = TestClient(app)

    index_res = client.get("/")
    assert index_res.status_code == 200
    assert "/decision-lab" in index_res.text

    page_res = client.get("/decision-lab")
    assert page_res.status_code == 200
    assert "Decision Provider Lab" in page_res.text
    assert "Multi-Provider Latency & Accuracy Benchmark" in page_res.text

    cat_res = client.get("/api/decision-lab/catalog")
    assert cat_res.status_code == 200
    data = cat_res.json()
    provider_ids = {p["id"] for p in data["providers"]}
    assert {"openai-decisions", "gemini-2-5-decisions", "gemini-3-decisions"}.issubset(provider_ids)
    assert len(data["scenarios"]) == 10


def test_decision_lab_walkthrough_evaluates_all_scenarios() -> None:
    client = TestClient(app)
    expected_badges = {
        "hybrid-image-router-complex": "COMPLEX ROUTE",
        "hybrid-image-router-fast": "FAST PATH ($0.009)",
        "animation-technique-planner": "TECHNIQUE: LAYERED",
        "music-catalog-reranker": "CATALOG HIT ($0.00)",
        "reference-tag-and-voice-casting": "REFERENCES CAST",
        "game-move-narration-gate": "NARRATION TRIGGERED",
        "text-beautifier-span-selector": "SPAN: SPAN_2",
        "beautifier-ranked-short": "SPANS: DEAFENING CRACK | RUN NOW",
        "beautifier-ranked-line": "SPANS: YOU SHALL NOT PASS!",
        "beautifier-ranked-quiet": "SPANS: NONE",
    }

    for scenario in DECISION_SCENARIOS:
        response = client.post(
            "/api/decision-lab/evaluate",
            json={
                "mode": "walkthrough",
                "scenario_id": scenario.id,
            },
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["mode"] == "walkthrough"
        assert len(payload["answers"]) == len(scenario.questions)
        assert payload["narratron_outcome"]["badge"] == expected_badges[scenario.id]
        assert len(payload["narratron_outcome"]["trace"]) >= 3


def test_decision_lab_live_mode_and_custom_questions() -> None:
    client = TestClient(app)
    mock_provider = MagicMock(spec=DecisionProvider)
    mock_provider.decide.return_value = DecisionResult(
        provider="openai-decisions",
        model="gpt-5-luna",
        request_id="live_dec_1",
        answers={
            "is_boss_encounter": PredicateAnswer(name="is_boss_encounter", probability=0.91),
            "combat_tier": ChoiceAnswer(
                name="combat_tier",
                choice="deadly",
                probabilities={"skirmish": 0.05, "deadly": 0.95},
                confidence=0.94,
            ),
        },
        usage={"input_tokens": 44, "output_tokens": 0},
    )

    with patch("testlab.decision_lab.get_decision_provider", return_value=mock_provider):
        response = client.post(
            "/api/decision-lab/evaluate",
            json={
                "provider_id": "openai-decisions",
                "mode": "live",
                "input_text": "An ancient lich rises from the obsidian throne.",
                "questions": [
                    {
                        "type": "predicate",
                        "name": "is_boss_encounter",
                        "instructions": "Is this a boss encounter?",
                    },
                    {
                        "type": "choice",
                        "name": "combat_tier",
                        "instructions": "Select the encounter tier.",
                        "choices": ["skirmish", "deadly"],
                    },
                ],
            },
        )

    assert response.status_code == 200
    data = response.json()
    assert data["mode"] == "live"
    assert data["provider"] == "openai-decisions"
    assert data["request_id"] == "live_dec_1"
    assert data["answers"][0]["name"] == "is_boss_encounter"
    assert data["answers"][0]["passed"] is True
    assert data["answers"][1]["choice"] == "deadly"
    assert data["narratron_outcome"]["badge"] == "CUSTOM DECISION"


def test_compute_benchmark_summary_calculates_percentiles_and_speedup() -> None:
    scenario = get_decision_scenario("hybrid-image-router-fast")
    items: list[dict[str, JsonValue]] = [
        {
            "provider_id": "openai-decisions",
            "scenario_id": scenario.id,
            "status": "completed",
            "latency_ms": 80.0,
            "outcome_badge": "FAST PATH ($0.009)",
            "usage": {"input_tokens": 90, "output_tokens": 0},
        },
        {
            "provider_id": "openai-decisions",
            "scenario_id": scenario.id,
            "status": "completed",
            "latency_ms": 100.0,
            "outcome_badge": "FAST PATH ($0.009)",
            "usage": {"input_tokens": 90, "output_tokens": 0},
        },
        {
            "provider_id": "gemini-2-5-decisions",
            "scenario_id": scenario.id,
            "status": "completed",
            "latency_ms": 340.0,
            "outcome_badge": "FAST PATH ($0.009)",
            "usage": {"prompt_token_count": 110, "candidates_token_count": 28},
        },
        {
            "provider_id": "gemini-2-5-decisions",
            "scenario_id": scenario.id,
            "status": "completed",
            "latency_ms": 380.0,
            "outcome_badge": "COMPLEX ROUTE",
            "usage": {"prompt_token_count": 110, "candidates_token_count": 32},
        },
    ]

    summary = compute_benchmark_summary(
        ["openai-decisions", "gemini-2-5-decisions"],
        [scenario],
        items,
    )
    providers_summary = TypeAdapter(dict[str, dict[str, JsonValue]]).validate_python(summary["providers"])
    openai_stats = providers_summary["openai-decisions"]
    gemini_stats = providers_summary["gemini-2-5-decisions"]

    assert openai_stats["mean_ms"] == pytest.approx(90.0)
    assert openai_stats["p50_ms"] == pytest.approx(90.0)
    assert openai_stats["mean_output_tokens"] == pytest.approx(0.0)
    assert openai_stats["outcome_match_rate"] == pytest.approx(1.0)
    assert openai_stats["speedup_vs_slowest"] == pytest.approx(4.0)

    assert gemini_stats["mean_ms"] == pytest.approx(360.0)
    assert gemini_stats["mean_output_tokens"] == pytest.approx(30.0)
    assert gemini_stats["outcome_match_rate"] == pytest.approx(0.5)
    assert gemini_stats["speedup_vs_slowest"] == pytest.approx(1.0)


def test_decision_benchmark_run_endpoints() -> None:
    client = TestClient(app)
    scenario = get_decision_scenario("hybrid-image-router-fast")

    def _fake_get_provider(
        provider_id: str,
        options: dict[str, JsonValue] | None = None,
    ) -> DecisionProvider:
        mock_prov = MagicMock(spec=DecisionProvider)
        mock_prov.decide.return_value = DecisionResult(
            provider=provider_id,
            model="gpt-5-luna" if provider_id == "openai-decisions" else "gemini-3.1-flash-lite-preview",
            request_id=f"req_{provider_id}",
            answers={
                q.name: q.parse_wire_answer(wire_ans)
                for q, wire_ans in zip(scenario.questions, scenario.sample_wire_answers, strict=True)
            },
            usage={"input_tokens": 64, "output_tokens": 0 if provider_id == "openai-decisions" else 24},
        )
        return mock_prov

    with patch("testlab.decision_lab.get_decision_provider", side_effect=_fake_get_provider):
        start_res = client.post(
            "/api/decision-lab/benchmark/runs",
            json={
                "provider_ids": ["openai-decisions", "gemini-2-5-decisions"],
                "scenario_ids": ["hybrid-image-router-fast"],
                "repetitions": 2,
                "synchronous": True,
            },
        )

    assert start_res.status_code == 200
    run_data = start_res.json()
    assert run_data["status"] == "completed"
    assert run_data["total"] == 4
    assert len(run_data["items"]) == 4

    run_id = run_data["id"]
    poll_res = client.get(f"/api/decision-lab/benchmark/runs/{run_id}")
    assert poll_res.status_code == 200
    polled = poll_res.json()
    assert polled["id"] == run_id
    assert polled["summary"]["providers"]["openai-decisions"]["completed"] == 2
    assert polled["summary"]["providers"]["gemini-2-5-decisions"]["completed"] == 2
    assert polled["summary"]["providers"]["openai-decisions"]["outcome_match_rate"] == pytest.approx(1.0)


def test_decision_lab_validation_errors() -> None:
    client = TestClient(app)

    bad_scenario = client.post(
        "/api/decision-lab/evaluate",
        json={"mode": "walkthrough", "scenario_id": "does-not-exist"},
    )
    assert bad_scenario.status_code == 400
    assert "Unknown decision scenario" in bad_scenario.json()["detail"]

    empty_input = client.post(
        "/api/decision-lab/evaluate",
        json={
            "mode": "walkthrough",
            "input_text": "   ",
            "questions": [
                {"type": "predicate", "name": "q1", "instructions": "Check condition"}
            ],
        },
    )
    assert empty_input.status_code == 400
    assert "input_text cannot be empty" in empty_input.json()["detail"]

    bad_bench = client.post(
        "/api/decision-lab/benchmark/runs",
        json={
            "provider_ids": ["unknown-provider"],
            "scenario_ids": ["hybrid-image-router-fast"],
            "repetitions": 1,
        },
    )
    assert bad_bench.status_code == 400
    assert "Unknown decision provider" in bad_bench.json()["detail"]


def test_ranked_span_live_mode_regenerates_candidates_for_edited_text() -> None:
    def decide(request: DecisionRequest) -> DecisionResult:
        assert request.input_text == "Quiet daylight."
        assert len(request.questions) == 3
        return DecisionResult(
            provider="test", model="test",
            answers={question.name: PredicateAnswer(name=question.name, probability=0.1)
                     for question in request.questions},
        )

    provider = MagicMock(spec=DecisionProvider)
    provider.decide.side_effect = decide
    with patch("testlab.decision_lab.get_decision_provider", return_value=provider):
        response = TestClient(app).post("/api/decision-lab/evaluate", json={
            "mode": "live", "scenario_id": "beautifier-ranked-short",
            "input_text": "Quiet daylight.",
        })
    assert response.status_code == 200
    outcome = response.json()["narratron_outcome"]
    assert outcome["badge"] == "SPANS: NONE"
    assert len(outcome["candidate_ranking"]) == 3
    assert outcome["preview_segments"] == [{"text": "Quiet daylight.", "highlight": False}]


def test_ranked_span_benchmark_retains_rankings_and_preview() -> None:
    scenario = get_decision_scenario("beautifier-ranked-short")
    provider = MagicMock(spec=DecisionProvider)
    provider.decide.return_value = DecisionResult(
        provider="test", model="test",
        answers={q.name: q.parse_wire_answer(answer)
                 for q, answer in zip(scenario.questions, scenario.sample_wire_answers, strict=True)},
    )
    with patch("testlab.decision_lab.get_decision_provider", return_value=provider):
        response = TestClient(app).post("/api/decision-lab/benchmark/runs", json={
            "provider_ids": ["openai-decisions"], "scenario_ids": [scenario.id],
            "repetitions": 1, "synchronous": True,
        })
    assert response.status_code == 200
    item = response.json()["items"][0]
    assert item["status"] == "completed"
    assert item["outcome_badge"] == scenario.expected_badge
    outcome = item["narratron_outcome"]
    assert len(outcome["candidate_ranking"]) == 38
    assert sum(segment["highlight"] for segment in outcome["preview_segments"]) == 2
