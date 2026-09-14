"""조가 원장에 **쓰는** 조각 — 확정 · 되돌리기 · 근거 붙이기. 셋 다 모달이다.

## 🔴 왜 '섹터 확정' 페이지를 없앴나 (2026-09-14 · ADR-SC-0012 ②)

확정은 "어느 섹터인가" 를 읽은 **바로 그 자리**에서 일어난다. 따로 떨어진 페이지는
랭킹에서 고른 섹터를 한 번 더 고르게 했고, 근거를 두 화면에 나눠 그렸다. 이제 랭킹의
"왜 이 점수인가" 에서 섹터를 고르고 거기서 모달을 연다. 조의 현재 상태 · 기록 · 되돌리기는
**조** 페이지로 갔다 — "우리 조는 어디인가" 가 그 페이지의 질문이다.

## 🔒 모달은 플래그로 연다 — 버튼으로 바로 열지 않는다

`if st.button(…): dialog()` 로 열면, 모달 안에서 무엇을 누르는 순간 스크립트가 다시 돌며
버튼이 `False` 가 되어 모달 호출 자체가 사라진다. 브라우저에서는 모달이 fragment 로만 다시
돌아 가려지지만 **AppTest 는 fragment 를 흉내 내지 않아** 입력하자마자 모달이 없어진다
(2026-09-14 프로브 — 제출 버튼 `KeyError`). 그래서 열림 상태를 `session_state` 에 들고,
닫기(X · 바깥 클릭)는 `on_dismiss` 콜백이 내린다. 브라우저와 AppTest 가 같은 길을 간다.

## 🔒 사유가 필수이고, 취소는 지우는 것이 아니다

동아리 가이드라인이 **한 섹터 30% 상한**이라 확정은 "무엇을 살까" 가 아니라 **"무엇을
중심에 둘까"** 이고, 그 판단은 3개월 뒤 운용보고서에서 되짚는다. 사유 없는 확정은 그때
아무것도 남기지 않는다(`events.sector_confirmed` 가 빈 사유를 거부한다). 되돌리기는
되돌리는 이벤트를 **더한다** — 보고서의 '포폴 변경 사유' 가 거기서 나온다.

## 🔴 사람이 쓴 글은 HTML 블록으로만 그린다

조 이름 · 참가자 이름 · 사유 · 근거 · 링크 · 원장에서 온 섹터 id · 저장소 오류 문장은 전부
`theme.esc`+`html_line` · `user_block` · `links_block` · `failure` 를 지난다. 마크다운에는
넣지 않는다 — 역슬래시 이스케이프로는 GFM 자동 링크를 못 막는다(`theme` 의 "사람이 쓴 글" 절).
모달 제목 · 버튼 라벨 · `st.success` 에는 코드가 쓴 글과 `sectors.yaml` 이름만 들어간다.
"""

from __future__ import annotations

import streamlit as st

from dashboard import data, evidence, explain, session, theme, view
from sector.workspace import events, fold, links

__all__ = ["render_comments", "render_history", "render_sector_actions", "render_team_core",
           "show_flash", "write"]

_DIALOG = "sc_dialog"   # (종류, 섹터 id) — 열린 모달이 없으면 키가 없다
_FLASH = "sc_flash"     # 모달이 닫힌 뒤 한 번 보여줄 성공 문장

_KIND_LABELS = {
    "team.created": "조 생성", "member.joined": "참가",
    "sector.confirmed": "확정", "sector.unconfirmed": "확정 취소",
    "comment.posted": "근거", "team.archived": "보관", "team.restored": "보관 해제",
}


# ── 모달 상태 · 원장 쓰기 ───────────────────────────────────────────────────

def _open(kind: str, sector_id: str) -> None:
    st.session_state[_DIALOG] = (kind, sector_id)


def _close() -> None:
    """🔒 `on_dismiss` 콜백이기도 하다 — X · 바깥 클릭으로 닫아도 플래그가 내려간다."""
    st.session_state.pop(_DIALOG, None)


def _opened() -> tuple[str, str] | None:
    value = st.session_state.get(_DIALOG)
    return (value[0], value[1]) if value else None


def _succeed(message: str) -> None:
    """모달을 닫고 문장을 남긴 뒤 **앱 전체**를 다시 돌린다.

    🔒 모달 안의 `st.success` 는 다시 돌면 사라진다 — 한 번 보여줄 문장을 세션에 둔다.
    """
    _close()
    st.session_state[_FLASH] = message
    st.rerun()


def show_flash() -> None:
    message = st.session_state.pop(_FLASH, None)
    if message:
        st.success(message)


def write(store, event, credential: str | None) -> bool:
    """원장에 쓴다. 🔴 실패를 삼키지 않는다 — 화면이 '됐다' 고 거짓말하면 안 된다.

    🔒 **자격증명을 인자로 받는다.** 지금 참가 중인 조가 아니라 *쓰려는 조*의 것이
       필요할 때가 있다(보관된 조 되돌리기 · `teams._render_archived`). 어느 조에
       쓰는지는 부르는 쪽이 안다.
    🔒 쓰기 권한은 키가 아니라 passcode 에 걸려 있다 (ADR-SC-0011 ⑤). `None` 이면 어떻게
       될지는 저장소가 정한다: 로컬 · HF 는 쓰고(거기서는 실제 경계가 파일 · 토큰이다)
       Supabase 는 거부하면서 무엇을 해야 하는지 말한다.
    """
    try:
        store.append([event], credential=credential)
        return True
    except Exception as exc:                          # noqa: BLE001 — 그대로 보여준다
        theme.failure("원장에 쓰지 못했다", exc)
        return False


def _refuse(exc: Exception) -> None:
    """이벤트를 만들지 못한 이유.

    🔒 `st.error` 에 그대로 둔다 — 문장에 섞인 것은 **방금 이 사람이 입력한 글**뿐이고,
       이 화면은 그 사람의 세션에만 그려진다. 원장에 남은 남의 글이 아니다.
    """
    st.error(str(exc))


def _when(at: str) -> str:
    """원장 시각(UTC ISO) → 화면 글자. 🔒 `at` 은 읽을 때 형식이 검증됐다(`events._AT_RE`)."""
    return at[:16].replace("T", " ") + " UTC"


def _known_label(names, sector_id: str | None) -> str | None:
    """`sectors.yaml` 에 있는 섹터의 이름. 🔴 원장의 섹터 id 는 RPC 로 아무 글자나 들어온다 —
    모르는 id 는 **마크다운 자리**(모달 제목)에 넣지 않도록 `None` 을 준다."""
    return names.sector_label(sector_id) if sector_id in names.sector else None


# ── 랭킹 — "왜 이 점수인가" 아래 ────────────────────────────────────────────

def render_sector_actions(frame, sector_id: str, *, profile: str, names) -> None:
    """조원이 이 섹터에 붙인 근거와 **확정 · 근거 붙이기** 버튼.

    🔒 랭킹을 죽이지 않는다. 원장에 닿지 못하면 이 칸만 이유를 말하고 멈춘다 — 섹터
       점수는 원장 없이도 읽혀야 한다. 🔴 그렇다고 삼키지 않는다(`theme.failure`).
    """
    st.subheader("우리 조")
    show_flash()
    team_id, actor = session.team_id(), session.actor()
    if not team_id or not actor:
        _close()
        st.info("**조** 페이지에서 이름을 적고 조에 참가하면, 여기서 이 섹터를 조의 핵심 "
                "섹터로 확정하거나 근거를 붙일 수 있다.")
        return
    try:
        store, source = data.workspace_store()
        workspace = fold.fold(store.read_all())
    except Exception as exc:                          # noqa: BLE001 — 이 칸만 멈춘다
        theme.failure("원장에 닿지 못해 확정 · 근거 칸을 그리지 않는다", exc)
        return
    team = workspace.team(team_id)
    if team is None or team.archived:
        # 🔒 보관된 조에는 쓰지 않는다 — 목록에서 감춘 조에 기록이 계속 쌓이면 되짚을 수 없다
        _close()
        st.warning("참가 중이던 조가 원장에 없거나 보관됐다. **조** 페이지에서 확인한다.")
        return
    if source.is_local:
        st.warning(f"⚠️ {source.label}")

    label = names.sector_label(sector_id)
    st.markdown(theme.html_line(f"<b>{theme.esc(team.name)}</b> 조원이 이 섹터에 붙인 근거"),
                unsafe_allow_html=True)
    render_comments(workspace, team.id, sector_id)

    left, right = st.columns(2)
    with left:
        if not team.is_confirmed:
            if st.button(f"{label}{explain.josa(label, '을를')} 핵심 섹터로 확정",
                         type="primary", key="open_confirm"):
                _open("confirm", sector_id)
        elif team.core_sector == sector_id:
            st.markdown(theme.user_block("✅ 이 섹터가 우리 조의 핵심 섹터다.", "sc-muted"),
                        unsafe_allow_html=True)
        else:
            core = names.sector_label(team.core_sector)
            st.markdown(theme.user_block(
                f"이미 핵심 섹터가 있다 — {core}. 바꾸려면 조 페이지에서 먼저 되돌린다.",
                "sc-muted"), unsafe_allow_html=True)
    with right:
        if st.button("근거 붙이기", key="open_attach"):
            _open("attach", sector_id)

    opened = _opened()
    if opened is None:
        return
    kind, target = opened
    if kind == "confirm" and target == sector_id and not team.is_confirmed:
        _confirm_dialog(store, team, actor, frame, sector_id, profile, names)
    elif kind == "attach" and target == sector_id:
        _attach_dialog(store, team, actor, sector_id, names)
    else:
        # 🔒 다른 섹터 · 다른 화면에서 남은 플래그다. 나중에 엉뚱한 모달을 열지 않게 내린다
        _close()


def _confirm_dialog(store, team: fold.Team, actor: str, frame, sector_id: str,
                    profile: str, names) -> None:
    label = names.sector_label(sector_id)      # 🔒 랭킹 표에서 고른 id 다 — 원장에서 온 것이 아니다

    @st.dialog(f"{label} — 핵심 섹터로 확정", width="large", on_dismiss=_close)
    def dialog() -> None:
        # 🔴 사유를 쓰는 동안 근거가 보여야 한다. 안 보이면 사유 칸이 "1위라서" 로
        #    채워진다. 모달이 랭킹을 가리므로 **같은 함수**로 여기에 한 번 더 그린다
        evidence.render_evidence(frame, sector_id, profile=profile, names=names)
        latest = view.latest_frame(frame).set_index("sector_id")
        # 🔴 `liquidity_ok` 는 창이 덜 찼으면 NA 다 — `bool(pd.NA)` 는 던진다.
        #    **미달이 확인된 때만** 경고한다. 모름은 미달이 아니다
        if (sector_id in latest.index
                and view._bool_or_none(latest.loc[sector_id]["liquidity_ok"]) is False):  # noqa: SLF001
            st.markdown(
                "<div class='sc-warn'>🔴 ETF 거래가 적어 실제 매수가 어렵다. "
                "구성종목으로 담을 수 있는지 먼저 확인한다.</div>", unsafe_allow_html=True)
        reason = st.text_area(
            "왜 이 섹터인가", height=140, key="confirm_reason",
            placeholder="어느 축이 좋았고, 무엇이 걸리는지. 3개월 뒤 운용보고서가 이 줄을 되짚는다.",
            help="🔴 비워 둘 수 없다. 사유 없는 확정은 아무것도 남기지 않는다")
        st.markdown(
            "<div class='sc-muted'>확정해도 조 페이지에서 되돌릴 수 있다. "
            "되돌린 기록도 남는다.</div>", unsafe_allow_html=True)
        if st.button("확정", type="primary", key="confirm_submit"):
            try:
                event = events.sector_confirmed(
                    team_id=team.id, sector_id=sector_id, reason=reason,
                    actor=actor, at=session.now_utc())
            except events.EventError as exc:
                _refuse(exc)
                return
            if write(store, event, session.credential()):
                _succeed(f"{label}{explain.josa(label, '을를')} 핵심 섹터로 확정했다.")

    dialog()


def _attach_dialog(store, team: fold.Team, actor: str, sector_id: str, names) -> None:
    label = names.sector_label(sector_id)      # 🔒 랭킹 표에서 고른 id 다

    @st.dialog(f"{label} — 근거 붙이기", width="large", on_dismiss=_close)
    def dialog() -> None:
        st.markdown(
            "<div class='sc-muted'>🔒 링크는 앱이 열어 보지 않는다 — 누르는 사람의 브라우저가 "
            "연다. 원장은 공개다 — 비밀 · 개인정보를 적지 않는다.</div>",
            unsafe_allow_html=True)
        body = st.text_area(
            "근거", height=140, key="attach_body",
            placeholder="무엇을 보고 이 섹터를 봤나. 숫자라면 어디서 본 숫자인가.")
        raw_links = st.text_area(
            f"링크 — 한 줄에 하나 · 최대 {links.MAX_LINKS}개 · https 만", height=90,
            key="attach_links", placeholder="https://dart.fss.or.kr/…")
        if st.button("붙이기", type="primary", key="attach_submit"):
            try:
                event = events.comment_posted(
                    team_id=team.id, body=body, actor=actor, at=session.now_utc(),
                    sector_id=sector_id, links=raw_links.splitlines())
            except events.EventError as exc:
                _refuse(exc)
                return
            if write(store, event, session.credential()):
                _succeed("근거를 붙였다.")

    dialog()


def render_comments(workspace: fold.Workspace, team_id: str, sector_id: str) -> None:
    """조원이 붙인 근거 — 최근 것이 위. 🔒 이름 · 본문 · 링크가 전부 HTML 블록이다."""
    comments = workspace.comments_of(team_id, sector_id=sector_id)
    if not comments:
        st.markdown("<div class='sc-muted'>아직 붙인 근거가 없다.</div>", unsafe_allow_html=True)
        return
    for comment in reversed(comments):
        st.markdown(theme.html_line(f"<b>{theme.esc(comment.author)}</b> · {_when(comment.at)}"),
                    unsafe_allow_html=True)
        st.markdown(theme.user_block(comment.body), unsafe_allow_html=True)
        if comment.links:
            st.markdown(theme.links_block(comment.links), unsafe_allow_html=True)


# ── 조 페이지 — 우리 조의 핵심 섹터 ─────────────────────────────────────────

def render_team_core(store, workspace: fold.Workspace, team: fold.Team, actor: str | None,
                     frame, names) -> None:
    """현재 핵심 섹터 · 그 근거 · 조원 근거 · **되돌리기**.

    🔴 확정은 끝이 아니라 **3개월 운용의 시작**이다. 사유 한 줄만 남기고 근거를 감추면
       다음 주에 "왜 이걸 골랐더라" 를 다시 랭킹에서 찾아야 한다.
    """
    show_flash()
    with theme.panel("우리 조의 핵심 섹터"):
        if team.archived:
            _close()
            st.markdown("<div class='sc-muted'>보관된 조다. 기록은 남아 있고 새로 쓰지 않는다.</div>",
                        unsafe_allow_html=True)
            return
        if not team.is_confirmed:
            _close()
            st.markdown(
                "<div class='sc-muted'>아직 정하지 않았다. <b>섹터 랭킹</b>의 "
                "'왜 이 점수인가' 에서 섹터를 골라 확정한다.</div>", unsafe_allow_html=True)
            return
        sector_id = team.core_sector
        st.markdown(theme.html_line(f"<b>{theme.esc(names.sector_full(sector_id))}</b>"),
                    unsafe_allow_html=True)
        st.markdown(theme.user_block(team.core_reason or ""), unsafe_allow_html=True)
        st.markdown(theme.user_block(
            f"{team.confirmed_by} · {_when(team.confirmed_at or '')}", "sc-muted"),
            unsafe_allow_html=True)
        if st.button("확정 되돌리기", key="open_unconfirm"):
            _open("unconfirm", sector_id)

    opened = _opened()
    if opened == ("unconfirm", sector_id):
        _unconfirm_dialog(store, team, actor, names)
    elif opened is not None:
        _close()

    if frame is not None:
        st.subheader("이 섹터의 근거")
        evidence.render_evidence(frame, sector_id, names=names)
    st.subheader("조원이 붙인 근거")
    render_comments(workspace, team.id, sector_id)


def _unconfirm_dialog(store, team: fold.Team, actor: str | None, names) -> None:
    known = _known_label(names, team.core_sector)
    title = f"{known} — 확정 되돌리기" if known else "확정 되돌리기"

    @st.dialog(title, on_dismiss=_close)
    def dialog() -> None:
        reason = st.text_area(
            "왜 바꾸나", height=100, key="cancel_reason",
            placeholder="무엇이 달라졌는지. 이 줄이 '포폴 변경 사유' 가 된다.")
        if st.button("되돌리기", key="cancel_submit"):
            try:
                event = events.sector_unconfirmed(
                    team_id=team.id, reason=reason, actor=actor or "", at=session.now_utc())
            except events.EventError as exc:
                _refuse(exc)
                return
            if write(store, event, session.credential()):
                _succeed("되돌렸다. 기록은 남는다.")

    dialog()


def render_history(workspace: fold.Workspace, team_id: str, names) -> None:
    """이 조의 원장 전체 — 최근 것이 위. 운용보고서의 '포폴 변경 사유' 가 여기서 나온다."""
    history = workspace.history_of(team_id)
    if not history:
        return
    with st.expander(f"이 조의 기록 {len(history)}건"):
        st.markdown(
            "<div class='sc-muted'>원장은 지워지지 않는다. 운용보고서의 "
            "'포폴 변경 사유' 가 여기서 나온다.</div>", unsafe_allow_html=True)
        for event in reversed(history):
            inner = f"• <b>{_KIND_LABELS.get(event.kind, event.kind)}</b>"
            sector_id = event.payload.get("sector_id")
            if sector_id:
                inner += f" · {theme.esc(names.sector_label(str(sector_id)))}"
            inner += f" — {theme.esc(event.actor)} · {_when(event.at)}"
            st.markdown(theme.html_line(inner), unsafe_allow_html=True)
            detail = (event.payload.get("reason") or event.payload.get("member")
                      or event.payload.get("body"))
            if detail:
                st.markdown(theme.user_block(detail), unsafe_allow_html=True)
