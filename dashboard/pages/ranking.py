"""섹터 랭킹 — "어느 섹터인가".

🔒 **가중치 프리셋 셋을 동시에** 보여준다. 하나만 보면 가중치를 바꿔 아무 섹터나
   1위로 만들 수 있다 (계획서 R11).

## 🔴 슬라이더는 **민감도를 보는 도구**다 — 새 관점이 아니다 (M9 · 2026-09-17)

가중치를 직접 고를 수 있게 하되, 그 상태에서는 **근거·확정 칸을 닫는다.**

에이전트는 근거의 출처를 `("column", "rank_balanced")` 처럼 **저장된 열 이름**으로
적고 guard 가 그 열에서 값을 다시 얻어 문장의 숫자를 대조한다(ADR-SC-0013 ④-1).
이름 없는 가중치에는 그런 열이 없다. guard 를 재계산 함수로 바꾸면 "원천에서 따로
다시 얻는다" 가 "같은 함수를 두 번 부른다" 가 되어 대조가 대조이기를 그친다.

🔒 대신 되돌아가는 길을 **한 번의 클릭**으로 만든다. 그리고 슬라이더를 프리셋과
   같은 비율로 맞추면 `Weighting.of` 가 알아서 프리셋으로 돌린다 —
   70/60/40/30 은 35/30/20/15 와 점수가 한 칸도 다르지 않기 때문이다.

## 🔴 필터는 **행을 숨길 뿐** 점수를 다시 매기지 않는다

순위·z·축 순위는 언제나 그날 21개 섹터 횡단면에서 나온 값이다. 고른 범위 안에서
다시 매기면 "F 축 1위" 가 "고른 다섯 중 1위" 가 되고, 화면은 그 차이를 말하지 않는다.
그래서 걸러진 집합은 `only=` 로만 넘기고 계산에는 **원본 프레임**이 들어간다.

## 🔒 하단(`evidence` · `team_actions`)에는 **언제나 원본 프레임**을 넘긴다

`sector_story` 의 `total` 이 곧 "21개 중 3위" 의 21 이다. 걸러진 프레임이 새면
그 문장이 "5개 중 1위" 가 된다.
"""

from __future__ import annotations

import streamlit as st

from dashboard import data, evidence, team_actions, theme, view, weights
from dashboard.explain import AXIS_NOT, lead_axis_text, preset_label, rank_badge, weighting_label
from sector.scoring import AXES, AXIS_NAMES, PRESETS

_STABILITY_DAYS = 20

#: 슬라이더 위젯 키. 🔒 콜백이 **위젯을 그리기 전에** 이 키에 쓴다 — 그린 뒤에 쓰면
#:    Streamlit 이 `StreamlitWidgetAlreadyInstantiatedError` 를 던진다
#:    (`evidence._reset_on_new_sector` 와 같은 규율).
_SLIDER_KEYS = {axis: f"rank_w_{axis}" for axis in AXES}

CUSTOM_NOTICE = (
    "가중치를 직접 고쳤다. 이 상태에서는 **설명 문장과 확정 칸을 열지 않는다** — "
    "설명에 쓰는 숫자는 게시된 프리셋 열에서 다시 얻어 대조하는데, 직접 고른 비율에는 "
    "그 열이 없기 때문이다. 조원이 붙인 근거는 **조** 페이지의 기록에서 그대로 읽을 수 있다."
)

LIQUIDITY_HELP = ("일평균 거래대금 1억 미만인 섹터만 숨긴다. "
                  "🔒 창이 덜 차서 **판정할 수 없는** 섹터는 남는다 — 모르는 것과 미달인 것은 다르다.")


def render() -> None:
    theme.header("섹터 랭킹", "21개 한국 테마 섹터를 네 가지 각도로 재서 줄 세운다")
    source_label = ""
    try:
        try:
            frame, source = data.load_scores()
        except data.DataUnavailable as exc:
            # 🔴 가짜 숫자를 그리지 않는다. 무엇을 해야 하는지 말하고 끝낸다
            st.error(str(exc))
            return
        source_label = source.label
        if source.is_local:
            st.info(f"⚠️ {source.label}")

        as_of = data.latest_day(frame)
        total = len(view.latest_frame(frame))
        names = data.sector_names()

        weighting, profile = _weight_controls()
        keep = _filters(frame, names)

        st.caption(
            f"기준일 **{as_of}** · 섹터 {total}개 중 **{len(keep)}개 표시** · "
            f"{weighting_label(weighting)} · "
            f"정의 `{view.latest_frame(frame)['config_version'].iloc[0]}`"
        )
        if not weighting.is_preset:
            st.info(CUSTOM_NOTICE)
            # 🔒 라벨이 **실제로 돌아갈 곳**을 말한다. 위에서 고른 프리셋이지 균형이 아니다
            st.button(f"{preset_label(profile)} 로 돌아가기", key="rank_back",
                      on_click=_leave_custom, args=(profile,))

        ranked = view.scored(frame, weighting)
        if not keep:
            st.markdown("<div class='sc-warn'>고른 조건에 맞는 섹터가 없다. "
                        "필터를 풀면 다시 보인다.</div>", unsafe_allow_html=True)
            return

        st.subheader("상위 3" if len(keep) == total else "고른 범위에서 가장 높은 셋")
        _render_podium(ranked, weighting, names, keep)

        st.subheader("순위")
        table = view.ranking_table(ranked, days=_STABILITY_DAYS, names=names)
        _render_table(table, keep)

        st.subheader("점수를 한눈에")
        _render_bars(ranked, names, keep, partial=len(keep) != total)

        st.subheader("셋 다 상위인 섹터")
        _render_consensus(frame, names)

        st.subheader("왜 이 점수인가")
        shown = [sid for sid in table.index if sid in keep]
        chosen = st.selectbox(
            "섹터", shown, key="rank_detail",
            format_func=lambda sid: f"{int(table.loc[sid, '순위'])}위 · "
                                    f"{names.sector_full(sid)}",
        )
        if chosen and weighting.is_preset:
            # 🔒 **원본 프레임**을 넘긴다 (머리주석) — 걸러진 것도, `scored()` 를 지난
            #    것도 아니다. 에이전트는 저장된 프리셋 열을 스스로 다시 읽는다
            _render_breakdown(frame, chosen, weighting.name, names)
            # ★ 확정 · 근거 붙이기는 **근거를 읽은 바로 그 자리**에서 한다 (ADR-SC-0012 ②)
            team_actions.render_sector_actions(frame, chosen, profile=weighting.name,
                                               names=names)
    finally:
        theme.footer(source_label)


# ── 가중치 ──────────────────────────────────────────────────────────────────

def _sync_sliders(profile: str) -> None:
    """슬라이더를 그 프리셋 값으로. 🔒 **콜백이다** — 위젯이 그려지기 전에 돌아야
    Streamlit 이 값을 받아 준다(`StreamlitWidgetAlreadyInstantiatedError`)."""
    for axis, key in _SLIDER_KEYS.items():
        st.session_state[key] = PRESETS[profile][axis]


def _leave_custom(profile: str) -> None:
    """슬라이더를 맞추고 '직접 고르기' 를 끈다 — 프리셋으로 돌아가는 길.

    🔴 **`profile` 을 받는다.** 예전에는 `"balanced"` 를 박아 두고 라디오를 안 건드려,
       라디오가 모멘텀인 상태에서 누르면 버튼은 "균형" 이라 말하고 캡션은 모멘텀
       55/25/15/5 를, 슬라이더는 균형 35/30/20/15 를 보여 줬다 — **지금 쓰는 가중치와
       화면에 적힌 가중치가 달랐다.** ADR-SC-0014 ④ 가 금지한 "화면의 거짓말" 이다.
    """
    _sync_sliders(profile)
    st.session_state["rank_custom"] = False


def _weight_controls() -> "tuple[weights.Weighting, str]":
    """프리셋 라디오 + 접어 둔 슬라이더.

    돌려주는 것은 **지금 쓰는 가중치**와 **라디오가 가리키는 프리셋** 둘이다 — 커스텀일 때
    돌아갈 곳이 어디인지 화면이 말해야 하기 때문이다(`_leave_custom`).
    """
    # 🔒 위젯을 그리기 **전에** 기본값을 심는다. `st.slider(value=...)` 와 `key=` 를 같이
    #    주면 세션값이 있는 매 rerun 마다 Streamlit 이 로거 경고를 남긴다(배포 로그가 더러워진다)
    for axis, key in _SLIDER_KEYS.items():
        st.session_state.setdefault(key, PRESETS["balanced"][axis])
    profile = st.radio(
        "가중치", list(view.PROFILES), horizontal=True,
        format_func=preset_label, key="rank_profile",
    )
    with st.expander("고급 — 가중치를 직접 고른다"):
        st.markdown(
            "<div class='sc-muted'>합이 100 이 아니어도 된다 — **비율만** 쓴다. "
            "그래서 70/60/40/30 은 35/30/20/15 와 점수가 같고, 화면도 그때는 "
            "균형 프리셋으로 돌아간다.</div>", unsafe_allow_html=True)
        custom = st.checkbox("직접 고른 값을 쓴다", key="rank_custom")
        for axis in AXES:
            st.slider(f"{AXIS_NAMES[axis]} ({axis})", 0, weights.MAX_WEIGHT,
                      key=_SLIDER_KEYS[axis], help=AXIS_NOT[axis])
        st.button(f"슬라이더를 {preset_label(profile)} 값으로 맞추기", key="rank_sync",
                  on_click=_sync_sliders, args=(profile,))
    if not custom:
        return weights.Weighting.preset(profile), profile
    try:
        return weights.Weighting.of(
            {a: st.session_state[_SLIDER_KEYS[a]] for a in AXES}), profile
    except weights.WeightError as exc:
        # 🔒 우리가 쓴 문장이다 — 사람이 쓴 글이 아니라 마크다운에 넣어도 된다
        st.error(f"{exc} — 프리셋으로 그린다.")
        return weights.Weighting.preset(profile), profile


# ── 필터 ────────────────────────────────────────────────────────────────────

def _filters(frame, names) -> frozenset[str]:
    """보일 섹터 id 집합. 🔴 **점수를 다시 매기지 않는다** — 행을 숨길 뿐이다."""
    options = view.gics_options(frame, names)
    labels = dict(options)
    picked = st.multiselect(
        "GICS 대분류", [gics for gics, _ in options], key="rank_gics",
        format_func=lambda g: labels.get(g, g),
        placeholder="전체 — 고르면 그 대분류만 남는다",
        help="🔒 대분류 점수를 따로 내지 않는다. 대분류 ETF 는 살 수 없어 매매 단위가 "
             "아니기 때문이다. 순위는 언제나 테마 21개 안에서의 자리다.")
    left, right = st.columns(2)
    with left:
        hide_illiquid = st.checkbox("유동성 미달 숨기기", key="rank_hide_illiquid",
                                    help=LIQUIDITY_HELP)
    with right:
        hide_single = st.checkbox("ETF 1종 섹터 숨기기", key="rank_hide_single",
                                  help="ETF 가 하나뿐이면 섹터 지수가 그 한 종목이다.")

    # 🔒 판정은 `view.visible_ids` 가 한다 — 여기는 위젯 값을 모으기만 한다.
    #    화면 없이 검증할 수 있어야 "판정 불가를 숨기지 않는다" 를 테스트로 고정한다
    return view.visible_ids(frame, gics=frozenset(picked),
                            hide_illiquid=hide_illiquid, hide_single_etf=hide_single)


# ── 그리기 ──────────────────────────────────────────────────────────────────

def _render_table(table, keep: frozenset[str]) -> None:
    shown = table.loc[[sid for sid in table.index if sid in keep]]
    st.dataframe(
        shown,
        width="stretch",
        column_config={
            # 🔴 숫자와 **막대를 같이** 준다. `12522` 는 즉시 못 읽지만 막대 길이는
            #    읽힌다. 🔒 눈금은 **걸러지기 전 21개**의 최소~최대다 — 필터로 눈금이
            #    좁아지면 같은 섹터의 막대 길이가 필터 유무로 달라지고 help 가 거짓이 된다
            "점수bp": st.column_config.ProgressColumn(
                "점수", help="Σ(가중치×z)/Σ가중치 × 10000. ±30000 = ±3σ. "
                            "막대는 이 날 21개 섹터의 최소~최대 안에서의 자리다",
                format="%d", min_value=_floor(table["점수bp"]),
                max_value=_ceil(table["점수bp"])),
            "평균순위": st.column_config.NumberColumn(
                f"최근{_window(table)}일 평균순위",
                help="🔴 '오늘만 1등' 과 '계속 1등' 을 가른다. "
                     "표본일이 이보다 작으면 그 섹터는 비워 둔다", format="%.1f"),
            "표본일": st.column_config.NumberColumn(
                "표본일", help="평균을 실제로 몇 영업일에서 냈는가", format="%d"),
            "진폭": st.column_config.NumberColumn(
                "진폭", help="순위 표준편차. 작을수록 꾸준하다", format="%.1f"),
            **{a: st.column_config.NumberColumn(
                f"{a} ({AXIS_NAMES[a]})", help=AXIS_NOT[a], format="%d") for a in AXES},
        },
    )
    st.markdown(
        "<div class='sc-muted'>축 값은 bp 다 — 10000 = 1.0σ. "
        "빈 칸은 값이 없다는 뜻이고 0 이 아니다. 순위는 걸러내기 전 21개 안에서의 "
        "자리라, 필터를 걸어도 번호가 이어지지 않는다.</div>",
        unsafe_allow_html=True,
    )


def _window(table) -> int:
    """표본일의 최댓값 = 실제로 볼 수 있었던 창. 🔒 요청한 20 을 그대로 쓰지 않는다."""
    days = table["표본일"].dropna()
    return int(days.max()) if len(days) else _STABILITY_DAYS


def _render_podium(frame, weighting, names, keep: frozenset[str]) -> None:
    """가장 높은 셋 — **등수를 크게, 이유를 옆에.**

    🔴 21행 표에서 1등을 눈으로 찾는 일이 개발자가 아닌 팀원에게는 진입 장벽이다.
    🔒 그렇다고 등수만 크게 그리지 않는다. "무엇이 끌어올렸나" 와 유동성 경고를
       같은 카드에 둔다 — 등수만 보이면 "1위 = 사면 오른다" 로 읽힌다.
    🔒 카드의 등수는 걸러내기 전 21개 안에서의 자리다. 필터를 걸면 "3위·11위·17위"
       처럼 이가 빠진 채로 나오는데, **그게 사실이다.**
    """
    entries = view.podium(frame, weighting=weighting, names=names, only=keep)
    if not entries:
        st.markdown("<div class='sc-muted'>순위를 낼 수 있는 섹터가 없다.</div>",
                    unsafe_allow_html=True)
        return
    for column, entry in zip(st.columns(len(entries)), entries, strict=True):
        with column, theme.panel():
            st.markdown(
                f"<div class='sc-rank'>{rank_badge(entry['rank'])}</div>"
                f"<div class='sc-rank-name'>{entry['label']}</div>",
                unsafe_allow_html=True)
            score = entry["score_bp"]
            st.markdown(f"**{score / 10000:+.2f}σ**" if score is not None
                        else theme.missing("점수 없음"), unsafe_allow_html=True)
            st.markdown(f"<div class='sc-muted'>{lead_axis_text(entry['lead_axis'])}</div>",
                        unsafe_allow_html=True)
            if entry["liquidity_ok"] is False:
                st.markdown("<div class='sc-warn'>🔴 유동성 미달</div>",
                            unsafe_allow_html=True)
            elif entry["liquidity_ok"] is None:
                st.markdown("<div class='sc-muted'>유동성 판정 불가 — 창이 덜 찼다</div>",
                            unsafe_allow_html=True)
            elif entry["etf_n"] == 1:
                st.markdown("<div class='sc-warn'>🔴 ETF 1종</div>",
                            unsafe_allow_html=True)


def _render_bars(frame, names, keep: frozenset[str], *, partial: bool) -> None:
    """섹터 점수를 가로 막대로. 🔒 순위 순서를 지킨다(`sort=False`).

    🔴 0 을 기준으로 좌우로 갈린다 — 음수 섹터가 왼쪽으로 뻗는 그림이 "평균보다
       아래" 를 표보다 빨리 말한다.
    """
    bars = view.score_bars(frame, names=names, only=keep)
    st.bar_chart(bars, horizontal=True, sort=False, height=460,
                 x_label="점수(σ) — 0 이 21개 섹터의 가운데다", y_label="")
    tail = (" 필터를 걸었으므로 **가로축은 지금 보이는 섹터들에 맞춰 다시 잡힌다** — "
            "점수 자체는 21개 전체에서 나온 값 그대로다." if partial else "")
    st.markdown(
        "<div class='sc-muted'>위에서부터 순위가 높다. 막대가 왼쪽이면 그날 섹터들의 "
        f"가운데보다 낮았다는 뜻이고, 앞으로 오른다·내린다는 뜻이 아니다.{tail}</div>",
        unsafe_allow_html=True)


def _render_consensus(frame, names) -> None:
    """🔒 가중치에 기대지 않는 신호. 셋 다 상위 5 안이면 배지를 준다.

    🔒 **필터와 무관하고 슬라이더와도 무관하다** — 프리셋 셋을 고정으로 본다.
       그것이 이 칸의 요점이다.
    """
    latest = view.latest_frame(frame).set_index("sector_id")
    top = {p: set(latest.nsmallest(5, f"rank_{p}").index) for p in view.PROFILES}
    consensus = sorted(set.intersection(*top.values()),
                       key=lambda sid: latest.loc[sid, "rank_balanced"])
    if not consensus:
        st.markdown(
            "<div class='sc-muted'>세 가중치 모두에서 상위 5에 든 섹터가 없다. "
            "즉 지금 순위는 가중치를 무엇으로 두느냐에 민감하다.</div>",
            unsafe_allow_html=True)
        return
    for sid in consensus:
        row = latest.loc[sid]
        ranks = " · ".join(f"{p} {int(row[f'rank_{p}'])}위" for p in view.PROFILES)
        st.markdown(f"- **{names.sector_label(sid)}** — {ranks}")
    st.markdown("<div class='sc-muted'>프리셋 셋을 고정으로 본다 — 슬라이더와 필터를 "
                "따르지 않는다.</div>", unsafe_allow_html=True)


def _render_breakdown(frame, sector_id: str, profile: str, names) -> None:
    with theme.panel(names.sector_full(sector_id)):
        # ★ 질문칸은 여기와 조 페이지에만 연다 — 확정 모달에는 두지 않는다 (ADR-SC-0013)
        evidence.render_evidence(frame, sector_id, profile=profile, names=names,
                                 days=_STABILITY_DAYS, ask_key="rank_ask")


def _floor(column) -> int:
    """막대 눈금의 아래끝. 🔒 0 으로 고정하지 않는다 — 음수 점수가 잘린다."""
    return int(min(column.min(), 0))


def _ceil(column) -> int:
    return int(max(column.max(), 1))
