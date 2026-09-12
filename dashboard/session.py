"""세션 상태와 **시계** — 화면 계층이 바깥 세계에 닿는 자리.

## 🔴 시계가 왜 여기 있는가

도메인(`sector/workspace/`)은 시계를 읽지 않는다 — `at` 을 입력으로 받고, 정적
검사가 `datetime.now` 를 막는다. 그래야 같은 원장이 언제나 같은 상태를 낸다.

그렇다고 시각이 하늘에서 오는 것은 아니다. **누군가는 시계를 읽어야 하고**, 그
자리가 여기다. 어댑터 한 곳으로 모아 두면 "언제 읽었나" 를 한 군데서 볼 수 있고,
테스트는 도메인을 시계 없이 검증할 수 있다.
"""

from __future__ import annotations

from datetime import datetime, timezone

import streamlit as st

__all__ = ["now_utc", "actor", "set_actor", "team_id", "set_team", "leave",
           "is_master", "set_master"]

_ACTOR = "sc_actor"
_TEAM = "sc_team_id"
_MASTER = "sc_master"


def now_utc() -> str:
    """지금을 UTC ISO 로. 🔒 **이 저장소에서 벽시계를 읽는 유일한 자리다.**

    초 미만을 버리는 이유 — 이벤트 id 에 들어가는 값이라 마이크로초까지 넣으면
    같은 동작이 매번 다른 id 를 낳아 멱등이 깨진다.
    """
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def actor() -> str | None:
    return st.session_state.get(_ACTOR)


def set_actor(name: str) -> None:
    st.session_state[_ACTOR] = name.strip()


def team_id() -> str | None:
    return st.session_state.get(_TEAM)


def set_team(value: str) -> None:
    st.session_state[_TEAM] = value


def is_master() -> bool:
    """🔒 **이 브라우저 세션에서만** 열려 있다. 원장에 기록되지 않는다."""
    return bool(st.session_state.get(_MASTER))


def set_master(value: bool) -> None:
    st.session_state[_MASTER] = bool(value)


def leave() -> None:
    """조에서 나간다. 🔒 **원장을 건드리지 않는다** — 이 브라우저의 상태일 뿐이다."""
    st.session_state.pop(_TEAM, None)
