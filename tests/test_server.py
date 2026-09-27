"""HTTP 계층 테스트."""

import http.client
import logging
from collections.abc import Iterator

import pytest

from localqpu.http_util import send_direct
from localqpu.server import (
    BadRequestError,
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
