"""실제 클라이언트의 Session·Batch가 localqpu에서 동작하는지 검증한다."""

import pytest
from qiskit_ibm_runtime import Batch, QiskitRuntimeService, SamplerV2, Session
from qiskit_ibm_runtime.exceptions import IBMRuntimeError
from qiskit_ibm_runtime.executor_sampler import Sampler as ExecutorSampler

from localqpu.control_client import LocalqpuControl
from tests.contract.helpers import bell_isa_circuit

pytestmark = pytest.mark.contract


def test_session_runs_jobs_and_reports_status(
    localqpu_service: QiskitRuntimeService, localqpu_control: LocalqpuControl
) -> None:
    """Session 안에서 두 Sampler가 모두 결과를 받고, 작업이 세션에 묶이며, 닫으면 Closed가 된다."""
    backend = localqpu_service.backend("ibm_brisbane")
    circuit = bell_isa_circuit(backend)
    with Session(backend=backend) as session:
        assert session.status() == "Pending"
        legacy = SamplerV2(mode=session).run([circuit], shots=50).result(timeout=120)
        executor_sampler = ExecutorSampler(mode=session)
        executor_sampler.options.default_shots = 50
        modern = executor_sampler.run([circuit]).result(timeout=120)
        assert session.status() == "In progress, accepting new jobs"
        assert session.details()["mode"] == "dedicated"
        session.close()
        assert session.status() == "Closed"
    assert legacy[0].data.meas.num_shots == 50 and modern[0].data.meas.num_shots == 50
    assert {job["session_id"] for job in localqpu_control.jobs()} == {session.session_id}


def test_batch_mode(localqpu_service: QiskitRuntimeService) -> None:
    """Batch도 세션 모드 batch로 만들어지고 작업을 처리한다."""
    backend = localqpu_service.backend("ibm_brisbane")
    with Batch(backend=backend) as batch:
        result = (
            SamplerV2(mode=batch).run([bell_isa_circuit(backend)], shots=20).result(timeout=120)
        )
        assert batch.details()["mode"] == "batch"
    assert result[0].data.meas.num_shots == 20


def test_closed_session_rejects_new_jobs(localqpu_service: QiskitRuntimeService) -> None:
    """닫힌 세션에 작업을 내면 IBMRuntimeError가 난다(primitive 계층이 먼저 막는다. 서버 409는
    그 검사를 거치지 않는 클라이언트용이며 test_ibm_routes에서 검증한다)."""
    backend = localqpu_service.backend("ibm_brisbane")
    session = Session(backend=backend)
    session.close()
    with pytest.raises(IBMRuntimeError, match="closed"):
        SamplerV2(mode=session).run([bell_isa_circuit(backend)], shots=10)


def test_session_from_id(localqpu_service: QiskitRuntimeService) -> None:
    """Session.from_id로 기존 세션을 다시 열 수 있다."""
    backend = localqpu_service.backend("ibm_brisbane")
    original = Session(backend=backend)
    reopened = Session.from_id(original.session_id, service=localqpu_service)
    assert reopened.session_id == original.session_id and reopened.backend() == "ibm_brisbane"
