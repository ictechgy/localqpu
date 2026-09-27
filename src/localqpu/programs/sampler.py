"""기존 SamplerV2(program_id="sampler") 어댑터."""

from __future__ import annotations

import json
from typing import Any

from qiskit.primitives.containers.sampler_pub import SamplerPub

from localqpu._compat import RuntimeDecoder, RuntimeEncoder
from localqpu.constants import DEFAULT_SAMPLER_SHOTS
from localqpu.programs.base import ExecutionSettings, ProgramInputError, ProgramOutput
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
        payload=payload, is_stub=outcome.is_stub, active_qubits=outcome.active_qubits
    )


def _decode_pubs(params: dict[str, Any]) -> list[SamplerPub]:
    """params를 클라이언트와 같은 디코더로 풀어 SamplerPub 목록으로 만든다."""
    try:
        decoded = json.loads(json.dumps(params), cls=RuntimeDecoder)
        default_shots = decoded.get("options", {}).get("default_shots") or DEFAULT_SAMPLER_SHOTS
        return [SamplerPub.coerce(_as_pub_like(pub), default_shots) for pub in decoded["pubs"]]
    except Exception as error:
        # QPY·numpy·키 누락 등 해석 단계의 모든 실패를 사용자에게 보일 메시지로 바꾼다.
        raise ProgramInputError(
            f"sampler 입력을 해석하지 못했습니다({type(error).__name__}: {error}). "
            "클라이언트의 qiskit이 localqpu 쪽보다 새로우면 localqpu를 업데이트하세요."
        ) from error


def _as_pub_like(pub: Any) -> Any:
    """JSON 배열로 풀린 PUB을 SamplerPub.coerce가 받는 튜플로 바꾼다."""
    return tuple(pub) if isinstance(pub, list) else pub
