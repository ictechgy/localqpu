"""localqpu에 연결된 Braket AwsSession을 만든다(v0.2 설계서 6절).

boto3·Braket은 선택 설치(`localqpu[braket]`)라 호출할 때 불러온다.
"""

from __future__ import annotations

import os
from typing import Any

from localqpu.aws.devices import BRAKET_REGION
from localqpu.client import bypass_environment_proxies
from localqpu.constants import DEFAULT_HOST, DEFAULT_PORT

#: connect_braket이 기본 출력 버킷으로 쓰는 이름. Braket 규칙대로 amazon-braket-로 시작한다.
LOCAL_BRAKET_BUCKET: str = "amazon-braket-localqpu"

#: localqpu 주소로 덮어쓰는 AWS 엔드포인트 환경변수. 서비스별 변수가 전역 변수보다 우선하므로 함께 덮어쓴다.
_ENDPOINT_VARIABLES: tuple[str, ...] = (
    "AWS_ENDPOINT_URL",
    "AWS_ENDPOINT_URL_BRAKET",
    "AWS_ENDPOINT_URL_S3",
    "AWS_ENDPOINT_URL_STS",
    "BRAKET_ENDPOINT",
)


def connect_braket(port: int = DEFAULT_PORT, host: str = DEFAULT_HOST) -> Any:
    """localqpu를 가리키는 braket.aws.AwsSession을 만든다.

    주의: 프로세스 환경변수를 바꾼다. AwsDevice.run은 세션을 copy_session으로 복사하는데 복사본은
    직접 넘긴 클라이언트 설정을 잃고 새 boto3 클라이언트를 만든다(2026-09-27 확인: 이 경로로 결과
    조회가 실제 AWS S3로 나갔다). 그래서 모든 AWS 서비스의 엔드포인트 환경변수를 localqpu로
    바꿔, SDK가 어떤 클라이언트를 새로 만들어도 localqpu로 오게 한다. localqpu가 모르는 AWS
    요청은 501로 드러나므로 외부로 새는 대신 실패로 보인다. 같은 프로세스에서 실제 AWS를 써야
    하는 테스트는 별도 프로세스에서 돌려야 한다.

    Raises:
        ImportError: Braket 선택 설치가 없을 때(Python 3.11 이상에서 pip install 'localqpu[braket]').
    """
    import boto3
    from braket.aws import AwsSession

    _route_aws_endpoints_to(f"http://{host}:{port}")
    bypass_environment_proxies(host)
    boto_session = boto3.Session(
        aws_access_key_id="localqpu", aws_secret_access_key="localqpu", region_name=BRAKET_REGION
    )
    return AwsSession(boto_session=boto_session, default_bucket=LOCAL_BRAKET_BUCKET)


def _route_aws_endpoints_to(endpoint_url: str) -> None:
    """모든 AWS 엔드포인트 환경변수를 localqpu로 바꾸고, 설정된 엔드포인트를 무시하는 옵션을 끈다."""
    for variable in _ENDPOINT_VARIABLES:
        os.environ[variable] = endpoint_url
    os.environ["AWS_IGNORE_CONFIGURED_ENDPOINT_URLS"] = "false"
