"""**"왜 이 점수인가" 한 벌** — 랭킹과 확정이 같이 쓰는 렌더 조각.

## 🔴 왜 페이지가 아니라 여기인가

랭킹 · 확정 모달 · 조 화면이 같은 섹터를 두고 **각자 근거를 그리면 문구가 갈라진다.**
팀원은 어느 쪽이 맞는지 판단할 방법이 없다. 그렇다고 조 화면이 랭킹 **페이지**를
import 하면 페이지끼리 묶여 `st.navigation` 이 무엇을 먼저 읽느냐에 화면이 달라진다.

그래서 조각을 여기 따로 둔다 — `view`(순수 계산) → `evidence`(공유 렌더) →
`pages`(배치) 세 층이다.

## 🔒 순서가 뜻을 만든다 — 말 → 숫자 → 사람이 쓴 근거

숫자를 먼저 두면 개발자가 아닌 팀원이 첫 줄에서 멈춘다. 같은 사실을 먼저 문장으로
말하고, 숫자는 접어 둔 칸에서 편다.

## ★ 말은 에이전트가 만든다 (2026-09-14 · ADR-SC-0013)

"말로" 칸은 `dashboard.agent` 가 채운다 — 질문칸이 있으면 자유 질문에, 없으면 "왜 이 자리인가" 에
답한다. 🔒 **guard 를 통과한 문장만 그린다.** 어긋나면 문장을 버리고 숫자 칸만 남기며 이유를 말한다.
🔒 **확정 모달에는 질문칸을 두지 않는다** — 사유를 쓰는 자리에서 대화가 시작되면 모달의 일이 흐려진다.

🔒 질문칸의 글은 **어디에도 되돌려 그리지 않는다.** 화면에 나가는 문장은 전부 코드와
`sectors.yaml` 이 쓴 것이다 — 사람 글을 마크다운에 넣는 경로를 새로 열지 않는다(ADR-SC-0012 ④).
"""

from __future__ import annotations

import streamlit as st

from dashboard import data, theme, view
from dashboard.agent import engine
from dashboard.agent.intent import EXAMPLE_QUESTIONS, INTENT_LABELS, INTENTS, MAX_CHARS
from dashboard.agent.inventory import SEARCH_NOTICE
from dashboard.agent.redteam import STATUS_PLAIN
from dashboard.explain import (
    WATERFALL_ABSENT, WATERFALL_READING, arithmetic_text, axis_line, degraded_text,
    liquidity_text, rank_stability_text, score_text,
)

__all__ = ["render_evidence", "GUARD_FAILED", "ASK_NOTICE"]

#: 순위 안정성을 볼 창. 🔒 랭킹 화면과 같은 값이라 두 화면의 "평균순위" 가 맞는다.
STABILITY_DAYS = 20

GUARD_FAILED = ("설명 문장을 검증하다 원천과 어긋난 것이 나와 문장을 보여주지 않는다 — "
                "아래 숫자 칸만 본다.")
ASK_NOTICE = ("네 가지만 답한다 — 사고파는 판단 · 앞으로의 예측 · 추천은 하지 않는다. "
              "모든 문장은 숫자를 원천과 대조한 뒤에 나간다.")


def render_evidence(frame, sector_id: str, *, profile: str = "balanced", names=None,
                    days: int = STABILITY_DAYS, ask_key: str | None = None,
                    workspace=None, team_id: str | None = None) -> None:
    """한 섹터의 근거를 통째로 그린다 (머리주석의 세 순서대로).

    `ask_key` 가 있으면 질문칸을 연다 — 🔒 모달은 넘기지 않는다(머리주석).
    """
    names = names or view.Names.empty()
    if not view.sector_story(frame, sector_id, profile=profile, days=days, names=names):
        st.markdown(theme.missing("이 섹터는 그날 표에 없다"), unsafe_allow_html=True)
        return
    master = data.sector_master()
    options = dict(frame=frame, names=names, master=master, profile=profile, days=days,
                   workspace=workspace, team_id=team_id)

    result = None
    if ask_key:
        _reset_on_new_sector(ask_key, sector_id)
        question = _question_box(ask_key)
        if question.strip():
            # 🔒 인계는 **사람이 물은 답**에서만 남긴다 — 기본 답으로 덮으면 처음 묻는 질문의
            #    빈칸이 "유지" 로 나왔다(리뷰 O8).
            # 🔴 같은 질문이 다시 그려질 때(라디오 · 버튼 · 새로고침)는 **그 질문 앞의 인계**와 견준다.
            #    자기 인계와 견주면 그릴 때마다 빈칸이 "신규 → 유지" 로 바뀌었다(재검증 R-F).
            chain = st.session_state.get(_handoff_key(ask_key))
            if chain is not None and chain["question"] == question:
                previous = chain["before"]
            else:
                previous = chain["handoff"] if chain is not None else None
            result = engine.answer(question, context_sector=sector_id, previous=previous, **options)
            st.session_state[_handoff_key(ask_key)] = {
                "question": question, "before": previous, "handoff": result.handoff}
    if result is None:
        result = engine.brief_for("why_rank", sector_id=sector_id, **options)

    # ① 말로
    _render_answer(result)

    answered = result.sector_id if result.brief is not None else sector_id
    story = view.sector_story(frame, answered, profile=profile, days=days, names=names)
    if not story:
        return

    # ② 숫자로 — 같은 사실을 축별로 편다
    with st.expander("축별 숫자로 보기"):
        total = story["total"]
        st.markdown("**총점** " + score_text(story["score_bp"], story["rank"], total))
        st.markdown(rank_stability_text(story["mean_rank"], story["spread"],
                                        story["window"]))
        # ★ 그림 → 같은 것을 줄로 → 잔차 문장. 머리주석의 «말 → 숫자» 와 같은 규율이다
        #   (모양을 먼저 보이고 자릿수를 뒤에 편다)
        _render_waterfall(view.waterfall(story))
        for item in story["parts"]:
            line = axis_line(item["axis"], raw_bp=item["raw_bp"], z_bp=item["z_bp"],
                             rank=item["rank"], total=total)
            share = (f" → 기여 **{item['contribution_bp']:+d}**"
                     if item["contribution_bp"] is not None else "")
            st.markdown(f"- {line} · 가중치 {item['weight']}{share}")
        # 🔴 **팀이 매일 쓰는 화면은 여기다.** 읽는 법에만 잔차를 적으면, 축별 기여를
        #    나란히 보여주면서 그 합이 총점과 다르다는 것을 말하지 않는 화면이 남는다 —
        #    최신일만 봐도 균형 6/21 섹터가 어긋난다 (이슈 #13 · ADR-SC-0018 ③)
        st.markdown(arithmetic_text(story["arithmetic"]))
        st.markdown(f"<div class='sc-muted'>{liquidity_text(story['liquidity_ok'])}</div>",
                    unsafe_allow_html=True)
        warning = degraded_text(story["missing"], story["degraded"])
        if warning:
            st.markdown(f"<div class='sc-warn'>{warning}</div>", unsafe_allow_html=True)

    # ③ 사람이 쓴 근거 — 🔒 `sectors.yaml` 의 `note` 가 그대로 나간다
    note = data.sector_notes().get(answered, "")
    if note:
        st.markdown("**사람이 쓴 근거** (`sectors.yaml`)")
        st.markdown(f"<div class='sc-note'>{note}</div>", unsafe_allow_html=True)


def _waterfall_chart(waterfall: "view.Waterfall"):
    """0 → M → F → B → V → 총점 (이슈 #16 · 계획서 D-5 ①).

    🔒 **색 인코딩을 주지 않는다.** 그래야 프런트엔드의 `theme="streamlit"` 이
       `chartCategoricalColors` 를 먹인다 — `ranking._bar_chart` 와 같은 경로다.
       🔴 부호를 초록·빨강으로 칠하려면 색을 **스펙에 박아야** 하고 그것은 팔레트의
       정본을 `config.toml` 하나로 둔 규율을 어긴다(ADR-SC-0010 ⑦). 게다가 한국
       관습은 **상승이 빨강**이라 팀 7명이 같은 색을 반대로 읽는다. 워터폴은
       **0 에서 출발하는 위치가 이미 부호를 말한다.**
    🔒 `import altair` 를 **함수 안에서** 한다 — streamlit 이 미리 로드하지 않고
       (실측 1.5s) 이 모듈은 부팅 때 함께 import 된다 (`ranking._bar_chart` 와 같다).
    🔒 `sort=None` — M · F · B · V · 총점 순서가 이 그림의 뜻이다. 풀리면 알파벳순이
       되어 «쌓아 올린다» 가 사라진다.
    ⚠️ `.interactive()` 를 붙이지 않는다. `_bar_chart` 가 그것을 붙인 이유는
       `st.bar_chart` 가 주던 줌·팬을 **빠짐없이 옮기려는** 것이었다(ADR-SC-0017 ⑤).
       여기는 옮겨 올 원본이 없고 막대가 다섯뿐이라 확대할 것이 없다.
    """
    import altair as alt

    return alt.Chart(waterfall.table).mark_bar().encode(
        x=alt.X("단계:N", sort=None, title="", axis=alt.Axis(labelAngle=0)),
        y=alt.Y("시작:Q", title="bp", axis=alt.Axis(grid=True)),
        y2=alt.Y2("끝:Q"),
        tooltip=["단계", "값(bp)"],
    ).properties(height=240)


def _render_waterfall(waterfall: "view.Waterfall") -> None:
    """축 기여가 총점까지 쌓이는 그림. 🔒 **그릴 수 없으면 왜 없는지 말한다.**

    🔴 `bp` 로 그린다 — `score_bars` 가 σ 를 고른 것과 **반대**다. 이 칸의 모든 줄이
       bp 로 적혀 있고(«→ 기여 +1,480» · 아래 합계 문장), 같은 칸에서 그림만 다른
       척도를 쓰면 팀원이 눈으로 검산할 수 없다. 막대 차트는 21개 섹터를 **비교**
       시키는 그림이라 눈금을 읽힐 일이 없었지만, 이 그림은 **검산시키는** 그림이다.
    """
    if len(waterfall.table) == 0:
        st.markdown(WATERFALL_ABSENT)
        return
    st.altair_chart(_waterfall_chart(waterfall), width="stretch")
    st.markdown(f"<div class='sc-muted'>{WATERFALL_READING}</div>",
                unsafe_allow_html=True)


def _handoff_key(ask_key: str) -> str:
    return f"{ask_key}__handoff"


def _reset_on_new_sector(key: str, sector_id: str) -> None:
    """🔴 섹터를 바꾸면 질문칸과 인계를 비운다 — 옛 질문이 새 섹터 패널 아래에서 옛 섹터로 답했다(리뷰 O8).

    🔒 위젯을 그리기 **전에** 부른다. 그려진 뒤에는 Streamlit 이 값을 바꾸게 두지 않는다.
    """
    marker = f"{key}__sector"
    if st.session_state.get(marker) != sector_id:
        st.session_state[key] = ""
        st.session_state.pop(_handoff_key(key), None)
        st.session_state[marker] = sector_id


def _fill(key: str, text: str) -> None:
    """🔒 콜백이다 — 위젯이 그려지기 **전에** 값을 넣어야 Streamlit 이 받아 준다."""
    st.session_state[key] = text


def _question_box(key: str) -> str:
    st.markdown("**이 섹터에 대해 묻기**")
    st.text_input("질문", key=key, max_chars=MAX_CHARS, label_visibility="collapsed",
                  placeholder="예: 왜 이 순위야? · 믿어도 돼? · 지난주보다 뭐가 바뀌었어? · 뭐가 들어 있어?")
    for column, intent in zip(st.columns(len(INTENTS)), INTENTS, strict=True):
        with column:
            st.button(INTENT_LABELS[intent], key=f"{key}__{intent}", on_click=_fill,
                      args=(key, EXAMPLE_QUESTIONS[intent]))
    st.markdown(f"<div class='sc-muted'>{ASK_NOTICE}</div>", unsafe_allow_html=True)
    return str(st.session_state.get(key) or "")


def _render_answer(result: engine.Answer) -> None:
    if result.message:
        # 🔒 거절 · 되묻기 문장은 코드와 `sectors.yaml` 이름뿐이다
        st.info(result.message)
        if result.route.reason == "ambiguous":
            st.markdown("<div class='sc-muted'>어느 쪽인가 — "
                        + " · ".join(INTENT_LABELS[c] for c in result.choices)
                        + "</div>", unsafe_allow_html=True)
        return
    brief = result.brief
    if brief is None:
        return
    if result.notice:
        st.info(result.notice)
    if result.violations:
        st.error(GUARD_FAILED)
        with st.expander(f"어긋난 것 {len(result.violations)}건"):
            for line in result.violations:
                st.markdown(theme.html_line(f"• {theme.esc(line)}"), unsafe_allow_html=True)
        return

    as_of = f"{brief.as_of[:4]}-{brief.as_of[4:6]}-{brief.as_of[6:]}"
    st.caption(f"질문 — {INTENT_LABELS[brief.intent]} · 기준일 {as_of}")
    for sentence in brief.headline:
        st.markdown(sentence.text)
    for index, card in enumerate(brief.cards):
        with st.expander(card.title, expanded=index == 0):
            for slot, sentences in card.slots():
                st.markdown(f"**{slot}** — " + " ".join(s.text for s in sentences))
    if brief.attacks:
        st.dataframe(_attack_table(brief), hide_index=True, width="stretch")
    if brief.listing:
        with st.expander(f"구성 {len(brief.listing)}줄"):
            for sentence in brief.listing:
                st.markdown(f"- {sentence.text}")
    with st.expander(f"이 답이 채우지 못한 것(Gap Log) {len(brief.gaps)}건"):
        if brief.gaps:
            st.dataframe(_gap_table(brief), hide_index=True, width="stretch")
        else:
            # 🔒 0건도 적는다 — 비어 있다는 것 자체가 알릴 사실이다
            st.markdown("<div class='sc-muted'>0건 — 이 답에서 채우지 못한 것이 없다.</div>",
                        unsafe_allow_html=True)
        st.markdown(f"<div class='sc-muted'>{SEARCH_NOTICE}</div>", unsafe_allow_html=True)
    with st.expander(f"이 답이 쓴 근거 {len(brief.evidence)}건"):
        st.dataframe(_evidence_table(brief), hide_index=True, width="stretch")
    st.markdown(brief.disclaimer.text)
    st.caption("다음에 물어볼 것 — " + " · ".join(INTENT_LABELS[i] for i in brief.followups))


def _attack_table(brief):
    import pandas as pd

    return pd.DataFrame([{
        "반론": a.id, "질문": a.question, "겨냥": a.target, "범주": a.category,
        "데이터": a.defense, "정량": "있음" if a.quantitative else "없음",
        "판정": f"{a.status} — {STATUS_PLAIN[a.status]}", "걸린 조건": a.rule,
    } for a in brief.attacks])


def _gap_table(brief):
    import pandas as pd

    return pd.DataFrame([{
        "번호": g.id, "미확인 항목": g.item, "결측 유형": g.kind, "왜 못 채웠나": g.why,
        "채우려면": g.needed, "대체 처리": g.workaround, "영향받는 결론": g.affects,
        "구조적 근거": g.basis, "상태": g.state,
    } for g in brief.gaps])


def _evidence_table(brief):
    import pandas as pd

    return pd.DataFrame([{
        "ID": e.id, "무엇": e.label, "값": e.shown(), "출처": e.source, "신뢰도": e.confidence,
        "기준일": e.as_of or "", "메모": e.note,
    } for e in brief.evidence.values()])
