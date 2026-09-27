"""실제 클라이언트가 장애 시나리오에 설계서대로 반응하는지 검증한다."""

from collections.abc import Iterator

import pytest
from qiskit.providers.exceptions import QiskitBackendNotFoundError
from qiskit_ibm_runtime import QiskitRuntimeService, SamplerV2
from qiskit_ibm_runtime.accounts.exceptions import InvalidAccountError
from qiskit_ibm_runtime.exceptions import (
    IBMRuntimeError,
    RuntimeInvalidStateError,
    RuntimeJobFailureError,
    RuntimeJobMaxTimeoutError,
)
from qiskit_ibm_runtime.executor_sampler import Sampler as ExecutorSampler

from localqpu import ServerConfig, connect, start_server
from localqpu.control_client import LocalqpuControl
from localqpu.server import RunningServer
from tests.contract.helpers import bell_isa_circuit

pytestmark = pytest.mark.contract


def run_bell(service: QiskitRuntimeService, shots: int = 50) -> object:
    """ibm_brisbane에 Bell 회로를 제출한 작업."""
    backend = service.backend("ibm_brisbane")
    return SamplerV2(mode=backend).run([bell_isa_circuit(backend)], shots=shots)


@pytest.fixture
def tiny_limit_server() -> Iterator[RunningServer]:
    """정확 시뮬레이션 한도가 1큐비트인 별도 서버(stub·한도 초과 검증용)."""
    server = start_server(ServerConfig(port=0, max_sim_qubits=1))
    yield server
    server.stop()


def test_planned_failure_surfaces_reason(
    localqpu_service: QiskitRuntimeService, localqpu_control: LocalqpuControl
) -> None:
    """예정된 실패는 사유와 함께 RuntimeJobFailureError가 된다."""
    localqpu_control.set_scenario(
        {
            "next_jobs": [
                {"outcome": "failed", "reason": "QPU calibration in progress", "reason_code": 1517}
            ]
        }
    )
    with pytest.raises(RuntimeJobFailureError, match="calibration"):
        run_bell(localqpu_service).result(timeout=60)


def test_planned_cancellation(
    localqpu_service: QiskitRuntimeService, localqpu_control: LocalqpuControl
) -> None:
    """예정된 취소는 결과 조회 시 RuntimeInvalidStateError가 된다."""
    localqpu_control.set_scenario({"next_jobs": [{"outcome": "cancelled"}]})
    with pytest.raises(RuntimeInvalidStateError):
        run_bell(localqpu_service).result(timeout=60)


def test_reason_code_1305_becomes_max_timeout(
    localqpu_service: QiskitRuntimeService, localqpu_control: LocalqpuControl
) -> None:
    """실패 + 1305는 클라이언트에서 RuntimeJobMaxTimeoutError가 된다."""
    localqpu_control.set_scenario(
        {"next_jobs": [{"outcome": "failed", "reason": "max time", "reason_code": 1305}]}
    )
    with pytest.raises(RuntimeJobMaxTimeoutError):
        run_bell(localqpu_service).result(timeout=60)


def test_cancelled_1305_without_reason_becomes_max_timeout(
    localqpu_service: QiskitRuntimeService, localqpu_control: LocalqpuControl
) -> None:
    """사유 없는 취소 + 1305도 RuntimeJobMaxTimeoutError가 된다(기본 사유가 채워져야 코드가 전달됨)."""
    localqpu_control.set_scenario({"next_jobs": [{"outcome": "cancelled", "reason_code": 1305}]})
    with pytest.raises(RuntimeJobMaxTimeoutError):
        run_bell(localqpu_service).result(timeout=60)


def test_offline_backend(
    localqpu_service: QiskitRuntimeService, localqpu_control: LocalqpuControl
) -> None:
    """오프라인이면 least_busy에서 빠지고, 제출된 작업은 온라인이 될 때까지 대기한다."""
    localqpu_control.set_scenario({"backends": {"ibm_brisbane": {"status": "offline"}}})
    with pytest.raises(QiskitBackendNotFoundError):
        localqpu_service.least_busy()
    job = run_bell(localqpu_service)
    assert [job.status() for _ in range(3)] == ["QUEUED"] * 3
    localqpu_control.set_scenario({})
    assert sum(job.result(timeout=60)[0].data.meas.get_counts().values()) == 50


def test_paused_backend_warns(
    localqpu_service: QiskitRuntimeService, localqpu_control: LocalqpuControl
) -> None:
    """일시 중지 상태면 클라이언트가 경고를 내고 작업은 정상 처리된다."""
    localqpu_control.set_scenario({"backends": {"ibm_brisbane": {"status": "paused"}}})
    with pytest.warns(UserWarning, match="paused"):
        job = run_bell(localqpu_service)
    assert job.result(timeout=60)[0].data.meas.num_shots == 50


def test_usage_exhausted(
    localqpu_service: QiskitRuntimeService, localqpu_control: LocalqpuControl
) -> None:
    """사용량 한도에 도달하면 경고 후 제출이 IBMRuntimeError(403)로 거절된다."""
    localqpu_control.set_scenario({"usage": {"limit_seconds": 10, "consumed_seconds": 10}})
    with (
        pytest.warns(UserWarning, match="usage limit"),
        pytest.raises(IBMRuntimeError, match="403"),
    ):
        run_bell(localqpu_service)


def test_rejected_token_fails_connect(
    localqpu_server: RunningServer, localqpu_control: LocalqpuControl
) -> None:
    """토큰 발급이 거부되면 서비스 생성이 InvalidAccountError로 실패한다."""
    localqpu_control.set_scenario({"auth": {"reject_tokens": True}})
    with pytest.raises(InvalidAccountError):
        connect(port=localqpu_server.port, host=localqpu_server.host)


def test_user_cancel(
    localqpu_service: QiskitRuntimeService, localqpu_control: LocalqpuControl
) -> None:
    """대기 중인 작업을 취소하면 CANCELLED가 되고 서버 기록에도 반영된다."""
    localqpu_control.set_scenario({"queue": {"polls_before_running": 1000}})
    job = run_bell(localqpu_service)
    job.cancel()
    assert job.status() == "CANCELLED"
    assert localqpu_control.jobs()[0]["status"] == "Cancelled"


def test_sampler_stub_over_limit(tiny_limit_server: RunningServer) -> None:
    """한도를 넘는 SamplerV2 작업은 표시된 stub 결과를 받는다."""
    service = connect(port=tiny_limit_server.port, host=tiny_limit_server.host)
    result = run_bell(service, shots=64).result(timeout=60)
    assert result.metadata["localqpu_stub"] is True
    assert result[0].data.meas.num_shots == 64


def test_executor_over_limit_fails_with_guidance(tiny_limit_server: RunningServer) -> None:
    """한도를 넘는 executor 작업은 --max-sim-qubits 안내와 함께 실패한다."""
    service = connect(port=tiny_limit_server.port, host=tiny_limit_server.host)
    backend = service.backend("ibm_brisbane")
    with pytest.raises(RuntimeJobFailureError, match="max-sim-qubits"):
        ExecutorSampler(mode=backend).run([bell_isa_circuit(backend)]).result(timeout=60)
