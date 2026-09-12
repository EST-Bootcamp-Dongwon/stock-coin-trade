"""섹터 확정 — ★ "우리 조의 핵심 섹터는 이것이다".

## 🔴 사유가 필수인 이유

동아리 가이드라인이 **한 섹터 30% 상한**이라 핵심 섹터 하나로 몰빵할 수 없다.
즉 이 확정은 "무엇을 살까" 가 아니라 **"무엇을 중심에 둘까"** 이고, 그 판단은
3개월 뒤 운용보고서에서 되짚어야 한다. 사유 없는 확정은 그때 아무것도 남기지 않는다.

`events.sector_confirmed` 가 빈 사유를 **거부**해서 이 규칙을 코드로 지킨다.

## 🔒 취소는 지우는 것이 아니다

되돌리는 이벤트를 **더한다.** "한 번 정했다가 바꿨다" 는 사실이 지워지지 않아야
보고서의 '포폴 변경 사유' 를 쓸 수 있다.
"""

from __future__ import annotations

import streamlit as st

from dashboard import data, evidence, explain, session, theme, view
from sector.workspace import events, fold


def render() -> None:
    theme.header("섹터 확정", "조의 핵심 섹터를 사유와 함께 기록한다")
    source_label = ""
    try:
        team_id = session.team_id()
        actor = session.actor()
        if not team_id or not actor:
            st.info("먼저 **조** 페이지에서 이름을 적고 조에 참가한다.")
            return

        store, source = data.workspace_store()
        source_label = source.label
        if source.is_local:
            st.warning(f"⚠️ {source.label}")

        workspace = fold.fold(store.read_all())
        team = workspace.team(team_id)
        if team is None:
            st.error(f"조 `{team_id}` 가 원장에 없다. **조** 페이지에서 다시 참가한다.")
            return

        scores = _scores()
        names = data.sector_names()
        _render_status(team, names)
        st.divider()
        if team.is_confirmed:
            _render_confirmed_evidence(scores, team, names)
            _render_cancel(store, team, actor)
        else:
            _render_confirm(store, team, actor, scores, names)
        _render_history(workspace, team_id, names)
    finally:
        theme.footer(source_label)


def _scores():
    try:
        frame, _ = data.load_scores()
        return frame
    except data.DataUnavailable:
        return None


def _render_status(team: fold.Team, names) -> None:
    with theme.panel(f"{team.name} (`{team.id}`)"):
        if not team.is_confirmed:
            st.markdown("핵심 섹터 **미정**")
            return
        st.markdown(f"핵심 섹터 **{names.sector_full(team.core_sector)}**")
        st.markdown(f"<div class='sc-note'>{team.core_reason}</div>", unsafe_allow_html=True)
        st.markdown(
            f"<div class='sc-muted'>{team.confirmed_by} · {team.confirmed_at}</div>",
            unsafe_allow_html=True)


def _render_confirmed_evidence(frame, team: fold.Team, names) -> None:
    """확정한 뒤에도 근거를 계속 보여준다.

    🔴 확정은 끝이 아니라 **3개월 운용의 시작**이다. 확정 화면이 사유 한 줄만
       남기고 근거를 감추면, 다음 주에 "왜 이걸 골랐더라" 를 다시 랭킹 화면에서
       찾아야 한다.
    """
    if frame is None:
        return
    st.subheader("이 섹터의 근거")
    evidence.render_evidence(frame, team.core_sector, names=names)


def _render_confirm(store, team: fold.Team, actor: str, frame, names) -> None:
    st.subheader("확정하기")
    if frame is None:
        st.error("섹터 점수를 불러올 수 없어 후보를 보여줄 수 없다.")
        return

    table = view.ranking_table(frame, profile="balanced", names=names)
    latest = view.latest_frame(frame).set_index("sector_id")

    def label(sid: str) -> str:
        row = latest.loc[sid]
        badge = "" if bool(row["liquidity_ok"]) else " 🔴유동성"
        return f"{int(row['rank_balanced'])}위 · {names.sector_label(sid)}{badge}"

    sector_id = st.selectbox("핵심 섹터", list(table.index), key="confirm_sector",
                             format_func=label)
    if sector_id:
        # 🔴 여기가 M8 의 빈 자리였다 — 점수 한 줄만 있었다. 사유를 쓰라고 하면서
        #    무엇을 근거로 쓸지는 안 보여주면, 사유 칸이 빈 채로 남거나 "1위라서"
        #    로 채워진다. 랭킹 화면과 **같은 함수**를 부른다
        evidence.render_evidence(frame, sector_id, names=names)
        if not bool(latest.loc[sector_id]["liquidity_ok"]):
            st.markdown(
                "<div class='sc-warn'>🔴 ETF 거래가 적어 실제 매수가 어렵다. "
                "구성종목으로 담을 수 있는지 먼저 확인한다.</div>", unsafe_allow_html=True)

    reason = st.text_area(
        "왜 이 섹터인가", height=140, key="confirm_reason",
        placeholder="어느 축이 좋았고, 무엇이 걸리는지. 3개월 뒤 운용보고서가 이 줄을 되짚는다.",
        help="🔴 비워 둘 수 없다. 사유 없는 확정은 아무것도 남기지 않는다")
    st.markdown(
        "<div class='sc-muted'>확정해도 되돌릴 수 있다. 되돌린 기록도 남는다.</div>",
        unsafe_allow_html=True)

    if st.button("확정", type="primary", key="confirm_submit"):
        try:
            event = events.sector_confirmed(
                team_id=team.id, sector_id=sector_id, reason=reason,
                actor=actor, at=session.now_utc())
        except events.EventError as exc:
            st.error(str(exc))
            return
        if _write(store, event):
            label_ko = names.sector_label(sector_id)
            st.success(f"{label_ko}{explain.josa(label_ko, '을를')} 핵심 섹터로 확정했다.")
            st.rerun()


def _render_cancel(store, team: fold.Team, actor: str) -> None:
    st.subheader("확정 되돌리기")
    reason = st.text_area(
        "왜 바꾸나", height=100, key="cancel_reason",
        placeholder="무엇이 달라졌는지. 이 줄이 '포폴 변경 사유' 가 된다.")
    if st.button("되돌리기", key="cancel_submit"):
        try:
            event = events.sector_unconfirmed(
                team_id=team.id, reason=reason, actor=actor, at=session.now_utc())
        except events.EventError as exc:
            st.error(str(exc))
            return
        if _write(store, event):
            st.success("되돌렸다. 기록은 남는다.")
            st.rerun()


def _render_history(workspace: fold.Workspace, team_id: str, names) -> None:
    history = workspace.history_of(team_id)
    if not history:
        return
    st.subheader("이 조의 기록")
    st.markdown(
        "<div class='sc-muted'>원장은 지워지지 않는다. 운용보고서의 "
        "'포폴 변경 사유' 가 여기서 나온다.</div>", unsafe_allow_html=True)
    labels = {
        "team.created": "조 생성", "member.joined": "참가",
        "sector.confirmed": "확정", "sector.unconfirmed": "확정 취소",
        "comment.posted": "코멘트",
    }
    for event in reversed(history):
        sector_id = event.payload.get("sector_id") or ""
        detail = names.sector_label(sector_id) if sector_id else ""
        reason = event.payload.get("reason") or event.payload.get("member") or ""
        head = f"**{labels.get(event.kind, event.kind)}**"
        if detail:
            head += f" · {detail}"
        st.markdown(f"- {head} — {event.actor} · {event.at[:16].replace('T', ' ')} UTC")
        if reason:
            st.markdown(f"  <div class='sc-note'>  {reason}</div>", unsafe_allow_html=True)


def _write(store, event) -> bool:
    """🔒 자격증명을 함께 보낸다 — 쓰기 권한이 키가 아니라 passcode 에 걸려 있다
    (ADR-SC-0011 ⑤). 참가할 때 세션에 담긴다(`dashboard/pages/teams.py`)."""
    try:
        store.append([event], credential=session.credential())
        return True
    except Exception as exc:                          # noqa: BLE001
        st.error(f"원장에 쓰지 못했다: {exc}")
        return False
