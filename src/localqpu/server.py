"""HTTP 계층: 요청·응답 타입, 경로 라우터, 백그라운드 HTTP 서버.

IBM 흉내와 무관한 범용 부분만 둔다. 프록시 형식(절대 URL) 요청과 직접 요청을 같은 경로로
처리하고, HTTPS CONNECT는 외부 유출 시도로 보고 막는다(설계서 5.1·5.2절).
"""

from __future__ import annotations

import json
import logging
import re
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlsplit

#: 모듈 로거. 요청마다 한 줄을 남긴다.
logger = logging.getLogger(__name__)

#: CONNECT를 막을 때 보여 주는 안내. {target}에 클라이언트가 나가려던 주소가 들어간다.
CONNECT_BLOCKED_MESSAGE = (
    "클라이언트가 {target}(HTTPS)로 나가려 합니다. localqpu는 외부로 요청을 전달하지 않습니다. "
    "localqpu.connect()를 쓰거나 IAM_URL 환경변수를 localqpu로 설정하세요."
)


class BadRequestError(ValueError):
    """요청이 형식에 맞지 않음. 라우터가 400으로 바꾼다."""


class NotFoundError(LookupError):
    """요청한 대상이 없음. 라우터가 404로 바꾼다."""


@dataclass(frozen=True)
class Request:
    """라우터가 다루는 요청. 프록시 형식이어도 path에는 경로만 들어 있다.

    host는 프록시 형식이면 절대 URL의 호스트, 직접 요청이면 Host 헤더다(로그용).
    """

    method: str
    path: str
    query: dict[str, list[str]]
    body: bytes
    host: str = ""

    def json_body(self) -> Any:
        """본문을 JSON으로 읽는다. 빈 본문은 None.

        Raises:
            BadRequestError: JSON이 아닐 때.
        """
        try:
            return json.loads(self.body or b"null")
        except json.JSONDecodeError as error:
            raise BadRequestError(f"요청 본문이 JSON이 아닙니다: {error}") from error


@dataclass(frozen=True)
class Response:
    """라우터가 돌려주는 응답. body가 str이면 그대로, None이면 빈 본문, 그 밖에는 JSON으로 보낸다.

    content_type은 평문 본문(예: 실패 작업의 사유)을 JSON으로 잘못 표시하지 않도록 바꿀 수 있다.
    """

    status: int
    body: Any = None
    content_type: str = "application/json"

    def encode(self) -> bytes:
        """전송할 바이트열."""
        if self.body is None:
            return b""
        if isinstance(self.body, str):
            return self.body.encode()
        return json.dumps(self.body).encode()


#: 핸들러 형태: (요청, 경로 파라미터) → 응답.
Handler = Callable[[Request, dict[str, str]], Response]


def error_response(status: int, message: str) -> Response:
    """IBM API와 같은 {"errors": [{"message": ...}]} 형식의 오류 응답."""
    return Response(status, {"errors": [{"message": message}]})


class Router:
    """(메서드, 경로 패턴)으로 핸들러를 찾는다. 패턴의 {이름}은 경로 조각 하나와 맞는다."""

    def __init__(self) -> None:
        """빈 라우트 목록으로 시작한다."""
        self._routes: list[tuple[str, re.Pattern[str], Handler]] = []

    def add(self, method: str, pattern: str, handler: Handler) -> None:
        """라우트를 등록한다. 예: add("GET", "/api/v1/jobs/{job_id}", handler).

        {name}은 경로 조각 하나, {name+}는 슬래시를 포함한 나머지 경로 전체와 맞는다(S3 객체 키용).
        """
        greedy = re.sub(r"\{(\w+)\+\}", r"(?P<\1>.+)", pattern)
        regex = re.compile("^" + re.sub(r"\{(\w+)\}", r"(?P<\1>[^/]+)", greedy) + "$")
        self._routes.append((method, regex, handler))

    def dispatch(self, request: Request) -> Response:
        """맞는 핸들러를 부른다. 없으면 501로 무엇이 없는지 알려 준다."""
        for method, regex, handler in self._routes:
            match = regex.match(request.path)
            if match and method == request.method:
                return _call_handler(handler, request, match.groupdict())
        return error_response(
            501,
            f"localqpu는 아직 {request.method} {request.path}를 흉내 내지 않습니다. 필요하면 이슈로 알려주세요.",
        )


def _call_handler(handler: Handler, request: Request, params: dict[str, str]) -> Response:
    """핸들러 예외를 상태 코드로 바꾼다. 예상 못 한 예외는 스택을 로그로 남긴다."""
    try:
        return handler(request, params)
    except BadRequestError as error:
        return error_response(400, str(error))
    except NotFoundError as error:
        return error_response(404, str(error))
    except Exception as error:
        logger.exception("핸들러 오류: %s %s", request.method, request.path)
        return error_response(
            500, f"localqpu 내부 오류({type(error).__name__}: {error}). 서버 로그를 확인하세요."
        )


class ServerStats:
    """서버 통계. 지금은 막은 CONNECT 요청 수만 센다."""

    def __init__(self) -> None:
        """0에서 시작한다."""
        self._lock = threading.Lock()
        self._blocked_connect_requests = 0

    def record_blocked_connect(self) -> None:
        """막은 CONNECT 하나를 센다."""
        with self._lock:
            self._blocked_connect_requests += 1

    @property
    def blocked_connect_requests(self) -> int:
        """지금까지 막은 CONNECT 요청 수."""
        with self._lock:
            return self._blocked_connect_requests

    def reset(self) -> None:
        """집계를 0으로 되돌린다."""
        with self._lock:
            self._blocked_connect_requests = 0


def _no_operation() -> None:
    """아무것도 하지 않는다(on_stop 기본값)."""


@dataclass
class RunningServer:
    """백그라운드에서 도는 HTTP 서버."""

    http_server: ThreadingHTTPServer
    thread: threading.Thread
    on_stop: Callable[[], None] = field(default=_no_operation)

    @property
    def host(self) -> str:
        """실제 바인딩 주소."""
        return str(self.http_server.server_address[0])

    @property
    def port(self) -> int:
        """실제 포트. port=0으로 띄웠다면 운영체제가 고른 번호다."""
        return int(self.http_server.server_address[1])

    @property
    def url(self) -> str:
        """직접 모드 기본 URL이자 프록시 URL."""
        return f"http://{self.host}:{self.port}"

    def wait(self) -> None:
        """서버 스레드가 끝날 때까지 기다린다. Ctrl+C가 먹히도록 짧게 나눠 기다린다."""
        while self.thread.is_alive():
            self.thread.join(timeout=0.5)

    def stop(self) -> None:
        """서버를 멈추고 정리 콜백을 부른다."""
        self.http_server.shutdown()
        self.http_server.server_close()
        self.thread.join(timeout=5)
        self.on_stop()


def serve(
    router: Router, stats: ServerStats, host: str, port: int, is_verbose: bool = False
) -> RunningServer:
    """라우터를 HTTP 서버로 띄운다. port=0이면 빈 포트를 쓴다."""
    http_server = ThreadingHTTPServer((host, port), _make_handler_class(router, stats, is_verbose))
    http_server.daemon_threads = True
    thread = threading.Thread(target=http_server.serve_forever, name="localqpu-http", daemon=True)
    thread.start()
    return RunningServer(http_server=http_server, thread=thread)


def _make_handler_class(
    router: Router, stats: ServerStats, is_verbose: bool
) -> type[BaseHTTPRequestHandler]:
    """라우터를 쓰는 요청 핸들러 클래스를 만든다."""

    class LocalqpuRequestHandler(BaseHTTPRequestHandler):
        """요청 하나를 Request로 바꿔 라우터에 넘긴다."""

        def _handle(self) -> None:
            """일반 메서드 공통 처리. 헤더가 잘못된 요청은 라우터에 넘기지 않고 400으로 답한다."""
            try:
                request = _parse_request(self)
            except BadRequestError as error:
                _write_response(self, error_response(400, str(error)))
                logger.info("400 %s %s (잘못된 요청 헤더: %s)", self.command, self.path, error)
                return
            response = router.dispatch(request)
            _write_response(self, response)
            _log_request(request, response, is_verbose)

        do_GET = do_POST = do_PUT = do_DELETE = do_PATCH = _handle

        def do_CONNECT(self) -> None:
            """HTTPS 터널 요청은 외부 유출 시도이므로 막고 센다."""
            stats.record_blocked_connect()
            _write_response(
                self, error_response(405, CONNECT_BLOCKED_MESSAGE.format(target=self.path))
            )
            logger.warning("외부 HTTPS 요청을 막았습니다: CONNECT %s", self.path)

        def log_message(self, format: str, *args: Any) -> None:
            """표준 라이브러리의 stderr 로그를 끈다. 로그는 _log_request가 logging으로 남긴다."""

    return LocalqpuRequestHandler


def _parse_request(handler: BaseHTTPRequestHandler) -> Request:
    """표준 라이브러리 핸들러에서 Request를 만든다.

    Raises:
        BadRequestError: Content-Length가 숫자가 아니거나 음수일 때.
    """
    url = urlsplit(handler.path)
    body = _read_body(handler)
    host = url.netloc or handler.headers.get("Host", "")
    return Request(
        method=handler.command,
        path=url.path or "/",
        query=parse_qs(url.query),
        body=body,
        host=host,
    )


def _read_body(handler: BaseHTTPRequestHandler) -> bytes:
    """Content-Length만큼 본문을 읽는다. 음수로 read(-1)하면 연결이 끊길 때까지 스레드가 묶이므로 막는다."""
    raw_length = handler.headers.get("Content-Length") or "0"
    try:
        size = int(raw_length)
    except ValueError as error:
        raise BadRequestError(f"Content-Length 헤더가 숫자가 아닙니다: {raw_length!r}") from error
    if size < 0:
        raise BadRequestError(f"Content-Length 헤더는 0 이상이어야 합니다: {size}")
    return handler.rfile.read(size) if size else b""


def _write_response(handler: BaseHTTPRequestHandler, response: Response) -> None:
    """응답을 Content-Type·Content-Length 헤더와 함께 쓴다."""
    payload = response.encode()
    handler.send_response(response.status)
    handler.send_header("Content-Type", response.content_type)
    handler.send_header("Content-Length", str(len(payload)))
    handler.end_headers()
    handler.wfile.write(payload)


#: 본문에 API 키가 들어 있어 verbose에서도 본문을 남기지 않는 경로(IAM 토큰 요청의 apikey).
_CREDENTIAL_BODY_PATHS: frozenset[str] = frozenset({"/identity/token"})


def _log_request(request: Request, response: Response, is_verbose: bool) -> None:
    """요청 한 줄 로그. verbose면 본문 앞부분도 남기되, 자격 증명이 든 본문은 남기지 않는다."""
    logger.info("%s %s %s%s", response.status, request.method, request.host, request.path)
    if is_verbose and request.body and request.path not in _CREDENTIAL_BODY_PATHS:
        logger.debug("  body: %s", request.body[:300].decode(errors="replace"))
