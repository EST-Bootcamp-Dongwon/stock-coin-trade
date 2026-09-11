"""섹터 ETF 레이더 — 도메인 패키지.

원천 취득(`sources/`) · 섹터 정의(`config/`) · 집계 · 스코어링이 여기 산다.
🔒 이 패키지는 **Streamlit 에 의존하지 않는다.** 화면은 `dashboard/`(M8) 가,
   배치는 `batch/`(M5·M6) 가 맡는다. 유일한 예외가 `secret_access` 인데,
   그쪽도 streamlit 을 **있으면 쓰고 없으면 넘긴다**(→ 그 모듈 머리주석).
"""
