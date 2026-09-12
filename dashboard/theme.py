"""화면의 뼈대 — 머리글 · 푸터 · 패널, 그리고 **남는 CSS**.

## 🔴 디자인의 정본은 이 파일이 아니다

폰트·색·모서리·타입스케일은 **`.streamlit/config.toml` 의 `[theme]`** 이 정한다.
여기 `_CSS` 에는 그쪽이 표현하지 못하는 것만 남는다.

그렇게 가른 이유는 취향이 아니다 —

1. 🔴 **`st.bar_chart` 는 Vega-Lite canvas 라 `<style>` 이 안으로 못 들어간다.**
   차트 색을 바꾸는 길은 `theme.chartCategoricalColors` 하나뿐이다. CSS 로
   디자인하려 들면 화면 절반(차트)은 끝까지 기본색으로 남는다.
2. 확정한 토큰이 **Django 쪽 Tailwind 로 그대로 넘어가야** 한다 (ADR-SC-0010 ⑦).
   두 곳에 흩어져 있으면 무엇을 베껴야 하는지 알 수 없다.

🔒 Pretendard 는 `[[theme.fontFaces]]` 가 받고, 시스템 폰트 폴백은 같은 파일의
   `theme.font` 쉼표 목록이 잇는다. **여기에 `@import` 를 되살리지 마라** —
   같은 폰트를 두 경로로 받게 되고 어느 쪽이 이겼는지 알 수 없어진다.

### 왜 Pretendard 인가

Inter·JetBrains Mono 에는 **한글 글리프가 없다.** 한글이 시스템 폰트로 떨어지면
라틴과 무게·베이스라인이 어긋나고, 그것이 한국어 대시보드가 "싸구려" 로 보이는
가장 큰 단일 원인이다. Pretendard 는 한글·라틴을 한 벌로 갖고 Inter 메트릭과
호환돼 라틴이 밀리지 않는다.

## 🔒 면책은 머리글, 출처는 푸터

면책을 푸터에 두면 읽히지 않는다. 이 도구의 가장 큰 위험은 오독이다.
출처("한국거래소 통계정보")는 **약관상 의무**라 `try/finally` 로 본문이 죽어도 남는다.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

import streamlit as st

__all__ = ["DATA_SOURCE", "DISCLAIMER", "setup", "header", "footer", "panel", "missing"]

#: 🔒 약관이 정한 **의무 문자열**이다. 글자를 바꾸지 않는다.
#:    ("KRX 통계정보" · "출처: 한국거래소" 는 저장소 문서와 어긋난다)
DATA_SOURCE = "한국거래소 통계정보"
DISCLAIMER = "🔴 **과거 데이터의 요약이다. 투자 권유가 아니다.**"

MISSING = "—"

_CSS = """
<style>
/* 🔒 여기에는 **`.streamlit/config.toml` 이 표현하지 못하는 것만** 남긴다.
   폰트·색·모서리·타입스케일의 정본은 그쪽이다. 같은 것을 두 곳에서 정하면
   어느 쪽이 이겼는지 아무도 모르게 되고, Django 쪽 Tailwind 로 토큰을 옮길 때
   무엇을 베껴야 하는지도 알 수 없어진다.
   🔒 Pretendard `@import` 를 여기 되살리지 마라 — `[[theme.fontFaces]]` 로 갔다. */

/* 숫자 폭 고정 — 자릿수가 바뀌어도 표·등수가 흔들리지 않는다.
   🔴 config.toml 에 대응하는 키가 없다. 그래서 여기 남는다. */
html, body, [class*="css"], .stMarkdown, .stDataFrame {
  font-feature-settings: "tnum" 1;
}

/* 본문 여백 12px 20px → 20px 32px (F-4 레이아웃). config 에 키가 없다 */
.block-container { padding: 20px 32px; }

/* 한글이 220px 에서 줄바꿈된다. 사이드바 폭도 config 가 못 정한다 */
section[data-testid="stSidebar"] { min-width: 260px; }

/* st.metric 은 기본으로 바탕이 없다 — **선이 아니라 배경 단차**로 패널처럼 세운다.
   🔒 색은 config.toml 의 secondaryBackgroundColor(2단)와 **같은 값**이다.
   모서리는 config 의 baseRadius 가 이미 먹는다 — 여기서 다시 정하지 않는다 */
div[data-testid="stMetric"] {
  background: #191D23;
  padding: 14px 16px;
}

/* 등수 카드 — 🔴 숫자를 크게 두는 것이 요점이다. 표에서 1등을 눈으로 찾는 일이
   개발자가 아닌 팀원에게는 진입 장벽이다. 🔒 색으로 순위를 매기지 않는다 —
   금·은·동은 "좋다/나쁘다" 를 말하고, 이 도구는 그 말을 하지 않는다.
   크기는 F-4 타입스케일(12/14/16/20/26/34)을 rem 으로 딴 것이다.
   🔒 baseFontSize=14 가 rem 의 기준이라, config 에서 그 값을 바꾸면 여기도 같이 움직인다 */
.sc-rank      { font-size: 2.143rem; font-weight: 700; line-height: 1.1; }   /* 30px */
.sc-rank-name { font-size: 1.143rem; font-weight: 600; margin-bottom: 2px; } /* 16px */

/* 보조 문구 — 🔒 색은 config.toml 의 grayColor·redColor 와 **같은 값**이다 */
.sc-muted { color: #8B93A1; font-size: 0.857rem; line-height: 1.5; }         /* 12px */
.sc-note  { color: #8B93A1; font-size: 0.857rem; white-space: pre-wrap; }
.sc-warn  { color: #FF6B6B; font-weight: 600; }
</style>
"""


def setup(page_title: str = "섹터 ETF 레이더") -> None:
    """페이지 설정 + CSS. 🔒 페이지마다 맨 처음에 한 번 부른다."""
    st.set_page_config(page_title=page_title, page_icon="📊", layout="wide")
    st.markdown(_CSS, unsafe_allow_html=True)


def header(title: str, subtitle: str = "") -> None:
    """제목과 **면책**. 면책은 머리글이다 (머리주석 참조)."""
    st.title(title)
    if subtitle:
        st.markdown(f"<div class='sc-muted'>{subtitle}</div>", unsafe_allow_html=True)
    st.warning(DISCLAIMER)


def footer(source_label: str = "") -> None:
    """출처 푸터 — **모든 화면 하단에 상시**. 약관상 의무다.

    🔒 아직 부르지 않는 원천(KIS · DART · 네이버)을 나열하지 않는다. v2.0 푸터가
       그렇게 해서 부르지도 않는 원천을 출처로 적고 있었다.
    """
    st.divider()
    st.caption(f"데이터 출처 — **{DATA_SOURCE}**")
    if source_label:
        st.caption(f"이 화면이 읽은 것 — {source_label}")


@contextmanager
def panel(title: str = "") -> Iterator[None]:
    with st.container(border=True):
        if title:
            st.markdown(f"**{title}**")
        yield


def missing(reason: str = "") -> str:
    """🔒 값이 없을 때 그리는 **유일한** 모양. 0 도 전일값도 아니다 (ADR-SC-0007)."""
    return f"{MISSING} <span class='sc-muted'>{reason}</span>" if reason else MISSING
