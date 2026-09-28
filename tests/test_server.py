"""HTTP 계층 테스트."""

import http.client
import logging
from collections.abc import Iterator

import pytest

from localqpu.http_util import send_direct
from localqpu.server import (
    BadRequestError,
    FaultDecision,
    NotFoundError,
    Request,
    Response,
    Router,
    RunningServer,
    ServerStats,
    serve,
)


def echo_job(request: Request, params: dict[str, str]) -> Response:
    """경로 파라미터와 본문을 그대로 돌려준다."""
    return Response(200, {"job_id": params["job_id"], "body": request.json_body()})


def raise_bad_request(request: Request, params: dict[str, str]) -> Response:
    """400을 유도한다."""
    raise BadRequestError("본문이 틀렸습니다")


def raise_not_found(request: Request, params: dict[str, str]) -> Response:
    """404를 유도한다."""
    raise NotFoundError("없습니다")


def raise_unexpected(request: Request, params: dict[str, str]) -> Response:
    """500을 유도한다."""
    raise RuntimeError("kaboom")


@pytest.fixture
def running() -> Iterator[tuple[RunningServer, ServerStats]]:
    """테스트 라우트만 가진 서버를 빈 포트에 띄운다."""
    router = Router()
    router.add("POST", "/jobs/{job_id}", echo_job)
    router.add("GET", "/bad", raise_bad_request)
    router.add("GET", "/missing", raise_not_found)
    router.add("GET", "/boom", raise_unexpected)
    router.add(
        "GET",
        "/text",
        lambda request, params: Response(200, "안녕", content_type="text/plain; charset=utf-8"),
    )
    stats = ServerStats()
    server = serve(router, stats, "127.0.0.1", 0)
    yield server, stats
    server.stop()


def test_path_params_and_json_body(running: tuple[RunningServer, ServerStats]) -> None:
    """경로 파라미터와 JSON 본문이 핸들러로 전달된다."""
    server, _ = running
    status, body = send_direct("POST", f"{server.url}/jobs/abc", {"x": 1})
    assert status == 200 and body == {"job_id": "abc", "body": {"x": 1}}


def test_unknown_route_is_501_with_path(running: tuple[RunningServer, ServerStats]) -> None:
    """흉내 내지 않는 경로는 501과 경로를 알려 준다."""
    server, _ = running
    status, body = send_direct("GET", f"{server.url}/nowhere")
    assert status == 501 and "GET /nowhere" in body["errors"][0]["message"]


@pytest.mark.parametrize(("path", "expected"), [("/bad", 400), ("/missing", 404), ("/boom", 500)])
def test_handler_errors_map_to_status(
    running: tuple[RunningServer, ServerStats], path: str, expected: int
) -> None:
    """핸들러 예외가 상태 코드로 바뀐다."""
    server, _ = running
    status, body = send_direct("GET", f"{server.url}{path}")
    assert status == expected and body["errors"][0]["message"]


def test_proxy_form_request_is_routed_by_path(running: tuple[RunningServer, ServerStats]) -> None:
    """프록시 형식(절대 URL) 요청도 경로로 라우팅된다."""
    server, _ = running
    connection = http.client.HTTPConnection(server.host, server.port, timeout=5)
    connection.request("POST", "http://anything.test/jobs/xyz", body=b"{}")
    response = connection.getresponse()
    assert response.status == 200 and b"xyz" in response.read()


def test_connect_is_blocked_and_counted(running: tuple[RunningServer, ServerStats]) -> None:
    """HTTPS CONNECT는 405로 막히고 집계된다."""
    server, stats = running
    connection = http.client.HTTPConnection(server.host, server.port, timeout=5)
    connection.request("CONNECT", "iam.cloud.ibm.com:443")
    response = connection.getresponse()
    assert response.status == 405 and "localqpu.connect()" in response.read().decode()
    assert stats.blocked_connect_requests == 1
    stats.reset()
    assert stats.blocked_connect_requests == 0


def test_verbose_log_never_contains_api_key(caplog: pytest.LogCaptureFixture) -> None:
    """verbose 로그에도 IAM 토큰 요청 본문(apikey)은 남지 않고, 다른 본문은 남는다."""
    router = Router()
    router.add("POST", "/identity/token", lambda request, params: Response(200, {}))
    router.add("POST", "/jobs/{job_id}", echo_job)
    server = serve(router, ServerStats(), "127.0.0.1", 0, is_verbose=True)
    try:
        with caplog.at_level(logging.DEBUG, logger="localqpu.server"):
            connection = http.client.HTTPConnection(server.host, server.port, timeout=5)
            connection.request(
                "POST", "/identity/token", body=b"grant_type=x&apikey=super-secret-key"
            )
            connection.getresponse().read()
            send_direct("POST", f"{server.url}/jobs/abc", {"visible": "yes"})
    finally:
        server.stop()
    assert "super-secret-key" not in caplog.text and "visible" in caplog.text


def test_invalid_json_body_is_400(running: tuple[RunningServer, ServerStats]) -> None:
    """JSON이 아닌 본문은 400이다."""
    server, _ = running
    connection = http.client.HTTPConnection(server.host, server.port, timeout=5)
    connection.request("POST", "/jobs/abc", body=b"{not json")
    assert connection.getresponse().status == 400


@pytest.mark.parametrize("length", ["abc", "-1"])
def test_invalid_content_length_is_400(
    running: tuple[RunningServer, ServerStats], length: str
) -> None:
    """숫자가 아니거나 음수인 Content-Length는 연결을 끊거나 스레드를 묶지 않고 400으로 답한다."""
    server, _ = running
    connection = http.client.HTTPConnection(server.host, server.port, timeout=5)
    connection.putrequest("POST", "/jobs/abc")
    connection.putheader("Content-Length", length)
    connection.endheaders()
    response = connection.getresponse()
    assert response.status == 400 and "Content-Length" in response.read().decode()


def test_request_log_includes_host(
    running: tuple[RunningServer, ServerStats], caplog: pytest.LogCaptureFixture
) -> None:
    """요청 로그에 호스트가 들어가 프록시 모드에서 어느 가짜 호스트로 온 요청인지 구분된다."""
    server, _ = running
    with caplog.at_level(logging.INFO, logger="localqpu.server"):
        connection = http.client.HTTPConnection(server.host, server.port, timeout=5)
        connection.request("POST", "http://iam.localqpu.test/jobs/xyz", body=b"{}")
        connection.getresponse().read()
    assert "iam.localqpu.test/jobs/xyz" in caplog.text


def test_text_response_uses_text_content_type(running: tuple[RunningServer, ServerStats]) -> None:
    """content_type을 지정한 응답은 그 Content-Type으로 나간다."""
    server, _ = running
    connection = http.client.HTTPConnection(server.host, server.port, timeout=5)
    connection.request("GET", "/text")
    response = connection.getresponse()
    assert response.getheader("Content-Type") == "text/plain; charset=utf-8"
    assert response.read() == "안녕".encode()


def test_greedy_path_parameter_matches_slashes() -> None:
    """{name+} 경로 파라미터는 슬래시를 포함한 나머지 경로 전체와 맞는다(S3 객체 키용)."""
    router = Router()
    router.add(
        "GET", "/amazon-braket-{suffix}/{key+}", lambda request, params: Response(200, params)
    )
    response = router.dispatch(
        Request("GET", "/amazon-braket-localqpu/tasks/abc/results.json", {}, b"")
    )
    assert response.status == 200
    assert response.body == {"suffix": "localqpu", "key": "tasks/abc/results.json"}
    assert router.dispatch(Request("GET", "/amazon-braket-localqpu", {}, b"")).status == 501


class CountingHandler:
    """호출 횟수를 세는 핸들러(장애가 처리 전·후 어디서 적용됐는지 확인용)."""

    def __init__(self) -> None:
        """0에서 시작한다."""
        self.calls = 0

    def __call__(self, request: Request, params: dict[str, str]) -> Response:
        """호출을 세고 200을 돌려준다."""
        self.calls += 1
        return Response(200, {"ok": True})


def serve_with_fault(
    decision: FaultDecision | None, handler: CountingHandler
) -> tuple[RunningServer, ServerStats]:
    """모든 /work 요청에 같은 장애 결정을 적용하는 서버. 제어 경로는 장애 정책이 판단한다."""
    router = Router()
    router.add("POST", "/work", handler)
    router.add("GET", "/_localqpu/health", lambda request, params: Response(200, {"status": "ok"}))
    stats = ServerStats()

    def policy(request: Request) -> FaultDecision | None:
        """/work에만 장애를 적용한다."""
        return decision if request.path == "/work" else None

    return serve(router, stats, "127.0.0.1", 0, fault_policy=policy), stats


def test_fault_before_skips_handler_and_sets_retry_after() -> None:
    """before 장애는 처리하지 않고 상태 코드와 Retry-After를 돌려주며 요청 기록에 남는다."""
    handler = CountingHandler()
    server, stats = serve_with_fault(
        FaultDecision(status=503, retry_after=2, message="busy"), handler
    )
    try:
        connection = http.client.HTTPConnection(server.host, server.port, timeout=5)
        connection.request("POST", "/work", body=b"{}")
        response = connection.getresponse()
        assert (response.status, response.getheader("Retry-After")) == (
            503,
            "2",
        ) and b"busy" in response.read()
        assert handler.calls == 0
        entry = stats.requests()[-1]
        assert (entry["method"], entry["path"], entry["status"]) == (
            "POST",
            "/work",
            503,
        ) and "503" in entry["fault"]
    finally:
        server.stop()


def test_fault_after_runs_handler_then_loses_response() -> None:
    """after 장애는 정상 처리한 뒤 응답만 실패시킨다(유실된 응답 재현)."""
    handler = CountingHandler()
    server, _ = serve_with_fault(FaultDecision(status=502, phase="after"), handler)
    try:
        status, _ = send_direct("POST", f"{server.url}/work", {})
        assert status == 502 and handler.calls == 1
    finally:
        server.stop()


def test_drop_connection_closes_without_response() -> None:
    """drop_connection이면 응답 없이 연결이 끊긴다."""
    handler = CountingHandler()
    server, stats = serve_with_fault(FaultDecision(drop_connection=True), handler)
    try:
        connection = http.client.HTTPConnection(server.host, server.port, timeout=5)
        connection.request("POST", "/work", body=b"{}")
        with pytest.raises((http.client.RemoteDisconnected, ConnectionResetError)):
            connection.getresponse()
        assert (
            stats.requests()[-1]["status"] is None
            and stats.requests()[-1]["fault"] == "drop_connection"
        )
    finally:
        server.stop()


def test_delay_only_fault_slows_normal_response() -> None:
    """delay만 있으면 기다린 뒤 정상 응답한다."""
    import time

    handler = CountingHandler()
    server, _ = serve_with_fault(FaultDecision(delay_seconds=0.3), handler)
    try:
        started = time.monotonic()
        status, _ = send_direct("POST", f"{server.url}/work", {})
        assert status == 200 and time.monotonic() - started >= 0.3 and handler.calls == 1
    finally:
        server.stop()


def test_request_journal_skips_control_paths_and_resets() -> None:
    """요청 기록은 제어 API 요청을 남기지 않고, reset으로 비워진다."""
    handler = CountingHandler()
    server, stats = serve_with_fault(None, handler)
    try:
        send_direct("POST", f"{server.url}/work", {})
        send_direct("GET", f"{server.url}/_localqpu/health")
        assert [entry["path"] for entry in stats.requests()] == ["/work"]
        stats.reset()
        assert stats.requests() == []
    finally:
        server.stop()
