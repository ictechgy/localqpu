"""Braket OpenQASM 실행기 테스트. Braket 패키지(Python 3.11+)가 없으면 건너뛴다."""

import json

import pytest

pytest.importorskip("braket.default_simulator")

from braket.circuits import Circuit  # noqa: E402
from braket.circuits.serialization import IRType  # noqa: E402

from localqpu.programs import find_program_runner  # noqa: E402
from localqpu.programs.base import ExecutionSettings, ProgramInputError  # noqa: E402
from localqpu.programs.braket import run_braket_program  # noqa: E402

SETTINGS = ExecutionSettings(max_sim_qubits=24, seed=None)


def action_for(circuit: Circuit) -> str:
    """SDK가 CreateQuantumTask의 action으로 보내는 것과 같은 JSON 문자열."""
    return str(circuit.to_ir(IRType.OPENQASM).json())


def test_bell_circuit_runs_on_default_simulator() -> None:
    """Bell 회로는 00·11만 나오고 측정 횟수가 shots와 같다."""
    output = run_braket_program(
        {"action": action_for(Circuit().h(0).cnot(0, 1)), "shots": 100}, SETTINGS
    )
    result = json.loads(output.payload)
    outcomes = {"".join(str(bit) for bit in row) for row in result["measurements"]}
    assert outcomes <= {"00", "11"} and len(result["measurements"]) == 100
    assert output.is_stub is False and output.entangled_qubits == 2


def test_non_openqasm_action_is_rejected() -> None:
    """OpenQASM 게이트 모델이 아닌 프로그램(예: AHS)은 지원 범위를 안내하며 거절한다."""
    action = json.dumps({"braketSchemaHeader": {"name": "braket.ir.ahs.program", "version": "1"}})
    with pytest.raises(ProgramInputError, match="OpenQASM"):
        run_braket_program({"action": action, "shots": 10}, SETTINGS)


def test_too_many_qubits_is_input_error_before_simulation() -> None:
    """선언된 큐비트가 --max-sim-qubits를 넘으면 계산 전에 입력 오류로 거절한다.

    원시 기본 시뮬레이터는 27큐비트도 statevector(2GB)로 그대로 계산하므로 localqpu가 막아야 한다.
    """
    circuit = Circuit()
    for qubit in range(5):
        circuit.h(qubit)
    with pytest.raises(ProgramInputError, match="max-sim-qubits"):
        run_braket_program({"action": action_for(circuit), "shots": 10}, ExecutionSettings(4, None))


def test_physical_qubit_references_are_counted() -> None:
    """$0 같은 물리 큐비트 참조도 큐비트 수에 들어간다."""
    source = "OPENQASM 3.0;\nbit[2] b;\nh $0;\ncnot $0, $3;\nb[0] = measure $0;\nb[1] = measure $3;"
    action = json.dumps(
        {
            "braketSchemaHeader": {"name": "braket.ir.openqasm.program", "version": "1"},
            "source": source,
        }
    )
    with pytest.raises(ProgramInputError, match="2"):
        run_braket_program({"action": action, "shots": 10}, ExecutionSettings(1, None))


def test_registry_includes_braket() -> None:
    """braket-openqasm이 레지스트리에 등록돼 있다."""
    assert find_program_runner("braket-openqasm") is run_braket_program
