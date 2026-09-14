"""내부 에이전트 엔진 — **Layer A · LLM 없음** (ADR-SC-0013).

    질문 → intent(라우터) → inventory(근거 장부) → redteam(반론) → compose(조립) → guard → 화면

🔒 이 패키지는 **읽기만 한다.** 원장에 쓰지 않고 네트워크를 부르지 않고 벽시계를 읽지 않는다.
   점수 계산 경로(`sector/` · `batch/`)는 이 패키지를 import 하지 않는다 — 되먹임이 없다는 것을
   `agent_test` 의 정적 검사가 고정한다(절대 제약 2 · 3).
"""
