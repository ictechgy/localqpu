"""계약 테스트 공용 도우미."""

from qiskit import QuantumCircuit
from qiskit.transpiler import generate_preset_pass_manager


def bell_isa_circuit(backend: object) -> QuantumCircuit:
    """백엔드에 맞게 트랜스파일한 Bell 회로(실제 사용자 코드와 같은 절차)."""
    circuit = QuantumCircuit(2)
    circuit.h(0)
    circuit.cx(0, 1)
    circuit.measure_all()
    return generate_preset_pass_manager(backend=backend, optimization_level=1).run(circuit)
