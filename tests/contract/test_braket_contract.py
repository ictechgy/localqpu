"""실제 Amazon Braket SDK가 localqpu에서 동작하는지 검증한다. Braket 패키지가 없으면 건너뛴다."""

import pytest

pytest.importorskip("braket.aws")

from braket.aws import AwsDevice, AwsQuantumTask  # noqa: E402
from braket.circuits import Circuit  # noqa: E402

from localqpu import connect_braket  # noqa: E402
from localqpu.aws.devices import SV1_ARN  # noqa: E402
from localqpu.control_client import LocalqpuControl  # noqa: E402
from localqpu.server import RunningServer  # noqa: E402

pytestmark = pytest.mark.contract


def sv1(server: RunningServer) -> AwsDevice:
    """localqpu에 연결된 SV1 장치."""
    return AwsDevice(SV1_ARN, aws_session=connect_braket(port=server.port, host=server.host))


def test_bell_counts_through_sdk(
    localqpu_server: RunningServer, localqpu_control: LocalqpuControl
) -> None:
    """SDK로 Bell 회로를 돌리면 00·11만 나오고 작업 상태가 COMPLETED다."""
    task = sv1(localqpu_server).run(Circuit().h(0).cnot(0, 1), shots=100)
    counts = task.result().measurement_counts
    assert set(counts) <= {"00", "11"} and sum(counts.values()) == 100
    assert task.state() == "COMPLETED" and task.id.startswith("arn:aws:braket:")


def test_device_properties_through_sdk(localqpu_server: RunningServer) -> None:
    """SDK가 SV1 장치 정보와 능력을 읽는다."""
    device = sv1(localqpu_server)
    assert device.name == "SV1" and device.status == "ONLINE" and device.type.value == "SIMULATOR"
    assert "braket.ir.openqasm.program" in device.properties.action


def test_failure_scenario_through_sdk(
    localqpu_server: RunningServer, localqpu_control: LocalqpuControl
) -> None:
    """시나리오 실패는 SDK에서 FAILED 상태와 실패 사유로 보인다."""
    localqpu_control.set_scenario(
        {"next_jobs": [{"outcome": "failed", "reason": "device maintenance"}]}
    )
    task = sv1(localqpu_server).run(Circuit().h(0), shots=10)
    assert task.result() is None and task.state() == "FAILED"
    assert "maintenance" in task.metadata()["failureReason"]


def test_cancel_through_sdk(
    localqpu_server: RunningServer, localqpu_control: LocalqpuControl
) -> None:
    """대기 중인 작업을 SDK로 취소하면 CANCELLED가 된다."""
    localqpu_control.set_scenario({"queue": {"polls_before_running": 1000}})
    task = sv1(localqpu_server).run(Circuit().h(0), shots=10)
    task.cancel()
    assert (
        AwsQuantumTask(
            task.id,
            aws_session=connect_braket(port=localqpu_server.port, host=localqpu_server.host),
        ).state()
        == "CANCELLED"
    )


def test_environment_proxy_does_not_hijack_braket(
    localqpu_server: RunningServer,
    localqpu_control: LocalqpuControl,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """HTTP_PROXY·HTTPS_PROXY가 있어도 Braket·S3 요청은 localqpu로 간다(닫힌 포트로 새면 실패)."""
    for variable in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
        monkeypatch.setenv(variable, "http://127.0.0.1:9")
    for variable in ("NO_PROXY", "no_proxy"):
        monkeypatch.delenv(variable, raising=False)
    counts = (
        sv1(localqpu_server).run(Circuit().h(0).cnot(0, 1), shots=20).result().measurement_counts
    )
    assert sum(counts.values()) == 20


def test_queue_position_through_sdk(
    localqpu_server: RunningServer, localqpu_control: LocalqpuControl
) -> None:
    """SDK의 queue_position()이 대기 중인 작업의 순서를 읽는다(v0.2.0에서는 KeyError)."""
    localqpu_control.set_scenario({"queue": {"polls_before_running": 1000}})
    task = sv1(localqpu_server).run(Circuit().h(0), shots=10)
    assert task.queue_position().queue_position == "1"
