# localqpu 설계 (v0.1)

- 작성일: 2026-09-27
- 상태: 설계 검토 중
- 근거: 2026-09-27 PoC (qiskit-ibm-runtime 0.50.0, 사용자 코드 무수정 Bell 회로 E2E 성공)

## 1. 한 줄 요약

양자 클라우드(IBM Quantum Platform) API를 로컬에서 흉내 내는 테스트용 에뮬레이터. 결제 서비스의 "테스트 모드"처럼, 양자컴을 부르는 앱을 **느리지 않고, 비용 없이, 원하는 장애 상황을 만들어** 테스트하게 한다.

## 2. 왜 필요한가

실제 양자 클라우드는 테스트 대상 의존성으로서 최악의 조건을 모두 갖췄다.

| 조건 | 실제 IBM | localqpu |
|---|---|---|
| 속도 | 대기열로 수 분~수 시간 | 1초 내외 |
| 비용 | 무료 플랜은 월 10분 수준, 이후 유료 | 무료, 무제한 |
| 재현성 | 노이즈·칩 상태 변동으로 매번 다름 | 시드 고정 시 동일 |
| 장애 재현 | 서버 다운·대기열 폭주를 일부러 만들 수 없음 | 시나리오로 즉시 재현 |

**위치 설정**: 유용한 규모의 양자 회로는 고전 컴퓨터로 시뮬레이션할 수 없다(그것이 양자컴의 존재 이유). 따라서 localqpu의 목적은 "양자 계산 결과가 맞는지"가 아니라 **양자 API를 호출하는 주변 코드(제출·대기·재시도·결과 파싱·에러 처리)가 맞는지** 검증하는 것이다.

**기존 대안과의 차이**:
- IBM `channel="local"`(로컬 테스트 모드): 파이썬 프로세스 안에서만 동작. HTTP 경로·인증·재시도·대기열 동작을 검증하지 못하고, 비-파이썬 클라이언트는 쓸 수 없다.
- IBM executor `dry_run`: 서버 측 기능이라 계정·네트워크가 필요하고 접근이 제한될 수 있다.
- localqpu: 실제 HTTP API를 흉내 내므로 클라이언트의 네트워크 경로 전체를 그대로 검증한다.

## 3. 성공 기준

1. 기존 Qiskit 코드에서 서비스 생성 한 줄만 `localqpu.connect()`로 바꾸면, **기존 `SamplerV2`와 새 executor 기반 Sampler** 모두 결과를 받는다.
2. 테스트 코드에서 대기열 지연, 작업 실패, 작업 취소, 백엔드 오프라인, 사용량 초과를 만들 수 있다.
3. `pip install localqpu && localqpu start` 또는 `docker run` 한 줄로 실행된다.
4. 실제 `qiskit-ibm-runtime` 클라이언트로 돌리는 계약 테스트가 CI에서 통과한다.
5. localqpu는 어떤 경우에도 실제 IBM으로 요청을 전달하지 않는다.

## 4. 범위

### v0.1에 포함
- IBM Quantum Platform API 호환 (PoC에서 확인한 11개 엔드포인트 + 작업 취소)
- 프로그램: `sampler`(기존 SamplerV2), `executor`(새 Sampler, 스키마 v2.0)
- 이상적(노이즈 없는) 시뮬레이션 + 큰 회로용 stub 모드
- 장애 시나리오 (파일 + 런타임 제어 API)
- 연결 도우미 `localqpu.connect()`, pytest 플러그인
- CLI, Dockerfile

### v0.1에서 제외 (다음 버전 후보)
- AWS Braket 등 타사 API
- 노이즈 흉내 (v0.2: qiskit-aer + 칩 스냅샷의 보정값)
- Estimator (기존·executor 둘 다)
- Session/Batch 모드
- Qiskit Functions, 펄스 수준 제어

## 5. 아키텍처

```
Qiskit 클라이언트 ──HTTP 프록시──▶ [HTTP 서버] ─▶ [라우터]
                                                   ├─ [IBM 어댑터: 인증·인스턴스·백엔드·사용량]
                                                   ├─ [작업 관리자] ─▶ [프로그램 어댑터: sampler / executor]
                                                   │                          └─▶ [시뮬레이션 엔진]
                                                   └─ [제어 API] ─▶ [시나리오]
```

### 5.1 HTTP 서버 (`localqpu/server/`)
- Python 표준 라이브러리 `ThreadingHTTPServer` 기반. PoC에서 프록시 형식(절대 URL) 요청 처리가 검증됐고, 웹 프레임워크 의존성이 필요 없다.
- 두 가지 접속 방식을 모두 받는다.
  - **프록시 모드**: Qiskit이 인증·검색용 호스트를 `iam.{호스트}` 식으로 파생해 포트가 사라지므로, Qiskit 연결은 프록시 모드를 쓴다.
  - **직접 모드**: `http://127.0.0.1:{포트}/api/v1/...`로 바로 호출. 비-파이썬 클라이언트용.
- 기본 바인딩은 `127.0.0.1`. 외부 바인딩은 `--host`를 명시할 때만 허용한다.
- 기본 포트 8787.

### 5.2 라우터 (`localqpu/server/router.py`)
- (메서드, 경로)로 핸들러를 찾는다. 프록시 모드일 때는 호스트로 서비스를 구분한다(`iam.*`, `api.global-search-tagging.*`, `globalcatalog.*`, 그 외는 런타임 API).
- 처리하지 않는 경로는 **501**과 함께 `"localqpu는 아직 GET /x를 흉내 내지 않습니다. 이슈로 알려주세요"`를 돌려준다. 조용히 실패하지 않는다.
- HTTPS `CONNECT` 요청은 **405**와 함께 `"클라이언트가 실제 IBM(HTTPS)으로 나가려 합니다. localqpu.connect()를 쓰거나 IAM_URL을 설정하세요"`를 돌려준다. 설정 누락으로 인한 키 유출 시도를 드러내기 위함이다.

### 5.3 IBM 어댑터 (`localqpu/ibm/`)
PoC에서 검증한 엔드포인트:

| 호스트 | 메서드·경로 | 응답 |
|---|---|---|
| iam | `POST /identity/token` | 서명되지 않은 JWT (클라이언트가 `exp`를 디코딩하므로 JWT 형식 필수) |
| global-search | `POST /v3/resources/search` | localqpu 인스턴스 CRN 1개 |
| globalcatalog | `GET /api/v1/localqpu-plan` | 플랜 이름 `localqpu`, 가격 유형 `free` |
| runtime | `GET /api/v1/backends` | `{"devices":[{name, status:{name:"online"}, qubits, queue_length}]}` |
| runtime | `GET /api/v1/backends/{name}/configuration` | 칩 스냅샷 `conf_*.json` |
| runtime | `GET /api/v1/backends/{name}/properties` | 칩 스냅샷 `props_*.json` |
| runtime | `GET /api/v1/backends/{name}/status` | 시나리오의 백엔드 상태 반영 |
| runtime | `GET /api/v1/instances/usage` | 시나리오의 사용량 반영 |
| runtime | `POST /api/v1/jobs` | 작업 관리자에 등록 후 `{id, backend}` |
| runtime | `GET /api/v1/jobs/{id}` | 작업 상태 |
| runtime | `GET /api/v1/jobs/{id}/results` | 인코딩된 결과 |
| runtime | `POST /api/v1/jobs/{id}/cancel` | 작업 취소 (클라이언트 `program_job.py`의 URL_MAP에서 확인) |

- 백엔드 목록은 `qiskit_ibm_runtime.fake_provider`에 포함된 실제 칩 스냅샷을 그대로 쓴다. 기본 노출은 `ibm_brisbane` 하나이고, `--backends`로 스냅샷이 있는 다른 칩을 추가할 수 있다.
- 토큰과 API 키는 어떤 값이든 받아들인다. 인증 실패는 시나리오로만 만든다.

### 5.4 작업 관리자 (`localqpu/jobs/`)
- 메모리 저장소. 서버를 재시작하면 작업 기록은 사라진다.
- 상태 전이: `Queued → Running → Completed | Failed | Cancelled`. 클라이언트 매핑표(`API_TO_JOB_STATUS`: QUEUED→QUEUED, RUNNING→RUNNING, COMPLETED→DONE, FAILED→ERROR, CANCELLED→CANCELLED)와 일치한다. 응답의 `state.status`에 이 문자열을 넣는다.
- 실행은 스레드 풀에서 한다. 대기 시간은 시나리오의 `queue` 설정을 따른다.
- 실패와 취소 시 `state.reason`과 `state.reason_code`를 채운다. 클라이언트가 이 값을 에러 메시지로 보여준다.

### 5.5 프로그램 어댑터 (`localqpu/programs/`)
`program_id`마다 하나씩 둔다. 새 프로그램 형식은 파일 하나를 추가해 지원한다.

- **sampler** (기존 SamplerV2): `RuntimeDecoder`로 입력을 해석하고 → 시뮬레이션 → `RuntimeEncoder`로 `PrimitiveResult`를 인코딩한다. PoC에서 검증했다.
- **executor** (새 Sampler, 스키마 v2.0): 클라이언트 패키지의 `quantum_program_from_2_0`으로 입력을 해석한다. 실행은 qiskit-ibm-runtime 로컬 테스트 모드의 `run_quantum_program` 재사용을 1순위로 한다. 결과는 `ibm_quantum_schemas`의 v2.0 결과 모델로 직접 구성한다. 클라이언트 패키지에는 결과를 해석하는 방향(`quantum_program_result_from_2_0`)만 있어서, 역방향 변환은 localqpu가 구현해야 한다(확인됨). **구현 계획의 첫 작업은 이 경로가 실제로 되는지 확인하는 스파이크다.** `run_quantum_program`을 재사용할 수 없으면 시뮬레이션 엔진으로 직접 실행한다.

### 5.6 시뮬레이션 엔진 (`localqpu/simulation/`)
- 칩 배치가 끝난 회로(예: 127큐비트 폭)에서 **실제로 쓰인 큐비트만 남긴다**. PoC에서 이것 없이는 메모리 한계로 실패했다.
- 쓰인 큐비트가 `--max-sim-qubits`(기본 24) 이하이면 `StatevectorSampler`로 정확히 계산한다.
- 초과하면 **stub 모드**로 전환한다. 결과 형태(비트 수, shots, 레지스터 이름)만 맞춘 균등 무작위 비트열을 돌려주고, 서버 로그와 결과 메타데이터에 stub이었다는 사실을 남긴다.
- 시나리오에 `seed`가 있으면 모든 무작위성(샘플링, stub, 실패 확률)에 적용한다.

### 5.7 시나리오 (`localqpu/scenario/`)
JSON 파일(`--scenario`)이나 제어 API로 설정한다. 모든 필드는 선택이고, 기본값은 "즉시 성공"이다.

```json
{
  "seed": 42,
  "queue": { "delay_seconds": 0, "polls_before_running": 1 },
  "failures": { "rate": 0.0, "reason": "Simulated failure", "reason_code": 9999 },
  "next_jobs": [
    { "outcome": "failed", "reason": "QPU calibration in progress", "reason_code": 1517 },
    { "outcome": "cancelled" }
  ],
  "backends": { "ibm_brisbane": { "status": "offline", "queue_length": 120 } },
  "usage": { "limit_seconds": 600, "consumed_seconds": 600 },
  "auth": { "reject_tokens": false }
}
```

- `next_jobs`는 큐처럼 앞에서부터 하나씩 소비된다. 비어 있으면 `failures.rate`를 적용한다.
- `reason_code` 값은 그대로 전달만 한다. localqpu는 IBM 에러 코드의 의미를 해석하지 않는다.
- 주의: 클라이언트는 `Cancelled` + `reason_code: 1305` 조합을 `ERROR`로 바꿔 취급한다(`runtime_job_v2.py`에서 확인). 이 조합도 그대로 재현되므로 README의 시나리오 예시에 적어 둔다.

### 5.8 제어 API (`/_localqpu/...`)
테스트 코드가 서버 상태를 바꾸고 검사하는 용도이며, 직접 모드와 프록시 모드 모두에서 접근할 수 있다.

| 메서드·경로 | 기능 |
|---|---|
| `GET /_localqpu/scenario` | 현재 시나리오 조회 |
| `PUT /_localqpu/scenario` | 시나리오 교체 |
| `POST /_localqpu/reset` | 작업 기록과 시나리오 초기화 |
| `GET /_localqpu/jobs` | 제출된 작업 목록 (프로그램, 백엔드, 큐비트 수, 결과 종류). 테스트 단언용 |
| `GET /_localqpu/health` | 상태 확인 |

### 5.9 연결 도우미와 pytest 플러그인 (`localqpu/client.py`, `localqpu/pytest_plugin.py`)
- `localqpu.connect(port=8787, host="127.0.0.1") -> QiskitRuntimeService`
  - `url`, `url_resolver`, `proxies`, `token`, `instance`를 localqpu용으로 채운다.
  - PoC에서 찾은 함정 대응: 런타임 인증은 `iam.cloud.ibm.com`이 코드에 고정돼 있어 `IAM_URL` 환경변수로만 바뀐다. 이 값을 설정하지 않으면 클라이언트가 **실제 IBM으로 키를 보낸다.** 그래서 `connect()`가 프로세스 환경변수 `IAM_URL`을 설정한다. 프로세스 전체에 영향이 있으므로 문서에 명시한다.
- pytest 플러그인 fixture
  - `localqpu_server`: 빈 포트에서 서버를 스레드로 띄운다(세션 범위).
  - `localqpu_service`: 연결된 `QiskitRuntimeService`.
  - `localqpu_scenario`: 시나리오 설정 도우미. 테스트마다 초기화된다.

### 5.10 CLI와 배포
- `localqpu start [--host] [--port] [--scenario FILE] [--backends a,b] [--max-sim-qubits N] [--verbose]`
- 요청마다 한 줄 로그를 남긴다(`상태코드 메서드 호스트경로`). `--verbose`면 본문 일부도 남긴다.
- PyPI 패키지 `localqpu`(Python ≥ 3.10, qiskit-ibm-runtime과 같은 기준), Dockerfile 제공. PyPI와 레지스트리 게시는 v0.1 범위 밖이고, 사용자 승인 후 진행한다.
- 라이선스는 Qiskit 생태계와 같은 Apache-2.0으로 제안한다.

## 6. 데이터 흐름 (SamplerV2 예)

1. `connect()`가 `IAM_URL`을 설정하고 서비스 객체를 만든다 → 토큰 발급, 인스턴스 검색, 플랜 조회.
2. `least_busy()`/`backend()` → 백엔드 목록, 설정, 속성.
3. `run()` → 사용량 조회, 백엔드 상태, `POST /jobs`.
4. 작업 관리자가 등록하고, 시나리오에 따라 대기한 뒤 프로그램 어댑터를 실행한다.
5. 클라이언트 폴링 `GET /jobs/{id}` → 완료되면 `GET /jobs/{id}/results`.

## 7. 에러 처리 원칙

- 흉내 내지 않는 경로: 501 + 무엇이 없는지와 이슈 안내.
- 입력 해석 실패(QPY 버전 불일치 등): 작업을 `Failed`로 두고, reason에 원인과 해결 방향을 적는다(예: "클라이언트 QPY 버전 N이 localqpu의 qiskit보다 새롭습니다. localqpu를 업데이트하세요").
- 시뮬레이션 예외: 작업을 `Failed`로 두고 서버 로그에 스택을 남긴다. 사용자에게 보이는 reason에는 요약만 넣는다.
- 빈 `except`는 금지. 잡은 예외는 항상 로그를 남기거나 작업 상태에 반영한다.

## 8. 테스트 전략

- **단위 테스트**: 안 쓰는 큐비트 걸러내기, 작업 상태 전이, 시나리오 소비 순서와 시드 재현성, 라우터의 501·405 응답.
- **계약 테스트**: 실제 `qiskit-ibm-runtime` 클라이언트를 localqpu에 붙여 실행한다.
  - SamplerV2로 Bell 회로 → `{'00','11'}`만 나오고, 합이 shots와 같다.
  - executor Sampler로 같은 검증.
  - 시나리오별로 실패, 취소, 오프라인, 사용량 초과 시 클라이언트가 예상한 예외나 상태를 내는지 확인.
  - `connect()`를 쓰면 외부 네트워크로 나가는 요청이 없는지 확인(프록시 로그에 CONNECT가 없어야 함).
- **CI 매트릭스**: qiskit-ibm-runtime `0.50.*`와 최신 버전. 최신 버전에서만 실패하면 IBM API가 바뀌었다는 신호다.
- 커밋 전 필수 검사: `ruff check`, `ruff format --check`, `mypy`, `pytest`.

## 9. 리스크

| 리스크 | 대응 |
|---|---|
| IBM API·SDK가 자주 바뀜 (0.50에서 SamplerV2 폐기 예정 공지) | 프로그램 어댑터 분리, CI 매트릭스로 조기 감지 |
| IBM이 비슷한 도구를 직접 제공 | 장애 주입, 비-파이썬 접근, (다음 버전) 여러 회사 지원으로 차별화 |
| `run_quantum_program` 재사용 불가 | 구현 계획 첫 스파이크에서 확인, 불가하면 자체 실행으로 전환 |
| 사용자가 `connect()` 없이 수동 설정하다 실제 IBM으로 키 유출 | CONNECT 405 경고, README 첫 화면에 명시 |
| qiskit 내부 모듈(json, converters) 의존 | 사용하는 내부 심볼을 한 모듈(`localqpu/_compat.py`)에 모아 변경 영향을 한곳에서 관리 |
