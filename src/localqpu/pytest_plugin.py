"""pytest 플러그인: localqpu 서버와 연결된 서비스를 fixture로 제공한다.

패키지를 설치하면 pytest가 entry point(pytest11)로 자동으로 불러온다.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from qiskit_ibm_runtime import QiskitRuntimeService

from localqpu.app import start_server
from localqpu.client import connect
from localqpu.context import ServerConfig
from localqpu.control_client import LocalqpuControl
from localqpu.server import RunningServer


@pytest.fixture(scope="session")
def localqpu_server() -> Iterator[RunningServer]:
    """테스트 세션 동안 빈 포트에서 도는 localqpu 서버."""
    server = start_server(ServerConfig(port=0))
    try:
        yield server
    finally:
        server.stop()


@pytest.fixture
def localqpu_control(localqpu_server: RunningServer) -> Iterator[LocalqpuControl]:
    """테스트마다 초기화된 제어 클라이언트. 테스트가 끝나면 다시 초기화한다."""
    control = LocalqpuControl(localqpu_server.url)
    control.reset()
    yield control
    control.reset()


@pytest.fixture
def localqpu_service(
    localqpu_server: RunningServer, localqpu_control: LocalqpuControl
) -> QiskitRuntimeService:
    """localqpu에 연결된 QiskitRuntimeService. 시나리오는 localqpu_control로 바꾼다."""
    return connect(port=localqpu_server.port, host=localqpu_server.host)
