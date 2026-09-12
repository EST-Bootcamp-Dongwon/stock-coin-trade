"""섹터 ETF 레이더 — 앱 진입점.

## 🔒 이 파일은 저장소 루트에 있어야 한다. 하위 폴더로 옮기지 마라.

Streamlit Cloud 는 의존성 파일을 ① 엔트리포인트가 있는 디렉터리 → ② 저장소 루트
순으로 찾고 **처음 만난 것 하나만** 쓴다(확정 사실 V20). 이 파일이 루트에 있으면
두 후보가 한 곳으로 붕괴해 루트 `requirements.txt` 가 확실히 선택된다. 하위 폴더로
옮기는 순간 그 폴더의 의존성 파일이 루트를 가리고, `sys.path` 도 달라져
`import sector` 가 깨진다.

## 🔒 `st.navigation` 을 쓴다

루트 `pages/` 방식은 파일명이 사이드바와 URL 에 그대로 노출된다. 팀원 7명은
개발자가 아니고 `01_ranking.py` 같은 이름을 볼 이유가 없다.

## 🔒 페이지가 죽어도 출처는 남는다

각 페이지가 `try/finally` 로 `theme.footer()` 를 부른다. "한국거래소 통계정보"
표시는 약관상 **의무**라 본문이 예외로 죽어도 빠지면 안 된다.
"""

import streamlit as st

from dashboard import theme
from dashboard.pages import confirm, howto, ranking, teams

# 🔒 `set_page_config` 는 다른 st 명령보다 먼저다. 그래서 navigation 앞에 둔다.
theme.setup()

# 🔒 `url_path` 를 **명시한다.** 주지 않으면 Streamlit 이 callable 이름에서
#    추론하는데 네 페이지가 전부 `render` 라 경로가 충돌해 앱이 뜨지 않는다
#    (2026-09-12 AppTest 로 잡았다 — 배포 후에 알았으면 흰 화면이었다).
st.navigation([
    st.Page(ranking.render, title="섹터 랭킹", icon="📊", url_path="ranking", default=True),
    st.Page(teams.render, title="조", icon="👥", url_path="teams"),
    st.Page(confirm.render, title="섹터 확정", icon="✅", url_path="confirm"),
    st.Page(howto.render, title="읽는 법", icon="📖", url_path="howto"),
]).run()
