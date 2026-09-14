"""섹터 랭킹 — "어느 섹터인가".

🔒 **가중치 프리셋 셋을 동시에** 보여준다. 하나만 보면 가중치를 바꿔 아무 섹터나
   1위로 만들 수 있다 (계획서 R11).
"""

from __future__ import annotations

import streamlit as st

from dashboard import data, evidence, team_actions, theme, view
from dashboard.explain import AXIS_NOT, lead_axis_text, preset_label, rank_badge
from sector.scoring import AXES, AXIS_NAMES

_STABILITY_DAYS = 20


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
        latest = view.latest_frame(frame)
        st.caption(
            f"기준일 **{as_of}** · 섹터 {len(latest)}개 · "
            f"정의 `{latest['config_version'].iloc[0]}`"
        )

        profile = st.radio(
            "가중치", list(view.PROFILES), horizontal=True,
            format_func=preset_label, key="rank_profile",
        )

        names = data.sector_names()

        st.subheader("상위 3")
        _render_podium(frame, profile, names)

        st.subheader("순위")
        table = view.ranking_table(frame, profile=profile, days=_STABILITY_DAYS,
                                   names=names)
        # 🔴 요청한 20일이 다 없을 수 있다. 있는 만큼으로 이름 붙인다
        window = view.stability_window(frame, days=_STABILITY_DAYS)
        st.dataframe(
            table,
            width="stretch",
            column_config={
                # 🔴 숫자와 **막대를 같이** 준다. `12522` 는 즉시 못 읽지만 막대 길이는
                #    읽힌다. 🔒 눈금은 이 날의 최소~최대라 절대 크기가 아니라 간격이다
                "점수bp": st.column_config.ProgressColumn(
                    "점수", help="Σ(가중치×z)/Σ가중치 × 10000. ±30000 = ±3σ. "
                                "막대는 이 날 21개 섹터의 최소~최대 안에서의 자리다",
                    format="%d", min_value=_floor(table["점수bp"]),
                    max_value=_ceil(table["점수bp"])),
                "평균순위": st.column_config.NumberColumn(
                    f"최근{window}일 평균순위",
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
            "빈 칸은 값이 없다는 뜻이고 0 이 아니다.</div>",
            unsafe_allow_html=True,
        )

        st.subheader("점수를 한눈에")
        _render_bars(frame, profile, names)

        st.subheader("셋 다 상위인 섹터")
        _render_consensus(frame, names)

        st.subheader("왜 이 점수인가")
        chosen = st.selectbox(
            "섹터", list(table.index), key="rank_detail",
            format_func=lambda sid: f"{int(table.loc[sid, '순위'])}위 · "
                                    f"{names.sector_full(sid)}",
        )
        if chosen:
            _render_breakdown(frame, chosen, profile, names)
            # ★ 확정 · 근거 붙이기는 **근거를 읽은 바로 그 자리**에서 한다 (ADR-SC-0012 ②)
            team_actions.render_sector_actions(frame, chosen, profile=profile, names=names)
    finally:
        theme.footer(source_label)


def _render_podium(frame, profile: str, names) -> None:
    """상위 3 — **등수를 크게, 이유를 옆에.**

    🔴 21행 표에서 1등을 눈으로 찾는 일이 개발자가 아닌 팀원에게는 진입 장벽이다.
    🔒 그렇다고 등수만 크게 그리지 않는다. "무엇이 끌어올렸나" 와 유동성 경고를
       같은 카드에 둔다 — 등수만 보이면 "1위 = 사면 오른다" 로 읽힌다.
    """
    entries = view.podium(frame, profile=profile, names=names)
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
            elif entry["etf_n"] == 1:
                st.markdown("<div class='sc-warn'>🔴 ETF 1종</div>",
                            unsafe_allow_html=True)


def _render_bars(frame, profile: str, names) -> None:
    """21개 섹터 점수를 가로 막대로. 🔒 순위 순서를 지킨다(`sort=False`).

    🔴 0 을 기준으로 좌우로 갈린다 — 음수 섹터가 왼쪽으로 뻗는 그림이 "평균보다
       아래" 를 표보다 빨리 말한다.
    """
    bars = view.score_bars(frame, profile=profile, names=names)
    st.bar_chart(bars, horizontal=True, sort=False, height=460,
                 x_label="점수(σ) — 0 이 21개 섹터의 가운데다", y_label="")
    st.markdown(
        "<div class='sc-muted'>위에서부터 1위다. 막대가 왼쪽이면 그날 섹터들의 "
        "가운데보다 낮았다는 뜻이고, 앞으로 오른다·내린다는 뜻이 아니다.</div>",
        unsafe_allow_html=True)


def _render_consensus(frame, names) -> None:
    """🔒 가중치에 기대지 않는 신호. 셋 다 상위 5 안이면 배지를 준다."""
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


def _render_breakdown(frame, sector_id: str, profile: str, names) -> None:
    with theme.panel(names.sector_full(sector_id)):
        evidence.render_evidence(frame, sector_id, profile=profile, names=names,
                                 days=_STABILITY_DAYS)


def _floor(column) -> int:
    """막대 눈금의 아래끝. 🔒 0 으로 고정하지 않는다 — 음수 점수가 잘린다."""
    return int(min(column.min(), 0))


def _ceil(column) -> int:
    return int(max(column.max(), 1))
