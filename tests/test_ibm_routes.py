"""IBM API 라우트 테스트(직접 모드 HTTP)."""

import http.client
import json
import time
from collections.abc import Iterator
from typing import Any

import jwt
import pytest
from qiskit import QuantumCircuit

from localqpu._compat import RuntimeDecoder, RuntimeEncoder
from localqpu.app import start_server
from localqpu.constants import LOCAL_INSTANCE_CRN, LOCAL_PLAN_ID
from localqpu.context import ServerConfig
from localqpu.http_util import send_direct
from localqpu.scenario import parse_scenario
from localqpu.server import RunningServer


def launch(scenario: dict[str, Any] | None = None) -> RunningServer:
    """시나리오를 넣어 빈 포트에 서버를 띄운다."""
    return start_server(ServerConfig(port=0, scenario=parse_scenario(scenario or {})))


@pytest.fixture
def server() -> Iterator[RunningServer]:
    """기본 시나리오 서버."""
    running = launch()
    yield running
    running.stop()


def sampler_payload() -> dict[str, Any]:
    """Bell 회로 SamplerV2 제출 본문(클라이언트와 같은 인코딩)."""
    circuit = QuantumCircuit(2)
    circuit.h(0)
    circuit.cx(0, 1)
    circuit.measure_all()
    params = json.loads(
        json.dumps({"pubs": [(circuit, None, 100)], "version": 2}, cls=RuntimeEncoder)
    )
    return {"program_id": "sampler", "backend": "ibm_brisbane", "params": params}


def poll_job(server: RunningServer, job_id: str, timeout: float = 30.0) -> dict[str, Any]:
    """작업이 끝날 때까지 GET /jobs/{id}를 부른다."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        _, body = send_direct("GET", f"{server.url}/api/v1/jobs/{job_id}")
        if body["state"]["status"] in {"Completed", "Failed", "Cancelled"}:
            return body
        time.sleep(0.05)
    raise AssertionError(f"{job_id}가 끝나지 않았습니다.")


def test_token_is_decodable_jwt(server: RunningServer) -> None:
    """토큰은 클라이언트가 exp를 읽을 수 있는 JWT다."""
    status, body = send_direct("POST", f"{server.url}/identity/token")
    claims = jwt.decode(body["access_token"], options={"verify_signature": False})
    assert status == 200 and claims["exp"] > claims["iat"]


def test_token_rejected_by_scenario() -> None:
    """auth.reject_tokens면 토큰 발급을 400으로 거절한다."""
    running = launch({"auth": {"reject_tokens": True}})
    try:
        status, body = send_direct("POST", f"{running.url}/identity/token")
        assert status == 400 and "reject_tokens" in body["errorMessage"]
    finally:
        running.stop()


def test_instance_search_and_plan(server: RunningServer) -> None:
    """인스턴스 검색과 플랜 조회가 localqpu 인스턴스를 돌려준다."""
    _, search = send_direct("POST", f"{server.url}/v3/resources/search")
    _, plan = send_direct("GET", f"{server.url}/api/v1/{LOCAL_PLAN_ID}")
    assert search["items"][0]["crn"] == LOCAL_INSTANCE_CRN
    assert plan["metadata"]["pricing"]["type"] == "free"


def test_backend_endpoints(server: RunningServer) -> None:
    """목록·설정·속성·상태가 스냅샷과 시나리오를 반영한다."""
    _, listing = send_direct("GET", f"{server.url}/api/v1/backends")
    _, configuration = send_direct(
        "GET", f"{server.url}/api/v1/backends/ibm_brisbane/configuration"
    )
    properties_status, _ = send_direct(
        "GET", f"{server.url}/api/v1/backends/ibm_brisbane/properties"
    )
    _, backend_status = send_direct("GET", f"{server.url}/api/v1/backends/ibm_brisbane/status")
    assert listing["devices"][0] == {
        "name": "ibm_brisbane",
        "status": {"name": "online", "reason": ""},
        "qubits": 127,
        "queue_length": 0,
    }
    assert configuration["n_qubits"] == 127 and properties_status == 200
    assert backend_status["state"] is True and backend_status["status"] == "active"


def test_unknown_backend_configuration_is_404(server: RunningServer) -> None:
    """없는 백엔드는 404다."""
    status, _ = send_direct("GET", f"{server.url}/api/v1/backends/ibm_atlantis/configuration")
    assert status == 404


def test_offline_backend_is_reported() -> None:
    """오프라인 백엔드는 목록과 상태에 반영된다."""
    running = launch({"backends": {"ibm_brisbane": {"status": "offline", "queue_length": 9}}})
    try:
        _, listing = send_direct("GET", f"{running.url}/api/v1/backends")
        _, backend_status = send_direct("GET", f"{running.url}/api/v1/backends/ibm_brisbane/status")
        assert listing["devices"][0]["status"]["name"] == "offline"
        assert backend_status["state"] is False and backend_status["length_queue"] == 9
    finally:
        running.stop()


def test_sampler_job_round_trip(server: RunningServer) -> None:
    """제출 → 폴링 → 결과가 클라이언트 디코더로 읽힌다."""
    status, submitted = send_direct("POST", f"{server.url}/api/v1/jobs", sampler_payload())
    assert status == 200
    job = poll_job(server, submitted["id"])
    assert job["state"]["status"] == "Completed" and job["program"]["id"] == "sampler"
    _, raw = send_direct("GET", f"{server.url}/api/v1/jobs/{submitted['id']}/results")
    result = json.loads(json.dumps(raw), cls=RuntimeDecoder)
    assert sum(result[0].data.meas.get_counts().values()) == 100


def test_usage_exhaustion_blocks_submission() -> None:
    """사용량 한도에 도달하면 usage_limit_reached가 true이고 제출은 403이다."""
    running = launch({"usage": {"limit_seconds": 10, "consumed_seconds": 10}})
    try:
        _, usage = send_direct("GET", f"{running.url}/api/v1/instances/usage")
        status, body = send_direct("POST", f"{running.url}/api/v1/jobs", sampler_payload())
        assert usage["usage_limit_reached"] is True
        assert status == 403 and "사용량 한도" in body["errors"][0]["message"]
    finally:
        running.stop()


def test_unsupported_program_is_404(server: RunningServer) -> None:
    """estimator는 지원 목록과 함께 404로 거절한다."""
    payload = {**sampler_payload(), "program_id": "noise-learner"}
    status, body = send_direct("POST", f"{server.url}/api/v1/jobs", payload)
    assert status == 404 and "noise-learner" in body["errors"][0]["message"]


def test_unknown_backend_submission_is_400(server: RunningServer) -> None:
    """없는 백엔드로 제출하면 400이다."""
    payload = {**sampler_payload(), "backend": "ibm_atlantis"}
    status, body = send_direct("POST", f"{server.url}/api/v1/jobs", payload)
    assert status == 400 and "ibm_brisbane" in body["errors"][0]["message"]


def test_failed_job_results_return_reason_text() -> None:
    """실패한 작업의 결과 조회는 200과 사유 문자열이다(클라이언트가 에러 메시지용으로 부른다)."""
    running = launch({"next_jobs": [{"outcome": "failed", "reason": "calibrating"}]})
    try:
        _, submitted = send_direct("POST", f"{running.url}/api/v1/jobs", sampler_payload())
        poll_job(running, submitted["id"])
        status, body = send_direct("GET", f"{running.url}/api/v1/jobs/{submitted['id']}/results")
        assert status == 200 and "calibrating" in body
    finally:
        running.stop()


def test_unknown_job_explains_restart(server: RunningServer) -> None:
    """모르는 작업 ID는 404와 함께 메모리 보관 사실을 알려 준다."""
    status, body = send_direct("GET", f"{server.url}/api/v1/jobs/localqpu-deadbeef")
    assert status == 404 and "재시작" in body["errors"][0]["message"]


def test_cancel_and_results_conflicts() -> None:
    """대기 중 취소는 204, 다시 취소하면 409, 끝나지 않은 결과 조회도 409다."""
    running = launch({"queue": {"polls_before_running": 1000}})
    try:
        _, submitted = send_direct("POST", f"{running.url}/api/v1/jobs", sampler_payload())
        job_url = f"{running.url}/api/v1/jobs/{submitted['id']}"
        assert send_direct("GET", f"{job_url}/results")[0] == 409
        assert send_direct("POST", f"{job_url}/cancel")[0] == 204
        assert send_direct("POST", f"{job_url}/cancel")[0] == 409
    finally:
        running.stop()


@pytest.mark.parametrize(
    ("override", "expected_field"),
    [
        ({"backend": ["ibm_brisbane"]}, "backend"),
        ({"backend": {"name": "ibm_brisbane"}}, "backend"),
        ({"program_id": None}, "program_id"),
        ({"program_id": 7}, "program_id"),
        ({"params": [1]}, "params"),
    ],
)
def test_malformed_submission_is_400(
    server: RunningServer, override: dict[str, Any], expected_field: str
) -> None:
    """형식이 틀린 제출 본문은 500이나 엉뚱한 404가 아니라 필드 이름을 담은 400이다."""
    status, body = send_direct(
        "POST", f"{server.url}/api/v1/jobs", {**sampler_payload(), **override}
    )
    assert status == 400 and expected_field in body["errors"][0]["message"]


def test_failed_job_results_are_plain_text() -> None:
    """실패 사유 문자열은 JSON이 아니므로 text/plain으로 보낸다."""
    running = launch({"next_jobs": [{"outcome": "failed", "reason": "calibrating"}]})
    try:
        _, submitted = send_direct("POST", f"{running.url}/api/v1/jobs", sampler_payload())
        poll_job(running, submitted["id"])
        connection = http.client.HTTPConnection(running.host, running.port, timeout=5)
        connection.request("GET", f"/api/v1/jobs/{submitted['id']}/results")
        assert connection.getresponse().getheader("Content-Type").startswith("text/plain")
    finally:
        running.stop()


def create_session(server: RunningServer, mode: str = "dedicated") -> str:
    """세션을 만들고 ID를 돌려준다."""
    status, body = send_direct(
        "POST",
        f"{server.url}/api/v1/sessions",
        {"mode": mode, "backend": "ibm_brisbane", "max_ttl": 600},
    )
    assert status == 200
    return str(body["id"])


def test_session_lifecycle(server: RunningServer) -> None:
    """세션은 open으로 만들어지고, 작업이 들어오면 active, 닫으면 closed가 된다."""
    session_id = create_session(server, "batch")
    _, details = send_direct("GET", f"{server.url}/api/v1/sessions/{session_id}")
    assert (
        details["state"],
        details["accepting_jobs"],
        details["mode"],
        details["backend_name"],
        details["max_ttl"],
    ) == ("open", True, "batch", "ibm_brisbane", 600)
    status, submitted = send_direct(
        "POST", f"{server.url}/api/v1/jobs", {**sampler_payload(), "session_id": session_id}
    )
    assert status == 200
    _, job = send_direct("GET", f"{server.url}/api/v1/jobs/{submitted['id']}")
    assert job["session_id"] == session_id
    assert send_direct("GET", f"{server.url}/api/v1/sessions/{session_id}")[1]["state"] == "active"
    assert (
        send_direct(
            "PATCH", f"{server.url}/api/v1/sessions/{session_id}", {"accepting_jobs": False}
        )[0]
        == 204
    )
    _, closed = send_direct("GET", f"{server.url}/api/v1/sessions/{session_id}")
    assert (closed["state"], closed["accepting_jobs"]) == ("closed", False) and closed["closed_at"]


def test_closed_session_rejects_submission_with_409(server: RunningServer) -> None:
    """닫힌 세션으로 제출하면 409다(클라이언트는 닫힌 세션에도 그대로 제출한다)."""
    session_id = create_session(server)
    send_direct("PATCH", f"{server.url}/api/v1/sessions/{session_id}", {"accepting_jobs": False})
    status, body = send_direct(
        "POST", f"{server.url}/api/v1/jobs", {**sampler_payload(), "session_id": session_id}
    )
    assert status == 409 and "닫힌" in body["errors"][0]["message"]


def test_unknown_session_submission_is_400(server: RunningServer) -> None:
    """모르는 세션으로 제출하면 400이다(404는 클라이언트가 Program not found로 바꾼다)."""
    status, body = send_direct(
        "POST", f"{server.url}/api/v1/jobs", {**sampler_payload(), "session_id": "nope"}
    )
    assert status == 400 and "nope" in body["errors"][0]["message"]


def test_session_cancel_cancels_pending_jobs() -> None:
    """세션 취소(DELETE .../close)는 대기 중인 작업을 취소하고 세션을 닫는다."""
    running = launch({"queue": {"polls_before_running": 1000}})
    try:
        session_id = create_session(running)
        _, submitted = send_direct(
            "POST", f"{running.url}/api/v1/jobs", {**sampler_payload(), "session_id": session_id}
        )
        assert send_direct("DELETE", f"{running.url}/api/v1/sessions/{session_id}/close")[0] == 204
        _, job = send_direct("GET", f"{running.url}/api/v1/jobs/{submitted['id']}")
        assert job["state"]["status"] == "Cancelled"
        assert (
            send_direct("GET", f"{running.url}/api/v1/sessions/{session_id}")[1]["state"]
            == "closed"
        )
    finally:
        running.stop()


@pytest.mark.parametrize(
    ("body", "expected_status"),
    [
        ({"mode": "turbo", "backend": "ibm_brisbane"}, 400),
        ({"mode": "dedicated", "backend": "ibm_atlantis"}, 400),
    ],
)
def test_invalid_session_creation_is_400(
    server: RunningServer, body: dict[str, Any], expected_status: int
) -> None:
    """모르는 모드나 백엔드로는 세션을 만들 수 없다."""
    assert send_direct("POST", f"{server.url}/api/v1/sessions", body)[0] == expected_status


def test_unknown_session_details_is_404(server: RunningServer) -> None:
    """모르는 세션 조회는 재시작 안내와 함께 404다."""
    status, body = send_direct("GET", f"{server.url}/api/v1/sessions/nope")
    assert status == 404 and "재시작" in body["errors"][0]["message"]


def test_session_closed_between_check_and_submit_is_rejected() -> None:
    """세션 검증을 통과한 직후 세션이 닫히면, 등록된 작업을 취소하고 409를 돌려준다(닫힌 세션에 작업이 남지 않음)."""
    from localqpu.app import build_context, build_router
    from localqpu.server import Request

    context = build_context(ServerConfig(port=0))
    router = build_router(context)
    created = router.dispatch(
        Request(
            "POST",
            "/api/v1/sessions",
            {},
            json.dumps({"mode": "dedicated", "backend": "ibm_brisbane"}).encode(),
        )
    )
    session_id = created.body["id"]
    original_submit = context.jobs.submit

    def submit_after_concurrent_close(*args: Any, **kwargs: Any) -> Any:
        """검증과 등록 사이에 다른 요청이 세션을 닫은 상황을 흉내 낸다."""
        context.sessions.close(session_id)
        return original_submit(*args, **kwargs)

    context.jobs.submit = submit_after_concurrent_close  # type: ignore[method-assign]
    try:
        response = router.dispatch(
            Request(
                "POST",
                "/api/v1/jobs",
                {},
                json.dumps({**sampler_payload(), "session_id": session_id}).encode(),
            )
        )
        assert response.status == 409
        assert all(job.status == "Cancelled" for job in context.jobs.list_jobs())
    finally:
        context.jobs.shutdown()


def submit_with(server: RunningServer, **extra: Any) -> str:
    """추가 필드(tags, private 등)를 넣어 Bell 작업을 제출하고 ID를 돌려준다."""
    status, body = send_direct("POST", f"{server.url}/api/v1/jobs", {**sampler_payload(), **extra})
    assert status == 200
    return str(body["id"])


def test_tags_and_private_are_kept_and_updatable(server: RunningServer) -> None:
    """제출한 tags·private가 작업 조회에 돌아오고, PUT tags(204)로 바꿀 수 있다."""
    job_id = submit_with(server, tags=["exp-1"], private=True)
    _, job = send_direct("GET", f"{server.url}/api/v1/jobs/{job_id}")
    assert job["tags"] == ["exp-1"] and job["private"] is True
    assert (
        send_direct("PUT", f"{server.url}/api/v1/jobs/{job_id}/tags", {"tags": ["exp-2", "rerun"]})[
            0
        ]
        == 204
    )
    assert send_direct("GET", f"{server.url}/api/v1/jobs/{job_id}")[1]["tags"] == ["exp-2", "rerun"]
    assert send_direct("PUT", f"{server.url}/api/v1/jobs/missing/tags", {"tags": []})[0] == 404


def test_job_listing_filters_paginates_and_counts(server: RunningServer) -> None:
    """목록은 필터 뒤 전체 개수를 count로 주고, offset·limit으로 나누며, 기본은 최신순이다."""
    ids = [submit_with(server, tags=["batch-a"]) for _ in range(3)] + [
        submit_with(server, tags=["batch-b"])
    ]
    _, page = send_direct(
        "GET", f"{server.url}/api/v1/jobs?limit=2&offset=0&tags=batch-a&exclude_params=true"
    )
    assert page["count"] == 3 and [job["id"] for job in page["jobs"]] == [ids[2], ids[1]]
    _, rest = send_direct("GET", f"{server.url}/api/v1/jobs?limit=2&offset=2&tags=batch-a")
    assert [job["id"] for job in rest["jobs"]] == [ids[0]]
    _, ascending = send_direct("GET", f"{server.url}/api/v1/jobs?sort=ASC&backend=ibm_brisbane")
    assert [job["id"] for job in ascending["jobs"]] == ids
    _, pending = send_direct("GET", f"{server.url}/api/v1/jobs?pending=false")
    assert pending["count"] == 0


def test_metrics_and_logs(server: RunningServer) -> None:
    """metrics는 대기 중 usage pending, 끝나면 final과 실행 시각을 주고, logs는 평문 기록을 준다."""
    job_id = submit_with(server)
    _, waiting = send_direct("GET", f"{server.url}/api/v1/jobs/{job_id}/metrics")
    assert waiting["usage"]["status"] == "pending" and waiting["timestamps"]["created"]
    poll_job(server, job_id)
    _, finished = send_direct("GET", f"{server.url}/api/v1/jobs/{job_id}/metrics")
    assert finished["usage"] == {
        "status": "final",
        "quantum_seconds": 0,
        "seconds": 0,
        "qpu_charge_time_seconds": 0,
    }
    assert finished["timestamps"]["running"] and finished["timestamps"]["finished"]
    status, logs = send_direct("GET", f"{server.url}/api/v1/jobs/{job_id}/logs")
    assert status == 200 and "Completed" in logs and job_id in logs


def test_delete_job(server: RunningServer) -> None:
    """삭제하면 204이고 이후 조회·목록에서 사라지며, 다시 지우면 404다."""
    job_id = submit_with(server)
    assert send_direct("DELETE", f"{server.url}/api/v1/jobs/{job_id}")[0] == 204
    assert send_direct("GET", f"{server.url}/api/v1/jobs/{job_id}")[0] == 404
    assert send_direct("GET", f"{server.url}/api/v1/jobs")[1]["count"] == 0
    assert send_direct("DELETE", f"{server.url}/api/v1/jobs/{job_id}")[0] == 404
