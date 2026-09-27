"""program_id별 실행 함수 레지스트리. 새 프로그램 형식은 여기에 한 줄을 추가해 지원한다."""

from __future__ import annotations

from localqpu.programs.base import ProgramRunner, UnsupportedProgramError
from localqpu.programs.executor import run_executor_program
from localqpu.programs.sampler import run_sampler_program

#: 지원하는 program_id와 실행 함수.
_RUNNERS: dict[str, ProgramRunner] = {
    "sampler": run_sampler_program,
    "executor": run_executor_program,
}

#: 지원하는 program_id 목록(오류 메시지와 문서용).
SUPPORTED_PROGRAM_IDS: tuple[str, ...] = tuple(sorted(_RUNNERS))


def find_program_runner(program_id: str) -> ProgramRunner:
    """program_id에 맞는 실행 함수를 찾는다.

    Raises:
        UnsupportedProgramError: 흉내 내지 않는 프로그램일 때.
    """
    try:
        return _RUNNERS[program_id]
    except KeyError as error:
        raise UnsupportedProgramError(
            f"localqpu v0.1은 '{program_id}' 프로그램을 지원하지 않습니다"
            f"(지원: {', '.join(SUPPORTED_PROGRAM_IDS)})."
        ) from error
