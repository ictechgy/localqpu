"""실제 qiskit-ibm-runtime 클라이언트로 정상 경로를 검증한다."""

import pytest
from qiskit import QuantumCircuit
from qiskit.quantum_info import SparsePauliOp
from qiskit.transpiler import generate_preset_pass_manager
from qiskit_ibm_runtime import EstimatorV2, QiskitRuntimeService, SamplerV2
from qiskit_ibm_runtime.executor_estimator import Estimator as ExecutorEstimator
from qiskit_ibm_runtime.executor_sampler import Sampler as ExecutorSampler

from localqpu import connect
from localqpu.control_client import LocalqpuControl
from localqpu.server import RunningServer
from tests.contract.helpers import bell_isa_circuit

pytestmark = pytest.mark.contract


def test_sampler_v2_bell_counts(localqpu_service: QiskitRuntimeService) -> None:
    """기존 SamplerV2로 Bell 회로를 돌리면 00/11만 나오고 합이 shots와 같다."""
    backend = localqpu_service.least_busy()
    job = SamplerV2(mode=backend).run([bell_isa_circuit(backend)], shots=500)
    counts = job.result(timeout=120)[0].data.meas.get_counts()
    assert set(counts) <= {"00", "11"} and sum(counts.values()) == 500


def test_executor_sampler_bell_counts(localqpu_service: QiskitRuntimeService) -> None:
    """새 executor 기반 Sampler로도 같은 결과가 나온다."""
    backend = localqpu_service.backend("ibm_brisbane")
    sampler = ExecutorSampler(mode=backend)
    sampler.options.default_shots = 500
    counts = sampler.run([bell_isa_circuit(backend)]).result(timeout=120)[0].data.meas.get_counts()
    assert set(counts) <= {"00", "11"} and sum(counts.values()) == 500


def test_environment_proxy_does_not_hijack_requests(
    localqpu_server: RunningServer,
    localqpu_control: LocalqpuControl,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """HTTP_PROXY·HTTPS_PROXY가 설정된 환경에서도 요청은 localqpu로 간다.

    프록시 주소로 닫힌 포트를 넣어, 요청이 새면 연결 거부로 테스트가 실패하게 한다.
    """
    for variable in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
        monkeypatch.setenv(variable, "http://127.0.0.1:9")
    for variable in ("NO_PROXY", "no_proxy"):
        monkeypatch.delenv(variable, raising=False)
    service = connect(port=localqpu_server.port, host=localqpu_server.host)
    backend = service.backend("ibm_brisbane")
    counts = (
        SamplerV2(mode=backend)
        .run([bell_isa_circuit(backend)], shots=10)
        .result(timeout=120)[0]
        .data.meas.get_counts()
    )
    assert sum(counts.values()) == 10


def test_connect_does_not_leak_to_network(
    localqpu_service: QiskitRuntimeService, localqpu_control: LocalqpuControl
) -> None:
    """connect()로 연결하면 외부로 나가려는 CONNECT가 한 건도 없다."""
    backend = localqpu_service.least_busy()
    SamplerV2(mode=backend).run([bell_isa_circuit(backend)], shots=10).result(timeout=120)
    assert localqpu_control.health()["blocked_connect_requests"] == 0
    assert [job["status"] for job in localqpu_control.jobs()] == ["Completed"]


def bell_observable_pub(backend: object) -> tuple[object, object]:
    """Bell 회로와 칩 배치에 맞춘 관측량(ZZ, XX, ZI) PUB."""
    circuit = QuantumCircuit(2)
    circuit.h(0)
    circuit.cx(0, 1)
    isa = generate_preset_pass_manager(backend=backend, optimization_level=1).run(circuit)
    return isa, [SparsePauliOp(label).apply_layout(isa.layout) for label in ("ZZ", "XX", "ZI")]


def test_estimator_v2_bell_expectation_values(localqpu_service: QiskitRuntimeService) -> None:
    """기존 EstimatorV2로 Bell 상태의 ZZ·XX는 1, ZI는 0이 나온다(정확 계산)."""
    backend = localqpu_service.backend("ibm_brisbane")
    result = EstimatorV2(mode=backend).run([bell_observable_pub(backend)]).result(timeout=120)
    assert result[0].data.evs == pytest.approx([1.0, 1.0, 0.0], abs=1e-9)


def test_executor_estimator_bell_expectation_values(localqpu_service: QiskitRuntimeService) -> None:
    """새 executor 기반 Estimator도 Bell 상태의 기대값을 샘플링 오차 안에서 돌려준다."""
    backend = localqpu_service.backend("ibm_brisbane")
    evs = (
        ExecutorEstimator(mode=backend)
        .run([bell_observable_pub(backend)])
        .result(timeout=120)[0]
        .data.evs
    )
    assert evs[0] == pytest.approx(1.0, abs=0.05) and evs[1] == pytest.approx(1.0, abs=0.05)
    assert abs(evs[2]) < 0.2
