"""칩 스냅샷 카탈로그.

qiskit-ibm-runtime는 실제 IBM 칩의 설정(conf_*.json)과 보정값(props_*.json)을 fake_provider 안에
함께 배포한다. localqpu는 이 파일을 그대로 백엔드 응답으로 써서 클라이언트의 트랜스파일 결과가
실제와 같게 만든다.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from localqpu._compat import FAKE_PROVIDER_BACKENDS_DIR


class BackendCatalogError(ValueError):
    """없는 칩을 요청했거나 백엔드가 비었을 때. 메시지에 사용 가능한 이름을 담는다."""


@dataclass(frozen=True)
class BackendSnapshot:
    """칩 하나의 스냅샷.

    Attributes:
        name: 백엔드 이름(예: ibm_brisbane). configuration의 backend_name과 같게 맞춘다.
        configuration: GET /backends/{name}/configuration 응답.
        properties: GET /backends/{name}/properties 응답. 파일이 없으면 None.
    """

    name: str
    configuration: dict[str, Any]
    properties: dict[str, Any] | None

    @property
    def num_qubits(self) -> int:
        """칩의 큐비트 수."""
        return int(self.configuration["n_qubits"])


def available_backend_names() -> list[str]:
    """설정 파일이 있는 스냅샷 이름(ibm_ 접두사)을 정렬해 돌려준다."""
    return sorted(
        f"ibm_{directory.name}"
        for directory in FAKE_PROVIDER_BACKENDS_DIR.iterdir()
        if (directory / f"conf_{directory.name}.json").is_file()
    )


def load_snapshot(name: str) -> BackendSnapshot:
    """이름으로 스냅샷을 읽는다.

    Raises:
        BackendCatalogError: 스냅샷이 없을 때.
    """
    short_name = name.removeprefix("ibm_")
    directory = FAKE_PROVIDER_BACKENDS_DIR / short_name
    configuration_path = directory / f"conf_{short_name}.json"
    if not configuration_path.is_file():
        raise BackendCatalogError(
            f"'{name}' 칩 스냅샷을 찾지 못했습니다. 사용 가능: {', '.join(available_backend_names())}"
        )
    configuration = {**_read_json(configuration_path), "backend_name": name}
    properties_path = directory / f"props_{short_name}.json"
    properties = _read_json(properties_path) if properties_path.is_file() else None
    return BackendSnapshot(name=name, configuration=configuration, properties=properties)


def _read_json(path: Path) -> dict[str, Any]:
    """UTF-8 JSON 파일을 읽는다."""
    return dict(json.loads(path.read_text(encoding="utf-8")))


class BackendCatalog:
    """서버가 노출하는 칩 스냅샷 모음. 시작할 때 한 번 읽는다."""

    def __init__(self, names: Iterable[str]) -> None:
        """이름마다 스냅샷을 읽는다.

        Raises:
            BackendCatalogError: 이름이 비었거나 없는 칩이 있을 때.
        """
        self._snapshots = {name: load_snapshot(name) for name in names}
        if not self._snapshots:
            raise BackendCatalogError("백엔드를 하나 이상 지정해야 합니다(예: ibm_brisbane).")

    @property
    def names(self) -> list[str]:
        """등록 순서대로의 백엔드 이름."""
        return list(self._snapshots)

    def get(self, name: str | None) -> BackendSnapshot | None:
        """이름으로 찾는다. 없으면 None."""
        return None if name is None else self._snapshots.get(name)

    def snapshots(self) -> list[BackendSnapshot]:
        """등록 순서대로의 스냅샷."""
        return list(self._snapshots.values())
