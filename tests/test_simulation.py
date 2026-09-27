"""시뮬레이션 엔진 테스트."""

import numpy as np
import pytest
from qiskit import ClassicalRegister, QuantumCircuit, QuantumRegister
from qiskit.circuit import Clbit, Parameter
from qiskit.primitives.containers.sampler_pub import SamplerPub

from localqpu.simulation import (
    CircuitShapeError,
    active_qubit_indices,
    compact_idle_qubits,
    count_entangled_qubits,
    entangled_qubit_indices,
    sample_pubs,
)


def wide_bell_circuit() -> QuantumCircuit:
    """127큐비트 폭에서 40번과 90번만 쓰는 Bell 회로(칩 배치 후 모양을 흉내 냄)."""
    circuit = QuantumCircuit(127)
    register = ClassicalRegister(2, "meas")
    circuit.add_register(register)
    circuit.h(40)
    circuit.cx(40, 90)
    circuit.barrier()
    circuit.measure(40, register[0])
    circuit.measure(90, register[1])
    return circuit


def sweep_pub() -> SamplerPub:
    """세 개의 각도(0, π, π/2)를 훑는 1큐비트 파라미터 회로 PUB."""
    theta = Parameter("theta")
    circuit = QuantumCircuit(3)
    register = ClassicalRegister(1, "c")
    circuit.add_register(register)
    circuit.ry(theta, 2)
    # 측정 대상(2번)의 분포는 바꾸지 않으면서 얽힌 큐비트 2개를 만들어 stub 경로도 검사한다.
    circuit.cx(2, 1)
    circuit.measure(2, register[0])
    return SamplerPub.coerce((circuit, np.array([[0.0], [np.pi], [np.pi / 2]]), 50))


def test_active_qubits_ignore_barriers() -> None:
    """전 큐비트 barrier가 있어도 실제 연산이 걸린 큐비트만 활성으로 센다."""
    assert active_qubit_indices(wide_bell_circuit()) == [40, 90]


def test_entangled_qubits_ignore_single_qubit_and_measure_only_qubits() -> None:
    """얽힌 큐비트는 여러 큐비트 게이트가 걸린 큐비트만 센다. 측정·단일 게이트만 걸린 큐비트는 빠진다."""
    circuit = QuantumCircuit(127)
    circuit.h(0)
    circuit.cx(0, 1)
    for qubit in range(127):
        circuit.rz(0.3, qubit)
    circuit.measure_all()
    assert entangled_qubit_indices(circuit) == [0, 1]


def test_count_entangled_qubits_takes_maximum() -> None:
    """여러 회로 중 가장 많은 얽힌 큐비트 수를 돌려준다."""
    single = QuantumCircuit(5)
    single.x(0)
    assert count_entangled_qubits([single, wide_bell_circuit()]) == 2
    assert count_entangled_qubits([]) == 0


def test_wide_measure_all_is_simulated_exactly() -> None:
    """127큐비트를 모두 측정해도 얽힌 큐비트가 적으면 stub이 아니라 정확히 계산한다."""
    circuit = QuantumCircuit(127)
    circuit.h(0)
    circuit.cx(0, 1)
    circuit.measure_all()
    outcome = sample_pubs([SamplerPub.coerce((circuit, None, 100))], 24, seed=6)
    counts = outcome.result[0].data.meas.get_counts()
    assert outcome.is_stub is False and outcome.entangled_qubits == 2
    assert set(counts) <= {"0" * 127, "0" * 125 + "11"}


def test_compaction_keeps_registers_and_shrinks_width() -> None:
    """압축하면 폭은 활성 큐비트 수가 되고 고전 레지스터는 그대로다."""
    compact = compact_idle_qubits(wide_bell_circuit())
    assert compact.num_qubits == 2
    assert [register.name for register in compact.cregs] == ["meas"]


def test_compaction_rejects_loose_clbits() -> None:
    """레지스터 밖 고전 비트는 해결 방법과 함께 거절한다."""
    circuit = QuantumCircuit(QuantumRegister(1), [Clbit()])
    circuit.measure(0, 0)
    with pytest.raises(CircuitShapeError, match="ClassicalRegister"):
        compact_idle_qubits(circuit)


def test_wide_bell_is_simulated_exactly() -> None:
    """127큐비트 폭 Bell 회로도 00/11만 나오고 합이 shots와 같다."""
    pub = SamplerPub.coerce((wide_bell_circuit(), None, 300))
    outcome = sample_pubs([pub], max_sim_qubits=24, seed=11)
    counts = outcome.result[0].data.meas.get_counts()
    assert set(counts) <= {"00", "11"} and sum(counts.values()) == 300
    assert outcome.is_stub is False and outcome.entangled_qubits == 2


def test_same_seed_gives_same_counts() -> None:
    """시드가 같으면 결과가 같다."""
    pub = SamplerPub.coerce((wide_bell_circuit(), None, 300))
    first = sample_pubs([pub], 24, seed=5).result[0].data.meas.get_counts()
    second = sample_pubs([pub], 24, seed=5).result[0].data.meas.get_counts()
    assert first == second


def test_over_limit_returns_marked_stub() -> None:
    """한도를 넘으면 모양만 맞춘 stub을 돌려주고 메타데이터에 표시한다."""
    pub = SamplerPub.coerce((wide_bell_circuit(), None, 64))
    outcome = sample_pubs([pub], max_sim_qubits=1, seed=1)
    bit_array = outcome.result[0].data.meas
    assert outcome.is_stub is True
    assert bit_array.num_shots == 64 and bit_array.num_bits == 2
    assert outcome.result.metadata["localqpu_stub"] is True


def test_parameter_sweep_shape_exact() -> None:
    """파라미터 스윕 PUB의 결과 모양이 PUB 모양과 같고 값이 물리적으로 맞다."""
    result = sample_pubs([sweep_pub()], 24, seed=2).result[0].data.c
    assert result.shape == (3,)
    assert result.get_counts(0) == {"0": 50}
    assert result.get_counts(1) == {"1": 50}


def test_parameter_sweep_shape_stub() -> None:
    """stub에서도 파라미터 스윕 모양이 유지된다."""
    result = sample_pubs([sweep_pub()], 0, seed=2).result[0].data.c
    assert result.shape == (3,) and result.num_shots == 50


def test_mid_circuit_measurement_is_simulated() -> None:
    """중간 측정이 있는 회로도 정확히 계산된다(실제 하드웨어가 지원하는 동적 회로)."""
    circuit = QuantumCircuit(3)
    register = ClassicalRegister(2, "c")
    circuit.add_register(register)
    circuit.h(2)
    circuit.measure(2, register[0])
    circuit.x(2)
    circuit.measure(2, register[1])
    outcome = sample_pubs([SamplerPub.coerce((circuit, None, 200))], 24, seed=4)
    counts = outcome.result[0].data.c.get_counts()
    assert set(counts) <= {"01", "10"} and sum(counts.values()) == 200


def test_classical_feedforward_is_simulated() -> None:
    """측정 결과에 따라 게이트를 거는 if_test 회로도 정확히 계산된다."""
    circuit = QuantumCircuit(2)
    register = ClassicalRegister(2, "c")
    circuit.add_register(register)
    circuit.h(0)
    circuit.measure(0, register[0])
    with circuit.if_test((register[0], 1)):
        circuit.x(1)
    circuit.measure(1, register[1])
    outcome = sample_pubs([SamplerPub.coerce((circuit, None, 200))], 24, seed=5)
    counts = outcome.result[0].data.c.get_counts()
    assert set(counts) <= {"00", "11"} and sum(counts.values()) == 200


def brisbane_bell_pub(shots: int) -> SamplerPub:
    """ibm_brisbane 칩에 맞게 배치한 Bell 회로 PUB(노이즈 모델은 물리 큐비트 기준이라 배치가 필요)."""
    from qiskit.transpiler import generate_preset_pass_manager

    from localqpu.noise import fake_backend_for

    circuit = QuantumCircuit(2)
    circuit.h(0)
    circuit.cx(0, 1)
    circuit.measure_all()
    backend = fake_backend_for("ibm_brisbane")
    isa = generate_preset_pass_manager(backend=backend, optimization_level=1).run(circuit)
    return SamplerPub.coerce((isa, None, shots))


def test_noise_introduces_errors_on_bell() -> None:
    """노이즈를 켜면 Bell 회로에 01·10 오류가 섞이고, 끄면 없다."""
    pub = brisbane_bell_pub(4000)
    noisy = (
        sample_pubs([pub], 24, seed=7, noise_backend="ibm_brisbane")
        .result[0]
        .data.meas.get_counts()
    )
    ideal = sample_pubs([pub], 24, seed=7).result[0].data.meas.get_counts()
    assert noisy.get("01", 0) + noisy.get("10", 0) > 0
    assert set(ideal) <= {"00", "11"}
