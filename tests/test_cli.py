"""CLI 테스트. 서버를 계속 띄우는 경로는 Step 5에서 수동으로 확인한다."""

import json
import socket
from pathlib import Path

import pytest

from localqpu.cli import build_parser, config_from_args, main


def test_backends_command_lists_snapshots(capsys: pytest.CaptureFixture[str]) -> None:
    """backends 명령은 사용 가능한 칩 이름을 출력한다."""
    assert main(["backends"]) == 0
    assert "ibm_brisbane" in capsys.readouterr().out


def test_start_options_become_config(tmp_path: Path) -> None:
    """start 옵션이 ServerConfig로 바뀐다."""
    scenario_path = tmp_path / "scenario.json"
    scenario_path.write_text(json.dumps({"seed": 5}), encoding="utf-8")
    args = build_parser().parse_args(
        [
            "start",
            "--port",
            "0",
            "--backends",
            "ibm_brisbane",
            "--max-sim-qubits",
            "12",
            "--scenario",
            str(scenario_path),
            "--verbose",
        ]
    )
    config = config_from_args(args)
    assert (
        config.port,
        config.backends,
        config.max_sim_qubits,
        config.scenario.seed,
        config.is_verbose,
    ) == (0, ("ibm_brisbane",), 12, 5, True)
    assert config.host == "127.0.0.1"


def test_invalid_scenario_file_exits_2(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """잘못된 시나리오 파일은 위치를 알려 주고 종료 코드 2로 끝난다."""
    scenario_path = tmp_path / "scenario.json"
    scenario_path.write_text(json.dumps({"colour": "red"}), encoding="utf-8")
    assert main(["start", "--port", "0", "--scenario", str(scenario_path)]) == 2
    assert "colour" in capsys.readouterr().err


def test_missing_scenario_file_exits_2(capsys: pytest.CaptureFixture[str]) -> None:
    """없는 시나리오 파일은 종료 코드 2다."""
    assert main(["start", "--port", "0", "--scenario", "/nonexistent/scenario.json"]) == 2
    assert "scenario.json" in capsys.readouterr().err


def test_unknown_backend_exits_2(capsys: pytest.CaptureFixture[str]) -> None:
    """없는 칩은 사용 가능한 이름과 함께 종료 코드 2다."""
    assert main(["start", "--port", "0", "--backends", "ibm_atlantis"]) == 2
    assert "ibm_brisbane" in capsys.readouterr().err


def test_port_in_use_exits_2(capsys: pytest.CaptureFixture[str]) -> None:
    """이미 쓰는 포트는 종료 코드 2와 해결 방향을 알려 준다."""
    with socket.socket() as occupied:
        occupied.bind(("127.0.0.1", 0))
        occupied.listen()
        port = occupied.getsockname()[1]
        assert main(["start", "--port", str(port)]) == 2
    assert "--port" in capsys.readouterr().err


@pytest.mark.parametrize(
    "arguments",
    [["--port", "70000"], ["--port", "-1"], ["--max-sim-qubits", "-3"]],
)
def test_out_of_range_options_exit_2_without_traceback(
    arguments: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    """범위를 벗어난 옵션은 traceback 없이 argparse 오류(종료 코드 2)로 끝난다."""
    with pytest.raises(SystemExit) as caught:
        main(["start", *arguments])
    error_output = capsys.readouterr().err
    assert caught.value.code == 2 and "Traceback" not in error_output and "범위" in error_output
