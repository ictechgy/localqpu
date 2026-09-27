"""기존 primitive(sampler·estimator) 입력의 공통 해석.

실패 원인에 따라 해결 방향이 다르므로 두 단계로 나눈다. 직렬화 해석(QPY 등) 실패는 대개
클라이언트와 localqpu의 qiskit 버전 차이이고, 구조 오류(pubs 누락 등)는 요청 본문을 고쳐야 한다.
"""

from __future__ import annotations

import json
from typing import Any

from localqpu._compat import RuntimeDecoder
from localqpu.programs.base import ProgramInputError

#: 구조 오류로 볼 예외. 이 밖의 예외는 localqpu 결함일 수 있으므로 그대로 올린다.
STRUCTURE_ERRORS: tuple[type[Exception], ...] = (KeyError, TypeError, AttributeError, ValueError)


def decode_runtime_json(params: dict[str, Any], program_name: str) -> dict[str, Any]:
    """클라이언트의 RuntimeDecoder로 회로(QPY)·배열을 되살린다.

    Raises:
        ProgramInputError: 역직렬화에 실패했거나 결과가 JSON 객체가 아닐 때.
    """
    try:
        decoded = json.loads(json.dumps(params), cls=RuntimeDecoder)
    except Exception as error:
        # QPY·numpy 역직렬화 실패는 대개 클라이언트와 localqpu의 qiskit 버전 차이다.
        raise ProgramInputError(
            f"{program_name} 입력을 해석하지 못했습니다({type(error).__name__}: {error}). "
            "클라이언트의 qiskit이 localqpu 쪽보다 새로우면 localqpu를 업데이트하세요."
        ) from error
    if not isinstance(decoded, dict):
        raise ProgramInputError(f"{program_name} 입력(params)은 JSON 객체여야 합니다.")
    return decoded


def structure_error(program_name: str, error: Exception, expected_shape: str) -> ProgramInputError:
    """입력 구조 오류를 사용자에게 보일 메시지로 바꾼다. expected_shape에는 기대하는 pubs 모양을 적는다."""
    return ProgramInputError(
        f"{program_name} 입력 구조가 올바르지 않습니다({type(error).__name__}: {error}). "
        f"params에 pubs 목록({expected_shape})과 options 객체가 있는지 확인하세요."
    )


def as_pub_like(pub: Any) -> Any:
    """JSON 배열로 풀린 PUB을 coerce가 받는 튜플로 바꾼다."""
    return tuple(pub) if isinstance(pub, list) else pub


def default_option(decoded: dict[str, Any], name: str) -> Any:
    """options.<name> 값. options가 없거나 null이면 None."""
    return (decoded.get("options") or {}).get(name)
