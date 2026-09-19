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

import html
from contextlib import contextmanager
from typing import Iterable, Iterator

import streamlit as st

from sector.workspace.links import display_link

__all__ = ["BROKEN_SCORES", "DATA_SOURCE", "DISCLAIMER", "setup", "header", "footer",
           "panel", "missing", "esc", "html_line", "user_block", "links_block", "failure"]

#: 🔒 약관이 정한 **의무 문자열**이다. 글자를 바꾸지 않는다.
#:    ("KRX 통계정보" · "출처: 한국거래소" 는 저장소 문서와 어긋난다)
DATA_SOURCE = "한국거래소 통계정보"
DISCLAIMER = "🔴 **과거 데이터의 요약이다. 투자 권유가 아니다.**"

#: 깨진 파생본을 만난 **모든 화면이 같은 말을 한다** (이슈 #12).
#: 🔒 **코드가 쓴 글**이다 — 사람이 입력한 글이 아니므로 `st.error` 로 나가도 된다.
#:    예외 내용은 `failure()` 가 escape 한 HTML 블록으로 따로 그린다 (ADR-SC-0012 ④).
#: 🔴 페이지마다 따로 적지 않는다. 검증이 `scored()` 안에만 있던 동안 페이지마다
#:    **보호 수준이 달랐고**, 문장을 각자 들고 있으면 그 상태가 눈에 띄지 않는다.
BROKEN_SCORES = "점수 표가 화면이 읽을 수 있는 모양이 아니다 — 파생본을 다시 만들어야 한다."

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

/* 코멘트 링크 — 주소가 길다. 🔒 줄을 못 바꾸게 두면 모달 폭을 밀어낸다.
   색은 config.toml 의 linkColor 가 이미 칠한다 — 여기서 다시 정하지 않는다 */
.sc-links { font-size: 0.857rem; line-height: 1.6; overflow-wrap: anywhere; }
/* 사람 글이 섞인 한 줄(이름 · 날짜) — 마크다운 목록 대신 HTML 블록이라 줄 간격만 준다 */
.sc-line  { line-height: 1.7; overflow-wrap: anywhere; }
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


# ── 사람이 쓴 글 ────────────────────────────────────────────────────────────
# 🔴 원장 읽기는 공개이고(ADR-SC-0011 ⑥), passcode 를 가진 사람은 RPC 로 앱의 쓰기
#    검사를 건너뛸 수 있다. 그래서 **쓸 때 검사**로는 모자라고 **그릴 때** 아래를 지난다.
#    조 이름 · 참가자 이름 · 사유 · 코멘트 · 원장에서 온 섹터 id · 저장소 오류 문장이 전부
#    대상이다(ADR-SC-0012 ④). `sectors.yaml` 에서 온 글은 커밋된 설정이라 대상이 아니다.
#
# 🔴🔴 **사람 글은 마크다운에 넣지 않는다 — 역슬래시 이스케이프로는 못 막는다** (Y9).
#    remark-gfm 은 이스케이프를 먼저 글자로 푼 **뒤** 그 글자에서 URL 을 찾는다. 그래서
#    이스케이프한 `http://192.168.0.1` 도 `www.evil.com` 도 살아 있는 링크가 되고, 인라인
#    `<span>` 으로 감싸도 태그 사이 글자는 똑같이 링크가 된다(2026-09-14 remark-gfm 으로
#    재현 · 리뷰에서 잡았다). **줄 전체가 `<div` 로 시작하는 HTML 블록**만 그 변환을 받지
#    않는다. 그래서 사람 글은 `html_line` · `user_block` · `links_block` 으로만 나간다.
#    🔒 위젯 라벨 · 모달 제목 · `st.success` · `st.error` 는 마크다운이다 — 사람 글을 넣지 않는다.


def esc(text: object) -> str:
    """사람이 쓴 글 **한 줄**을 HTML 글자로. 🔒 `html_line` 안에만 넣는다 — 마크다운에 넣지 않는다.

    줄바꿈은 공백으로 접는다. HTML 블록은 빈 줄에서 끝나고 그 뒤가 마크다운으로 샌다.
    """
    return html.escape(" ".join(str(text).splitlines()), quote=True)


def html_line(inner: str, cls: str = "sc-line") -> str:
    """한 줄짜리 **HTML 블록**. `st.markdown(…, unsafe_allow_html=True)` 에 넘긴다.

    🔒 `inner` 의 사람 글은 이미 `esc` 를 지나 있어야 한다. 태그(`<b>` 등)는 코드가 쓴다.
    🔴 줄이 둘이면 두 번째 줄부터 HTML 블록 밖(= 마크다운)이다 — 받지 않고 던진다.
    """
    if len(inner.splitlines()) > 1:
        raise ValueError("html_line 은 한 줄만 받는다 — 사람 글은 esc 를 지나게 한다")
    return f"<div class='{cls}'>{inner}</div>"


def failure(summary: str, exc: BaseException) -> None:
    """저장소 · 원장 오류. 🔴 오류 문장에 **원장의 글**이 섞일 수 있다(`event_id!r` 등).

    요약(코드가 쓴 글)은 `st.error` 로, 오류 내용은 HTML 블록으로 나눠 그린다.
    """
    st.error(summary)
    st.markdown(user_block(str(exc), "sc-muted"), unsafe_allow_html=True)


def user_block(text: object, cls: str = "sc-note") -> str:
    """사람이 쓴 여러 줄 글을 **HTML 블록 하나**로 — `st.markdown(…, unsafe_allow_html=True)` 에 넘긴다.

    🔴 Streamlit 1.63 은 `unsafe_allow_html` 마크다운을 **정화하지 않는다.** 번들의
       `StreamlitMarkdown` 이 DOMPurify 를 부르지 않고, 금지 요소 목록은 위젯 라벨에서만
       걸린다(2026-09-14 번들 확인 · 실브라우저 재현은 못 했다). 그러니
       `f"<div>{사유}</div>"` 는 `<iframe srcdoc=…>` 한 줄로 **팀원 브라우저에서 스크립트를
       돌릴 수 있는** 모양이다. 옛 확정 화면이 그 모양이었다.
    🔒 두 겹이다 —
       ① `html.escape` 가 `<`·`>`·`&`·따옴표를 글자로 바꾼다.
       ② **줄바꿈을 `<br>` 로 바꿔 한 줄로 만든다.** CommonMark 의 HTML 블록은 빈 줄에서
          끝나므로, 사유에 빈 줄이 있으면 그 뒤가 `<div>` 밖으로 나와 **마크다운으로**
          읽힌다 — ①을 지난 `![](…)` 이미지가 거기서 되살아난다.
    """
    lines = html.escape(str(text), quote=True).splitlines()
    return f"<div class='{cls}'>{'<br>'.join(lines)}</div>"


def links_block(links: Iterable[str]) -> str:
    """코멘트 링크. 🔒 **주소 자체를 보여준다** — 이름을 붙이게 하지 않는다(`links.display_link`).

    🔒 `rel="noopener noreferrer nofollow"` — 새 탭이 이 앱 창을 되조작하지 못하고
       (`noopener`), 상대 서버에 이 앱 주소가 넘어가지 않는다(`noreferrer`).
    🔴 여기 오는 링크는 `fold` 가 **다시 굳혀 본** 것뿐이다. 그래도 `html.escape` 를
       지난다 — 한 겹이 무너져도 다른 겹이 남게.
    """
    anchors = [
        f'<a href="{html.escape(link, quote=True)}" target="_blank" '
        f'rel="noopener noreferrer nofollow">{html.escape(display_link(link))}</a>'
        for link in links
    ]
    return f"<div class='sc-links'>🔗 {' · '.join(anchors)}</div>" if anchors else ""
