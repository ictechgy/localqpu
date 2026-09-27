"""구성 요소를 조립해 서버를 띄운다."""

from __future__ import annotations

from localqpu.aws.routes import register_aws_routes
from localqpu.context import AppContext, ServerConfig
from localqpu.control import register_control_routes
from localqpu.ibm.backends import BackendCatalog
from localqpu.ibm.routes import register_ibm_routes
from localqpu.ibm.session_routes import register_session_routes
from localqpu.jobs import JobManager
from localqpu.scenario import ScenarioState, ensure_known_backends
from localqpu.server import Router, RunningServer, ServerStats, serve
from localqpu.sessions import SessionManager


def build_context(config: ServerConfig) -> AppContext:
    """설정으로 카탈로그·시나리오·작업 관리자·통계를 만든다.

    Raises:
        BackendCatalogError: 없는 칩을 지정했을 때.
        ScenarioError: 시나리오가 카탈로그에 없는 백엔드를 가리킬 때.
    """
    catalog = BackendCatalog(config.backends)
    ensure_known_backends(config.scenario, catalog.names)
    scenario_state = ScenarioState(config.scenario)
    return AppContext(
        config=config,
        catalog=catalog,
        scenario_state=scenario_state,
        jobs=JobManager(scenario_state, config.max_sim_qubits),
        stats=ServerStats(),
        sessions=SessionManager(),
    )


def build_router(context: AppContext) -> Router:
    """모든 라우트를 등록한 라우터."""
    router = Router()
    register_ibm_routes(router, context)
    register_session_routes(router, context)
    register_control_routes(router, context)
    register_aws_routes(router, context)
    return router


def start_server(config: ServerConfig | None = None) -> RunningServer:
    """localqpu 서버를 백그라운드로 띄운다. 멈출 때 작업 스레드 풀도 정리한다."""
    context = build_context(config or ServerConfig())
    server = serve(
        build_router(context),
        context.stats,
        context.config.host,
        context.config.port,
        context.config.is_verbose,
    )
    server.on_stop = context.jobs.shutdown
    return server
