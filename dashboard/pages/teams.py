"""조 — "우리 조는 어디인가".

조 만들기 · passcode 로 참가 · 목록. 🔒 **원장에 쓰는 화면이다.**

## 🔴 passcode 가 지키는 것

앱은 public 이다. passcode 는 **쓰기 권한을 가르는 것**이지 기밀을 지키는 것이
아니다 — 섹터 점수 화면은 어차피 누구나 본다. `sector/workspace/auth.py` 의
머리주석에 위협 모델을 적어 뒀다.
"""

from __future__ import annotations

import streamlit as st

from dashboard import data, session, theme
from sector.workspace import auth, events, fold


def render() -> None:
    theme.header("조", "조를 만들고 참가한다. 확정·코멘트는 참가한 조에만 쓸 수 있다")
    source_label = ""
    try:
        store, source = data.workspace_store()
        source_label = source.label
        if source.is_local:
            st.warning(f"⚠️ {source.label}\n\n`HF_TOKEN_WRITE` 가 없어 로컬 원장을 쓴다.")

        try:
            workspace = fold.fold(store.read_all())
        except Exception as exc:                      # noqa: BLE001 — 화면에 그대로 알린다
            st.error(f"원장을 읽지 못했다: {exc}")
            return

        if workspace.anomalies:
            with st.expander(f"🔴 원장에 어긋난 것 {len(workspace.anomalies)}건"):
                for line in workspace.anomalies:
                    st.markdown(f"- {line}")

        _render_identity()
        _render_master_gate()
        _render_current(store, workspace)
        st.divider()
        left, right = st.columns(2)
        with left:
            _render_join(store, workspace)
        with right:
            _render_create(store, workspace)
        _render_list(workspace)
        if session.is_master():
            _render_archived(store, workspace)
    finally:
        theme.footer(source_label)


def _render_identity() -> None:
    with theme.panel("내 이름"):
        st.markdown(
            "<div class='sc-muted'>원장에 이 이름으로 기록된다. "
            "누가 무엇을 정했는지 3개월 뒤에 알아볼 수 있어야 한다.</div>",
            unsafe_allow_html=True)
        name = st.text_input("이름", value=session.actor() or "", max_chars=40,
                             key="identity_name", label_visibility="collapsed")
        if name.strip() and name.strip() != session.actor():
            session.set_actor(name)
            st.rerun()


def _render_current(store, workspace: fold.Workspace) -> None:
    team_id = session.team_id()
    team = workspace.team(team_id) if team_id else None
    if team is None:
        if team_id:
            # 원장에서 사라졌다 — 조용히 넘기지 않는다
            st.warning(f"참가 중이던 조 `{team_id}` 가 원장에 없다. 다시 참가한다.")
            session.leave()
        return
    with theme.panel("참가 중"):
        st.markdown(f"**{team.name}** (`{team.id}`) · 조원 {len(team.members)}명 — "
                    f"{', '.join(team.members)}")
        if team.is_confirmed:
            st.markdown(f"핵심 섹터 **{team.core_sector}** · {team.confirmed_by} · "
                        f"{team.confirmed_at[:10]}")
        else:
            st.markdown("<div class='sc-muted'>핵심 섹터가 아직 정해지지 않았다.</div>",
                        unsafe_allow_html=True)
        st.markdown(
            "<div class='sc-muted'>나가도 조는 남는다 — 다른 조원이 계속 쓴다. "
            "조 자체를 없애려면 아래 «보관하기» 를 쓴다.</div>", unsafe_allow_html=True)
        if st.button("이 브라우저에서 나가기", key="leave"):
            session.leave()
            st.rerun()
    _render_archive(store, team, session.actor())


def _render_join(store, workspace: fold.Workspace) -> None:
    st.subheader("참가")
    teams_ = workspace.active_teams
    if not teams_:
        st.markdown("<div class='sc-muted'>아직 조가 없다.</div>", unsafe_allow_html=True)
        return
    with st.form("join"):
        team_id = st.selectbox("조", sorted(teams_), key="join_team",
                               format_func=lambda t: f"{teams_[t].name} ({t})")
        passcode = st.text_input("passcode", type="password", key="join_passcode")
        submitted = st.form_submit_button("참가")
    if not submitted:
        return
    actor = _require_actor()
    if actor is None:
        return
    team = workspace.active_teams[team_id]
    # 🔒 틀린 이유를 말하지 않는다 — 구별해 주면 그것이 곧 정보다
    if not auth.verify_passcode(passcode, team.passcode_hash):
        st.error("참가하지 못했다. passcode 를 확인한다.")
        return
    session.set_team(team_id)
    if actor not in team.members:
        _write(store, events.member_joined(
            team_id=team_id, member=actor, actor=actor, at=session.now_utc()))
    st.success(f"{team.name} 에 참가했다.")
    st.rerun()


def _render_create(store, workspace: fold.Workspace) -> None:
    st.subheader("새 조 만들기")
    with st.form("create"):
        team_id = st.text_input("조 id", placeholder="team_a", key="create_team_id",
                                help="소문자·숫자·밑줄 2~40자. 나중에 바꿀 수 없다")
        name = st.text_input("조 이름", placeholder="A조", key="create_name")
        passcode = st.text_input(
            "passcode", type="password", key="create_passcode",
            help=f"{auth.MIN_LENGTH}자 이상. 공개 앱이라 시도 횟수를 막을 수 없으니 길게 둔다")
        st.caption(f"예: `{auth.suggest_passcode()}`")
        submitted = st.form_submit_button("만들기")
    if not submitted:
        return
    actor = _require_actor()
    if actor is None:
        return
    if team_id in workspace.teams:
        st.error(f"`{team_id}` 는 이미 있다. 다른 id 를 쓴다.")
        return
    try:
        hashed = auth.hash_passcode(passcode)
        event = events.team_created(team_id=team_id, name=name, passcode_hash=hashed,
                                    actor=actor, at=session.now_utc())
    except (auth.PasscodeError, events.EventError) as exc:
        st.error(str(exc))
        return
    if _write(store, event):
        session.set_team(team_id)
        st.success(f"{name} 을(를) 만들었다.")
        st.rerun()


def _render_list(workspace: fold.Workspace) -> None:
    st.subheader("전체 조")
    teams_ = workspace.active_teams
    if not teams_:
        st.markdown("<div class='sc-muted'>아직 조가 없다.</div>", unsafe_allow_html=True)
        return
    for team in sorted(teams_.values(), key=lambda t: t.created_at):
        core = f"**{team.core_sector}**" if team.is_confirmed else theme.MISSING
        st.markdown(
            f"- **{team.name}** (`{team.id}`) · 핵심 섹터 {core} · "
            f"조원 {len(team.members)}명 · 만든이 {team.created_by}")


# ── 마스터(개발자) ──────────────────────────────────────────────────────────

def _render_master_gate() -> None:
    """🔴 마스터는 **시크릿이 설정돼 있을 때만** 존재한다.

    `ADMIN_PASSCODE_HASH` 가 없으면 이 칸을 아예 그리지 않는다 — 없는 문을
    보여 주면 "여기 뭔가 있나" 만 남는다.
    """
    if auth.master_hash() is None:
        return
    if session.is_master():
        left, right = st.columns([4, 1])
        left.markdown("<div class='sc-muted'>🔑 <b>마스터</b>로 열려 있다 — "
                      "모든 조를 보관·복구할 수 있다.</div>", unsafe_allow_html=True)
        if right.button("잠그기", key="master_lock"):
            session.set_master(False)
            st.rerun()
        return
    with st.expander("🔑 마스터로 열기 (개발자)"):
        passcode = st.text_input("마스터 passcode", type="password", key="master_pass")
        if st.button("열기", key="master_unlock"):
            if auth.is_master(passcode):
                session.set_master(True)
                st.rerun()
            else:
                st.error("열지 못했다.")     # 🔒 이유를 말하지 않는다


def _can_archive(team: fold.Team, actor: str | None) -> bool:
    """만든 사람이거나 마스터. 🔴 **원장이 아니라 화면이 거는 권한**이다 —
    토큰이 있으면 누구나 이벤트를 append 할 수 있다. 숨기지 않고 적어 둔다."""
    return session.is_master() or (actor is not None and actor == team.created_by)


def _render_archive(store, team: fold.Team, actor: str | None) -> None:
    if not _can_archive(team, actor):
        return
    with st.expander(f"🗃 «{team.name}» 보관하기"):
        st.markdown(
            "<div class='sc-muted'>목록에서 감춘다. <b>지우는 것이 아니다</b> — "
            "무엇을 정했었는지는 원장에 그대로 남고, 마스터가 되돌릴 수 있다.</div>",
            unsafe_allow_html=True)
        reason = st.text_input("왜 보관하나", key=f"archive_reason_{team.id}")
        if st.button("보관", key=f"archive_{team.id}"):
            try:
                event = events.team_archived(team_id=team.id, reason=reason,
                                             actor=actor or "", at=session.now_utc())
            except events.EventError as exc:
                st.error(str(exc))
                return
            if _write(store, event):
                session.leave()
                st.success(f"«{team.name}» 을(를) 보관했다.")
                st.rerun()


def _render_archived(store, workspace: fold.Workspace) -> None:
    st.subheader("보관된 조 (마스터)")
    archived = workspace.archived_teams
    if not archived:
        st.markdown("<div class='sc-muted'>없다.</div>", unsafe_allow_html=True)
        return
    for team in sorted(archived.values(), key=lambda t: t.archived_at or ""):
        st.markdown(
            f"- **{team.name}** (`{team.id}`) · 보관 {team.archived_by} · "
            f"{(team.archived_at or '')[:10]} · 사유 «{team.archived_reason}»")
        if st.button("복구", key=f"restore_{team.id}"):
            event = events.team_restored(
                team_id=team.id, reason="마스터 복구", actor=session.actor() or "master",
                at=session.now_utc())
            if _write(store, event):
                st.rerun()


def _require_actor() -> str | None:
    actor = session.actor()
    if not actor:
        st.error("먼저 이름을 적는다. 원장에 누가 했는지 남아야 한다.")
        return None
    return actor


def _write(store, event) -> bool:
    """원장에 쓴다. 🔴 실패를 삼키지 않는다 — 화면이 '됐다' 고 거짓말하면 안 된다."""
    try:
        store.append([event])
        return True
    except Exception as exc:                          # noqa: BLE001 — 그대로 보여준다
        st.error(f"원장에 쓰지 못했다: {exc}")
        return False
