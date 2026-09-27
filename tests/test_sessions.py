"""세션 관리자 테스트."""

import pytest

from localqpu.sessions import (
    SessionManager,
    SessionNotFoundError,
    SessionRejectedError,
)


def test_created_session_is_open_and_accepting() -> None:
    """새 세션은 open 상태이고 작업을 받는다."""
    manager = SessionManager()
    session = manager.create("dedicated", "ibm_brisbane", max_ttl=600)
    assert (session.state, session.accepting_jobs, session.mode, session.max_ttl) == (
        "open",
        True,
        "dedicated",
        600,
    )
    assert manager.get(session.session_id) == session


def test_invalid_mode_is_rejected() -> None:
    """dedicated·batch 외의 모드는 거절한다."""
    with pytest.raises(ValueError, match="dedicated"):
        SessionManager().create("turbo", "ibm_brisbane", max_ttl=None)


def test_first_job_activates_session() -> None:
    """작업이 처음 들어오면 active가 되고 시작 시각이 남는다."""
    manager = SessionManager()
    session = manager.create("batch", "ibm_brisbane", max_ttl=None)
    manager.accept_job(session.session_id, "ibm_brisbane")
    active = manager.get(session.session_id)
    assert active is not None and active.state == "active" and active.activated_at is not None


def test_closed_session_rejects_jobs() -> None:
    """닫힌 세션은 작업을 거절한다."""
    manager = SessionManager()
    session = manager.create("dedicated", "ibm_brisbane", max_ttl=None)
    assert manager.close(session.session_id) is True
    closed = manager.get(session.session_id)
    assert closed is not None and (closed.state, closed.accepting_jobs) == ("closed", False)
    with pytest.raises(SessionRejectedError, match="닫힌"):
        manager.accept_job(session.session_id, "ibm_brisbane")


def test_backend_mismatch_is_rejected() -> None:
    """세션과 다른 백엔드로 제출하면 거절한다."""
    manager = SessionManager()
    session = manager.create("dedicated", "ibm_brisbane", max_ttl=None)
    with pytest.raises(SessionRejectedError, match="ibm_brisbane"):
        manager.accept_job(session.session_id, "ibm_torino")


def test_unknown_session() -> None:
    """모르는 세션은 조회·닫기·제출 모두에서 구분된다."""
    manager = SessionManager()
    assert manager.get("missing") is None and manager.close("missing") is False
    with pytest.raises(SessionNotFoundError, match="missing"):
        manager.accept_job("missing", "ibm_brisbane")


def test_reset_clears_sessions() -> None:
    """reset은 세션을 모두 지운다."""
    manager = SessionManager()
    session = manager.create("dedicated", "ibm_brisbane", max_ttl=None)
    manager.reset()
    assert manager.get(session.session_id) is None
