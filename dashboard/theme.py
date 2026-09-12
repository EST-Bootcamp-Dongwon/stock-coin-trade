"""화면의 뼈대 — 폰트 · 색 · 머리글 · 푸터.

## 🔴 왜 Pretendard 인가

Inter·JetBrains Mono 에는 **한글 글리프가 없다.** 한글이 시스템 폰트로 떨어지면
라틴과 무게·베이스라인이 어긋나고, 그것이 한국어 대시보드가 "싸구려" 로 보이는
가장 큰 단일 원인이다. Pretendard 는 한글·라틴을 한 벌로 갖고 Inter 메트릭과
호환돼 라틴이 밀리지 않는다.

🔒 폰트를 못 받아도 화면은 살아 있어야 한다 — `font-family` 에 시스템 폰트를
   이어 둔다. CDN 이 막히는 것은 사고가 아니라 흔한 일이다.

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
@import url('https://cdn.jsdelivr.net/gh/orioncactus/pretendard@v1.3.9/dist/web/static/pretendard.css');

html, body, [class*="css"], .stMarkdown, .stDataFrame {
  font-family: Pretendard, -apple-system, BlinkMacSystemFont, 'Segoe UI',
               'Apple SD Gothic Neo', 'Malgun Gothic', sans-serif;
  font-feature-settings: "tnum" 1;   /* 숫자 폭을 고정 — 표가 흔들리지 않는다 */
}
/* 배경 단차로 패널을 가른다. 선을 줄이면 화면이 조용해진다 */
div[data-testid="stMetric"], .sc-panel {
  background: rgba(255,255,255,0.03);
  border-radius: 8px;
  padding: 14px 16px;
}
/* 등수 카드 — 🔴 숫자를 크게 두는 것이 요점이다. 표에서 1등을 눈으로 찾는 일이
   개발자가 아닌 팀원에게는 진입 장벽이다. 🔒 색으로 순위를 매기지 않는다 —
   금·은·동은 "좋다/나쁘다" 를 말하고, 이 도구는 그 말을 하지 않는다 */
.sc-rank { font-size: 1.9rem; font-weight: 700; line-height: 1.1;
           font-feature-settings: "tnum" 1; }
.sc-rank-name { font-size: 1.05rem; font-weight: 600; margin-bottom: 2px; }
.sc-muted { color: #8B93A1; font-size: 0.88rem; line-height: 1.5; }
.sc-note  { color: #8B93A1; font-size: 0.86rem; white-space: pre-wrap; }
.sc-warn  { color: #FF6B6B; font-weight: 600; }
section[data-testid="stSidebar"] { min-width: 260px; }  /* 한글이 220px 에서 줄바꿈된다 */
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
