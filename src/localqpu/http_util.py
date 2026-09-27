"""프록시를 거치지 않는 직접 HTTP 호출.

제어 API와 테스트는 환경변수 프록시 설정(HTTP_PROXY 등)과 무관하게 localqpu에 바로 닿아야 한다.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

#: 환경변수 프록시를 무시하는 opener.
_DIRECT_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def send_direct(
    method: str, url: str, payload: Any = None, timeout: float = 10.0
) -> tuple[int, Any]:
    """JSON 요청을 보내고 (상태 코드, 본문)을 돌려준다. 본문은 JSON 객체·문자열·None. 4xx·5xx도 예외 없이 돌려준다."""
    data = None if payload is None else json.dumps(payload).encode()
    request = urllib.request.Request(
        url, data=data, method=method, headers={"Content-Type": "application/json"}
    )
    try:
        with _DIRECT_OPENER.open(request, timeout=timeout) as response:
            return response.status, _decode_body(response.read())
    except urllib.error.HTTPError as error:
        return error.code, _decode_body(error.read())


def _decode_body(raw: bytes) -> Any:
    """빈 본문은 None, JSON이면 객체, 아니면 문자열(실패 작업의 결과처럼 평문인 응답)."""
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw.decode(errors="replace")
