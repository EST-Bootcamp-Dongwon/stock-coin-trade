"""**"왜 이 점수인가" 한 벌** — 랭킹과 확정이 같이 쓰는 렌더 조각.

## 🔴 왜 페이지가 아니라 여기인가

랭킹과 확정 두 화면이 같은 섹터를 두고 **각자 근거를 그리면 문구가 갈라진다.**
팀원은 어느 쪽이 맞는지 판단할 방법이 없다. 그렇다고 확정 화면이 랭킹 **페이지**를
import 하면 페이지끼리 묶여 `st.navigation` 이 무엇을 먼저 읽느냐에 화면이 달라진다.

그래서 조각을 여기 따로 둔다 — `view`(순수 계산) → `evidence`(공유 렌더) →
`pages`(배치) 세 층이다.

## 🔒 순서가 뜻을 만든다 — 말 → 숫자 → 사람이 쓴 근거

숫자를 먼저 두면 개발자가 아닌 팀원이 첫 줄에서 멈춘다. `narrative()` 가 같은
사실을 먼저 문장으로 말하고, 숫자는 접어 둔 칸에서 편다.
"""

from __future__ import annotations

import streamlit as st

from dashboard import data, theme, view
from dashboard.explain import (
    axis_line, degraded_text, liquidity_text, narrative, rank_stability_text,
    score_text,
)

__all__ = ["render_evidence"]

#: 순위 안정성을 볼 창. 🔒 랭킹 화면과 같은 값이라 두 화면의 "평균순위" 가 맞는다.
STABILITY_DAYS = 20


def render_evidence(frame, sector_id: str, *, profile: str = "balanced",
                    names=None, days: int = STABILITY_DAYS) -> None:
    """한 섹터의 근거를 통째로 그린다 (머리주석의 세 순서대로)."""
    names = names or view.Names.empty()
    story = view.sector_story(frame, sector_id, profile=profile, days=days, names=names)
    if not story:
        st.markdown(theme.missing("이 섹터는 그날 표에 없다"), unsafe_allow_html=True)
        return

    # ① 말로
    for line in narrative(**story):
        st.markdown(line)

    # ② 숫자로 — 같은 사실을 축별로 편다
    with st.expander("축별 숫자로 보기"):
        total = story["total"]
        st.markdown("**총점** " + score_text(story["score_bp"], story["rank"], total))
        st.markdown(rank_stability_text(story["mean_rank"], story["spread"],
                                        story["window"]))
        for item in story["parts"]:
            line = axis_line(item["axis"], raw_bp=item["raw_bp"], z_bp=item["z_bp"],
                             rank=item["rank"], total=total)
            share = (f" → 기여 **{item['contribution_bp']:+d}**"
                     if item["contribution_bp"] is not None else "")
            st.markdown(f"- {line} · 가중치 {item['weight']}{share}")
        st.markdown(f"<div class='sc-muted'>{liquidity_text(story['liquidity_ok'])}</div>",
                    unsafe_allow_html=True)
        warning = degraded_text(story["missing"], story["degraded"])
        if warning:
            st.markdown(f"<div class='sc-warn'>{warning}</div>", unsafe_allow_html=True)

    # ③ 사람이 쓴 근거 — 🔒 `sectors.yaml` 의 `note` 가 그대로 나간다
    note = data.sector_notes().get(sector_id, "")
    if note:
        st.markdown("**사람이 쓴 근거** (`sectors.yaml`)")
        st.markdown(f"<div class='sc-note'>{note}</div>", unsafe_allow_html=True)


