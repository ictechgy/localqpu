"""세션·배치 API 흉내(v0.2 설계서 3절)."""

from __future__ import annotations

from functools import partial
from typing import Any

from localqpu.context import AppContext
from localqpu.server import (
    BadRequestError,
    NotFoundError,
    Request,
    Response,
    Router,
    error_response,
)
from localqpu.sessions import SessionRecord


def register_session_routes(router: Router, context: AppContext) -> None:
    """세션 라우트를 등록한다."""
    routes = [
        ("POST", "/api/v1/sessions", create_session),
        ("GET", "/api/v1/sessions/{session_id}", get_session),
        ("PATCH", "/api/v1/sessions/{session_id}", close_session),
        ("DELETE", "/api/v1/sessions/{session_id}/close", cancel_session),
    ]
    for method, pattern, handler in routes:
        router.add(method, pattern, partial(handler, context))


def create_session(context: AppContext, request: Request, params: dict[str, str]) -> Response:
    """세션을 만든다. 클라이언트는 응답의 id만 읽는다."""
    body = request.json_body()
    if not isinstance(body, dict):
        raise BadRequestError("세션 생성 본문은 JSON 객체여야 합니다.")
    backend_name = body.get("backend")
    if not isinstance(backend_name, str) or context.catalog.get(backend_name) is None:
        return error_response(
            400,
            f"localqpu에 '{backend_name}' 백엔드가 없습니다. 사용 가능: {', '.join(context.catalog.names)}",
        )
    try:
        session = context.sessions.create(
            str(body.get("mode", "dedicated")), backend_name, _optional_seconds(body.get("max_ttl"))
        )
    except ValueError as error:
        return error_response(400, str(error))
    return Response(200, session_to_api(session))


def get_session(context: AppContext, request: Request, params: dict[str, str]) -> Response:
    """세션 상세. Session.status()·details()·from_id()가 읽는다."""
    session = context.sessions.get(params["session_id"])
    if session is None:
        raise NotFoundError(_unknown_session_message(params["session_id"]))
    return Response(200, session_to_api(session))


def close_session(context: AppContext, request: Request, params: dict[str, str]) -> Response:
    """세션을 닫는다(accepting_jobs=false). 이미 제출된 작업은 계속 진행한다."""
    if not context.sessions.close(params["session_id"]):
        raise NotFoundError(_unknown_session_message(params["session_id"]))
    return Response(204)


def cancel_session(context: AppContext, request: Request, params: dict[str, str]) -> Response:
    """세션을 취소한다. 끝나지 않은 작업을 모두 취소하고 세션을 닫는다."""
    if not context.sessions.close(params["session_id"]):
        raise NotFoundError(_unknown_session_message(params["session_id"]))
    context.jobs.cancel_session_jobs(params["session_id"])
    return Response(204)


def session_to_api(session: SessionRecord) -> dict[str, Any]:
    """세션을 IBM 세션 상세 응답 형식으로 바꾼다."""
    return {
        "id": session.session_id,
        "mode": session.mode,
        "backend_name": session.backend_name,
        "state": session.state,
        "accepting_jobs": session.accepting_jobs,
        "max_ttl": session.max_ttl,
        "interactive_ttl": None,
        "active_ttl": None,
        "started_at": session.started_at,
        "activated_at": session.activated_at,
        "closed_at": session.closed_at,
        "last_job_started": session.last_job_started,
        "last_job_completed": None,
        "elapsed_time": 0,
    }


def _optional_seconds(value: object) -> int | None:
    """max_ttl 값. 정수가 아니면 없는 것으로 본다."""
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _unknown_session_message(session_id: str) -> str:
    """모르는 세션 ID 안내."""
    return f"localqpu에 '{session_id}' 세션이 없습니다. 세션은 메모리에만 보관하므로 서버를 재시작하면 사라집니다."
