"""섹터 랭킹 — "어느 섹터인가".

🔒 **가중치 프리셋 셋을 동시에** 보여준다. 하나만 보면 가중치를 바꿔 아무 섹터나
   1위로 만들 수 있다 (계획서 R11).
"""

from __future__ import annotations

import streamlit as st

from dashboard import data, theme, view
from dashboard.explain import (
    AXIS_NOT, axis_line, degraded_text, liquidity_text, preset_label,
    rank_stability_text, score_text,
)
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

        st.subheader("순위")
        table = view.ranking_table(frame, profile=profile, days=_STABILITY_DAYS)
        # 🔴 요청한 20일이 다 없을 수 있다. 있는 만큼으로 이름 붙인다
        window = view.stability_window(frame, days=_STABILITY_DAYS)
        st.dataframe(
            table,
            width="stretch",
            column_config={
                "점수bp": st.column_config.NumberColumn(
                    "점수", help="Σ(가중치×z)/Σ가중치 × 10000. ±30000 = ±3σ", format="%d"),
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

        st.subheader("셋 다 상위인 섹터")
        _render_consensus(frame)

        st.subheader("왜 이 점수인가")
        chosen = st.selectbox(
            "섹터", list(table.index), key="rank_detail",
            format_func=lambda sid: f"{table.loc[sid, '순위']}위 · {sid}",
        )
        if chosen:
            _render_breakdown(frame, table, chosen, profile)
    finally:
        theme.footer(source_label)


def _render_consensus(frame) -> None:
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
        st.markdown(f"- **{sid}** — {ranks}")


def _render_breakdown(frame, table, sector_id: str, profile: str) -> None:
    latest = view.latest_frame(frame).set_index("sector_id")
    row = latest.loc[sector_id]
    total = len(latest)

    with theme.panel(f"{sector_id}"):
        st.markdown(
            "**총점** " + score_text(int(row[f"score_{profile}_bp"]),
                                     int(row[f"rank_{profile}"]), total))
        stability = table.loc[sector_id]
        st.markdown(rank_stability_text(
            _num(stability["평균순위"]), _num(stability["진폭"]),
            view.stability_window(frame, days=_STABILITY_DAYS)))

        for item in view.axis_breakdown(frame, sector_id, profile=profile):
            axis = item["axis"]
            line = axis_line(axis, raw_bp=item["raw_bp"], z_bp=item["z_bp"],
                             rank=item["rank"], total=total)
            share = (f" → 기여 **{item['contribution_bp']:+d}**"
                     if item["contribution_bp"] is not None else "")
            st.markdown(f"- {line} · 가중치 {item['weight']}{share}")

        st.markdown(f"<div class='sc-muted'>{liquidity_text(_bool(row['liquidity_ok']))}</div>",
                    unsafe_allow_html=True)
        warning = degraded_text(row.get("axes_missing"), row.get("axes_degraded"))
        if warning:
            st.markdown(f"<div class='sc-warn'>{warning}</div>", unsafe_allow_html=True)
        if int(row["etf_n"]) == 1:
            st.markdown(
                "<div class='sc-warn'>🔴 ETF 가 하나뿐이라 자금흐름이 그 한 종목에 달려 있다"
                "</div>", unsafe_allow_html=True)

        note = _note_of(sector_id)
        if note:
            st.markdown("**사람이 쓴 근거** (`sectors.yaml`)")
            st.markdown(f"<div class='sc-note'>{note}</div>", unsafe_allow_html=True)


@st.cache_data(ttl=None, max_entries=4, show_spinner=False)
def _notes() -> dict[str, str]:
    """`sectors.yaml` 의 `note` — 왜 이렇게 묶었나. 🔒 이 줄이 화면에 그대로 나간다."""
    return {s.id: s.note for s in data.load_sectors().sectors}


def _note_of(sector_id: str) -> str:
    return _notes().get(sector_id, "")


def _num(value) -> float | None:
    import pandas as pd
    return None if value is None or pd.isna(value) else float(value)


def _bool(value) -> bool | None:
    import pandas as pd
    return None if value is None or pd.isna(value) else bool(value)
