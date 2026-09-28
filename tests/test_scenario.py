"""장애 시나리오 파싱·소비 테스트."""

import threading

import pytest

from localqpu.scenario import (
    BackendOverride,
    JobOutcome,
    Scenario,
    ScenarioError,
    ScenarioState,
    parse_scenario,
    scenario_to_json,
)

SPEC_EXAMPLE = {
    "seed": 42,
    "queue": {"delay_seconds": 0, "polls_before_running": 1},
    "failures": {"rate": 0.0, "reason": "Simulated failure", "reason_code": 9999},
    "next_jobs": [
        {"outcome": "failed", "reason": "QPU calibration in progress", "reason_code": 1517},
        {"outcome": "cancelled"},
    ],
    "backends": {"ibm_brisbane": {"status": "offline", "queue_length": 120}},
    "usage": {"limit_seconds": 600, "consumed_seconds": 600},
    "auth": {"reject_tokens": False},
}


def test_empty_scenario_is_default() -> None:
    """빈 객체는 기본 시나리오(즉시 성공)가 된다."""
    assert parse_scenario({}) == Scenario()


def test_spec_example_parses() -> None:
    """설계서 5.7절 예시가 그대로 해석된다."""
    scenario = parse_scenario(SPEC_EXAMPLE)
    assert scenario.seed == 42
    assert scenario.next_jobs[0] == JobOutcome("failed", "QPU calibration in progress", 1517)
    assert scenario.backend_override("ibm_brisbane") == BackendOverride("offline", 120)
    assert scenario.usage.is_exhausted is True


def test_unknown_top_level_key_is_rejected() -> None:
    """모르는 키는 위치와 허용 키를 알려 주며 거절된다."""
    with pytest.raises(ScenarioError, match="알 수 없는 키.*colour"):
        parse_scenario({"colour": "red"})


def test_invalid_outcome_is_rejected() -> None:
    """허용되지 않은 outcome 값은 거절된다."""
    with pytest.raises(ScenarioError, match="outcome"):
        parse_scenario({"next_jobs": [{"outcome": "exploded"}]})


@pytest.mark.parametrize("rate", [-0.1, 1.5, "high"])
def test_invalid_failure_rate_is_rejected(rate: object) -> None:
    """실패 확률은 0~1 사이 숫자여야 한다."""
    with pytest.raises(ScenarioError):
        parse_scenario({"failures": {"rate": rate}})


@pytest.mark.parametrize(
    ("raw", "location"),
    [
        ({"next_jobs": [{"outcome": "failed", "reason_code": "1305"}]}, "reason_code"),
        ({"next_jobs": [{"outcome": "failed", "reason": ["a"]}]}, "reason"),
        ({"queue": {"polls_before_running": True}}, "polls_before_running"),
        ({"queue": {"delay_seconds": float("nan")}}, "delay_seconds"),
        ({"usage": {"limit_seconds": 1.5}}, "limit_seconds"),
    ],
)
def test_wrong_field_types_are_rejected(raw: dict[str, object], location: str) -> None:
    """타입이 틀린 필드는 필드 위치와 함께 거절된다(문자열 숫자, 불리언 정수, NaN 포함)."""
    with pytest.raises(ScenarioError, match=location):
        parse_scenario(raw)


def test_non_boolean_reject_tokens_is_rejected() -> None:
    """auth.reject_tokens는 불리언이어야 한다."""
    with pytest.raises(ScenarioError, match="reject_tokens"):
        parse_scenario({"auth": {"reject_tokens": "yes"}})


def test_round_trip_through_json() -> None:
    """JSON으로 내보낸 시나리오를 다시 읽으면 같은 값이 된다."""
    scenario = parse_scenario(SPEC_EXAMPLE)
    assert parse_scenario(scenario_to_json(scenario)) == scenario


def test_next_jobs_are_consumed_in_order_then_default() -> None:
    """next_jobs를 앞에서부터 소비하고, 다 쓰면 성공을 돌려준다."""
    state = ScenarioState(parse_scenario(SPEC_EXAMPLE))
    assert state.next_outcome().outcome == "failed"
    assert state.next_outcome().outcome == "cancelled"
    assert state.next_outcome() == JobOutcome()
    assert state.current().next_jobs == ()


def test_failure_rate_one_always_fails_with_reason() -> None:
    """실패 확률 1이면 설정한 사유와 코드로 항상 실패한다."""
    state = ScenarioState(
        parse_scenario({"failures": {"rate": 1.0, "reason": "boom", "reason_code": 7}})
    )
    assert state.next_outcome() == JobOutcome("failed", "boom", 7)


def test_same_seed_gives_same_sequence() -> None:
    """시드가 같으면 무작위 실패와 파생 시드의 순서가 같다."""
    raw = {"seed": 3, "failures": {"rate": 0.5}}
    first, second = ScenarioState(parse_scenario(raw)), ScenarioState(parse_scenario(raw))
    assert [first.next_outcome() for _ in range(20)] == [second.next_outcome() for _ in range(20)]
    assert first.derive_seed() == second.derive_seed()


def test_derive_seed_is_none_without_seed() -> None:
    """시드가 없으면 파생 시드도 없다(매번 다른 결과)."""
    assert ScenarioState().derive_seed() is None


def test_reset_restores_initial_scenario() -> None:
    """reset은 처음 받은 시나리오와 소비 전 next_jobs를 되돌린다."""
    state = ScenarioState(parse_scenario(SPEC_EXAMPLE))
    state.next_outcome()
    state.set_scenario(Scenario())
    state.reset()
    assert len(state.current().next_jobs) == 2


def test_concurrent_consumption_is_exact() -> None:
    """여러 스레드가 동시에 소비해도 next_jobs는 정확히 한 번씩만 나간다."""
    state = ScenarioState(parse_scenario({"next_jobs": [{"outcome": "failed"}] * 10}))
    outcomes: list[JobOutcome] = []
    lock = threading.Lock()

    def consume() -> None:
        """결말 하나를 소비해 목록에 넣는다."""
        outcome = state.next_outcome()
        with lock:
            outcomes.append(outcome)

    threads = [threading.Thread(target=consume) for _ in range(50)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sum(outcome.outcome == "failed" for outcome in outcomes) == 10


@pytest.mark.parametrize(
    "raw",
    [{"queue": {"delay_seconds": 10**400}}, {"failures": {"rate": 10**400}}],
)
def test_huge_integer_is_rejected_not_crashed(raw: dict[str, object]) -> None:
    """float로 바꿀 수 없을 만큼 큰 정수도 OverflowError가 아니라 ScenarioError로 거절된다."""
    with pytest.raises(ScenarioError, match="유한한 숫자"):
        parse_scenario(raw)


def test_nested_error_names_list_index() -> None:
    """next_jobs 항목 오류 메시지에 몇 번째 항목인지가 들어간다."""
    with pytest.raises(ScenarioError, match=r"next_jobs\[2\]"):
        parse_scenario({"next_jobs": [{}, {}, {"reason": 5}]})


def test_nested_error_names_backend() -> None:
    """backends 항목 오류 메시지에 어느 백엔드인지가 들어간다."""
    with pytest.raises(ScenarioError, match=r"backends\.ibm_b"):
        parse_scenario({"backends": {"ibm_a": {}, "ibm_b": {"status": "down"}}})


def test_noise_flag_parses_and_round_trips() -> None:
    """noise는 불리언이고, 기본값은 false이며, JSON 왕복에서 유지된다."""
    assert Scenario().noise is False
    scenario = parse_scenario({"noise": True})
    assert scenario.noise is True and parse_scenario(scenario_to_json(scenario)) == scenario
    with pytest.raises(ScenarioError, match="noise"):
        parse_scenario({"noise": "yes"})


def test_http_faults_parse_and_are_consumed_in_order() -> None:
    """http_faults는 목록 순서대로 처음 맞는 규칙이 times만큼 소비되고, reset으로 되돌아간다."""
    state = ScenarioState(
        parse_scenario(
            {
                "http_faults": [
                    {
                        "method": "POST",
                        "path": "/api/v1/jobs",
                        "status": 503,
                        "times": 2,
                        "retry_after": 1,
                    },
                    {"path": "/api/v1/jobs/*/results", "drop_connection": True},
                ]
            }
        )
    )
    assert state.consume_http_fault("GET", "/api/v1/jobs") is None
    first = state.consume_http_fault("POST", "/api/v1/jobs")
    assert first is not None and (first.status, first.retry_after, first.phase) == (
        503,
        1,
        "before",
    )
    assert state.consume_http_fault("POST", "/api/v1/jobs") is not None
    assert state.consume_http_fault("POST", "/api/v1/jobs") is None
    assert state.current().http_faults[0].times == 0
    dropped = state.consume_http_fault("GET", "/api/v1/jobs/abc/results")
    assert dropped is not None and dropped.drop_connection is True
    state.reset()
    assert state.current().http_faults[0].times == 2


@pytest.mark.parametrize(
    ("fault", "location"),
    [
        ({"status": 503}, "path"),
        ({"path": "/x", "status": 99}, "status"),
        ({"path": "/x", "status": 503, "times": 0}, "times"),
        ({"path": "/x", "status": 503, "phase": "during"}, "phase"),
        ({"path": "/x"}, "효과"),
        ({"path": "/x", "status": 503, "colour": "red"}, "colour"),
    ],
)
def test_invalid_http_faults_are_rejected(fault: dict[str, object], location: str) -> None:
    """형식이 틀리거나 아무 효과가 없는 규칙은 위치와 함께 거절된다."""
    with pytest.raises(ScenarioError, match=location):
        parse_scenario({"http_faults": [fault]})


def test_http_faults_round_trip_through_json() -> None:
    """http_faults도 JSON 왕복에서 유지된다."""
    scenario = parse_scenario({"http_faults": [{"path": "/api/*", "delay_seconds": 0.5}]})
    assert parse_scenario(scenario_to_json(scenario)) == scenario
