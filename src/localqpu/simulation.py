"""회로 시뮬레이션.

칩 배치가 끝난 회로는 칩 전체 폭(예: 127큐비트)을 가진다. 계산 비용을 좌우하는 것은 폭이 아니라
여러 큐비트 게이트로 얽힌 큐비트 수이므로, 한도는 얽힌 큐비트 수로 판단하고 정확 계산은
Aer MPS(matrix_product_state)로 한다. 얽히지 않은 큐비트는 곱 상태라 비용이 거의 없다.
한도를 넘으면 결과 모양만 맞춘 stub을 돌려준다(v0.2 설계서 1절).
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

import numpy as np
from qiskit import QuantumCircuit
from qiskit.circuit import Barrier, CircuitInstruction
from qiskit.primitives.containers import BitArray, DataBin, PrimitiveResult, SamplerPubResult
from qiskit.primitives.containers.sampler_pub import SamplerPub
from qiskit_aer.primitives import SamplerV2 as AerSampler

#: 큐비트를 "사용 중"으로 만들지 않는 연산. 배치 후 회로에는 전 큐비트 barrier가 흔하다.
_NON_COMPUTATIONAL_OPERATIONS: frozenset[str] = frozenset({"barrier", "delay"})

#: 정확 계산에 쓰는 Aer 옵션. MPS는 절단 설정이 없으면 정확하고, 넓지만 덜 얽힌 회로에 빠르다.
EXACT_AER_BACKEND_OPTIONS: dict[str, str] = {"method": "matrix_product_state"}


class CircuitShapeError(ValueError):
    """localqpu가 아직 다루지 못하는 회로 구조. 메시지에 회로를 고치는 방법을 담는다."""


@dataclass(frozen=True)
class SimulationOutcome:
    """시뮬레이션 결과와 부가 정보.

    Attributes:
        result: 클라이언트에 돌려줄 PrimitiveResult.
        is_stub: 정확 계산 대신 모양만 맞춘 무작위 값인지.
        entangled_qubits: PUB들 중 가장 많은 얽힌 큐비트 수(한도 판단 기준).
    """

    result: PrimitiveResult
    is_stub: bool
    entangled_qubits: int


def active_qubit_indices(circuit: QuantumCircuit) -> list[int]:
    """barrier·delay를 제외한 연산이 걸린 큐비트 인덱스를 오름차순으로 돌려준다."""
    indices = {
        circuit.find_bit(qubit).index
        for instruction in circuit.data
        if instruction.operation.name not in _NON_COMPUTATIONAL_OPERATIONS
        for qubit in instruction.qubits
    }
    return sorted(indices)


def entangled_qubit_indices(circuit: QuantumCircuit) -> list[int]:
    """두 개 이상의 큐비트에 걸린 연산(barrier 제외)의 큐비트 인덱스를 오름차순으로 돌려준다.

    제어 흐름 연산은 블록 안의 모든 큐비트에 걸쳐 있으므로 보수적으로 얽힌 것으로 센다.
    """
    indices = {
        circuit.find_bit(qubit).index
        for instruction in circuit.data
        if instruction.operation.name != "barrier" and len(instruction.qubits) >= 2
        for qubit in instruction.qubits
    }
    return sorted(indices)


def count_entangled_qubits(circuits: Iterable[QuantumCircuit]) -> int:
    """여러 회로 중 가장 많은 얽힌 큐비트 수. 회로가 없으면 0."""
    return max((len(entangled_qubit_indices(circuit)) for circuit in circuits), default=0)


def compact_idle_qubits(circuit: QuantumCircuit) -> QuantumCircuit:
    """활성 큐비트만 남긴 회로를 만든다. 고전 레지스터와 연산 순서는 그대로 둔다.

    Raises:
        CircuitShapeError: 레지스터에 속하지 않은 고전 비트가 있을 때.
    """
    _ensure_registered_clbits(circuit)
    mapping = {old: new for new, old in enumerate(active_qubit_indices(circuit))}
    compact = QuantumCircuit(len(mapping), name=circuit.name, global_phase=circuit.global_phase)
    for register in circuit.cregs:
        compact.add_register(register)
    for instruction in circuit.data:
        _append_mapped(compact, circuit, instruction, mapping)
    return compact


def sample_pubs(
    pubs: Sequence[SamplerPub], max_sim_qubits: int, seed: int | None
) -> SimulationOutcome:
    """PUB들을 샘플링한다. 얽힌 큐비트가 한도를 넘으면 stub을 만든다."""
    entangled_qubits = count_entangled_qubits(pub.circuit for pub in pubs)
    if entangled_qubits > max_sim_qubits:
        stub = _stub_result(pubs, np.random.default_rng(seed))
        return SimulationOutcome(stub, is_stub=True, entangled_qubits=entangled_qubits)
    compacted = [
        SamplerPub(compact_idle_qubits(pub.circuit), pub.parameter_values, pub.shots)
        for pub in pubs
    ]
    # 중간 측정·조건 분기(동적 회로)를 실제 하드웨어처럼 지원하려고 Aer를 쓴다.
    sampler = AerSampler(seed=seed, options={"backend_options": EXACT_AER_BACKEND_OPTIONS})
    result = sampler.run(compacted).result()
    return SimulationOutcome(result, is_stub=False, entangled_qubits=entangled_qubits)


def _ensure_registered_clbits(circuit: QuantumCircuit) -> None:
    """모든 고전 비트가 레지스터에 속하는지 확인한다. 압축 시 비트 순서를 보존하기 위함이다."""
    if len(circuit.clbits) != sum(register.size for register in circuit.cregs):
        raise CircuitShapeError(
            "레지스터에 속하지 않은 고전 비트가 있는 회로는 아직 지원하지 않습니다. "
            "측정 대상은 ClassicalRegister로 만들어 주세요."
        )


def _append_mapped(
    compact: QuantumCircuit,
    original: QuantumCircuit,
    instruction: CircuitInstruction,
    mapping: dict[int, int],
) -> None:
    """연산 하나를 압축된 큐비트 번호로 옮겨 붙인다. 유휴 큐비트에만 걸린 barrier·delay는 버린다."""
    original_indices = [original.find_bit(qubit).index for qubit in instruction.qubits]
    kept_indices = [mapping[index] for index in original_indices if index in mapping]
    if not kept_indices:
        return
    operation = instruction.operation
    if operation.name == "barrier" and len(kept_indices) != len(original_indices):
        operation = Barrier(len(kept_indices))
    compact.append(operation, [compact.qubits[index] for index in kept_indices], instruction.clbits)


def _stub_result(pubs: Sequence[SamplerPub], generator: np.random.Generator) -> PrimitiveResult:
    """PUB마다 레지스터 이름·비트 수·shots·모양만 맞춘 무작위 결과를 만든다."""
    pub_results = [_stub_pub_result(pub, generator) for pub in pubs]
    return PrimitiveResult(pub_results, metadata={"localqpu_stub": True})


def _stub_pub_result(pub: SamplerPub, generator: np.random.Generator) -> SamplerPubResult:
    """PUB 하나의 stub 결과."""
    arrays = {
        register.name: _random_bit_array(pub.shape, pub.shots, register.size, generator)
        for register in pub.circuit.cregs
    }
    data = DataBin(**arrays, shape=pub.shape)
    return SamplerPubResult(data, metadata={"shots": pub.shots, "localqpu_stub": True})


def _random_bit_array(
    shape: tuple[int, ...], shots: int, num_bits: int, generator: np.random.Generator
) -> BitArray:
    """균등 무작위 비트열로 채운 BitArray."""
    bits = generator.integers(0, 2, size=(*shape, shots, num_bits)).astype(bool)
    return BitArray.from_bool_array(bits)
