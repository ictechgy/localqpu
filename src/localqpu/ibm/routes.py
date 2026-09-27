"""IBM Quantum Platform API 흉내. 경로와 응답 형식은 설계서 5.3절 표를 따른다.

경로가 서로 겹치지 않으므로 호스트는 보지 않는다. 인증·검색·카탈로그는 IBM Cloud 공통 API이고,
/api/v1/* 나머지가 런타임 API다.
"""

from __future__ import annotations

import time
from functools import partial
from typing import Any

import jwt

from localqpu.constants import LOCAL_INSTANCE_CRN, LOCAL_PLAN_ID, TOKEN_LIFETIME_SECONDS
from localqpu.context import AppContext
from localqpu.ibm.backends import BackendSnapshot
from localqpu.jobs import JobRecord
from localqpu.programs.base import UnsupportedProgramError
from localqpu.scenario import BackendOverride
from localqpu.server import (
    BadRequestError,
    NotFoundError,
    Request,
    Response,
    Router,
    error_response,
)

#: 가짜 토큰 서명 키. 비밀이 아니다(클라이언트는 서명을 검증하지 않고 exp만 읽는다).
#: 32바이트 이상이어야 PyJWT의 짧은 키 경고가 나지 않는다.
_TOKEN_SIGNING_KEY = "localqpu-token-signing-key-not-a-secret"


def register_ibm_routes(router: Router, context: AppContext) -> None:
    """IBM API 라우트를 모두 등록한다."""
    routes = [
        ("POST", "/identity/token", issue_token),
        ("POST", "/v3/resources/search", search_instances),
        ("GET", f"/api/v1/{LOCAL_PLAN_ID}", get_plan),
        ("GET", "/api/v1/backends", list_backends),
        ("GET", "/api/v1/backends/{name}/configuration", get_configuration),
        ("GET", "/api/v1/backends/{name}/properties", get_properties),
        ("GET", "/api/v1/backends/{name}/status", get_backend_status),
        ("GET", "/api/v1/instances/usage", get_usage),
        ("POST", "/api/v1/jobs", submit_job),
        ("GET", "/api/v1/jobs/{job_id}", get_job),
        ("GET", "/api/v1/jobs/{job_id}/results", get_job_results),
        ("POST", "/api/v1/jobs/{job_id}/cancel", cancel_job),
    ]
    for method, pattern, handler in routes:
        router.add(method, pattern, partial(handler, context))


def issue_token(context: AppContext, request: Request, params: dict[str, str]) -> Response:
    """IAM 토큰을 발급한다. 클라이언트가 exp를 디코딩하므로 JWT 형식이어야 한다."""
    if context.scenario_state.current().reject_tokens:
        message = "localqpu: 시나리오 auth.reject_tokens=true라 토큰 발급을 거부했습니다."
        return Response(400, {"errorCode": "BXNIM0415E", "errorMessage": message})
    now = int(time.time())
    claims = {"iat": now, "exp": now + TOKEN_LIFETIME_SECONDS, "sub": "localqpu"}
    token = jwt.encode(claims, _TOKEN_SIGNING_KEY, algorithm="HS256")
    return Response(
        200,
        {
            "access_token": token,
            "refresh_token": "localqpu-refresh",
            "token_type": "Bearer",
            "expires_in": TOKEN_LIFETIME_SECONDS,
            "expiration": now + TOKEN_LIFETIME_SECONDS,
        },
    )


def search_instances(context: AppContext, request: Request, params: dict[str, str]) -> Response:
    """localqpu 인스턴스 하나를 돌려준다. extensions가 있어야 클라이언트가 인스턴스로 인정한다."""
    item = {
        "crn": LOCAL_INSTANCE_CRN,
        "service_plan_unique_id": LOCAL_PLAN_ID,
        "name": "localqpu",
        "doc": {"extensions": {"localqpu": True}},
        "tags": [],
    }
    return Response(200, {"items": [item], "search_cursor": None})


def get_plan(context: AppContext, request: Request, params: dict[str, str]) -> Response:
    """카탈로그 플랜 정보."""
    return Response(
        200,
        {
            "overview_ui": {"en": {"display_name": "localqpu"}},
            "metadata": {"pricing": {"type": "free"}},
        },
    )


def list_backends(context: AppContext, request: Request, params: dict[str, str]) -> Response:
    """백엔드 목록. 시나리오의 상태와 대기열 길이를 반영한다."""
    scenario = context.scenario_state.current()
    devices = [
        _device_entry(snapshot, scenario.backend_override(snapshot.name))
        for snapshot in context.catalog.snapshots()
    ]
    return Response(200, {"devices": devices})


def _device_entry(snapshot: BackendSnapshot, override: BackendOverride) -> dict[str, Any]:
    """목록 항목 하나. least_busy()가 status.name과 queue_length를 읽는다."""
    return {
        "name": snapshot.name,
        "status": {"name": override.status, "reason": ""},
        "qubits": snapshot.num_qubits,
        "queue_length": override.queue_length,
    }


def get_configuration(context: AppContext, request: Request, params: dict[str, str]) -> Response:
    """칩 설정 스냅샷."""
    return Response(200, _require_backend(context, params["name"]).configuration)


def get_properties(context: AppContext, request: Request, params: dict[str, str]) -> Response:
    """칩 보정값 스냅샷."""
    snapshot = _require_backend(context, params["name"])
    if snapshot.properties is None:
        raise NotFoundError(f"'{snapshot.name}' 스냅샷에는 보정값(props) 파일이 없습니다.")
    return Response(200, snapshot.properties)


def get_backend_status(context: AppContext, request: Request, params: dict[str, str]) -> Response:
    """백엔드 상태. 클라이언트는 state를 operational로, status를 status_msg로 읽는다."""
    snapshot = _require_backend(context, params["name"])
    override = context.scenario_state.current().backend_override(snapshot.name)
    status_message = "active" if override.status == "online" else override.status
    body = {
        "state": override.status != "offline",
        "status": status_message,
        "message": "",
        "length_queue": override.queue_length,
        "backend_version": str(snapshot.configuration.get("backend_version", "1.0.0")),
    }
    return Response(200, body)


def get_usage(context: AppContext, request: Request, params: dict[str, str]) -> Response:
    """인스턴스 사용량. 한도에 도달하면 클라이언트는 경고를 낸다."""
    usage = context.scenario_state.current().usage
    body = {
        "usage_consumed_seconds": usage.consumed_seconds,
        "usage_limit_seconds": usage.limit_seconds,
        "usage_allocation_seconds": usage.limit_seconds,
        "usage_limit_reached": usage.is_exhausted,
    }
    return Response(200, body)


def submit_job(context: AppContext, request: Request, params: dict[str, str]) -> Response:
    """작업 제출. 사용량·백엔드·프로그램을 확인한 뒤 작업 관리자에 등록한다."""
    payload = _require_object(request.json_body())
    rejection = _reject_malformed_submission(payload) or _reject_submission(context, payload)
    if rejection is not None:
        return rejection
    try:
        job = context.jobs.submit(
            payload["program_id"], payload["backend"], payload.get("params") or {}
        )
    except UnsupportedProgramError as error:
        return error_response(404, str(error))
    return Response(200, {"id": job.job_id, "backend": job.backend_name})


def _reject_malformed_submission(payload: dict[str, Any]) -> Response | None:
    """필드 타입이 틀린 제출은 400으로 거절한다. 직접 모드 클라이언트의 실수를 500으로 만들지 않기 위함이다."""
    for field_name in ("program_id", "backend"):
        if not isinstance(payload.get(field_name), str):
            return error_response(
                400,
                f"제출 본문의 '{field_name}'은(는) 문자열이어야 합니다(받은 값: {payload.get(field_name)!r}).",
            )
    if not isinstance(payload.get("params", {}), dict):
        return error_response(400, "제출 본문의 'params'는 JSON 객체여야 합니다.")
    return None


def _reject_submission(context: AppContext, payload: dict[str, Any]) -> Response | None:
    """제출을 거절해야 하면 오류 응답을, 아니면 None을 돌려준다."""
    if context.scenario_state.current().usage.is_exhausted:
        return error_response(
            403,
            "localqpu: 사용량 한도에 도달했습니다(시나리오 usage). 시나리오를 바꾸거나 /_localqpu/reset을 호출하세요.",
        )
    backend_name = payload.get("backend")
    if context.catalog.get(backend_name) is None:
        return error_response(
            400,
            f"localqpu에 '{backend_name}' 백엔드가 없습니다. 사용 가능: {', '.join(context.catalog.names)}",
        )
    return None


def get_job(context: AppContext, request: Request, params: dict[str, str]) -> Response:
    """작업 상태 조회. 조회할 때마다 작업이 한 단계씩 진행된다."""
    job = context.jobs.poll(params["job_id"])
    if job is None:
        raise NotFoundError(_unknown_job_message(params["job_id"]))
    return Response(200, job_to_api(job))


def get_job_results(context: AppContext, request: Request, params: dict[str, str]) -> Response:
    """완료된 작업은 결과 JSON을, 실패·취소된 작업은 사유 문자열을 돌려준다.

    클라이언트는 작업이 실패해도 에러 메시지를 만들려고 이 경로를 부른다
    (base_runtime_job._error_msg_from_job_response). 여기서 오류 코드를 돌려주면
    기대한 RuntimeJobFailureError 대신 RequestsApiError가 난다.
    """
    job = context.jobs.get(params["job_id"])
    if job is None:
        raise NotFoundError(_unknown_job_message(params["job_id"]))
    if job.status in ("Failed", "Cancelled"):
        return Response(
            200,
            f"localqpu: job {job.job_id} {job.status.lower()}: {job.reason or 'no reason given'}",
        )
    if job.status != "Completed" or job.result_payload is None:
        return error_response(409, f"작업 '{job.job_id}'는 {job.status} 상태라 결과가 없습니다.")
    return Response(200, job.result_payload)


def cancel_job(context: AppContext, request: Request, params: dict[str, str]) -> Response:
    """작업 취소. 이미 끝난 작업은 409(클라이언트는 RuntimeInvalidStateError)."""
    outcome = context.jobs.cancel(params["job_id"])
    if outcome == "not_found":
        raise NotFoundError(_unknown_job_message(params["job_id"]))
    if outcome == "already_final":
        return error_response(409, f"작업 '{params['job_id']}'는 이미 끝나서 취소할 수 없습니다.")
    return Response(204)


def job_to_api(job: JobRecord) -> dict[str, Any]:
    """작업을 IBM 작업 조회 응답 형식으로 바꾼다."""
    state = {"status": job.status, "reason": job.reason, "reason_code": job.reason_code}
    return {
        "id": job.job_id,
        "backend": job.backend_name,
        "status": job.status,
        "state": state,
        "program": {"id": job.program_id},
        "created": job.created,
        "usage": {"quantum_seconds": 0},
    }


def _require_backend(context: AppContext, name: str) -> BackendSnapshot:
    """이름으로 스냅샷을 찾는다. 없으면 404."""
    snapshot = context.catalog.get(name)
    if snapshot is None:
        raise NotFoundError(
            f"localqpu에 '{name}' 백엔드가 없습니다. 사용 가능: {', '.join(context.catalog.names)}"
        )
    return snapshot


def _require_object(body: Any) -> dict[str, Any]:
    """본문이 JSON 객체인지 확인한다."""
    if not isinstance(body, dict):
        raise BadRequestError("요청 본문은 JSON 객체여야 합니다.")
    return body


def _unknown_job_message(job_id: str) -> str:
    """모르는 작업 ID 안내. 재시작으로 기록이 사라졌을 가능성을 알려 준다."""
    return f"localqpu에 '{job_id}' 작업이 없습니다. localqpu는 작업을 메모리에만 보관하므로 서버를 재시작하면 이전 작업은 사라집니다."
