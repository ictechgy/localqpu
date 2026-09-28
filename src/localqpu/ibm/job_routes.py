"""작업 조회·관리 API 흉내: 목록, metrics, logs, 태그 갱신, 삭제(v0.3 설계서 3절)."""

from __future__ import annotations

import datetime
from functools import partial
from typing import Any

from localqpu.aws.routes import BRAKET_PROGRAM_ID
from localqpu.context import AppContext
from localqpu.ibm.routes import job_to_api, unknown_job_message
from localqpu.jobs import JobRecord
from localqpu.server import BadRequestError, NotFoundError, Request, Response, Router

#: 목록 기본 페이지 크기(클라이언트 기본값과 같다).
DEFAULT_PAGE_LIMIT = 20


def register_job_routes(router: Router, context: AppContext) -> None:
    """작업 조회·관리 라우트를 등록한다."""
    routes = [
        ("GET", "/api/v1/jobs", list_jobs),
        ("GET", "/api/v1/jobs/{job_id}/metrics", get_job_metrics),
        ("GET", "/api/v1/jobs/{job_id}/logs", get_job_logs),
        ("PUT", "/api/v1/jobs/{job_id}/tags", update_job_tags),
        ("DELETE", "/api/v1/jobs/{job_id}", delete_job),
    ]
    for method, pattern, handler in routes:
        router.add(method, pattern, partial(handler, context))


def list_jobs(context: AppContext, request: Request, params: dict[str, str]) -> Response:
    """service.jobs()가 부르는 목록. 필터 뒤 전체 개수를 count로, 요청한 구간을 jobs로 돌려준다."""
    query = request.query
    matching = [job for job in context.jobs.list_jobs() if _matches(job, query)]
    if _first(query, "sort") != "ASC":
        matching.reverse()
    offset = _integer(query, "offset", 0)
    limit = _integer(query, "limit", DEFAULT_PAGE_LIMIT)
    page = matching[offset : offset + limit]
    return Response(200, {"jobs": [job_to_api(job) for job in page], "count": len(matching)})


def get_job_metrics(context: AppContext, request: Request, params: dict[str, str]) -> Response:
    """job.metrics()·usage()·properties()가 읽는 metrics. 시뮬레이터라 QPU 사용 시간은 0이다."""
    job = _require_job(context, params["job_id"])
    usage: dict[str, Any] = {"status": "pending"}
    if job.is_final:
        usage = {
            "status": "final",
            "quantum_seconds": 0,
            "seconds": 0,
            "qpu_charge_time_seconds": 0,
        }
    timestamps = {"created": job.created, "running": job.running_at, "finished": job.ended_at}
    return Response(200, {"timestamps": timestamps, "usage": usage, "bss": {"seconds": 0}})


def get_job_logs(context: AppContext, request: Request, params: dict[str, str]) -> Response:
    """job.logs()가 읽는 평문 기록: 생성, 실행 시작, 종료(사유 포함)."""
    job = _require_job(context, params["job_id"])
    lines = [
        f"{job.created} localqpu: job {job.job_id} created (program {job.program_id}, backend {job.backend_name})"
    ]
    if job.running_at:
        lines.append(f"{job.running_at} localqpu: job {job.job_id} running")
    if job.ended_at:
        reason = f": {job.reason}" if job.reason else ""
        lines.append(f"{job.ended_at} localqpu: job {job.job_id} {job.status}{reason}")
    return Response(200, "\n".join(lines) + "\n", content_type="text/plain; charset=utf-8")


def update_job_tags(context: AppContext, request: Request, params: dict[str, str]) -> Response:
    """job.update_tags()용 태그 교체. 클라이언트는 204여야 성공으로 본다."""
    body = request.json_body()
    tags = body.get("tags") if isinstance(body, dict) else None
    if not isinstance(tags, list) or not all(isinstance(tag, str) for tag in tags):
        raise BadRequestError('본문은 {"tags": [문자열, ...]} 형식이어야 합니다.')
    if not context.jobs.update_metadata(params["job_id"], "tags", list(tags)):
        raise NotFoundError(unknown_job_message(params["job_id"]))
    return Response(204)


def delete_job(context: AppContext, request: Request, params: dict[str, str]) -> Response:
    """service.delete_job()용 삭제. 없는 작업은 404(클라이언트는 RuntimeJobNotFound)."""
    if not context.jobs.delete(params["job_id"]):
        raise NotFoundError(unknown_job_message(params["job_id"]))
    return Response(204)


def _matches(job: JobRecord, query: dict[str, list[str]]) -> bool:
    """목록 필터를 모두 만족하는 IBM 작업인지(Braket 작업은 뺀다)."""
    if job.program_id == BRAKET_PROGRAM_ID:
        return False
    checks = [
        _first(query, "backend") in (None, job.backend_name),
        _first(query, "program") in (None, job.program_id),
        _first(query, "session_id") in (None, job.session_id),
        _pending_matches(_first(query, "pending"), job),
        set(query.get("tags", [])) <= set(job.metadata.get("tags", [])),
        _created_within(
            job.created, _first(query, "created_after"), _first(query, "created_before")
        ),
    ]
    return all(checks)


def _pending_matches(pending: str | None, job: JobRecord) -> bool:
    """pending=true면 끝나지 않은 작업, false면 끝난 작업만."""
    if pending is None:
        return True
    return (pending == "true") != job.is_final


def _created_within(created: str, after: str | None, before: str | None) -> bool:
    """생성 시각이 [after, before] 구간인지."""
    created_at = _parse_time(created)
    return (after is None or created_at >= _parse_time(after)) and (
        before is None or created_at <= _parse_time(before)
    )


def _parse_time(value: str) -> datetime.datetime:
    """ISO-8601 시각. Python 3.10의 fromisoformat은 끝의 Z를 읽지 못해 +00:00으로 바꾼다.

    Raises:
        BadRequestError: 시각 형식이 틀렸을 때.
    """
    try:
        parsed = datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise BadRequestError(f"시각 형식이 틀렸습니다(ISO-8601 필요): {value}") from error
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=datetime.timezone.utc)


def _first(query: dict[str, list[str]], name: str) -> str | None:
    """쿼리 파라미터의 첫 값."""
    values = query.get(name)
    return values[0] if values else None


def _integer(query: dict[str, list[str]], name: str, default: int) -> int:
    """0 이상의 정수 쿼리 파라미터."""
    raw = _first(query, name)
    if raw is None:
        return default
    if not raw.isdigit():
        raise BadRequestError(f"'{name}'은(는) 0 이상의 정수여야 합니다: {raw}")
    return int(raw)


def _require_job(context: AppContext, job_id: str) -> JobRecord:
    """작업을 찾는다. 없으면 404."""
    job = context.jobs.get(job_id)
    if job is None:
        raise NotFoundError(unknown_job_message(job_id))
    return job
