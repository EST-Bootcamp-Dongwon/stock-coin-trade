"""조 — "우리 조는 어디인가".

조 만들기 · passcode 로 참가 · 목록. 🔒 **원장에 쓰는 화면이다.**

## 🔴 passcode 가 지키는 것

앱은 public 이다. passcode 는 **쓰기 권한을 가르는 것**이지 기밀을 지키는 것이
아니다 — 섹터 점수 화면은 어차피 누구나 본다. `sector/workspace/auth.py` 의
머리주석에 위협 모델을 적어 뒀다.

## 🔴 검증을 화면이 하지 않는다 (2026-09-12 · ADR-SC-0011 ④)

옛 구조는 `fold` 결과에 실려 온 해시로 **이 화면이** 직접 검증했다. 원장 읽기가
공개가 되면서 해시를 화면까지 들고 오는 것 자체가 노출 경로가 됐다. 지금은 —

1. 저장소에서 `scrypt$n$r$p$salt$` 까지만 받는다 (**digest 는 오지 않는다**)
2. 사람이 적은 passcode 로 같은 해시를 **다시 계산**한다 (`auth.recompute_passcode`)
3. 맞는지는 **저장소가** 답한다 (`store.verify`) — Supabase 에서는 DB 안에서 판정한다

그 결과 원장에 쓸 때마다 자격증명이 필요하고, 그것을 세션이 들고 있는다
(`session.credential`).

## 🔴 마스터(개발자)가 없다 (2026-09-12 · ADR-SC-0011 ⑫ · V42)

옛 화면에는 `ADMIN_PASSCODE_HASH` 로 열리는 마스터가 있었고 **남의 조를 자격증명
없이** 보관·복구했다. 그것이 되던 이유는 원장에 쓰기 관문이 아예 없었기 때문이다.
Supabase 로 옮기면 그 경로가 막힌다 — DB 에 마스터 개념이 없다(⑤).

셋 중 **"보관·복구를 조 안으로 되돌린다"** 를 골랐다. 마스터 RPC 를 더했다면 해시
하나가 **모든 조**에 대한 쓰기 권한이 됐을 것이고, 그것은 `service_role` 키를 앱에
두는 것과 같은 모양이다. 그래서 —

- 보관은 **만든 사람**이 한다 (`_can_archive`)
- 복구는 **그 조의 passcode** 가 연다 (`_render_archived`) — 마스터가 아니다
- 🔒 `ADMIN_PASSCODE_HASH` 는 이제 아무것도 열지 않는다. 시크릿에서 지운다

대가는 숨기지 않고 적는다 — **잘못 만든 조를 개발자가 치울 수 없다.** 대회 기간에
조는 7개뿐이고, 그런 조는 만든 사람이 보관하면 된다.
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
            st.warning(f"⚠️ {source.label}\n\n"
                       "`SUPABASE_URL`·`SUPABASE_ANON_KEY` 도 `HF_TOKEN_WRITE` 도 없어 "
                       "로컬 원장을 쓴다.")

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
        _render_current(store, workspace)
        st.divider()
        left, right = st.columns(2)
        with left:
            _render_join(store, workspace)
        with right:
            _render_create(store, workspace)
        _render_list(workspace)
        # 🔒 보관된 조가 없으면 칸을 그리지 않는다 — 평소에는 있을 일이 아니다
        if workspace.archived_teams:
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
    credential = _credential(store, team_id, passcode)
    if credential is None:
        return                      # 🔒 이유는 `_credential` 이 이미 그렸다
    _enter_team(store, team, actor, credential)
    st.success(f"{team.name} 에 참가했다.")
    st.rerun()


def _enter_team(store, team: fold.Team, actor: str, credential: str) -> None:
    """세션을 이 조에 묶는다. 🔒 **참가와 복구가 같은 꼬리를 쓴다.**

    둘 다 passcode 를 방금 증명한 직후다. 갈라 두면 한쪽에서만 조원 기록이 빠지고,
    3개월 뒤 "누가 참가했었나" 가 조용히 비어 있게 된다.
    """
    session.set_team(team.id)
    session.set_credential(credential)
    if actor not in team.members:
        _write(store, events.member_joined(
            team_id=team.id, member=actor, actor=actor, at=session.now_utc()), credential)


def _credential(store, team_id: str, passcode: str) -> str | None:
    """passcode 를 자격증명으로 바꾼다. 못 하면 `None` 이고 화면에 이유를 적는다.

    🔒 **틀린 이유를 구별해 주지 않는다** — "그 조가 없다" 와 "passcode 가 틀렸다"
       가 같은 문장이다. 구별해 주면 그것이 곧 정보다(`auth.verify_passcode` 와
       같은 규율이고, `workspace_append` 도 DB 에서 같게 답한다).
    """
    try:
        params = store.passcode_params(team_id)
        if params is None:
            st.error("참가하지 못했다. 조와 passcode 를 확인한다.")
            return None
        credential = auth.recompute_passcode(passcode, params)
        if not store.verify(team_id, credential):
            st.error("참가하지 못했다. 조와 passcode 를 확인한다.")
            return None
    except auth.PasscodeError:
        # 🔒 길이·형식 문제도 같은 문장으로 답한다
        st.error("참가하지 못했다. 조와 passcode 를 확인한다.")
        return None
    except Exception as exc:            # noqa: BLE001 — 원장에 닿지 못한 것은 삼키지 않는다
        st.error(f"원장에 닿지 못해 참가를 확인할 수 없다: {exc}")
        return None
    return credential


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
        event = events.team_created(team_id=team_id, name=name,
                                    actor=actor, at=session.now_utc())
    except (auth.PasscodeError, events.EventError) as exc:
        st.error(str(exc))
        return
    # 🔒 이벤트와 해시가 함께 들어간다 — `append` 로는 조를 만들 수 없다.
    #    갈라지면 "passcode 없는 조" 나 "조 없는 passcode" 가 남는다.
    try:
        store.create_team(event, passcode_hash=hashed)
    except Exception as exc:            # noqa: BLE001 — 실패를 삼키지 않는다
        st.error(f"조를 만들지 못했다: {exc}")
        return
    session.set_team(team_id)
    # 🔒 방금 만든 해시가 곧 자격증명이다 — 만든 사람은 바로 쓸 수 있어야 한다.
    #    (DB 에 다시 물어 salt 를 받아올 필요가 없다. 같은 값이다)
    session.set_credential(hashed)
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


# ── 보관과 되돌리기 ─────────────────────────────────────────────────────────

def _can_archive(team: fold.Team, actor: str | None) -> bool:
    """만든 사람만. 🔒 **마스터는 없다** (2026-09-12 · ADR-SC-0011 ⑫ · V42).

    옛 주석은 *"권한은 원장이 아니라 화면이 건다"* 였다 — 원장에 쓰기 관문이 아예
    없었기 때문이다. 이제는 원장이 passcode 로 가른다(⑤). 그래서 이 함수가 거는
    것은 **권한이 아니라 실수 방지**다: 쓸 수 있는지는 `_write` 가 보내는 자격증명이
    정하고, 여기서는 "내가 만든 조" 에만 버튼을 보여 준다.
    """
    return actor is not None and actor == team.created_by


def _render_archive(store, team: fold.Team, actor: str | None) -> None:
    if not _can_archive(team, actor):
        return
    with st.expander(f"🗃 «{team.name}» 보관하기"):
        st.markdown(
            "<div class='sc-muted'>목록에서 감춘다. <b>지우는 것이 아니다</b> — "
            "무엇을 정했었는지는 원장에 그대로 남고, <b>passcode 를 아는 사람이</b> "
            "아래 «보관된 조» 에서 되돌릴 수 있다.</div>",
            unsafe_allow_html=True)
        reason = st.text_input("왜 보관하나", key=f"archive_reason_{team.id}")
        if st.button("보관", key=f"archive_{team.id}"):
            try:
                event = events.team_archived(team_id=team.id, reason=reason,
                                             actor=actor or "", at=session.now_utc())
            except events.EventError as exc:
                st.error(str(exc))
                return
            if _write(store, event, session.credential()):
                session.leave()
                st.success(f"«{team.name}» 을(를) 보관했다.")
                st.rerun()


def _render_archived(store, workspace: fold.Workspace) -> None:
    """보관된 조와 **되돌리기**. 🔴 마스터가 아니라 **그 조의 passcode** 가 연다.

    🔒 목록은 누구에게나 보인다. 원장 읽기는 이미 공개고(ADR-SC-0011 ⑥) 감추면
       자기 조가 어디로 갔는지 아무도 못 찾는다.
    🔒 보관은 만든 사람만(`_can_archive`)이지만 되돌리기는 passcode 만 묻는다 —
       **되살리는 쪽을 더 쉽게 둔다.** 되돌린 조는 원장에 그대로 남아 있던 것이고,
       잘못 되돌려도 다시 보관하면 된다.
    """
    st.subheader("보관된 조")
    st.markdown(
        "<div class='sc-muted'>목록에서 감춰졌을 뿐 기록은 그대로다. "
        "그 조의 passcode 를 알면 되돌릴 수 있다.</div>", unsafe_allow_html=True)
    for team in sorted(workspace.archived_teams.values(), key=lambda t: t.archived_at or ""):
        st.markdown(
            f"- **{team.name}** (`{team.id}`) · 보관 {team.archived_by} · "
            f"{(team.archived_at or '')[:10]} · 사유 «{team.archived_reason}»")
        with st.form(f"restore_{team.id}"):
            passcode = st.text_input("passcode", type="password",
                                     key=f"restore_passcode_{team.id}")
            submitted = st.form_submit_button("되돌리기")
        if not submitted:
            continue
        actor = _require_actor()
        if actor is None:
            continue
        credential = _credential(store, team.id, passcode)
        if credential is None:
            continue                    # 🔒 이유는 `_credential` 이 이미 그렸다
        try:
            event = events.team_restored(team_id=team.id, reason="passcode 로 되돌렸다",
                                         actor=actor, at=session.now_utc())
        except events.EventError as exc:
            st.error(str(exc))
            continue
        if _write(store, event, credential):
            # 🔒 방금 passcode 를 증명했다 — 참가와 같은 꼬리를 쓴다
            _enter_team(store, team, actor, credential)
            st.success(f"«{team.name}» 을(를) 되돌렸다.")
            st.rerun()


def _require_actor() -> str | None:
    actor = session.actor()
    if not actor:
        st.error("먼저 이름을 적는다. 원장에 누가 했는지 남아야 한다.")
        return None
    return actor


def _write(store, event, credential: str | None) -> bool:
    """원장에 쓴다. 🔴 실패를 삼키지 않는다 — 화면이 '됐다' 고 거짓말하면 안 된다.

    🔒 **자격증명을 인자로 받는다.** 예전에는 `session.credential()` 을 안에서
       읽었는데, 그러면 지금 참가 중인 조의 것밖에 못 쓴다 — 보관된 조를 되돌릴
       때는 *그 조의* 자격증명이 필요하다(`_render_archived`). 어느 조에 쓰는지는
       부르는 쪽이 안다.

    🔒 쓰기 권한은 키가 아니라 passcode 에 걸려 있다 (ADR-SC-0011 ⑤). `None` 이면
       어떻게 될지는 저장소가 정한다: 로컬·HF 는 쓰고(거기서는 실제 경계가
       파일·토큰이다) Supabase 는 거부하면서 무엇을 해야 하는지 말한다.
    """
    try:
        store.append([event], credential=credential)
        return True
    except Exception as exc:                          # noqa: BLE001 — 그대로 보여준다
        st.error(f"원장에 쓰지 못했다: {exc}")
        return False
