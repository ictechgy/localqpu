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


def openqasm_action(source: str) -> str:
    """OpenQASM 소스를 CreateQuantumTask action JSON으로 감싼다."""
    return json.dumps(
        {
            "braketSchemaHeader": {"name": "braket.ir.openqasm.program", "version": "1"},
            "source": source,
        }
    )


@pytest.mark.parametrize(
    "declaration",
    ["qubit [5] q;", "const int n = 5;\nqubit[n] q;"],
)
def test_limit_cannot_be_bypassed_by_declaration_style(declaration: str) -> None:
    """공백이 있는 선언이나 상수 크기 선언도 실제로 쓰인 큐비트 수로 세어 한도를 적용한다."""
    gates = "\n".join(f"h q[{index}];" for index in range(5))
    action = openqasm_action(f"OPENQASM 3.0;\n{declaration}\n{gates}")
    with pytest.raises(ProgramInputError, match="max-sim-qubits"):
        run_braket_program({"action": action, "shots": 10}, ExecutionSettings(4, None))


def test_declared_but_unused_qubits_do_not_count() -> None:
    """선언만 하고 쓰지 않은 큐비트는 계산 비용이 없으므로 세지 않는다."""
    action = openqasm_action(
        "OPENQASM 3.0;\nqubit[20] q;\nbit[1] b;\nh q[0];\nb[0] = measure q[0];"
    )
    output = run_braket_program({"action": action, "shots": 10}, ExecutionSettings(4, None))
    assert output.entangled_qubits == 1


def test_unparsable_source_is_input_error() -> None:
    """문법이 틀린 OpenQASM은 입력 오류다."""
    with pytest.raises(ProgramInputError, match="OpenQASM"):
        run_braket_program(
            {"action": openqasm_action("OPENQASM 3.0;\nqubit[2 q;"), "shots": 1}, SETTINGS
        )
