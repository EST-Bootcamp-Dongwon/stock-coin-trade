"""읽는 법 — "이 화면을 어떻게 보나".

🔴 팀원 7명은 개발자가 아니다. 이 도구의 가장 큰 위험은 오독이고, 그 위험은
   기능이 아니라 **이 한 장**으로 줄인다.
"""

from __future__ import annotations

import streamlit as st

from dashboard import theme
from dashboard.explain import AXIS_MEANING, AXIS_NOT, AXIS_UNIT, preset_label
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
