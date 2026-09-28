"""흉내 내는 Braket 장치(SV1)와 ARN 규칙."""

from __future__ import annotations

from functools import lru_cache
from typing import Any

#: 흉내 내는 유일한 Braket 장치. 실제 SV1 관리형 시뮬레이터의 ARN과 같다.
SV1_ARN: str = "arn:aws:braket:::device/quantum-simulator/amazon/sv1"

#: 작업 ARN에 쓰는 리전과 가짜 계정 ID.
BRAKET_REGION: str = "us-east-1"
BRAKET_ACCOUNT_ID: str = "000000000000"

#: 작업 ARN 접두사. 뒤에 작업 ID가 붙는다.
TASK_ARN_PREFIX: str = f"arn:aws:braket:{BRAKET_REGION}:{BRAKET_ACCOUNT_ID}:quantum-task/"

#: 작업 관리자에 넘기는 백엔드 이름(시나리오의 backends 설정 키로도 쓰인다).
SV1_BACKEND_NAME: str = "sv1"


def task_arn_for(job_id: str) -> str:
    """작업 ID로 작업 ARN을 만든다."""
    return TASK_ARN_PREFIX + job_id


def job_id_from_task_arn(task_arn: str) -> str | None:
    """작업 ARN에서 작업 ID를 꺼낸다. localqpu 형식이 아니면 None."""
    return task_arn.removeprefix(TASK_ARN_PREFIX) if task_arn.startswith(TASK_ARN_PREFIX) else None


def sv1_summary(device_status: str = "ONLINE") -> dict[str, Any]:
    """SearchDevices 항목이자 GetDevice 응답의 공통 부분. 상태는 시나리오의 sv1 설정을 따른다."""
    return {
        "deviceArn": SV1_ARN,
        "deviceName": "SV1",
        "providerName": "Amazon Braket",
        "deviceType": "SIMULATOR",
        "deviceStatus": device_status,
    }


@lru_cache(maxsize=1)
def sv1_capabilities_json() -> str:
    """SV1 능력 JSON. Braket 기본 statevector 시뮬레이터의 능력을 그대로 쓴다.

    Raises:
        ImportError: Braket 선택 설치가 없을 때.
    """
    from braket.default_simulator import StateVectorSimulator

    return str(StateVectorSimulator().properties.json())
