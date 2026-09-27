"""패키지 메타데이터 테스트."""

from importlib.metadata import version

import localqpu


def test_version_matches_installed_metadata() -> None:
    """코드의 __version__이 설치된 배포판 버전과 같아야 한다."""
    assert localqpu.__version__ == version("localqpu")
