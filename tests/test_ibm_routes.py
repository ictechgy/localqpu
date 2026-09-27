"""IBM API 라우트 테스트(직접 모드 HTTP)."""

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
    payload = {**sampler_payload(), "program_id": "estimator"}
    status, body = send_direct("POST", f"{server.url}/api/v1/jobs", payload)
    assert status == 404 and "estimator" in body["errors"][0]["message"]


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
