"""프로그램 어댑터 테스트."""

import json
from typing import Any

import pytest
from qiskit import QuantumCircuit
from qiskit.quantum_info import SparsePauliOp

from localqpu._compat import RuntimeDecoder, RuntimeEncoder
from localqpu.programs import SUPPORTED_PROGRAM_IDS, find_program_runner
from localqpu.programs.base import ExecutionSettings, ProgramInputError, UnsupportedProgramError
from localqpu.programs.estimator import run_estimator_program
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
    assert output.is_stub is False and output.entangled_qubits == 2


def test_sampler_uses_default_shots_when_pub_has_none() -> None:
    """PUB에 shots가 없으면 options.default_shots, 그것도 없으면 4096을 쓴다."""
    params = encode_like_client({"pubs": [(bell_circuit(),)], "options": {"default_shots": 33}})
    result = json.loads(run_sampler_program(params, SETTINGS).payload, cls=RuntimeDecoder)
    assert result[0].data.meas.num_shots == 33


def test_sampler_rejects_missing_pubs() -> None:
    """pubs가 없으면 버전 업데이트가 아니라 입력 구조를 고치라는 안내가 나온다."""
    with pytest.raises(ProgramInputError, match="pubs") as caught:
        run_sampler_program({"options": {}}, SETTINGS)
    assert "업데이트" not in str(caught.value)


def test_sampler_accepts_null_options() -> None:
    """options가 null이어도 기본 shots로 실행된다."""
    params = encode_like_client({"pubs": [(bell_circuit(),)], "options": None})
    result = json.loads(run_sampler_program(params, SETTINGS).payload, cls=RuntimeDecoder)
    assert result[0].data.meas.num_shots == 4096


def test_sampler_rejects_corrupt_circuit() -> None:
    """QPY가 깨져 있으면 입력 오류가 난다."""
    params = {"pubs": [[{"__type__": "QuantumCircuit", "__value__": "not-base64"}, None, 10]]}
    with pytest.raises(ProgramInputError, match="업데이트"):
        run_sampler_program(params, SETTINGS)


def test_registry_rejects_unknown_program() -> None:
    """지원하지 않는 프로그램은 지원 목록과 함께 거절한다."""
    with pytest.raises(UnsupportedProgramError, match="noise-learner"):
        find_program_runner("noise-learner")
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


def bell_state() -> QuantumCircuit:
    """측정 없는 2큐비트 Bell 상태 준비 회로(Estimator 입력용)."""
    circuit = QuantumCircuit(2)
    circuit.h(0)
    circuit.cx(0, 1)
    return circuit


def estimator_params(options: dict[str, Any] | None = None) -> dict[str, Any]:
    """Bell 상태에서 ZZ·ZI를 재는 EstimatorV2 입력(클라이언트와 같은 인코딩)."""
    observables = [SparsePauliOp("ZZ"), SparsePauliOp("ZI")]
    return encode_like_client({"pubs": [(bell_state(), observables)], "options": options or {}})


def test_estimator_exact_expectation_values() -> None:
    """정밀도를 지정하지 않으면 정확한 기대값(ZZ=1, ZI=0)과 표준편차 0을 돌려준다."""
    output = run_estimator_program(estimator_params(), SETTINGS)
    result = json.loads(output.payload, cls=RuntimeDecoder)
    assert result[0].data.evs == pytest.approx([1.0, 0.0], abs=1e-9)
    assert result[0].data.stds == pytest.approx([0.0, 0.0])
    assert output.is_stub is False and output.entangled_qubits == 2


def test_estimator_stub_over_limit_keeps_shape() -> None:
    """얽힌 큐비트가 한도를 넘으면 모양이 같은 stub 기대값을 표시와 함께 돌려준다."""
    output = run_estimator_program(
        estimator_params({"default_precision": 0.01}), ExecutionSettings(1, 3)
    )
    result = json.loads(output.payload, cls=RuntimeDecoder)
    assert output.is_stub is True and result.metadata["localqpu_stub"] is True
    assert result[0].data.evs.shape == (2,) and all(abs(value) <= 1 for value in result[0].data.evs)
    assert result[0].data.stds == pytest.approx([0.01, 0.01])


def test_estimator_rejects_missing_pubs_with_structure_hint() -> None:
    """pubs가 없으면 관측량을 포함한 입력 구조를 안내한다."""
    with pytest.raises(ProgramInputError, match="관측량"):
        run_estimator_program({"options": {}}, SETTINGS)


def test_registry_includes_estimator() -> None:
    """estimator가 레지스트리에 등록돼 있다."""
    assert find_program_runner("estimator") is run_estimator_program


def test_estimator_with_noise_lowers_correlation() -> None:
    """노이즈를 켜면 칩에 배치한 Bell 상태의 ZZ가 1보다 작아진다."""
    from qiskit.transpiler import generate_preset_pass_manager

    from localqpu.noise import fake_backend_for

    backend = fake_backend_for("ibm_brisbane")
    isa = generate_preset_pass_manager(backend=backend, optimization_level=1).run(bell_state())
    params = encode_like_client(
        {"pubs": [(isa, SparsePauliOp("ZZ").apply_layout(isa.layout))], "options": {}}
    )
    settings = ExecutionSettings(max_sim_qubits=24, seed=7, noise_backend="ibm_brisbane")
    result = json.loads(run_estimator_program(params, settings).payload, cls=RuntimeDecoder)
    assert 0.5 < float(result[0].data.evs) < 0.999
