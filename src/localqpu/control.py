"""/_localqpu 제어 API. 테스트 코드가 서버 상태를 바꾸고 검사하는 용도다(설계서 5.8절)."""

from __future__ import annotations

from functools import partial
from typing import Any

from localqpu import __version__
from localqpu.context import AppContext
from localqpu.jobs import JobRecord
from localqpu.scenario import (
    ScenarioError,
    ensure_known_backends,
    parse_scenario,
    scenario_to_json,
)
from localqpu.server import Request, Response, Router, error_response


def register_control_routes(router: Router, context: AppContext) -> None:
    """제어 라우트를 등록한다."""
    routes = [
        ("GET", "/_localqpu/health", get_health),
        ("GET", "/_localqpu/scenario", get_scenario),
        ("PUT", "/_localqpu/scenario", put_scenario),
        ("POST", "/_localqpu/reset", reset_state),
        ("GET", "/_localqpu/jobs", list_jobs),
    ]
    for method, pattern, handler in routes:
        router.add(method, pattern, partial(handler, context))


def get_health(context: AppContext, request: Request, params: dict[str, str]) -> Response:
    """상태 확인. 막힌 CONNECT 수로 외부 유출 시도를 확인할 수 있다."""
    body = {
        "status": "ok",
        "version": __version__,
        "backends": context.catalog.names,
        "blocked_connect_requests": context.stats.blocked_connect_requests,
    }
    return Response(200, body)


def get_scenario(context: AppContext, request: Request, params: dict[str, str]) -> Response:
    """현재 시나리오(소비하지 않은 next_jobs 포함)."""
    return Response(200, scenario_to_json(context.scenario_state.current()))


def put_scenario(context: AppContext, request: Request, params: dict[str, str]) -> Response:
    """시나리오를 교체한다. 잘못된 시나리오는 400이고 기존 시나리오를 유지한다."""
    try:
        scenario = parse_scenario(request.json_body())
        ensure_known_backends(scenario, context.catalog.names)
    except ScenarioError as error:
        return error_response(400, str(error))
    context.scenario_state.set_scenario(scenario)
    return Response(200, scenario_to_json(scenario))


def reset_state(context: AppContext, request: Request, params: dict[str, str]) -> Response:
    """작업 기록, 시나리오(시작 시점으로), 통계를 초기화한다. 작업·시나리오는 한 잠금에서 함께 되돌린다."""
    context.jobs.reset()
    context.sessions.reset()
    context.stats.reset()
    return Response(204)


def list_jobs(context: AppContext, request: Request, params: dict[str, str]) -> Response:
    """제출된 작업 요약. 테스트 단언용이며 상태를 진행시키지 않는다."""
    return Response(200, {"jobs": [_job_summary(job) for job in context.jobs.list_jobs()]})


def _job_summary(job: JobRecord) -> dict[str, Any]:
    """작업 하나의 요약."""
    return {
        "id": job.job_id,
        "program_id": job.program_id,
        "backend": job.backend_name,
        "status": job.status,
        "reason": job.reason,
        "reason_code": job.reason_code,
        "is_stub": job.is_stub,
        "entangled_qubits": job.entangled_qubits,
        "session_id": job.session_id,
        "polls": job.polls,
    }
