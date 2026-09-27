"""제어 API 클라이언트. 프록시를 거치지 않고 localqpu에 바로 요청한다."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from localqpu.http_util import send_direct


class LocalqpuControlError(RuntimeError):
    """제어 API가 기대와 다른 상태 코드를 돌려줬을 때. 메시지에 서버 사유가 들어 있다."""


class LocalqpuControl:
    """테스트 코드에서 시나리오를 바꾸고 작업을 검사하는 얇은 클라이언트."""

    def __init__(self, base_url: str) -> None:
        """서버 기본 URL(예: http://127.0.0.1:8787)을 받는다."""
        self._base_url = base_url.rstrip("/")

    def health(self) -> dict[str, Any]:
        """서버 상태."""
        return dict(self._call("GET", "/_localqpu/health"))

    def scenario(self) -> dict[str, Any]:
        """현재 시나리오."""
        return dict(self._call("GET", "/_localqpu/scenario"))

    def set_scenario(self, scenario: Mapping[str, Any]) -> dict[str, Any]:
        """시나리오를 교체한다.

        Raises:
            LocalqpuControlError: 시나리오가 잘못됐을 때. 메시지에 문제 위치가 있다.
        """
        return dict(self._call("PUT", "/_localqpu/scenario", dict(scenario)))

    def reset(self) -> None:
        """작업·시나리오·통계를 초기화한다."""
        self._call("POST", "/_localqpu/reset", expected_status=204)

    def jobs(self) -> list[dict[str, Any]]:
        """제출된 작업 요약 목록."""
        return list(self._call("GET", "/_localqpu/jobs")["jobs"])

    def _call(self, method: str, path: str, payload: Any = None, expected_status: int = 200) -> Any:
        """요청을 보내고 상태 코드를 확인한다."""
        status, body = send_direct(method, f"{self._base_url}{path}", payload)
        if status != expected_status:
            raise LocalqpuControlError(f"{method} {path} → {status}: {_error_text(body)}")
        return body


def _error_text(body: Any) -> str:
    """오류 본문에서 사람이 읽을 메시지를 꺼낸다."""
    if isinstance(body, dict) and body.get("errors"):
        return "; ".join(str(error.get("message")) for error in body["errors"])
    return str(body)
