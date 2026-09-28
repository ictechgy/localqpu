"""AWS Braket·S3 라우트 테스트(직접 모드 HTTP). Braket 패키지가 없으면 건너뛴다."""

import json
import time
from collections.abc import Iterator
from typing import Any
from urllib.parse import quote

import pytest

pytest.importorskip("braket.default_simulator")

from braket.circuits import Circuit  # noqa: E402
from braket.circuits.serialization import IRType  # noqa: E402

from localqpu.app import start_server  # noqa: E402
from localqpu.aws.devices import SV1_ARN  # noqa: E402
from localqpu.context import ServerConfig  # noqa: E402
from localqpu.http_util import send_direct  # noqa: E402
from localqpu.scenario import parse_scenario  # noqa: E402
from localqpu.server import RunningServer  # noqa: E402


def launch(scenario: dict[str, Any] | None = None) -> RunningServer:
    """시나리오를 넣어 빈 포트에 서버를 띄운다."""
    return start_server(ServerConfig(port=0, scenario=parse_scenario(scenario or {})))


@pytest.fixture
def server() -> Iterator[RunningServer]:
    """기본 서버."""
    running = launch()
    yield running
    running.stop()


def create_task(server: RunningServer, shots: int = 50, client_token: str | None = None) -> str:
    """Bell 회로 작업을 만들고 ARN을 돌려준다. client_token이 없으면 매번 새 토큰을 쓴다."""
    import uuid

    body = {
        "clientToken": client_token or uuid.uuid4().hex,
        "deviceArn": SV1_ARN,
        "shots": shots,
        "outputS3Bucket": "amazon-braket-localqpu",
        "outputS3KeyPrefix": "tasks",
        "action": Circuit().h(0).cnot(0, 1).to_ir(IRType.OPENQASM).json(),
        "deviceParameters": "{}",
    }
    status, response = send_direct("POST", f"{server.url}/quantum-task", body)
    assert status == 201
    return str(response["quantumTaskArn"])


def wait_for_status(server: RunningServer, arn: str, timeout: float = 30.0) -> dict[str, Any]:
    """작업이 끝날 때까지 GetQuantumTask를 부른다."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        _, task = send_direct("GET", f"{server.url}/quantum-task/{quote(arn, safe='')}")
        if task["status"] in {"COMPLETED", "FAILED", "CANCELLED"}:
            return dict(task)
        time.sleep(0.05)
    raise AssertionError(f"{arn}가 끝나지 않았습니다.")


def test_task_round_trip_through_s3(server: RunningServer) -> None:
    """작업을 만들면 QUEUED로 시작하고, 끝나면 S3 results.json에 ARN이 박힌 결과가 있다."""
    arn = create_task(server)
    _, first = send_direct("GET", f"{server.url}/quantum-task/{quote(arn, safe='')}")
    assert first["status"] == "QUEUED" and first["deviceArn"] == SV1_ARN and first["shots"] == 50
    assert first["actionMetadata"]["actionType"] == "braket.ir.openqasm.program"
    task = wait_for_status(server, arn)
    assert task["status"] == "COMPLETED" and task["endedAt"]
    status, result = send_direct(
        "GET", f"{server.url}/{task['outputS3Bucket']}/{task['outputS3Directory']}/results.json"
    )
    assert (
        status == 200
        and result["taskMetadata"]["id"] == arn
        and result["taskMetadata"]["deviceId"] == SV1_ARN
    )
    assert {"".join(map(str, row)) for row in result["measurements"]} <= {"00", "11"}


def test_failed_task_reports_reason() -> None:
    """시나리오 실패는 FAILED와 failureReason으로 보인다."""
    running = launch({"next_jobs": [{"outcome": "failed", "reason": "device maintenance"}]})
    try:
        task = wait_for_status(running, create_task(running))
        assert task["status"] == "FAILED" and "maintenance" in task["failureReason"]
    finally:
        running.stop()


def test_cancel_queued_task() -> None:
    """대기 중인 작업을 취소하면 CANCELLING을 돌려주고 이후 CANCELLED가 된다. 두 번째 취소는 400이다."""
    running = launch({"queue": {"polls_before_running": 1000}})
    try:
        arn = create_task(running)
        status, response = send_direct(
            "PUT", f"{running.url}/quantum-task/{quote(arn, safe='')}/cancel"
        )
        assert status == 200 and response["cancellationStatus"] == "CANCELLING"
        assert (
            send_direct("GET", f"{running.url}/quantum-task/{quote(arn, safe='')}")[1]["status"]
            == "CANCELLED"
        )
        status, error = send_direct(
            "PUT", f"{running.url}/quantum-task/{quote(arn, safe='')}/cancel"
        )
        assert status == 400 and error["__type"] == "ValidationException"
    finally:
        running.stop()


def test_device_endpoints(server: RunningServer) -> None:
    """SV1 장치 조회·검색이 시뮬레이터 능력과 함께 나온다."""
    status, device = send_direct("GET", f"{server.url}/device/{quote(SV1_ARN, safe='')}")
    capabilities = json.loads(device["deviceCapabilities"])
    assert (
        status == 200 and device["deviceType"] == "SIMULATOR" and device["deviceStatus"] == "ONLINE"
    )
    assert capabilities["braketSchemaHeader"]["name"].endswith(
        "gate_model_simulator_device_capabilities"
    )
    _, search = send_direct("POST", f"{server.url}/devices", {"filters": []})
    assert [entry["deviceArn"] for entry in search["devices"]] == [SV1_ARN]


@pytest.mark.parametrize(
    "path",
    [
        "/device/" + quote("arn:aws:braket:::device/qpu/ionq/Aria-1", safe=""),
        "/quantum-task/"
        + quote("arn:aws:braket:us-east-1:000000000000:quantum-task/nope", safe=""),
    ],
)
def test_unknown_resources_are_aws_not_found(server: RunningServer, path: str) -> None:
    """모르는 장치·작업은 AWS 형식의 ResourceNotFoundException(404)이다."""
    status, error = send_direct("GET", f"{server.url}{path}")
    assert status == 404 and error["__type"] == "ResourceNotFoundException"


def test_unknown_task_device_on_create_is_400(server: RunningServer) -> None:
    """지원하지 않는 장치로 작업을 만들면 ValidationException(400)이다."""
    body = {
        "deviceArn": "arn:aws:braket:::device/qpu/ionq/Aria-1",
        "shots": 1,
        "outputS3Bucket": "amazon-braket-x",
        "outputS3KeyPrefix": "t",
        "action": "{}",
    }
    status, error = send_direct("POST", f"{server.url}/quantum-task", body)
    assert status == 400 and error["__type"] == "ValidationException" and "sv1" in error["message"]


def test_missing_s3_object_is_xml_no_such_key(server: RunningServer) -> None:
    """없는 S3 객체는 S3 형식의 XML NoSuchKey(404)다."""
    status, body = send_direct(
        "GET", f"{server.url}/amazon-braket-localqpu/tasks/none/results.json"
    )
    assert status == 404 and "<Code>NoSuchKey</Code>" in body


def test_connect_braket_routes_every_aws_service_to_localqpu(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """connect_braket 뒤에는 SDK가 세션을 복사해 새로 만든 클라이언트도 localqpu를 가리킨다.

    AwsDevice.run은 세션을 copy_session으로 복사하는데, 복사본은 직접 넘긴 클라이언트 설정을 잃는다.
    사용자의 서비스별 엔드포인트 변수와 무시 설정도 덮어써 외부로 새지 않게 한다.
    """
    import boto3

    from localqpu import connect_braket

    monkeypatch.setenv("AWS_ENDPOINT_URL_S3", "https://s3.amazonaws.com")
    monkeypatch.setenv("AWS_IGNORE_CONFIGURED_ENDPOINT_URLS", "true")
    for variable in (
        "AWS_ENDPOINT_URL",
        "AWS_ENDPOINT_URL_BRAKET",
        "AWS_ENDPOINT_URL_STS",
        "BRAKET_ENDPOINT",
    ):
        monkeypatch.delenv(variable, raising=False)
    session = connect_braket(port=18999)
    copied = session.copy_session()
    fresh = boto3.Session(aws_access_key_id="x", aws_secret_access_key="y", region_name="us-east-1")
    for client in (copied.braket_client, copied.s3_client, fresh.client("s3"), fresh.client("sts")):
        assert client.meta.endpoint_url == "http://127.0.0.1:18999"


def test_device_without_braket_extra_explains_install(
    server: RunningServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Braket 선택 설치가 없는 서버는 내부 오류 대신 설치 방법을 담은 501을 돌려준다."""
    import sys

    from localqpu.aws.devices import sv1_capabilities_json

    monkeypatch.setitem(sys.modules, "braket.default_simulator", None)
    sv1_capabilities_json.cache_clear()
    try:
        status, error = send_direct("GET", f"{server.url}/device/{quote(SV1_ARN, safe='')}")
    finally:
        sv1_capabilities_json.cache_clear()
    assert status == 501 and "localqpu[braket]" in error["message"]


def test_queue_info_reports_position_while_queued() -> None:
    """대기 중인 작업은 대기열 순서를, 끝난 작업은 "None"을 queueInfo로 알려 준다(SDK queue_position용)."""
    running = launch({"queue": {"polls_before_running": 1000}})
    try:
        first, second = create_task(running), create_task(running)
        _, second_task = send_direct("GET", f"{running.url}/quantum-task/{quote(second, safe='')}")
        assert (
            second_task["queueInfo"]["position"] == "2"
            and second_task["queueInfo"]["queuePriority"] == "Normal"
        )
        send_direct("PUT", f"{running.url}/quantum-task/{quote(first, safe='')}/cancel")
        _, first_task = send_direct("GET", f"{running.url}/quantum-task/{quote(first, safe='')}")
        assert (
            first_task["queueInfo"]["position"] == "None"
            and "CANCELLED" in first_task["queueInfo"]["message"]
        )
    finally:
        running.stop()


def test_client_token_is_idempotent(server: RunningServer) -> None:
    """같은 clientToken으로 다시 만들면 같은 작업을 돌려주고, 다른 토큰은 새 작업이다."""
    first = create_task(server, client_token="retry-me")
    assert create_task(server, client_token="retry-me") == first
    assert create_task(server, client_token="another") != first


def test_sv1_can_be_set_offline_by_scenario() -> None:
    """시나리오로 sv1을 오프라인으로 두면 서버가 받아들이고, 장치 상태와 작업 대기에 반영된다."""
    running = launch({"backends": {"sv1": {"status": "offline"}}})
    try:
        _, device = send_direct("GET", f"{running.url}/device/{quote(SV1_ARN, safe='')}")
        assert device["deviceStatus"] == "OFFLINE"
        arn = create_task(running)
        statuses = [
            send_direct("GET", f"{running.url}/quantum-task/{quote(arn, safe='')}")[1]["status"]
            for _ in range(3)
        ]
        assert statuses == ["QUEUED"] * 3
    finally:
        running.stop()
