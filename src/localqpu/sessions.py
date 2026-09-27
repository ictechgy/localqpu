"""세션·배치(Session/Batch) 관리(v0.2 설계서 3절).

세션은 메모리에만 보관한다. 만료(max_ttl·interactive_ttl) 흉내는 v0.2 범위 밖이다.
"""

from __future__ import annotations

import dataclasses
import threading
import uuid
from dataclasses import dataclass
from typing import Literal, get_args

from localqpu.clock import utc_now_iso

#: 세션 모드. Session은 dedicated, Batch는 batch로 만든다.
SessionMode = Literal["dedicated", "batch"]

#: 세션 상태. 클라이언트의 Session.status()가 이 값을 사람이 읽는 상태로 바꾼다.
SessionState = Literal["open", "active", "closed"]


class SessionNotFoundError(LookupError):
    """모르는 세션 ID. 메시지에 서버 재시작 가능성을 안내한다."""


class SessionRejectedError(RuntimeError):
    """세션이 작업을 받을 수 없음(닫힘, 다른 백엔드). 제출은 409로 거절된다."""


@dataclass
class SessionRecord:
    """세션 하나의 상태."""

    session_id: str
    mode: SessionMode
    backend_name: str
    max_ttl: int | None
    started_at: str
    state: SessionState = "open"
    accepting_jobs: bool = True
    activated_at: str | None = None
    closed_at: str | None = None
    last_job_started: str | None = None


class SessionManager:
    """세션을 만들고 상태를 관리한다. 모든 공개 메서드는 스레드 안전하다."""

    def __init__(self) -> None:
        """빈 세션 목록으로 시작한다."""
        self._lock = threading.Lock()
        self._sessions: dict[str, SessionRecord] = {}

    def create(self, mode: str, backend_name: str, max_ttl: int | None) -> SessionRecord:
        """세션을 만든다.

        Raises:
            ValueError: 모드가 dedicated·batch가 아닐 때.
        """
        if mode not in get_args(SessionMode):
            raise ValueError(f"세션 모드 '{mode}'는 지원하지 않습니다(지원: dedicated, batch).")
        session = SessionRecord(
            session_id=f"localqpu-session-{uuid.uuid4().hex[:12]}",
            mode=mode,  # type: ignore[arg-type]
            backend_name=backend_name,
            max_ttl=max_ttl,
            started_at=utc_now_iso(),
        )
        with self._lock:
            self._sessions[session.session_id] = session
            return dataclasses.replace(session)

    def get(self, session_id: str) -> SessionRecord | None:
        """세션 사본. 없으면 None."""
        with self._lock:
            session = self._sessions.get(session_id)
            return None if session is None else dataclasses.replace(session)

    def accept_job(self, session_id: str, backend_name: str) -> None:
        """세션이 이 작업을 받을 수 있는지 확인하고, 받으면 active로 바꾼다.

        Raises:
            SessionNotFoundError: 모르는 세션일 때.
            SessionRejectedError: 닫혔거나 다른 백엔드일 때.
        """
        with self._lock:
            session = self._require(session_id)
            _ensure_accepting(session, backend_name)
            now = utc_now_iso()
            session.state, session.last_job_started = "active", now
            session.activated_at = session.activated_at or now

    def close(self, session_id: str) -> bool:
        """세션을 닫는다(더 이상 작업을 받지 않음). 모르는 세션이면 False."""
        with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                return False
            session.state, session.accepting_jobs = "closed", False
            session.closed_at = session.closed_at or utc_now_iso()
            return True

    def reset(self) -> None:
        """세션을 모두 지운다."""
        with self._lock:
            self._sessions.clear()

    def _require(self, session_id: str) -> SessionRecord:
        """잠금을 잡은 상태에서 세션을 찾는다."""
        session = self._sessions.get(session_id)
        if session is None:
            raise SessionNotFoundError(
                f"localqpu에 '{session_id}' 세션이 없습니다. 세션은 메모리에만 보관하므로 서버를 재시작하면 사라집니다."
            )
        return session


def _ensure_accepting(session: SessionRecord, backend_name: str) -> None:
    """세션이 작업을 받을 수 있는 상태인지 확인한다."""
    if not session.accepting_jobs:
        raise SessionRejectedError(
            f"세션 '{session.session_id}'는 닫힌 세션이라 작업을 받지 않습니다."
        )
    if backend_name != session.backend_name:
        raise SessionRejectedError(
            f"세션 '{session.session_id}'는 {session.backend_name} 전용입니다(제출한 백엔드: {backend_name})."
        )
