"""localqpu 전역 상수.

클라이언트 설정(connect)과 서버 응답이 같은 값을 써야 하므로 한곳에 모은다.
"""

from __future__ import annotations

#: localqpu가 흉내 내는 유일한 인스턴스의 CRN. 클라이언트의 is_crn 검사를 통과하는 형식이다.
LOCAL_INSTANCE_CRN: str = "crn:v1:bluemix:public:quantum-computing:us-east:a/localqpu::localqpu"

#: 글로벌 카탈로그 플랜 ID. 런타임 경로(/api/v1/backends 등)와 겹치지 않는 이름이라 경로만으로 라우팅할 수 있다.
LOCAL_PLAN_ID: str = "localqpu-plan"

#: 프록시 모드에서 클라이언트에 알려 주는 가짜 클라우드 호스트. 실제로 해석될 일이 없는 .test 도메인이다.
FAKE_CLOUD_HOST: str = "localqpu.test"

#: 기본 바인딩 주소. 외부 노출을 막기 위해 루프백만 쓴다.
DEFAULT_HOST: str = "127.0.0.1"

#: 기본 포트.
DEFAULT_PORT: int = 8787

#: 기본으로 노출하는 칩 스냅샷 이름.
DEFAULT_BACKENDS: tuple[str, ...] = ("ibm_brisbane",)

#: 정확 시뮬레이션을 허용하는 최대 활성 큐비트 수. 넘으면 sampler는 stub, executor는 실패한다.
DEFAULT_MAX_SIM_QUBITS: int = 24

#: localqpu 자체 사유(입력 해석 실패, 한도 초과 등)로 작업이 실패했을 때 쓰는 사유 코드.
LOCALQPU_ERROR_CODE: int = 9000

#: 기존 SamplerV2에서 shots가 지정되지 않았을 때 쓰는 기본값. IBM SamplerV2 기본값과 같다.
DEFAULT_SAMPLER_SHOTS: int = 4096

#: 발급하는 가짜 IAM 토큰의 수명(초).
TOKEN_LIFETIME_SECONDS: int = 3600
