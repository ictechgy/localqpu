"""프로그램 어댑터 공통 타입."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ExecutionSettings:
    """작업 하나의 실행 설정.

    Attributes:
        max_sim_qubits: 정확 시뮬레이션을 허용하는 최대 얽힌 큐비트 수.
        seed: 시뮬레이션 시드. None이면 매번 다른 결과가 나온다.
    """

    max_sim_qubits: int
    seed: int | None


@dataclass(frozen=True)
class ProgramOutput:
    """프로그램 실행 결과.

    Attributes:
        payload: GET /jobs/{id}/results로 그대로 내보낼 JSON 문자열.
        is_stub: 모양만 맞춘 무작위 결과인지.
        entangled_qubits: 실행한 회로들의 최대 얽힌 큐비트 수.
    """

    payload: str
    is_stub: bool
    entangled_qubits: int


class ProgramInputError(ValueError):
    """입력을 해석·검증하지 못했을 때. 메시지는 작업 실패 사유로 사용자에게 그대로 보인다."""


class UnsupportedProgramError(LookupError):
    """localqpu가 흉내 내지 않는 program_id. 제출 단계에서 404로 거절된다."""


#: 프로그램 실행 함수의 형태: (POST /jobs의 params, 실행 설정) → 결과.
ProgramRunner = Callable[[dict[str, Any], ExecutionSettings], ProgramOutput]
