"""작업 관리자 테스트. 실제 시뮬레이션 대신 가짜 실행 함수를 주입한다."""

import logging
import threading
import time
from typing import Any

import pytest

from localqpu.constants import LOCALQPU_ERROR_CODE
from localqpu.jobs import JobManager, JobRecord
from localqpu.programs.base import (
    ExecutionSettings,
    ProgramInputError,
    ProgramOutput,
    ProgramRunner,
    UnsupportedProgramError,
)
from localqpu.scenario import Scenario, ScenarioState, parse_scenario

OK_OUTPUT = ProgramOutput(payload='{"ok": true}', is_stub=False, active_qubits=2)


class FakeClock:
    """테스트가 직접 시간을 넘기는 시계."""

    def __init__(self) -> None:
        """0초에서 시작한다."""
        self.now = 0.0

    def __call__(self) -> float:
        """현재 시각."""
        return self.now


def make_runner(
    error: Exception | None = None, gate: threading.Event | None = None
) -> ProgramRunner:
    """gate가 열릴 때까지 기다렸다가 error를 올리거나 OK_OUTPUT을 돌려주는 실행 함수."""

    def runner(params: dict[str, Any], settings: ExecutionSettings) -> ProgramOutput:
        """가짜 실행."""
        if gate is not None:
            gate.wait(5)
        if error is not None:
            raise error
        return OK_OUTPUT

    return runner


def make_manager(
    scenario: Scenario | None = None,
    runner: ProgramRunner | None = None,
    clock: FakeClock | None = None,
) -> JobManager:
    """가짜 실행 함수를 쓰는 작업 관리자."""
    chosen_runner = runner or make_runner()
    return JobManager(
        ScenarioState(scenario),
        max_sim_qubits=24,
        runner_lookup=lambda program_id: chosen_runner,
        clock=clock or time.monotonic,
    )


def poll_until_final(manager: JobManager, job_id: str, timeout: float = 5.0) -> JobRecord:
    """작업이 끝날 때까지 폴링한다."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = manager.poll(job_id)
        assert job is not None
        if job.is_final:
            return job
        time.sleep(0.01)
    raise AssertionError(f"{job_id}가 {timeout}초 안에 끝나지 않았습니다.")


def test_first_poll_is_queued_then_completes() -> None:
    """첫 조회는 Queued, 이후 Running을 거쳐 Completed가 된다."""
    gate = threading.Event()
    manager = make_manager(runner=make_runner(gate=gate))
    job = manager.submit("sampler", "ibm_brisbane", {})
    assert manager.poll(job.job_id).status == "Queued"
    assert manager.poll(job.job_id).status == "Running"
    gate.set()
    finished = poll_until_final(manager, job.job_id)
    assert finished.status == "Completed" and finished.result_payload == OK_OUTPUT.payload


def test_planned_failure_skips_runner() -> None:
    """시나리오가 실패를 정하면 실행 없이 사유와 코드를 달고 실패한다."""
    runner_calls: list[str] = []

    def recording_runner(params: dict[str, Any], settings: ExecutionSettings) -> ProgramOutput:
        """호출을 기록한다."""
        runner_calls.append("called")
        return OK_OUTPUT

    scenario = parse_scenario(
        {"next_jobs": [{"outcome": "failed", "reason": "calibrating", "reason_code": 1517}]}
    )
    manager = make_manager(scenario, runner=recording_runner)
    job = poll_until_final(manager, manager.submit("sampler", "ibm_brisbane", {}).job_id)
    assert (job.status, job.reason, job.reason_code) == ("Failed", "calibrating", 1517)
    assert runner_calls == []


def test_planned_failure_without_reason_gets_default_reason() -> None:
    """사유 없는 실패에도 사람이 읽을 사유가 붙는다."""
    manager = make_manager(parse_scenario({"next_jobs": [{"outcome": "failed"}]}))
    job = poll_until_final(manager, manager.submit("sampler", "ibm_brisbane", {}).job_id)
    assert job.reason and "시나리오" in job.reason


def test_planned_cancellation_has_reason() -> None:
    """시나리오가 취소를 정하면 Cancelled가 되고, 사유가 없어도 기본 사유와 코드가 남는다."""
    manager = make_manager(
        parse_scenario({"next_jobs": [{"outcome": "cancelled", "reason_code": 1305}]})
    )
    job = poll_until_final(manager, manager.submit("sampler", "ibm_brisbane", {}).job_id)
    assert job.status == "Cancelled" and job.reason and job.reason_code == 1305


def test_cancel_after_completion_is_already_final() -> None:
    """실행이 끝난 뒤라면 다음 조회 전이라도 취소는 already_final이다."""
    gate = threading.Event()
    manager = make_manager(runner=make_runner(gate=gate))
    job = manager.submit("sampler", "ibm_brisbane", {})
    manager.poll(job.job_id)
    assert manager.poll(job.job_id).status == "Running"
    gate.set()
    deadline = time.monotonic() + 5
    while manager.get(job.job_id).status != "Completed":
        assert time.monotonic() < deadline, "실행이 끝나지 않았습니다."
        time.sleep(0.01)
    assert manager.cancel(job.job_id) == "already_final"


def test_stub_completion_is_logged(caplog: pytest.LogCaptureFixture) -> None:
    """stub 결과로 끝난 작업은 작업 ID와 함께 경고 로그를 남긴다."""
    stub_output = ProgramOutput(payload="{}", is_stub=True, active_qubits=30)
    manager = make_manager(runner=lambda params, settings: stub_output)
    with caplog.at_level(logging.WARNING, logger="localqpu.jobs"):
        job = poll_until_final(manager, manager.submit("sampler", "ibm_brisbane", {}).job_id)
    assert job.is_stub and job.job_id in caplog.text and "stub" in caplog.text


def test_input_error_becomes_failure_with_message() -> None:
    """입력 오류는 메시지 그대로 실패 사유가 되고 localqpu 코드가 붙는다."""
    manager = make_manager(runner=make_runner(error=ProgramInputError("회로를 줄이세요")))
    job = poll_until_final(manager, manager.submit("sampler", "ibm_brisbane", {}).job_id)
    assert (job.status, job.reason, job.reason_code) == (
        "Failed",
        "회로를 줄이세요",
        LOCALQPU_ERROR_CODE,
    )


def test_unexpected_error_is_logged_and_fails(caplog: pytest.LogCaptureFixture) -> None:
    """예상 못 한 예외는 로그로 남고 작업은 요약 사유로 실패한다."""
    manager = make_manager(runner=make_runner(error=RuntimeError("kaboom")))
    with caplog.at_level(logging.ERROR, logger="localqpu.jobs"):
        job = poll_until_final(manager, manager.submit("sampler", "ibm_brisbane", {}).job_id)
    assert job.status == "Failed" and "kaboom" in (job.reason or "")
    assert "kaboom" in caplog.text


def test_delay_holds_job_until_clock_passes() -> None:
    """delay_seconds가 지나기 전에는 몇 번을 조회해도 Queued다."""
    clock = FakeClock()
    manager = make_manager(parse_scenario({"queue": {"delay_seconds": 10}}), clock=clock)
    job = manager.submit("sampler", "ibm_brisbane", {})
    for _ in range(5):
        assert manager.poll(job.job_id).status == "Queued"
    clock.now = 10.0
    assert poll_until_final(manager, job.job_id).status == "Completed"


def test_offline_backend_holds_job_until_online() -> None:
    """백엔드가 오프라인이면 대기하고, 온라인이 되면 다음 조회에서 진행한다."""
    state = ScenarioState(parse_scenario({"backends": {"ibm_brisbane": {"status": "offline"}}}))
    manager = JobManager(state, 24, runner_lookup=lambda program_id: make_runner())
    job = manager.submit("sampler", "ibm_brisbane", {})
    for _ in range(5):
        assert manager.poll(job.job_id).status == "Queued"
    state.set_scenario(Scenario())
    assert poll_until_final(manager, job.job_id).status == "Completed"


def test_cancel_results() -> None:
    """대기 중 취소는 성공, 끝난 작업은 already_final, 없는 작업은 not_found."""
    manager = make_manager(parse_scenario({"queue": {"polls_before_running": 100}}))
    job = manager.submit("sampler", "ibm_brisbane", {})
    assert manager.cancel(job.job_id) == "cancelled"
    assert manager.get(job.job_id).status == "Cancelled"
    assert manager.cancel(job.job_id) == "already_final"
    assert manager.cancel("missing") == "not_found"


def test_unsupported_program_is_rejected_before_registration() -> None:
    """지원하지 않는 프로그램은 등록 전에 거절한다."""

    def reject(program_id: str) -> ProgramRunner:
        """모든 프로그램을 거절한다."""
        raise UnsupportedProgramError(program_id)

    manager = JobManager(ScenarioState(), 24, runner_lookup=reject)
    with pytest.raises(UnsupportedProgramError):
        manager.submit("estimator", "ibm_brisbane", {})
    assert manager.list_jobs() == []


def test_concurrent_submissions_consume_next_jobs_once() -> None:
    """동시에 50개를 제출해도 실패 예정 10개가 정확히 배정되고 ID가 겹치지 않는다."""
    manager = make_manager(parse_scenario({"next_jobs": [{"outcome": "failed"}] * 10}))
    threads = [
        threading.Thread(target=manager.submit, args=("sampler", "ibm_brisbane", {}))
        for _ in range(50)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    jobs = manager.list_jobs()
    assert len({job.job_id for job in jobs}) == 50
    assert sum(job.planned.outcome == "failed" for job in jobs) == 10


def test_reset_clears_jobs_and_restores_scenario() -> None:
    """reset은 작업 기록을 비우고 소비된 next_jobs를 되돌린다."""
    manager = make_manager(parse_scenario({"next_jobs": [{"outcome": "failed"}]}))
    assert manager.submit("sampler", "ibm_brisbane", {}).planned.outcome == "failed"
    manager.reset()
    assert manager.list_jobs() == []
    assert manager.submit("sampler", "ibm_brisbane", {}).planned.outcome == "failed"
