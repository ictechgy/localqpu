"""Braket REST(rest-json)와 S3 GetObject 흉내(v0.2 설계서 6절).

작업은 IBM 쪽과 같은 작업 관리자에 program_id="braket-openqasm"으로 넣어 대기열과 시나리오를
공유한다. 결과는 작업의 outputS3Directory/results.json을 S3 GetObject로 돌려준다.
"""

from __future__ import annotations

import json
import uuid
from functools import partial
from typing import Any
from urllib.parse import unquote

from localqpu.aws.devices import (
    SV1_ARN,
    SV1_BACKEND_NAME,
    job_id_from_task_arn,
    sv1_capabilities_json,
    sv1_summary,
    task_arn_for,
)
from localqpu.context import AppContext
from localqpu.jobs import JobRecord
from localqpu.programs.braket import MISSING_BRAKET_MESSAGE
from localqpu.server import Request, Response, Router

#: 작업 관리자의 상태를 Braket 상태로 바꾸는 표.
BRAKET_STATUS: dict[str, str] = {
    "Queued": "QUEUED",
    "Running": "RUNNING",
    "Completed": "COMPLETED",
    "Failed": "FAILED",
    "Cancelled": "CANCELLED",
}

#: Braket 작업의 program_id.
BRAKET_PROGRAM_ID = "braket-openqasm"


def register_aws_routes(router: Router, context: AppContext) -> None:
    """Braket·S3 라우트를 등록한다. S3 경로는 Braket 규칙대로 amazon-braket- 버킷만 받는다."""
    routes = [
        ("POST", "/quantum-task", create_quantum_task),
        ("GET", "/quantum-task/{task_arn}", get_quantum_task),
        ("PUT", "/quantum-task/{task_arn}/cancel", cancel_quantum_task),
        ("GET", "/device/{device_arn}", get_device),
        ("POST", "/devices", search_devices),
        ("GET", "/amazon-braket-{bucket_suffix}/{key+}", get_s3_object),
    ]
    for method, pattern, handler in routes:
        router.add(method, pattern, partial(handler, context))


def aws_error(status: int, error_type: str, message: str) -> Response:
    """botocore가 읽는 AWS rest-json 오류 형식."""
    return Response(status, {"__type": error_type, "message": message})


def create_quantum_task(context: AppContext, request: Request, params: dict[str, str]) -> Response:
    """CreateQuantumTask. 작업 ID를 먼저 정해 출력 경로에 넣은 뒤 작업 관리자에 등록한다."""
    body = request.json_body()
    rejection = _reject_task_request(body)
    if rejection is not None:
        return rejection
    job_id = f"localqpu-{uuid.uuid4().hex[:12]}"
    metadata = {
        "output_bucket": body["outputS3Bucket"],
        "output_directory": f"{body['outputS3KeyPrefix']}/{job_id}",
        "shots": body["shots"],
        "device_parameters": body.get("deviceParameters") or "{}",
    }
    context.jobs.submit(
        BRAKET_PROGRAM_ID,
        SV1_BACKEND_NAME,
        {"action": body["action"], "shots": body["shots"]},
        job_id=job_id,
        metadata=metadata,
    )
    return Response(201, {"quantumTaskArn": task_arn_for(job_id)})


def _reject_task_request(body: Any) -> Response | None:
    """작업 생성 본문을 검증한다. 문제가 있으면 ValidationException."""
    if not isinstance(body, dict):
        return aws_error(
            400, "ValidationException", "CreateQuantumTask 본문은 JSON 객체여야 합니다."
        )
    if body.get("deviceArn") != SV1_ARN:
        return aws_error(
            400,
            "ValidationException",
            f"localqpu는 SV1({SV1_ARN}) 장치만 흉내 냅니다(받은 장치: {body.get('deviceArn')}).",
        )
    for field_name, expected_type in (
        ("action", str),
        ("shots", int),
        ("outputS3Bucket", str),
        ("outputS3KeyPrefix", str),
    ):
        if not isinstance(body.get(field_name), expected_type) or isinstance(
            body.get(field_name), bool
        ):
            return aws_error(
                400, "ValidationException", f"'{field_name}' 값이 없거나 형식이 틀렸습니다."
            )
    if not body["outputS3Bucket"].startswith("amazon-braket-"):
        return aws_error(
            400, "ValidationException", "Braket 출력 버킷 이름은 amazon-braket-로 시작해야 합니다."
        )
    return None


def get_quantum_task(context: AppContext, request: Request, params: dict[str, str]) -> Response:
    """GetQuantumTask. 조회할 때마다 작업이 한 단계씩 진행된다."""
    job_id = job_id_from_task_arn(unquote(params["task_arn"]))
    job = context.jobs.poll(job_id) if job_id else None
    if job is None or job.program_id != BRAKET_PROGRAM_ID:
        return _task_not_found(unquote(params["task_arn"]))
    return Response(200, task_to_api(job))


def cancel_quantum_task(context: AppContext, request: Request, params: dict[str, str]) -> Response:
    """CancelQuantumTask. 끝난 작업은 ValidationException."""
    task_arn = unquote(params["task_arn"])
    job_id = job_id_from_task_arn(task_arn)
    outcome = context.jobs.cancel(job_id) if job_id else "not_found"
    if outcome == "not_found":
        return _task_not_found(task_arn)
    if outcome == "already_final":
        return aws_error(
            400, "ValidationException", f"작업 '{task_arn}'는 이미 끝나서 취소할 수 없습니다."
        )
    return Response(200, {"quantumTaskArn": task_arn, "cancellationStatus": "CANCELLING"})


def get_device(context: AppContext, request: Request, params: dict[str, str]) -> Response:
    """GetDevice. SV1만 알려 준다."""
    device_arn = unquote(params["device_arn"])
    if device_arn != SV1_ARN:
        return aws_error(
            404,
            "ResourceNotFoundException",
            f"localqpu에 '{device_arn}' 장치가 없습니다(흉내 내는 장치: {SV1_ARN}).",
        )
    try:
        capabilities = sv1_capabilities_json()
    except ImportError:
        return aws_error(501, "ValidationException", MISSING_BRAKET_MESSAGE)
    return Response(
        200, {**sv1_summary(), "deviceCapabilities": capabilities, "deviceQueueInfo": []}
    )


def search_devices(context: AppContext, request: Request, params: dict[str, str]) -> Response:
    """SearchDevices. 필터와 무관하게 SV1 하나를 돌려준다."""
    return Response(200, {"devices": [sv1_summary()]})


def get_s3_object(context: AppContext, request: Request, params: dict[str, str]) -> Response:
    """S3 GetObject. 완료된 Braket 작업의 results.json만 제공한다."""
    bucket = "amazon-braket-" + unquote(params["bucket_suffix"])
    key = unquote(params["key"])
    for job in context.jobs.list_jobs():
        if _is_result_object(job, bucket, key):
            return Response(200, _result_with_arns(job))
    return _s3_no_such_key(key)


def task_to_api(job: JobRecord) -> dict[str, Any]:
    """작업을 GetQuantumTask 응답 형식으로 바꾼다."""
    response: dict[str, Any] = {
        "quantumTaskArn": task_arn_for(job.job_id),
        "status": BRAKET_STATUS[job.status],
        "deviceArn": SV1_ARN,
        "deviceParameters": job.metadata.get("device_parameters", "{}"),
        "shots": job.metadata.get("shots", 0),
        "outputS3Bucket": job.metadata.get("output_bucket"),
        "outputS3Directory": job.metadata.get("output_directory"),
        "createdAt": job.created,
        "tags": {},
        # SDK는 metadata()["actionMetadata"]["actionType"]로 결과 형식을 판단한다(aws_quantum_task.py).
        "actionMetadata": {"actionType": "braket.ir.openqasm.program", "programCount": 1},
    }
    if job.ended_at is not None:
        response["endedAt"] = job.ended_at
    if job.status in ("Failed", "Cancelled") and job.reason:
        response["failureReason"] = job.reason
    return response


def _is_result_object(job: JobRecord, bucket: str, key: str) -> bool:
    """이 작업의 완료된 results.json 객체인지."""
    expected_key = f"{job.metadata.get('output_directory')}/results.json"
    is_braket_result = job.program_id == BRAKET_PROGRAM_ID and job.status == "Completed"
    return is_braket_result and job.metadata.get("output_bucket") == bucket and key == expected_key


def _result_with_arns(job: JobRecord) -> str:
    """결과의 taskMetadata.id·deviceId를 작업 ARN·장치 ARN으로 바꾼다(SDK가 이 값을 읽는다)."""
    result = json.loads(job.result_payload or "{}")
    result["taskMetadata"]["id"] = task_arn_for(job.job_id)
    result["taskMetadata"]["deviceId"] = SV1_ARN
    return json.dumps(result)


def _task_not_found(task_arn: str) -> Response:
    """모르는 작업 ARN."""
    return aws_error(
        404,
        "ResourceNotFoundException",
        f"localqpu에 '{task_arn}' 작업이 없습니다. 작업은 메모리에만 보관하므로 서버를 재시작하면 사라집니다.",
    )


def _s3_no_such_key(key: str) -> Response:
    """S3 형식의 NoSuchKey 오류(XML)."""
    body = f'<?xml version="1.0" encoding="UTF-8"?><Error><Code>NoSuchKey</Code><Message>localqpu에 \'{key}\' 객체가 없습니다.</Message></Error>'
    return Response(404, body, content_type="application/xml")
