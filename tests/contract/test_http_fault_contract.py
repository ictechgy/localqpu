"""실제 qiskit-ibm-runtime 클라이언트가 HTTP 수준 장애에 어떻게 반응하는지 검증한다.

클라이언트는 500·502·503·504·52x를 POST까지 최대 5번 재시도하고 429는 재시도하지 않는다
(qiskit_ibm_runtime/api/session.py, 0.50.0).
"""

import pytest
from qiskit_ibm_runtime import QiskitRuntimeService, SamplerV2
from qiskit_ibm_runtime.exceptions import IBMRuntimeError

from localqpu.control_client import LocalqpuControl
from tests.contract.helpers import bell_isa_circuit

pytestmark = pytest.mark.contract


def submit_bell(service: QiskitRuntimeService) -> object:
    """Bell 회로를 제출한 작업."""
    backend = service.backend("ibm_brisbane")
    return SamplerV2(mode=backend).run([bell_isa_circuit(backend)], shots=20)


def job_submission_statuses(control: LocalqpuControl) -> list[int | None]:
    """요청 기록에 남은 작업 제출(POST /api/v1/jobs)의 상태 코드 순서."""
    return [
        entry["status"]
        for entry in control.requests()
        if entry["method"] == "POST" and entry["path"] == "/api/v1/jobs"
    ]


def test_client_recovers_from_transient_503(
    localqpu_service: QiskitRuntimeService, localqpu_control: LocalqpuControl
) -> None:
    """제출이 두 번 503이어도 클라이언트가 재시도해 성공하고, 요청 기록에 세 번 남는다."""
    localqpu_control.set_scenario(
        {"http_faults": [{"method": "POST", "path": "/api/v1/jobs", "status": 503, "times": 2}]}
    )
    assert submit_bell(localqpu_service).result(timeout=120)[0].data.meas.num_shots == 20
    assert job_submission_statuses(localqpu_control) == [503, 503, 200]


def test_client_gives_up_after_retry_budget(
    localqpu_service: QiskitRuntimeService, localqpu_control: LocalqpuControl
) -> None:
    """503이 재시도 한도(5회)를 넘으면 IBMRuntimeError가 난다."""
    localqpu_control.set_scenario(
        {"http_faults": [{"method": "POST", "path": "/api/v1/jobs", "status": 503, "times": 10}]}
    )
    with pytest.raises(IBMRuntimeError):
        submit_bell(localqpu_service)
    assert job_submission_statuses(localqpu_control) == [503] * 6


def test_rate_limit_is_not_retried(
    localqpu_service: QiskitRuntimeService, localqpu_control: LocalqpuControl
) -> None:
    """429는 재시도하지 않아 곧바로 IBMRuntimeError가 난다(사용자 코드가 처리해야 함)."""
    localqpu_control.set_scenario(
        {
            "http_faults": [
                {"method": "POST", "path": "/api/v1/jobs", "status": 429, "retry_after": 1}
            ]
        }
    )
    with pytest.raises(IBMRuntimeError, match="429"):
        submit_bell(localqpu_service)
    assert job_submission_statuses(localqpu_control) == [429]


def test_lost_response_causes_duplicate_submission(
    localqpu_service: QiskitRuntimeService, localqpu_control: LocalqpuControl
) -> None:
    """서버가 처리했는데 응답이 유실되면(phase=after) 클라이언트 재시도로 작업이 두 번 제출된다."""
    localqpu_control.set_scenario(
        {
            "http_faults": [
                {"method": "POST", "path": "/api/v1/jobs", "status": 503, "phase": "after"}
            ]
        }
    )
    submit_bell(localqpu_service).result(timeout=120)
    assert job_submission_statuses(localqpu_control) == [503, 200]
    assert len(localqpu_control.jobs()) == 2


def test_client_recovers_from_dropped_status_poll(
    localqpu_service: QiskitRuntimeService, localqpu_control: LocalqpuControl
) -> None:
    """상태 조회 연결이 한 번 끊겨도 클라이언트가 다시 조회해 결과를 받는다."""
    localqpu_control.set_scenario(
        {
            "http_faults": [
                {"method": "GET", "path": "/api/v1/jobs/localqpu-*", "drop_connection": True}
            ]
        }
    )
    assert submit_bell(localqpu_service).result(timeout=120)[0].data.meas.num_shots == 20
    assert any(entry["fault"] == "drop_connection" for entry in localqpu_control.requests())
