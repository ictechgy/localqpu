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


def semantic_role_of(program: Any) -> str | None:
    """QuantumProgram의 semantic_role. 클라이언트가 비공개 속성에 두므로 여기서만 읽는다."""
    return getattr(program, "_semantic_role", None)


__all__ = [
    "FAKE_PROVIDER_BACKENDS_DIR",
    "CompressedTensorModel",
    "ExecutorParamsModel",
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
