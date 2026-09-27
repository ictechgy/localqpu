"""작업 관리자.

작업 상태는 클라이언트가 조회(poll)할 때만 진행한다. 실제 IBM처럼 첫 조회에서 Queued를 보이고,
시나리오의 대기 조건을 만족하면 대기열을 벗어나 실행(스레드 풀) 또는 예정된 실패·취소로 간다.
작업 기록은 메모리에만 보관한다.
"""

from __future__ import annotations

import dataclasses
import datetime
import logging
import threading
import time
import uuid
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Literal

from localqpu.constants import LOCALQPU_CANCEL_CODE, LOCALQPU_ERROR_CODE
from localqpu.programs import find_program_runner
from localqpu.programs.base import (
    ExecutionSettings,
    ProgramInputError,
    ProgramOutput,
    ProgramRunner,
)
from localqpu.scenario import JobOutcome, ScenarioState

#: 모듈 로거. 예상 못 한 실행 오류의 스택을 남긴다.
logger = logging.getLogger(__name__)

#: IBM API의 state.status 값. 클라이언트 매핑표(API_TO_JOB_STATUS)와 일치한다.
JobStatus = Literal["Queued", "Running", "Completed", "Failed", "Cancelled"]

#: 더 이상 바뀌지 않는 상태.
FINAL_STATUSES: frozenset[str] = frozenset({"Completed", "Failed", "Cancelled"})

#: cancel()의 결과.
CancelResult = Literal["cancelled", "not_found", "already_final"]

#: program_id로 실행 함수를 찾는 함수. 테스트에서 가짜 실행 함수를 주입하려고 분리했다.
RunnerLookup = Callable[[str], ProgramRunner]

#: 현재 시각(초)을 돌려주는 함수. 테스트에서 시간을 직접 넘기려고 주입한다.
Clock = Callable[[], float]

#: 사유 없이 실패가 예정된 작업에 붙이는 사유.
_DEFAULT_PLANNED_FAILURE_REASON = "localqpu 시나리오가 지정한 실패입니다."

#: 사유 없이 취소가 예정된 작업에 붙이는 사유.
_DEFAULT_PLANNED_CANCEL_REASON = "localqpu 시나리오가 지정한 취소입니다."

#: 사용자가 취소한 작업에 붙이는 사유.
_USER_CANCEL_REASON = "localqpu: 사용자가 작업을 취소했습니다."


@dataclass
class JobRecord:
    """작업 하나의 상태. 응답 형식으로 바꾸는 일은 IBM 라우트가 맡는다."""

    job_id: str
    program_id: str
    backend_name: str
    params: dict[str, Any] = field(repr=False)
    planned: JobOutcome
    settings: ExecutionSettings
    submitted_at: float
    created: str
    status: JobStatus = "Queued"
    reason: str | None = None
    reason_code: int | None = None
    polls: int = 0
    result_payload: str | None = field(default=None, repr=False)
    is_stub: bool = False
    entangled_qubits: int | None = None
    future: Future[ProgramOutput] | None = field(default=None, repr=False, compare=False)

    @property
    def is_final(self) -> bool:
        """더 이상 상태가 바뀌지 않는지."""
        return self.status in FINAL_STATUSES


class JobManager:
    """작업을 등록하고 폴링에 맞춰 진행시킨다. 모든 공개 메서드는 스레드 안전하다."""

    def __init__(
        self,
        scenario_state: ScenarioState,
        max_sim_qubits: int,
        runner_lookup: RunnerLookup = find_program_runner,
        clock: Clock = time.monotonic,
        max_workers: int = 2,
    ) -> None:
        """시나리오 상태, 시뮬레이션 한도, 실행 함수 조회, 시계, 동시 실행 수를 받는다."""
        self._scenario_state = scenario_state
        self._max_sim_qubits = max_sim_qubits
        self._runner_lookup = runner_lookup
        self._clock = clock
        self._max_workers = max_workers
        self._pool = self._new_pool()
        self._lock = threading.Lock()
        self._jobs: dict[str, JobRecord] = {}

    def submit(self, program_id: str, backend_name: str, params: dict[str, Any]) -> JobRecord:
        """작업을 등록한다. 결말과 시드는 제출 순서대로 정해져 재현 가능하다.

        Raises:
            UnsupportedProgramError: 흉내 내지 않는 프로그램일 때(등록하지 않는다).
        """
        self._runner_lookup(program_id)
        # 결말 소비·시드 파생·등록을 한 잠금 안에서 해 reset과 섞이지 않게 한다.
        with self._lock:
            job = JobRecord(
                job_id=f"localqpu-{uuid.uuid4().hex[:12]}",
                program_id=program_id,
                backend_name=backend_name,
                params=params,
                planned=self._scenario_state.next_outcome(),
                settings=ExecutionSettings(
                    self._max_sim_qubits, self._scenario_state.derive_seed()
                ),
                submitted_at=self._clock(),
                created=_utc_now_iso(),
            )
            self._jobs[job.job_id] = job
            return dataclasses.replace(job)

    def poll(self, job_id: str) -> JobRecord | None:
        """조회 한 번을 기록하고 상태를 진행시킨 뒤 사본을 돌려준다."""
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return None
            job.polls += 1
            self._advance(job)
            return dataclasses.replace(job)

    def get(self, job_id: str) -> JobRecord | None:
        """조회 횟수를 늘리지 않고 사본을 돌려준다. 끝난 실행 결과는 반영한다."""
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return None
            self._settle(job)
            return dataclasses.replace(job)

    def list_jobs(self) -> list[JobRecord]:
        """제출 순서대로 모든 작업의 사본. 끝난 실행 결과는 반영한다."""
        with self._lock:
            for job in self._jobs.values():
                self._settle(job)
            return [dataclasses.replace(job) for job in self._jobs.values()]

    def cancel(self, job_id: str) -> CancelResult:
        """작업을 취소한다. 이미 끝난 작업은 취소하지 않는다."""
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return "not_found"
            self._settle(job)
            if job.is_final:
                return "already_final"
            if job.future is not None:
                job.future.cancel()
            job.status, job.reason, job.reason_code = (
                "Cancelled",
                _USER_CANCEL_REASON,
                LOCALQPU_CANCEL_CODE,
            )
            job.params = {}
            return "cancelled"

    def reset(self) -> None:
        """작업 기록을 지우고 시나리오를 처음 상태로 되돌린다.

        제출과 같은 잠금 안에서 해야, 제출 도중의 reset이 이미 소비된 next_jobs를
        되살리면서 그 작업도 남기는 경쟁이 생기지 않는다. 실행 중인 작업은 결과를 버린다.

        이미 실행 중인 시뮬레이션은 중간에 멈출 수 없으므로, 스레드 풀을 새로 만들어
        이전 작업이 워커를 차지한 채로 다음 테스트의 작업을 굶기지 않게 한다.
        이전 스레드는 계산을 마칠 때까지 CPU를 쓰지만 결과는 버려진다.
        """
        with self._lock:
            for job in self._jobs.values():
                if job.future is not None:
                    job.future.cancel()
            self._jobs.clear()
            self._scenario_state.reset()
            self._pool.shutdown(wait=False, cancel_futures=True)
            self._pool = self._new_pool()

    def _new_pool(self) -> ThreadPoolExecutor:
        """작업 실행용 스레드 풀."""
        return ThreadPoolExecutor(max_workers=self._max_workers, thread_name_prefix="localqpu-job")

    def shutdown(self) -> None:
        """스레드 풀을 정리한다. 서버 종료 시 부른다."""
        self._pool.shutdown(wait=False, cancel_futures=True)

    def _advance(self, job: JobRecord) -> None:
        """잠금을 잡은 상태에서 상태를 한 단계씩 진행시킨다."""
        if job.status == "Queued" and self._is_ready(job):
            self._leave_queue(job)
        self._settle(job)

    def _settle(self, job: JobRecord) -> None:
        """실행이 끝났는데 아직 반영하지 않은 결과를 반영한다. 조회 없이도 완료가 보이게 한다."""
        if job.status == "Running" and job.future is not None and job.future.done():
            self._finish(job)

    def _is_ready(self, job: JobRecord) -> bool:
        """대기열을 벗어날 조건(조회 횟수, 대기 시간, 백엔드 온라인)을 모두 만족하는지."""
        scenario = self._scenario_state.current()
        has_waited = self._clock() - job.submitted_at >= scenario.queue.delay_seconds
        is_online = scenario.backend_override(job.backend_name).status != "offline"
        return is_online and has_waited and job.polls > scenario.queue.polls_before_running

    def _leave_queue(self, job: JobRecord) -> None:
        """예정된 결말을 적용하거나 실행을 시작한다. 입력 params는 여기서 넘기고 기록에서 비운다.

        params에는 QPY 회로가 들어 있어 크기가 크므로, 오래 도는 서버가 끝난 작업의 입력을
        계속 들고 있지 않게 한다.
        """
        params, job.params = job.params, {}
        if job.planned.outcome == "failed":
            reason = job.planned.reason or _DEFAULT_PLANNED_FAILURE_REASON
            job.status, job.reason, job.reason_code = "Failed", reason, job.planned.reason_code
            return
        if job.planned.outcome == "cancelled":
            # 클라이언트는 reason이 있을 때만 reason_code를 저장하므로(base_runtime_job.py) 사유를 항상 채운다.
            reason = job.planned.reason or _DEFAULT_PLANNED_CANCEL_REASON
            job.status, job.reason, job.reason_code = "Cancelled", reason, job.planned.reason_code
            return
        job.status = "Running"
        job.future = self._pool.submit(self._runner_lookup(job.program_id), params, job.settings)

    def _finish(self, job: JobRecord) -> None:
        """끝난 실행의 결과나 오류를 작업에 반영한다."""
        assert job.future is not None
        try:
            output = job.future.result()
        except ProgramInputError as error:
            # 사용자에게는 사유만 보이고, 원인 예외(스택)는 개발자 로그로 남긴다.
            logger.info("작업 %s 입력 오류: %s", job.job_id, error, exc_info=error)
            self._fail(job, str(error))
        except Exception as error:
            logger.exception("작업 %s 실행 중 예상하지 못한 오류", job.job_id)
            self._fail(
                job, f"localqpu 내부 오류({type(error).__name__}: {error}). 서버 로그를 확인하세요."
            )
        else:
            job.status, job.result_payload = "Completed", output.payload
            job.is_stub, job.entangled_qubits = output.is_stub, output.entangled_qubits
            if output.is_stub:
                logger.warning(
                    "작업 %s: 얽힌 큐비트 %d개가 한도(%d)를 넘어 stub 결과를 돌려줍니다.",
                    job.job_id,
                    output.entangled_qubits,
                    job.settings.max_sim_qubits,
                )

    def _fail(self, job: JobRecord, reason: str) -> None:
        """localqpu 사유 코드로 작업을 실패시킨다."""
        job.status, job.reason, job.reason_code = "Failed", reason, LOCALQPU_ERROR_CODE


def _utc_now_iso() -> str:
    """IBM 응답과 같은 UTC ISO-8601 문자열(끝에 Z)."""
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
