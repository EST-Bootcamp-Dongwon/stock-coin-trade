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
           "credential", "set_credential"]

_ACTOR = "sc_actor"
_TEAM = "sc_team_id"
_CREDENTIAL = "sc_credential"


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


def credential() -> str | None:
    """이 조에 쓸 자격증명 — `scrypt$n$r$p$salt$digest`.

    🔴 **평문 passcode 가 아니다.** 참가할 때 저장된 salt 로 재계산한 값이고
       (`auth.recompute_passcode`), 원장에 쓸 때마다 저장소가 이것을 요구한다
       (ADR-SC-0011 ⑤ — 쓰기 권한이 키가 아니라 passcode 에 걸려 있다).

    🔒 `st.session_state` 는 **서버 쪽**에 있고 브라우저로 내려가지 않는다. 그래서
       평문을 들고 있는 것보다 낫다 — 평문은 다른 조에서도 쓰일 수 있지만 이 값은
       이 조에서만 쓸 수 있다.
    """
    return st.session_state.get(_CREDENTIAL)


def set_credential(value: str) -> None:
    st.session_state[_CREDENTIAL] = value


def leave() -> None:
    """조에서 나간다. 🔒 **원장을 건드리지 않는다** — 이 브라우저의 상태일 뿐이다.

    🔒 자격증명도 함께 버린다. 조만 지우고 남겨 두면 다음 조에 옛 자격증명을
       들고 들어간다.
    """
    st.session_state.pop(_TEAM, None)
    st.session_state.pop(_CREDENTIAL, None)
