"""칩 스냅샷 기반 노이즈 모델(v0.2 설계서 5절)."""

from __future__ import annotations

import warnings
from functools import lru_cache
from typing import Any

from qiskit_aer.noise import NoiseModel
from qiskit_ibm_runtime.fake_provider import FakeProviderForBackendV2

#: 정확 계산에 쓰는 Aer 옵션. MPS는 절단 설정이 없으면 정확하고, 넓지만 덜 얽힌 회로에 빠르다.
EXACT_AER_BACKEND_OPTIONS: dict[str, str] = {"method": "matrix_product_state"}


@lru_cache(maxsize=8)
def fake_backend_for(backend_name: str) -> Any:
    """같은 칩 스냅샷의 fake 백엔드(ibm_brisbane → fake_brisbane).

    FakeProviderForBackendV2는 모든 스냅샷을 불러오며, 일부(예: fake_nighthawk)는 "대표 오류값이
    아니다"라는 UserWarning을 낸다. 요청한 칩과 무관한 경고라 그 메시지만 좁게 걸러낸다.
    """
    with warnings.catch_warnings():
        warnings.filterwarnings(
            "ignore", message=".*not intended to represent typical", category=UserWarning
        )
        provider = FakeProviderForBackendV2()
    return provider.backend("fake_" + backend_name.removeprefix("ibm_"))


@lru_cache(maxsize=8)
def noise_model_for(backend_name: str) -> NoiseModel:
    """같은 칩 스냅샷의 보정값으로 노이즈 모델을 만든다. 0.5초 남짓 걸려 백엔드별로 캐시한다."""
    return NoiseModel.from_backend(fake_backend_for(backend_name))


def aer_backend_options(noise_backend: str | None) -> dict[str, Any]:
    """Aer 실행 옵션. 노이즈 백엔드가 있으면 그 칩의 노이즈 모델을 더한다."""
    options: dict[str, Any] = dict(EXACT_AER_BACKEND_OPTIONS)
    if noise_backend is not None:
        options["noise_model"] = noise_model_for(noise_backend)
    return options
