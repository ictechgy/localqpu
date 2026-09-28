"""AWS Braket OpenQASM 작업(program_id="braket-openqasm") 실행기(v0.2 설계서 6절).

Braket 패키지는 선택 설치(`localqpu[braket]`, Python 3.11+)이므로 실행할 때 불러온다.
"""

from __future__ import annotations

import json
from typing import Any

from localqpu.programs.base import ExecutionSettings, ProgramInputError, ProgramOutput

#: 지원하는 Braket 프로그램 스키마(게이트 모델 OpenQASM).
OPENQASM_SCHEMA_NAME = "braket.ir.openqasm.program"


def run_braket_program(params: dict[str, Any], settings: ExecutionSettings) -> ProgramOutput:
    """Braket OpenQASM 프로그램을 기본 statevector 시뮬레이터로 실행하고 결과 JSON을 돌려준다.

    결과의 taskMetadata.id·deviceId는 S3로 내보낼 때 라우트가 작업·장치 ARN으로 바꾼다.
    """
    program = _parse_program(params)
    qubit_count = count_used_qubits(program)
    _ensure_within_limit(qubit_count, settings.max_sim_qubits)
    simulator = _load_simulator()
    result = simulator.run_openqasm(program, shots=_shots(params))
    return ProgramOutput(payload=str(result.json()), is_stub=False, entangled_qubits=qubit_count)


def count_used_qubits(program: Any) -> int:
    """프로그램이 실제로 쓰는 큐비트 수를 Braket 자체 해석기로 센다(계산하지 않고 해석만 한다).

    기본 시뮬레이터도 쓰인 큐비트만 계산하므로 이 값이 곧 계산 비용이다. 정규식으로 선언을 세면
    `qubit [25] q;`(공백)·`qubit[n] q;`(상수 크기)를 놓쳐 한도를 우회당했다(v0.2.0 결함).
    Braket에서는 얽힘을 따로 세지 않고 쓰인 큐비트를 모두 얽힐 수 있는 것으로 본다(보수적).
    """
    interpreter_class = _load_interpreter_class()
    try:
        circuit = interpreter_class().build_circuit(program.source, program.inputs or {})
    except Exception as error:
        # 해석기 예외 종류가 다양해(문법·타입·미정의 식별자) 모두 사용자 입력 오류로 바꾼다.
        raise ProgramInputError(
            f"OpenQASM 프로그램을 해석하지 못했습니다({type(error).__name__}: {error})."
        ) from error
    return int(circuit.num_qubits)


def _parse_program(params: dict[str, Any]) -> Any:
    """action JSON을 Braket OpenQASM 프로그램으로 해석한다."""
    program_class = _load_program_class()
    try:
        action = json.loads(params["action"])
    except (KeyError, TypeError, json.JSONDecodeError) as error:
        raise ProgramInputError(
            f"Braket 작업의 action을 JSON으로 읽지 못했습니다({type(error).__name__}: {error})."
        ) from error
    schema_name = (
        (action.get("braketSchemaHeader") or {}).get("name") if isinstance(action, dict) else None
    )
    if schema_name != OPENQASM_SCHEMA_NAME:
        raise ProgramInputError(
            f"localqpu의 Braket 흉내는 게이트 모델 OpenQASM 프로그램만 지원합니다(받은 스키마: {schema_name})."
        )
    return program_class(**action)


def _ensure_within_limit(qubit_count: int, limit: int) -> None:
    """큐비트 수가 한도를 넘으면 계산 전에 거절한다(원시 시뮬레이터는 스스로 막지 않는다)."""
    if qubit_count > limit:
        raise ProgramInputError(
            f"Braket 프로그램의 큐비트 {qubit_count}개가 --max-sim-qubits({limit})를 넘습니다. "
            "한도를 올리거나 회로를 줄이세요(Braket 흉내는 stub을 지원하지 않습니다)."
        )


def _shots(params: dict[str, Any]) -> int:
    """shots 값. 0이면 샘플링 없이 정확한 결과 유형만 계산한다(Braket 규약)."""
    shots = params.get("shots", 0)
    if isinstance(shots, bool) or not isinstance(shots, int) or shots < 0:
        raise ProgramInputError(
            f"Braket 작업의 shots는 0 이상의 정수여야 합니다(받은 값: {shots!r})."
        )
    return shots


def _load_program_class() -> Any:
    """Braket OpenQASM 프로그램 모델. 선택 설치가 없으면 설치 방법을 안내한다."""
    try:
        from braket.ir.openqasm import Program
    except ImportError as error:
        raise ProgramInputError(MISSING_BRAKET_MESSAGE) from error
    return Program


def _load_interpreter_class() -> Any:
    """Braket OpenQASM 해석기 클래스."""
    try:
        from braket.default_simulator.openqasm.interpreter import Interpreter
    except ImportError as error:
        raise ProgramInputError(MISSING_BRAKET_MESSAGE) from error
    return Interpreter


def _load_simulator() -> Any:
    """Braket 기본 statevector 시뮬레이터."""
    try:
        from braket.default_simulator import StateVectorSimulator
    except ImportError as error:
        raise ProgramInputError(MISSING_BRAKET_MESSAGE) from error
    return StateVectorSimulator()


#: Braket 선택 설치가 없을 때의 안내.
MISSING_BRAKET_MESSAGE = (
    "이 localqpu 서버에는 Braket 지원이 설치돼 있지 않습니다. "
    "Python 3.11 이상에서 pip install 'localqpu[braket]'로 설치하세요."
)
