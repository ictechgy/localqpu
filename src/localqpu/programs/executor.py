"""새 executor(program_id="executor", 스키마 v2.0) 어댑터.

입력 해석과 실행은 qiskit-ibm-runtime 로컬 테스트 모드의 구현을 재사용하고,
결과를 v2.0 전송 형식으로 되돌리는 부분만 직접 구현한다(클라이언트에는 해석 방향만 있다).
2026-09-27 스파이크로 전체 경로를 검증했다.
"""

from __future__ import annotations

import secrets
from typing import Any

from qiskit_aer import AerSimulator

from localqpu._compat import (
    CompressedTensorModel,
    ExecutorParamsModel,
    ItemMetadataModel,
    MetadataModel,
    QuantumProgramResultItemModel,
    QuantumProgramResultModel,
    SimulatorOptions,
    finalize_samplex_items,
    passthrough_data_to_2_0,
    quantum_program_from_2_0,
    run_quantum_program,
    semantic_role_of,
)
from localqpu.noise import aer_backend_options
from localqpu.programs.base import ExecutionSettings, ProgramInputError, ProgramOutput
from localqpu.simulation import count_entangled_qubits

#: 지원하는 executor 입력 스키마 버전(qiskit-ibm-runtime 0.50의 기본값).
SUPPORTED_EXECUTOR_SCHEMA: str = "v2.0"


def run_executor_program(params: dict[str, Any], settings: ExecutionSettings) -> ProgramOutput:
    """executor 입력을 노이즈 없는 Aer MPS 시뮬레이터로 실행하고 v2.0 결과 JSON을 돌려준다.

    executor 회로는 칩 전체 폭으로 오고(Estimator는 관측량 측정을 칩 전체에 붙인다) samplex 구조
    때문에 큐비트를 걸러낼 수 없으므로, 폭에 강한 MPS로 그대로 계산한다.
    """
    program = _decode_program(params)
    entangled_qubits = count_entangled_qubits(item.circuit for item in program.items)
    _ensure_within_limit(entangled_qubits, settings.max_sim_qubits)
    options = SimulatorOptions(seed_simulator=_resolve_seed(settings.seed))
    result = run_quantum_program(
        AerSimulator(**aer_backend_options(settings.noise_backend)), program, options
    )
    return ProgramOutput(
        payload=_encode_result(program, result), is_stub=False, entangled_qubits=entangled_qubits
    )


def _decode_program(params: dict[str, Any]) -> Any:
    """스키마 버전을 확인하고 QuantumProgram으로 해석한다."""
    if not isinstance(params, dict):
        raise ProgramInputError("executor 입력(params)은 JSON 객체여야 합니다.")
    version = params.get("schema_version")
    if version != SUPPORTED_EXECUTOR_SCHEMA:
        raise ProgramInputError(
            f"executor 스키마 '{version}'는 지원하지 않습니다(지원: {SUPPORTED_EXECUTOR_SCHEMA}). "
            "localqpu를 업데이트하거나 qiskit-ibm-runtime 버전을 맞추세요."
        )
    try:
        program, _options = quantum_program_from_2_0(ExecutorParamsModel.model_validate(params))
        finalize_samplex_items(program)
    except Exception as error:
        # pydantic 검증·QPY 해석 실패를 사용자 메시지로 바꾼다.
        raise ProgramInputError(
            f"executor 입력을 해석하지 못했습니다({type(error).__name__}: {error})."
        ) from error
    return program


def _ensure_within_limit(entangled_qubits: int, limit: int) -> None:
    """얽힌 큐비트가 한도를 넘으면 실패시킨다. executor는 아직 stub을 지원하지 않는다."""
    if entangled_qubits > limit:
        raise ProgramInputError(
            f"얽힌 큐비트 {entangled_qubits}개가 --max-sim-qubits({limit})를 넘습니다. "
            "executor는 v0.1에서 stub 모드를 지원하지 않으니 한도를 올리거나 회로를 줄이세요."
        )


def _resolve_seed(seed: int | None) -> int:
    """SimulatorOptions는 정수 시드를 요구하므로, 시드가 없으면 무작위 정수를 만든다."""
    return seed if seed is not None else secrets.randbelow(2**31)


def _encode_result(program: Any, result: Any) -> str:
    """QuantumProgramResult를 v2.0 전송 형식 JSON으로 바꾼다."""
    model = QuantumProgramResultModel(
        data=[_encode_item(item) for item in result],
        metadata=MetadataModel(chunk_timing=[]),
        passthrough_data=passthrough_data_to_2_0(program.passthrough_data),
        semantic_role=semantic_role_of(program),
    )
    return str(model.model_dump_json())


def _encode_item(item: Any) -> Any:
    """결과 항목 하나(이름 → numpy 배열)를 v2.0 항목 모델로 바꾼다."""
    results = {name: CompressedTensorModel.from_numpy(value) for name, value in item.items()}
    return QuantumProgramResultItemModel(results=results, metadata=ItemMetadataModel())
