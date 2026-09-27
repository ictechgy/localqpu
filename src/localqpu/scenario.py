"""장애 시나리오.

테스트가 "다음 작업은 실패", "대기열 3초", "백엔드 오프라인" 같은 상황을 선언하면
작업 관리자와 IBM 라우트가 이를 읽어 동작을 바꾼다. JSON 형식은 설계서 5.7절을 따른다.
"""

from __future__ import annotations

import dataclasses
import math
import random
import threading
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Literal, get_args

#: 작업 하나가 맞을 수 있는 결말.
JobOutcomeKind = Literal["completed", "failed", "cancelled"]

#: 백엔드가 가질 수 있는 상태.
BackendStatusKind = Literal["online", "offline", "paused"]

#: 시나리오 JSON 최상위에서 허용하는 키.
_TOP_LEVEL_KEYS: frozenset[str] = frozenset(
    {"seed", "queue", "failures", "next_jobs", "backends", "usage", "auth"}
)


class ScenarioError(ValueError):
    """시나리오가 형식에 맞지 않을 때 발생한다.

    메시지에 문제 위치와 허용 값을 담아 사용자가 JSON을 바로 고칠 수 있게 한다.
    """


@dataclass(frozen=True)
class QueueSettings:
    """작업이 대기열을 벗어나는 조건. 두 조건을 모두 만족해야 한다.

    Attributes:
        delay_seconds: 제출 후 최소 대기 시간(초).
        polls_before_running: 처음 몇 번의 상태 조회 동안 Queued를 보여 줄지.
            실제 IBM처럼 첫 조회에서 Queued를 한 번 보이도록 기본값이 1이다.
    """

    delay_seconds: float = 0.0
    polls_before_running: int = 1

    def __post_init__(self) -> None:
        """타입을 확인하고 음수 대기값을 거부한다."""
        _require_number(self.delay_seconds, "queue.delay_seconds")
        _require_int(self.polls_before_running, "queue.polls_before_running")
        if self.delay_seconds < 0 or self.polls_before_running < 0:
            raise ScenarioError(
                "queue.delay_seconds와 queue.polls_before_running은 0 이상이어야 합니다."
            )


@dataclass(frozen=True)
class FailureSettings:
    """next_jobs가 비었을 때 무작위 실패를 만드는 설정."""

    rate: float = 0.0
    reason: str = "Simulated failure"
    reason_code: int = 9999

    def __post_init__(self) -> None:
        """타입을 확인하고 실패 확률이 0~1 사이인지 확인한다."""
        _require_number(self.rate, "failures.rate")
        _require_str(self.reason, "failures.reason")
        _require_int(self.reason_code, "failures.reason_code")
        if not 0.0 <= self.rate <= 1.0:
            raise ScenarioError(f"failures.rate는 0~1 사이여야 합니다(받은 값: {self.rate}).")


@dataclass(frozen=True)
class JobOutcome:
    """작업 하나의 예정된 결말. 제출 시점에 정해지고 대기열을 벗어날 때 적용된다."""

    outcome: JobOutcomeKind = "completed"
    reason: str | None = None
    reason_code: int | None = None

    def __post_init__(self) -> None:
        """outcome이 허용 값인지, reason·reason_code 타입이 맞는지 확인한다."""
        _require_optional_str(self.reason, "next_jobs[].reason")
        _require_optional_int(self.reason_code, "next_jobs[].reason_code")
        if self.outcome not in get_args(JobOutcomeKind):
            raise ScenarioError(
                f"next_jobs[].outcome '{self.outcome}'는 허용되지 않습니다(허용: {', '.join(get_args(JobOutcomeKind))})."
            )


@dataclass(frozen=True)
class BackendOverride:
    """백엔드 하나의 상태와 대기열 길이."""

    status: BackendStatusKind = "online"
    queue_length: int = 0

    def __post_init__(self) -> None:
        """상태 값과 대기열 길이를 확인한다."""
        _require_int(self.queue_length, "backends[].queue_length")
        if self.status not in get_args(BackendStatusKind) or self.queue_length < 0:
            raise ScenarioError(
                f"backends 항목은 status∈{get_args(BackendStatusKind)}, queue_length≥0 이어야 합니다."
            )


@dataclass(frozen=True)
class UsageSettings:
    """인스턴스 사용량. consumed가 limit 이상이면 새 작업 제출이 거절된다."""

    limit_seconds: int = 600
    consumed_seconds: int = 0

    def __post_init__(self) -> None:
        """타입을 확인하고 음수 사용량을 거부한다."""
        _require_int(self.limit_seconds, "usage.limit_seconds")
        _require_int(self.consumed_seconds, "usage.consumed_seconds")
        if self.limit_seconds < 0 or self.consumed_seconds < 0:
            raise ScenarioError(
                "usage.limit_seconds와 usage.consumed_seconds는 0 이상이어야 합니다."
            )

    @property
    def is_exhausted(self) -> bool:
        """사용량 한도에 도달했는지."""
        return self.consumed_seconds >= self.limit_seconds


@dataclass(frozen=True)
class Scenario:
    """서버 전체의 장애 시나리오. 기본값은 "모든 작업 즉시 성공"이다."""

    seed: int | None = None
    queue: QueueSettings = field(default_factory=QueueSettings)
    failures: FailureSettings = field(default_factory=FailureSettings)
    next_jobs: tuple[JobOutcome, ...] = ()
    backends: Mapping[str, BackendOverride] = field(default_factory=dict)
    usage: UsageSettings = field(default_factory=UsageSettings)
    reject_tokens: bool = False

    def backend_override(self, name: str) -> BackendOverride:
        """백엔드의 상태 설정을 돌려준다. 지정하지 않았으면 온라인이다."""
        return self.backends.get(name, BackendOverride())


def parse_scenario(raw: object) -> Scenario:
    """시나리오 JSON(파이썬 객체)을 검증해 Scenario로 바꾼다.

    Raises:
        ScenarioError: 형식이 틀렸을 때. 메시지에 위치가 들어 있다.
    """
    mapping = _require_mapping(raw, "scenario")
    _reject_unknown_keys(mapping, _TOP_LEVEL_KEYS, "scenario")
    return Scenario(
        seed=_optional_int(mapping.get("seed"), "seed"),
        queue=_build(QueueSettings, mapping.get("queue"), "queue"),
        failures=_build(FailureSettings, mapping.get("failures"), "failures"),
        next_jobs=_parse_next_jobs(mapping.get("next_jobs", [])),
        backends=_parse_backends(mapping.get("backends", {})),
        usage=_build(UsageSettings, mapping.get("usage"), "usage"),
        reject_tokens=_parse_reject_tokens(mapping.get("auth", {})),
    )


def ensure_known_backends(scenario: Scenario, known_names: list[str]) -> None:
    """시나리오가 서버에 없는 백엔드를 가리키면 거절한다.

    이름 오타(예: ibm_brisbne)를 받아들이면 오프라인 설정이 아무 효과 없이 무시되어
    원인을 찾기 어려우므로, 모르는 키를 거절하는 파서 원칙과 같이 다룬다.

    Raises:
        ScenarioError: 모르는 백엔드 이름이 있을 때. 메시지에 사용 가능한 이름을 담는다.
    """
    unknown = sorted(set(scenario.backends) - set(known_names))
    if unknown:
        raise ScenarioError(
            f"backends에 서버에 없는 백엔드가 있습니다: {', '.join(unknown)} (사용 가능: {', '.join(known_names)})"
        )


def scenario_to_json(scenario: Scenario) -> dict[str, Any]:
    """시나리오를 parse_scenario가 다시 읽을 수 있는 JSON 객체로 바꾼다."""
    raw = dataclasses.asdict(scenario)
    raw["next_jobs"] = list(raw["next_jobs"])
    raw["auth"] = {"reject_tokens": raw.pop("reject_tokens")}
    return raw


def _parse_next_jobs(raw: object) -> tuple[JobOutcome, ...]:
    """next_jobs 배열을 JobOutcome 튜플로 바꾼다."""
    if not isinstance(raw, list):
        raise ScenarioError("next_jobs는 배열이어야 합니다.")
    return tuple(_build(JobOutcome, item, f"next_jobs[{index}]") for index, item in enumerate(raw))


def _parse_backends(raw: object) -> dict[str, BackendOverride]:
    """backends 객체를 이름별 BackendOverride로 바꾼다."""
    mapping = _require_mapping(raw, "backends")
    return {
        name: _build(BackendOverride, value, f"backends.{name}") for name, value in mapping.items()
    }


def _parse_reject_tokens(raw: object) -> bool:
    """auth.reject_tokens를 읽는다."""
    mapping = _require_mapping(raw, "auth")
    _reject_unknown_keys(mapping, frozenset({"reject_tokens"}), "auth")
    value = mapping.get("reject_tokens", False)
    if not isinstance(value, bool):
        raise ScenarioError("auth.reject_tokens는 true 또는 false여야 합니다.")
    return value


def _build(settings_type: Any, raw: object, location: str) -> Any:
    """JSON 객체 하나를 설정 데이터클래스로 바꾼다. 값이 없으면 기본값을 쓴다."""
    if raw is None:
        return settings_type()
    mapping = _require_mapping(raw, location)
    allowed = frozenset(item.name for item in dataclasses.fields(settings_type))
    _reject_unknown_keys(mapping, allowed, location)
    try:
        return settings_type(**mapping)
    except ScenarioError as error:
        # __post_init__은 목록 인덱스·백엔드 이름을 모르므로 여기서 정확한 위치를 붙인다.
        raise ScenarioError(f"{location}: {error}") from error
    except TypeError as error:
        # 숫자 자리에 문자열이 오면 __post_init__의 비교에서 TypeError가 난다.
        raise ScenarioError(
            f"{location}({', '.join(mapping)})의 값 형식이 잘못됐습니다: {error}"
        ) from error


def _require_mapping(raw: object, location: str) -> Mapping[str, Any]:
    """값이 JSON 객체인지 확인한다."""
    if not isinstance(raw, Mapping):
        raise ScenarioError(f"{location}은(는) JSON 객체여야 합니다.")
    return raw


def _reject_unknown_keys(
    mapping: Mapping[str, Any], allowed: frozenset[str], location: str
) -> None:
    """허용하지 않은 키가 있으면 거절한다. 오타를 조용히 무시하지 않기 위함이다."""
    unknown = sorted(set(mapping) - allowed)
    if unknown:
        raise ScenarioError(
            f"{location}에 알 수 없는 키가 있습니다: {', '.join(unknown)} (허용: {', '.join(sorted(allowed))})"
        )


def _optional_int(value: object, location: str) -> int | None:
    """정수 또는 null을 읽는다."""
    if value is None:
        return None
    _require_int(value, location)
    assert isinstance(value, int)  # _require_int가 보장한다. mypy에 타입을 좁혀 주기 위함
    return value


def _require_int(value: object, location: str) -> None:
    """정수인지 확인한다. 불리언은 정수로 받지 않는다(JSON true가 1로 새지 않게)."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise ScenarioError(f"{location}은(는) 정수여야 합니다(받은 값: {value!r}).")


def _require_optional_int(value: object, location: str) -> None:
    """정수 또는 null인지 확인한다."""
    if value is not None:
        _require_int(value, location)


def _require_number(value: object, location: str) -> None:
    """유한한 숫자인지 확인한다. 불리언, NaN·무한대, float로 바꿀 수 없는 거대한 정수는 거절한다."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ScenarioError(f"{location}은(는) 유한한 숫자여야 합니다(받은 값: {value!r}).")
    try:
        is_finite = math.isfinite(value)
    except OverflowError as error:
        # 10**400 같은 정수는 float 변환에서 OverflowError가 난다.
        raise ScenarioError(
            f"{location}은(는) 유한한 숫자여야 합니다(받은 값이 너무 큽니다)."
        ) from error
    if not is_finite:
        raise ScenarioError(f"{location}은(는) 유한한 숫자여야 합니다(받은 값: {value!r}).")


def _require_str(value: object, location: str) -> None:
    """문자열인지 확인한다."""
    if not isinstance(value, str):
        raise ScenarioError(f"{location}은(는) 문자열이어야 합니다(받은 값: {value!r}).")


def _require_optional_str(value: object, location: str) -> None:
    """문자열 또는 null인지 확인한다."""
    if value is not None:
        _require_str(value, location)


class ScenarioState:
    """현재 시나리오와 난수 생성기를 스레드 안전하게 보관한다.

    next_jobs는 여기서 소비된다. 시드가 같으면 소비 순서와 파생 시드가 같아
    테스트를 재현할 수 있다.
    """

    def __init__(self, scenario: Scenario | None = None) -> None:
        """처음 시나리오를 기억해 두고 설치한다. reset은 이 시나리오로 돌아간다."""
        self._lock = threading.Lock()
        self._initial = scenario or Scenario()
        self._install(self._initial)

    def current(self) -> Scenario:
        """아직 소비하지 않은 next_jobs를 반영한 현재 시나리오."""
        with self._lock:
            return dataclasses.replace(self._scenario, next_jobs=tuple(self._pending))

    def set_scenario(self, scenario: Scenario) -> None:
        """시나리오를 교체하고 난수 생성기를 새 시드로 초기화한다."""
        with self._lock:
            self._install(scenario)

    def reset(self) -> None:
        """처음 받은 시나리오로 되돌린다."""
        with self._lock:
            self._install(self._initial)

    def next_outcome(self) -> JobOutcome:
        """다음 작업의 결말을 정한다. next_jobs가 우선이고, 없으면 실패 확률을 적용한다."""
        with self._lock:
            if self._pending:
                return self._pending.pop(0)
            failures = self._scenario.failures
            if self._random.random() < failures.rate:
                return JobOutcome("failed", failures.reason, failures.reason_code)
            return JobOutcome()

    def derive_seed(self) -> int | None:
        """작업 하나에 쓸 시뮬레이션 시드. 시나리오에 seed가 없으면 None이다."""
        with self._lock:
            if self._scenario.seed is None:
                return None
            return self._random.randrange(2**31)

    def _install(self, scenario: Scenario) -> None:
        """잠금을 잡은 상태에서 시나리오와 난수 상태를 바꾼다."""
        self._scenario = scenario
        self._pending = list(scenario.next_jobs)
        self._random = random.Random(scenario.seed)
