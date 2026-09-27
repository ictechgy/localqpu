"""기존 SamplerV2(program_id="sampler") 어댑터."""

from __future__ import annotations

import json
from typing import Any

from qiskit.primitives.containers.sampler_pub import SamplerPub

from localqpu._compat import RuntimeEncoder
from localqpu.constants import DEFAULT_SAMPLER_SHOTS
from localqpu.programs.base import ExecutionSettings, ProgramInputError, ProgramOutput
from localqpu.programs.decoding import (
    STRUCTURE_ERRORS,
    as_pub_like,
    decode_runtime_json,
    default_option,
    structure_error,
)
from localqpu.simulation import CircuitShapeError, sample_pubs


def run_sampler_program(params: dict[str, Any], settings: ExecutionSettings) -> ProgramOutput:
    """SamplerV2 입력을 해석해 시뮬레이션하고, 클라이언트 디코더가 읽는 JSON으로 돌려준다."""
    pubs = _decode_pubs(params)
    try:
        outcome = sample_pubs(pubs, settings.max_sim_qubits, settings.seed)
    except CircuitShapeError as error:
        raise ProgramInputError(str(error)) from error
    payload = json.dumps(outcome.result, cls=RuntimeEncoder)
    return ProgramOutput(
        payload=payload, is_stub=outcome.is_stub, entangled_qubits=outcome.entangled_qubits
    )


def _decode_pubs(params: dict[str, Any]) -> list[SamplerPub]:
    """params를 클라이언트와 같은 디코더로 풀어 SamplerPub 목록으로 만든다."""
    decoded = decode_runtime_json(params, "sampler")
    try:
        default_shots = default_option(decoded, "default_shots") or DEFAULT_SAMPLER_SHOTS
        return [SamplerPub.coerce(as_pub_like(pub), default_shots) for pub in decoded["pubs"]]
    except STRUCTURE_ERRORS as error:
        raise structure_error("sampler", error, "회로, 파라미터 값, shots") from error
