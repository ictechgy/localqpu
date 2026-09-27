"""서버 설정과 구성 요소 묶음. 라우트 모듈이 app 모듈을 import하지 않도록 따로 둔다."""

from __future__ import annotations

from dataclasses import dataclass, field

from localqpu.constants import DEFAULT_BACKENDS, DEFAULT_HOST, DEFAULT_MAX_SIM_QUBITS, DEFAULT_PORT
from localqpu.ibm.backends import BackendCatalog
from localqpu.jobs import JobManager
from localqpu.scenario import Scenario, ScenarioState
from localqpu.server import ServerStats


@dataclass(frozen=True)
class ServerConfig:
    """서버 실행 설정. CLI 옵션과 1:1로 대응한다."""

    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT
    backends: tuple[str, ...] = DEFAULT_BACKENDS
    max_sim_qubits: int = DEFAULT_MAX_SIM_QUBITS
    scenario: Scenario = field(default_factory=Scenario)
    is_verbose: bool = False


@dataclass
class AppContext:
    """라우트 핸들러가 공유하는 구성 요소."""

    config: ServerConfig
    catalog: BackendCatalog
    scenario_state: ScenarioState
    jobs: JobManager
    stats: ServerStats
