"""명령줄 진입점: localqpu start / localqpu backends."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from collections.abc import Sequence
from pathlib import Path

from localqpu import __version__
from localqpu.app import start_server
from localqpu.constants import DEFAULT_BACKENDS, DEFAULT_HOST, DEFAULT_MAX_SIM_QUBITS, DEFAULT_PORT
from localqpu.context import ServerConfig
from localqpu.ibm.backends import BackendCatalogError, available_backend_names
from localqpu.scenario import Scenario, ScenarioError, parse_scenario
from localqpu.server import RunningServer

#: 모듈 로거.
logger = logging.getLogger(__name__)


def build_parser() -> argparse.ArgumentParser:
    """명령줄 파서."""
    parser = argparse.ArgumentParser(
        prog="localqpu", description="IBM Quantum Platform API 로컬 에뮬레이터"
    )
    parser.add_argument("--version", action="version", version=f"localqpu {__version__}")
    commands = parser.add_subparsers(dest="command", required=True)
    start = commands.add_parser("start", help="에뮬레이터 서버를 실행한다")
    start.add_argument(
        "--host",
        default=DEFAULT_HOST,
        help=f"바인딩 주소(기본 {DEFAULT_HOST}). 외부 노출은 명시할 때만",
    )
    start.add_argument(
        "--port",
        type=_port_number,
        default=DEFAULT_PORT,
        help=f"포트(기본 {DEFAULT_PORT}, 0이면 빈 포트)",
    )
    start.add_argument(
        "--backends", default=",".join(DEFAULT_BACKENDS), help="노출할 칩, 쉼표 구분"
    )
    start.add_argument(
        "--max-sim-qubits",
        type=_non_negative_int,
        default=DEFAULT_MAX_SIM_QUBITS,
        help="정확 시뮬레이션 최대 활성 큐비트",
    )
    start.add_argument("--scenario", type=Path, help="장애 시나리오 JSON 파일")
    start.add_argument("--verbose", action="store_true", help="요청 본문 일부도 로그로 남긴다")
    commands.add_parser("backends", help="사용 가능한 칩 스냅샷 이름을 출력한다")
    return parser


def _port_number(raw: str) -> int:
    """0~65535 범위의 포트 번호. 범위를 벗어나면 bind 단계의 OverflowError traceback 대신 인자 오류로 알린다."""
    value = _integer(raw)
    if not 0 <= value <= 65535:
        raise argparse.ArgumentTypeError(f"포트는 0~65535 범위여야 합니다(0이면 빈 포트): {value}")
    return value


def _non_negative_int(raw: str) -> int:
    """0 이상의 정수."""
    value = _integer(raw)
    if value < 0:
        raise argparse.ArgumentTypeError(f"0 이상 범위의 정수여야 합니다: {value}")
    return value


def _integer(raw: str) -> int:
    """정수 문자열을 읽는다."""
    try:
        return int(raw)
    except ValueError as error:
        raise argparse.ArgumentTypeError(f"정수 범위의 값이어야 합니다: {raw!r}") from error


def config_from_args(args: argparse.Namespace) -> ServerConfig:
    """start 옵션을 ServerConfig로 바꾼다.

    Raises:
        ScenarioError: 시나리오 내용이 잘못됐을 때.
        OSError: 시나리오 파일을 읽지 못했을 때.
    """
    backends = tuple(name.strip() for name in args.backends.split(",") if name.strip())
    return ServerConfig(
        host=args.host,
        port=args.port,
        backends=backends,
        max_sim_qubits=args.max_sim_qubits,
        scenario=_load_scenario(args.scenario),
        is_verbose=args.verbose,
    )


def main(argv: Sequence[str] | None = None) -> int:
    """명령을 실행하고 종료 코드를 돌려준다(0 정상, 2 설정 오류)."""
    args = build_parser().parse_args(argv)
    if args.command == "backends":
        print("\n".join(available_backend_names()))
        return 0
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO, format="%(asctime)s %(message)s"
    )
    server = _start_or_report(args)
    if server is None:
        return 2
    print(_banner(server), flush=True)  # 출력을 파일로 돌리고 SIGTERM으로 끝내도 배너가 남도록
    _serve_until_interrupted(server)
    return 0


def _load_scenario(path: Path | None) -> Scenario:
    """시나리오 파일을 읽는다. 없으면 기본 시나리오."""
    if path is None:
        return Scenario()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise ScenarioError(f"{path}가 올바른 JSON이 아닙니다: {error}") from error
    return parse_scenario(raw)


def _start_or_report(args: argparse.Namespace) -> RunningServer | None:
    """서버를 띄운다. 설정 오류는 원인과 해결 방향을 stderr에 쓰고 None을 돌려준다."""
    try:
        return start_server(config_from_args(args))
    except (ScenarioError, BackendCatalogError) as error:
        print(f"localqpu: {error}", file=sys.stderr)
    except OSError as error:
        print(
            f"localqpu: 서버를 시작하지 못했습니다({error}). 시나리오 파일 경로를 확인하거나, 포트가 사용 중이면 --port로 다른 포트를 지정하세요.",
            file=sys.stderr,
        )
    return None


def _serve_until_interrupted(server: RunningServer) -> None:
    """Ctrl+C까지 기다렸다가 서버를 멈춘다."""
    try:
        server.wait()
    except KeyboardInterrupt:
        print("\nlocalqpu를 종료합니다.")
    finally:
        server.stop()


def _banner(server: RunningServer) -> str:
    """시작 안내문."""
    return (
        f"localqpu {__version__} 실행 중: {server.url}\n"
        f"  Python에서 연결:  import localqpu; service = localqpu.connect(port={server.port})\n"
        "  종료: Ctrl+C"
    )
