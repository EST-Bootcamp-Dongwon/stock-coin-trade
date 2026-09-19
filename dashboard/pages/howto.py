"""읽는 법 — "이 화면을 어떻게 보나".

🔴 팀원 7명은 개발자가 아니다. 이 도구의 가장 큰 위험은 오독이고, 그 위험은
   기능이 아니라 **이 한 장**으로 줄인다.
"""

from __future__ import annotations

import streamlit as st

from dashboard import data, theme, view, weights
from dashboard.explain import (
    AXIS_MEANING, AXIS_NOT, AXIS_UNIT, axis_plain, narrative, preset_label,
)
from sector.scoring import AXES, AXIS_NAMES


def render() -> None:
    theme.header("읽는 법", "이 화면이 무엇을 말하고, 무엇을 말하지 않는가")
    try:
        st.subheader("한 문장으로")
        st.markdown(
            "**과거 데이터를 정해진 규칙으로 요약한 표다.** 어느 섹터가 최근에 어떤 모습이었는지를 "
            "네 가지 각도로 재서 줄 세운 것이고, 앞으로 어떻게 될지는 말하지 않는다."
        )

        st.subheader("네 가지 각도")
        for axis in AXES:
            with theme.panel(f"{AXIS_NAMES[axis]} ({axis})"):
                st.markdown(f"**묻는 것** — {AXIS_MEANING[axis]}")
                st.markdown(f"**단위** — {AXIS_UNIT[axis]}")
                st.markdown(f"🔴 **뜻하지 않는 것** — {AXIS_NOT[axis]}")

        st.subheader("σ 가 무엇인가")
        st.markdown(
            "같은 날 21개 섹터를 **서로 비교해** 얼마나 튀는지를 잰 값이다. "
            "`+1.0σ` 는 '다른 섹터들보다 뚜렷이 높다', `0σ` 는 '가운데', "
            "`-1.0σ` 는 '뚜렷이 낮다' 는 뜻이다. ±3.0σ 에서 자른다 — "
            "한 축이 총점을 통째로 지배하지 못하게 하기 위해서다."
        )

        st.subheader("예시 하나를 끝까지")
        _render_worked_example()

        st.subheader("가중치 셋을 동시에 보는 이유")
        st.markdown(
            "가중치를 바꾸면 **거의 아무 섹터나 1위로 만들 수 있다.** 그래서 셋을 나란히 둔다 — "
            "셋 다 상위인 섹터가 있다면 그것은 가중치에 기대지 않는 신호다."
        )
        for profile in ("balanced", "momentum", "contrarian"):
            st.markdown(f"- {preset_label(profile)}")

        st.subheader("믿지 말아야 할 때")
        st.markdown(
            "- **유동성 미달 배지** — ETF 거래가 너무 적어 실제로 사기 어렵다. 구성종목을 본다\n"
            "- **ETF 수가 1** — 그 섹터의 자금흐름이 ETF 한 종목에 통째로 달려 있다\n"
            "- **결측축 / 강등축** — 계산할 재료가 모자랐다는 표시다\n"
            "- **축수가 4 보다 작다** — 그만큼 적은 근거로 매긴 점수다. "
            "값이 없어서일 수도, 가중치를 0 으로 두어서일 수도 있다\n"
            "- **`—`** — 값이 없다는 뜻이다. 0 이 아니다. 이 도구는 빈 칸을 채우지 않는다"
        )

        st.subheader("이 도구가 하지 않는 것")
        st.markdown(
            "- 값을 지어내지 않는다. 못 받은 칸은 `—` 로 남는다\n"
            "- 뉴스를 요약하거나 '호재/악재' 를 붙이지 않는다\n"
            "- **무엇을 사라고 말하지 않는다.** 고르는 것은 사람이다"
        )
    finally:
        theme.footer()


def _render_worked_example() -> None:
    """**오늘 1위 섹터 하나를 처음부터 끝까지 따라간다.**

    🔴 축을 하나씩 설명하는 것만으로는 "그래서 이 순위가 어떻게 나왔나" 가 안 잡힌다.
       팀원이 실제로 묻는 것은 정의가 아니라 **경로**다.

    🔒 예시 숫자를 지어내지 않는다 — 오늘 실제 1위 섹터의 실제 값을 쓴다
       (ADR-SC-0007). 데이터를 못 읽으면 예시를 **빼고** 그 사실을 말한다.
       그럴듯한 가짜 철강 예시를 그리면, 팀원이 그 숫자를 실제로 인용한다.
    """
    try:
        frame, _ = data.load_scores()
    except data.DataUnavailable:
        st.markdown(
            "<div class='sc-muted'>지금은 점수를 읽을 수 없어 예시를 그리지 않는다. "
            "숫자를 지어내지 않기 때문이다.</div>", unsafe_allow_html=True)
        return
    except view.ViewError as exc:
        # 🔴 이제 **로드가** 던진다 (이슈 #12). 잡지 않으면 이 페이지에는 바깥 try 가
        #    없어 팀원이 파이썬 트레이스백을 본다 — 랭킹과 같은 규율이다
        theme.failure(theme.BROKEN_SCORES, exc)
        return

    names = data.sector_names()
    # 🔒 읽는 법 화면은 **언제나 균형 프리셋**이다. 랭킹의 슬라이더를 따라가면
    #    "네 걸음" 설명이 사람마다 다른 숫자 위에서 돌아간다
    balanced = weights.Weighting.preset("balanced")
    try:
        top = view.podium(view.scored(frame, balanced), weighting=balanced, top=1,
                          names=names)
        if not top:
            return
        sector_id = top[0]["sector_id"]
        story = view.sector_story(frame, sector_id, names=names)
    except view.ViewError as exc:
        # 🔴 랭킹과 같은 규율이다 — 깨진 파생본으로 **예시를 지어내지 않는다**
        #    🔒 경계(`load_scores`)가 이미 걸렀어도 여기를 지우지 않는다 — `scored()` 는
        #       경계가 못 보는 것(호출자가 거른 프레임)을 본다
        theme.failure(theme.BROKEN_SCORES, exc)
        return
    label = story["label"]

    st.markdown(
        f"오늘 1위인 **{label}**{_josa(label)} 어떻게 1위가 됐는지 "
        f"네 걸음으로 따라간다. 아래 숫자는 **예시가 아니라 지금 화면의 실제 값**이다."
    )

    with theme.panel("① 무슨 일이 있었나 — 원시값"):
        st.markdown("네 각도로 각각 **무슨 일이 있었는지** 먼저 본다. 단위가 다 다르다.")
        for part in story["parts"]:
            st.markdown(f"- **{AXIS_NAMES[part['axis']]}** — "
                        f"{axis_plain(part['axis'], part['raw_bp'])}")

    with theme.panel("② 단위를 맞춘다 — σ"):
        st.markdown(
            "`%p` 와 `%` 를 그냥 더할 수는 없다. 그래서 각 축을 **그날 21개 섹터 안에서 "
            "얼마나 튀는가**(σ)로 바꾼다. 이제 넷이 같은 자로 재진다."
        )

    with theme.panel("③ 가중치를 곱해 더한다"):
        st.markdown(
            "축마다 중요도가 다르다. σ 에 가중치를 곱해 더한 것이 총점이다. "
            "**아래 ④ 열을 세로로 더하면 총점이 정확히 나온다.**"
        )
        st.dataframe(
            view.arithmetic_table(frame, sector_id),
            width="stretch",
            column_config={
                "② σ": st.column_config.NumberColumn("② σ", format="%.2f"),
                "③ 가중치": st.column_config.NumberColumn("③ 가중치", format="%d"),
                "④ 기여(bp)": st.column_config.NumberColumn("④ 기여(bp)", format="%d"),
            },
        )
        total = story["score_bp"]
        if total is not None:
            parts_sum = sum(p["contribution_bp"] for p in story["parts"]
                            if p["contribution_bp"] is not None)
            st.markdown(f"**합계 {parts_sum:+d} bp = 총점 {total / 10000:+.2f}σ**")

    with theme.panel("④ 21개를 줄 세우면 순위"):
        st.markdown(f"같은 계산을 21개 섹터에 하고 총점 순으로 줄 세운 것이 "
                    f"랭킹 화면이다. **{label}**{_josa(label)} 그 줄의 "
                    f"**{story['rank']}번째**에 있다.")

    with theme.panel("🔴 그래서 무슨 뜻이 아닌가"):
        # 🔒 같은 섹터를 서술 함수로 한 번 더 말한다 — 랭킹 화면에서 볼 문장과
        #    **같은 문장**이라 팀원이 두 화면을 잇는다
        for line in narrative(**story):
            st.markdown(line)


def _josa(word: str) -> str:
    from dashboard.explain import josa

    return josa(word, "은는")
