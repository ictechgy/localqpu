"""localqpu에 연결된 QiskitRuntimeService를 만든다(설계서 5.9절)."""

from __future__ import annotations

import os

from qiskit_ibm_runtime import QiskitRuntimeService

from localqpu.constants import DEFAULT_HOST, DEFAULT_PORT, FAKE_CLOUD_HOST, LOCAL_INSTANCE_CRN


def connect(port: int = DEFAULT_PORT, host: str = DEFAULT_HOST) -> QiskitRuntimeService:
    """localqpu를 가리키는 QiskitRuntimeService를 만든다.

    주의: 런타임 인증 주소는 클라이언트 코드에 iam.cloud.ibm.com으로 고정돼 있고
    IAM_URL 환경변수로만 바뀐다. 이 값을 설정하지 않으면 클라이언트가 실제 IBM으로
    키를 보낸다. 그래서 이 함수는 프로세스 환경변수 IAM_URL과 NO_PROXY를 설정한다
    (프로세스 전체에 영향). http·https 프록시를 모두 localqpu로 지정해, 설정이 새더라도
    외부로 직접 나가지 않고 localqpu에서 CONNECT로 막히게 한다.
    """
    proxy_url = f"http://{host}:{port}"
    os.environ["IAM_URL"] = f"http://iam.{FAKE_CLOUD_HOST}"
    bypass_environment_proxies(FAKE_CLOUD_HOST)
    return QiskitRuntimeService(
        channel="ibm_quantum_platform",
        token="localqpu",
        instance=LOCAL_INSTANCE_CRN,
        url=f"http://{FAKE_CLOUD_HOST}",
        url_resolver=_resolve_runtime_url,
        proxies={"urls": {"http": proxy_url, "https": proxy_url}},
    )


def bypass_environment_proxies(host: str) -> None:
    """NO_PROXY·no_proxy에 가짜 호스트를 더해, 환경 프록시가 localqpu 대신 쓰이지 않게 한다.

    Qiskit은 프록시를 세션(session.proxies)에만 두는데, requests는 환경 프록시(HTTP_PROXY 등)를
    세션 프록시보다 먼저 적용한다(Session.merge_environment_settings). 이것이 없으면 회사 프록시가
    설정된 환경에서 요청이 localqpu가 아니라 외부 프록시로 나간다. requests는 NO_PROXY 항목을
    접미사로 비교하므로 iam.localqpu.test 같은 하위 호스트도 함께 제외된다.
    """
    for variable in ("NO_PROXY", "no_proxy"):
        entries = [
            entry.strip() for entry in os.environ.get(variable, "").split(",") if entry.strip()
        ]
        if host not in entries:
            os.environ[variable] = ",".join([*entries, host])


def _resolve_runtime_url(
    url: str,
    instance: str,
    private_endpoint: bool | None = None,
    channel: str = "ibm_quantum_platform",
) -> str:
    """런타임 API 기본 URL. 클라이언트의 url_resolver 규약(인자 4개)을 따른다."""
    return f"http://{FAKE_CLOUD_HOST}/api/v1"
