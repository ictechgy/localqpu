"""기존 EstimatorV2(program_id="estimator") 어댑터(v0.2 설계서 2절)."""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

import numpy as np
from qiskit.primitives.containers import DataBin, PrimitiveResult, PubResult
from qiskit.primitives.containers.estimator_pub import EstimatorPub
from qiskit_aer.primitives import EstimatorV2 as AerEstimator

from localqpu._compat import RuntimeEncoder
from localqpu.programs.base import ExecutionSettings, ProgramOutput
from localqpu.programs.decoding import (
    STRUCTURE_ERRORS,
    as_pub_like,
    decode_runtime_json,
    default_option,
    structure_error,
)
from localqpu.simulation import EXACT_AER_BACKEND_OPTIONS, count_entangled_qubits


def run_estimator_program(params: dict[str, Any], settings: ExecutionSettings) -> ProgramOutput:
    """EstimatorV2 입력의 기대값을 계산해 클라이언트 디코더가 읽는 JSON으로 돌려준다.

    정밀도가 없으면 정확한 기대값, 있으면 Aer가 그 정밀도의 정규분포 잡음을 더한다.
    얽힌 큐비트가 한도를 넘으면 모양만 맞춘 stub을 돌려준다.
    """
    pubs = _decode_pubs(params)
    entangled_qubits = count_entangled_qubits(pub.circuit for pub in pubs)
    is_stub = entangled_qubits > settings.max_sim_qubits
    result = (
        _stub_result(pubs, np.random.default_rng(settings.seed))
        if is_stub
        else _estimate(pubs, settings.seed)
    )
    payload = json.dumps(result, cls=RuntimeEncoder)
    return ProgramOutput(payload=payload, is_stub=is_stub, entangled_qubits=entangled_qubits)


def _decode_pubs(params: dict[str, Any]) -> list[EstimatorPub]:
    """params를 클라이언트와 같은 디코더로 풀어 EstimatorPub 목록으로 만든다."""
    decoded = decode_runtime_json(params, "estimator")
    try:
        precision = default_option(decoded, "default_precision")
        return [EstimatorPub.coerce(as_pub_like(pub), precision) for pub in decoded["pubs"]]
    except STRUCTURE_ERRORS as error:
        raise structure_error("estimator", error, "회로, 관측량, 파라미터 값, 정밀도") from error


def _estimate(pubs: Sequence[EstimatorPub], seed: int | None) -> PrimitiveResult:
    """Aer MPS로 기대값을 계산한다. 칩 폭 회로와 관측량을 그대로 넘긴다."""
    options: dict[str, Any] = {"backend_options": EXACT_AER_BACKEND_OPTIONS}
    if seed is not None:
        options["run_options"] = {"seed": seed}
    return AerEstimator(options=options).run(pubs).result()


def _stub_result(pubs: Sequence[EstimatorPub], generator: np.random.Generator) -> PrimitiveResult:
    """PUB마다 모양만 맞춘 무작위 기대값([-1, 1])과 요청 정밀도의 표준편차를 만든다."""
    return PrimitiveResult(
        [_stub_pub_result(pub, generator) for pub in pubs], metadata={"localqpu_stub": True}
    )


def _stub_pub_result(pub: EstimatorPub, generator: np.random.Generator) -> PubResult:
    """PUB 하나의 stub 결과."""
    precision = pub.precision or 0.0
    data = DataBin(
        evs=generator.uniform(-1.0, 1.0, size=pub.shape),
        stds=np.full(pub.shape, precision),
        shape=pub.shape,
    )
    return PubResult(data, metadata={"target_precision": precision, "localqpu_stub": True})
