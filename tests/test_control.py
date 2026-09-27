"""제어 API 테스트."""

import http.client
from collections.abc import Iterator

import pytest

from localqpu.app import start_server
from localqpu.context import ServerConfig
from localqpu.control_client import LocalqpuControl, LocalqpuControlError
from localqpu.http_util import send_direct
from localqpu.server import RunningServer


@pytest.fixture
def server() -> Iterator[RunningServer]:
    """기본 서버."""
    running = start_server(ServerConfig(port=0))
    yield running
    running.stop()


def test_health_reports_backends_and_blocked_connects(server: RunningServer) -> None:
    """health는 백엔드 목록과 막힌 CONNECT 수를 알려 준다."""
    control = LocalqpuControl(server.url)
    connection = http.client.HTTPConnection(server.host, server.port, timeout=5)
    connection.request("CONNECT", "iam.cloud.ibm.com:443")
    connection.getresponse().read()
    health = control.health()
    assert health["status"] == "ok" and health["backends"] == ["ibm_brisbane"]
    assert health["blocked_connect_requests"] == 1


def test_set_and_get_scenario(server: RunningServer) -> None:
    """시나리오를 바꾸면 조회 결과와 백엔드 목록에 반영된다."""
    control = LocalqpuControl(server.url)
    control.set_scenario({"backends": {"ibm_brisbane": {"status": "paused"}}})
    assert control.scenario()["backends"]["ibm_brisbane"]["status"] == "paused"
    _, listing = send_direct("GET", f"{server.url}/api/v1/backends")
    assert listing["devices"][0]["status"]["name"] == "paused"


def test_put_invalid_scenario_keeps_previous(server: RunningServer) -> None:
    """잘못된 시나리오는 위치를 알려 주며 거절되고 기존 시나리오는 유지된다."""
    control = LocalqpuControl(server.url)
    control.set_scenario({"seed": 1})
    with pytest.raises(LocalqpuControlError, match="colour"):
        control.set_scenario({"colour": "red"})
    with pytest.raises(LocalqpuControlError, match="rate"):
        control.set_scenario({"failures": {"rate": "high"}})
    assert control.scenario()["seed"] == 1


def test_reset_clears_jobs_scenario_and_stats(server: RunningServer) -> None:
    """reset은 작업 기록, 시나리오, 막힌 CONNECT 집계를 초기화한다."""
    control = LocalqpuControl(server.url)
    control.set_scenario({"seed": 9, "queue": {"polls_before_running": 1000}})
    send_direct(
        "POST",
        f"{server.url}/api/v1/jobs",
        {"program_id": "sampler", "backend": "ibm_brisbane", "params": {}},
    )
    assert len(control.jobs()) == 1
    control.reset()
    assert control.jobs() == [] and control.scenario()["seed"] is None
    assert control.health()["blocked_connect_requests"] == 0
