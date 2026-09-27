"""시각 표현 도우미."""

from __future__ import annotations

import datetime


def utc_now_iso() -> str:
    """IBM 응답과 같은 UTC ISO-8601 문자열(마이크로초, 끝에 Z)."""
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
