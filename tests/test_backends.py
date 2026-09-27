"""칩 스냅샷 카탈로그 테스트."""

import pytest

from localqpu.ibm.backends import (
    BackendCatalog,
    BackendCatalogError,
    available_backend_names,
    load_snapshot,
)


def test_brisbane_is_available() -> None:
    """qiskit-ibm-runtime에 포함된 brisbane 스냅샷이 목록에 있다."""
    assert "ibm_brisbane" in available_backend_names()


def test_load_brisbane_snapshot() -> None:
    """brisbane은 127큐비트이고 보정값 파일이 있다."""
    snapshot = load_snapshot("ibm_brisbane")
    assert snapshot.num_qubits == 127
    assert snapshot.configuration["backend_name"] == "ibm_brisbane"
    assert snapshot.properties is not None


def test_unknown_backend_lists_alternatives() -> None:
    """없는 칩은 사용 가능한 이름을 알려 주며 거절한다."""
    with pytest.raises(BackendCatalogError, match="ibm_brisbane"):
        load_snapshot("ibm_atlantis")


def test_catalog_lookup() -> None:
    """카탈로그는 등록한 이름만 찾는다."""
    catalog = BackendCatalog(["ibm_brisbane"])
    assert catalog.names == ["ibm_brisbane"]
    assert catalog.get("ibm_brisbane") is not None
    assert catalog.get("ibm_torino") is None
    assert catalog.get(None) is None


def test_empty_catalog_is_rejected() -> None:
    """백엔드가 하나도 없으면 거절한다."""
    with pytest.raises(BackendCatalogError):
        BackendCatalog([])
