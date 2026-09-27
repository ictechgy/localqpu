"""프로그램 어댑터 테스트."""

import json
from typing import Any

import pytest
from qiskit import QuantumCircuit

from localqpu._compat import RuntimeDecoder, RuntimeEncoder
from localqpu.programs import SUPPORTED_PROGRAM_IDS, find_program_runner
from localqpu.programs.base import ExecutionSettings, ProgramInputError, UnsupportedProgramError
from localqpu.programs.executor import run_executor_program
from localqpu.programs.sampler import run_sampler_program

SETTINGS = ExecutionSettings(max_sim_qubits=24, seed=7)


def bell_circuit() -> QuantumCircuit:
    """2큐비트 Bell 회로."""
    circuit = QuantumCircuit(2)
    circuit.h(0)
    circuit.cx(0, 1)
    circuit.measure_all()
    return circuit


def encode_like_client(inputs: dict[str, Any]) -> dict[str, Any]:
    """클라이언트가 POST /jobs의 params로 보내는 것과 같은 JSON 객체를 만든다."""
    return json.loads(json.dumps(inputs, cls=RuntimeEncoder))


def test_sampler_round_trip_matches_client_decoder() -> None:
    """클라이언트 형식으로 인코딩한 입력을 실행하면 클라이언트 디코더로 읽히는 결과가 나온다."""
    params = encode_like_client(
        {"pubs": [(bell_circuit(), None, 200)], "options": {}, "version": 2}
    )
    output = run_sampler_program(params, SETTINGS)
    result = json.loads(output.payload, cls=RuntimeDecoder)
    counts = result[0].data.meas.get_counts()
    assert set(counts) <= {"00", "11"} and sum(counts.values()) == 200
    assert output.is_stub is False and output.active_qubits == 2


def test_sampler_uses_default_shots_when_pub_has_none() -> None:
    """PUB에 shots가 없으면 options.default_shots, 그것도 없으면 4096을 쓴다."""
    params = encode_like_client({"pubs": [(bell_circuit(),)], "options": {"default_shots": 33}})
    result = json.loads(run_sampler_program(params, SETTINGS).payload, cls=RuntimeDecoder)
    assert result[0].data.meas.num_shots == 33


def test_sampler_rejects_missing_pubs() -> None:
    """pubs가 없으면 해결 방향을 담은 입력 오류가 난다."""
    with pytest.raises(ProgramInputError, match="localqpu를 업데이트"):
        run_sampler_program({"options": {}}, SETTINGS)


def test_sampler_rejects_corrupt_circuit() -> None:
    """QPY가 깨져 있으면 입력 오류가 난다."""
    params = {"pubs": [[{"__type__": "QuantumCircuit", "__value__": "not-base64"}, None, 10]]}
    with pytest.raises(ProgramInputError):
        run_sampler_program(params, SETTINGS)


def test_registry_rejects_unknown_program() -> None:
    """지원하지 않는 프로그램은 지원 목록과 함께 거절한다."""
    with pytest.raises(UnsupportedProgramError, match="estimator"):
        find_program_runner("estimator")
    assert "sampler" in SUPPORTED_PROGRAM_IDS


def test_executor_rejects_other_schema_versions() -> None:
    """v2.0이 아닌 스키마는 버전 안내와 함께 거절한다."""
    with pytest.raises(ProgramInputError, match="v2.0"):
        run_executor_program({"schema_version": "v9.9"}, SETTINGS)


def test_executor_rejects_malformed_v2_params() -> None:
    """v2.0이라도 형식이 틀리면 입력 오류가 난다."""
    with pytest.raises(ProgramInputError, match="executor 입력"):
        run_executor_program({"schema_version": "v2.0", "quantum_program": {}}, SETTINGS)


def test_registry_includes_executor() -> None:
    """executor가 레지스트리에 등록돼 있다."""
    assert find_program_runner("executor") is run_executor_program


def test_executor_rejects_non_object_params() -> None:
    """params가 객체가 아니면 내부 오류가 아니라 입력 오류다."""
    with pytest.raises(ProgramInputError, match="JSON 객체"):
        run_executor_program([1], SETTINGS)  # type: ignore[arg-type]
