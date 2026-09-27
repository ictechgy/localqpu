"""qiskit-ibm-runtime 내부 모듈 의존을 한곳에 모은다.

localqpu는 클라이언트와 똑같이 인코딩·디코딩하려고 공개 API가 아닌 내부 모듈을 쓴다.
SDK가 바뀌면 이 파일만 고치면 되도록, 다른 모듈은 이 심볼들을 여기서만 가져온다(설계서 9절).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import qiskit_ibm_runtime.fake_provider as _fake_provider_package
from ibm_quantum_schemas.common.tensor import CompressedTensorModel
from ibm_quantum_schemas.executor.version_2_0.models import (
    ItemMetadataModel,
    MetadataModel,
    QuantumProgramResultItemModel,
    QuantumProgramResultModel,
)
from ibm_quantum_schemas.executor.version_2_0.models import ParamsModel as ExecutorParamsModel
from qiskit_ibm_runtime.fake_provider.executor.run_quantum_program import run_quantum_program
from qiskit_ibm_runtime.json import RuntimeDecoder, RuntimeEncoder
from qiskit_ibm_runtime.options_models.simulator import SimulatorOptions
from qiskit_ibm_runtime.quantum_program.converters.converters_2_0 import (
    passthrough_data_to_2_0,
    quantum_program_from_2_0,
)

#: qiskit-ibm-runtime가 함께 배포하는 실제 칩 스냅샷(conf_*.json, props_*.json) 폴더.
FAKE_PROVIDER_BACKENDS_DIR: Path = Path(_fake_provider_package.__file__).parent / "backends"


def finalize_samplex_items(program: Any) -> None:
    """JSON에서 되살린 samplex를 실행할 수 있게 완성한다.

    converters_2_0은 samplex를 finalize 전 상태로 되살린다(to_samplex). 클라이언트 로컬 모드는
    이미 완성된 객체를 넘기므로 이 단계가 없지만, localqpu는 전송 형식에서 복원하므로 필요하다.
    """
    for item in program.items:
        samplex = getattr(item, "samplex", None)
        if samplex is not None and not getattr(samplex, "_finalized", True):
            samplex.finalize()


def semantic_role_of(program: Any) -> str | None:
    """QuantumProgram의 semantic_role. 클라이언트가 비공개 속성에 두므로 여기서만 읽는다."""
    return getattr(program, "_semantic_role", None)


__all__ = [
    "FAKE_PROVIDER_BACKENDS_DIR",
    "CompressedTensorModel",
    "ExecutorParamsModel",
    "finalize_samplex_items",
    "ItemMetadataModel",
    "MetadataModel",
    "QuantumProgramResultItemModel",
    "QuantumProgramResultModel",
    "RuntimeDecoder",
    "RuntimeEncoder",
    "SimulatorOptions",
    "passthrough_data_to_2_0",
    "quantum_program_from_2_0",
    "run_quantum_program",
    "semantic_role_of",
]
