"""구성 요소를 조립해 서버를 띄운다."""

from __future__ import annotations

from localqpu.aws.devices import SV1_BACKEND_NAME
from localqpu.aws.routes import register_aws_routes
from localqpu.context import AppContext, ServerConfig
from localqpu.control import register_control_routes
from localqpu.ibm.backends import BackendCatalog
from localqpu.ibm.job_routes import register_job_routes
from localqpu.ibm.routes import register_ibm_routes
from localqpu.ibm.session_routes import register_session_routes
from localqpu.jobs import JobManager
from localqpu.scenario import ScenarioState, ensure_known_backends
from localqpu.server import (
    FaultDecision,
    FaultPolicy,
    Request,
    Router,
    RunningServer,
    ServerStats,
    serve,
)
from localqpu.sessions import SessionManager


def build_context(config: ServerConfig) -> AppContext:
    """설정으로 카탈로그·시나리오·작업 관리자·통계를 만든다.

    Raises:
        BackendCatalogError: 없는 칩을 지정했을 때.
        ScenarioError: 시나리오가 카탈로그에 없는 백엔드를 가리킬 때.
    """
    catalog = BackendCatalog(config.backends)
    ensure_known_backends(config.scenario, [*catalog.names, SV1_BACKEND_NAME])
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
    register_job_routes(router, context)
    register_session_routes(router, context)
    register_control_routes(router, context)
    register_aws_routes(router, context)
    return router


def scenario_fault_policy(scenario_state: ScenarioState) -> FaultPolicy:
    """시나리오의 http_faults 규칙을 요청마다 소비해 서버의 장애 결정으로 바꾸는 정책."""

    def decide(request: Request) -> FaultDecision | None:
        """요청에 맞는 규칙이 있으면 한 번 소비해 장애 결정을 돌려준다."""
        fault = scenario_state.consume_http_fault(request.method, request.path)
        if fault is None:
            return None
        return FaultDecision(
            status=fault.status,
            retry_after=fault.retry_after,
            message=fault.message,
            drop_connection=fault.drop_connection,
            delay_seconds=fault.delay_seconds,
            phase=fault.phase,
        )

    return decide


def start_server(config: ServerConfig | None = None) -> RunningServer:
    """localqpu 서버를 백그라운드로 띄운다. 멈출 때 작업 스레드 풀도 정리한다."""
    context = build_context(config or ServerConfig())
    server = serve(
        build_router(context),
        context.stats,
        context.config.host,
        context.config.port,
        context.config.is_verbose,
        fault_policy=scenario_fault_policy(context.scenario_state),
    )
    server.on_stop = context.jobs.shutdown
    return server
