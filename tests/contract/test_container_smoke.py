"""이미 떠 있는 localqpu 서버(예: Docker 컨테이너)에 실제 클라이언트를 붙이는 스모크 테스트.

LOCALQPU_EXTERNAL_PORT가 있을 때만 돈다. CI의 docker 워크플로가 컨테이너를 띄운 뒤 실행한다.
"""

import os

import pytest
from qiskit_ibm_runtime import SamplerV2
from qiskit_ibm_runtime.executor_sampler import Sampler as ExecutorSampler

from localqpu import connect
from localqpu.control_client import LocalqpuControl
from tests.contract.helpers import bell_isa_circuit

#: 외부 서버 포트. 없으면 이 파일의 테스트를 건너뛴다.
EXTERNAL_PORT = os.environ.get("LOCALQPU_EXTERNAL_PORT")

pytestmark = [
    pytest.mark.contract,
    pytest.mark.skipif(
        EXTERNAL_PORT is None,
        reason="LOCALQPU_EXTERNAL_PORT가 없어 외부 서버 스모크 테스트를 건너뜀",
    ),
]


def test_external_server_runs_both_samplers() -> None:
    """외부 서버에서 기존·새 Sampler 모두 Bell 결과를 받고, 외부로 샌 요청이 없다."""
    port = int(EXTERNAL_PORT or 0)
    service = connect(port=port)
    backend = service.least_busy()
    circuit = bell_isa_circuit(backend)
    legacy = (
        SamplerV2(mode=backend)
        .run([circuit], shots=200)
        .result(timeout=120)[0]
        .data.meas.get_counts()
    )
    sampler = ExecutorSampler(mode=backend)
    sampler.options.default_shots = 200
    modern = sampler.run([circuit]).result(timeout=120)[0].data.meas.get_counts()
    assert set(legacy) <= {"00", "11"} and sum(legacy.values()) == 200
    assert set(modern) <= {"00", "11"} and sum(modern.values()) == 200
    assert LocalqpuControl(f"http://127.0.0.1:{port}").health()["blocked_connect_requests"] == 0
