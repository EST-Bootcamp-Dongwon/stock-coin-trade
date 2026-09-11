"""값 타입 규약을 한 곳에 모은다. 개별 모델은 여기서만 가져다 쓴다.

같은 `max_digits` 를 44개 모델에 손으로 적으면 반드시 어긋난다 (규약 2.1).

Django 관점 — 마이그레이션은 `QTY` 라는 이름이 아니라 **풀린 값**
(`max_digits=28, decimal_places=8`)을 기록한다. 나중에 `QTY` 를 바꾸면
그때 `makemigrations` 가 변경을 감지한다.
FastAPI + Alembic 에서 타입 별칭을 쓰던 감각과 같다.

원화 금액에 `DecimalField` 를 쓰지 않는 이유는 규약 2.2 참조 —
수수료·세금을 원 단위 floor 절사하기로 확정해 소수 원이 생기지 않는다.
금액은 전부 `BigIntegerField` 다.
"""

# 수량 — 주식은 정수값이 들어가고, 코인이 소수점 8자리를 쓴다.
# 통합 테이블(`Order` · `Position`)이라 넓은 쪽에 맞춘다.
QTY = dict(max_digits=28, decimal_places=8)

# 단가 — 주식 원 단위 · 코인 소수 · 대체자산 원 단위를 모두 담는다.
PRICE = dict(max_digits=20, decimal_places=8)

# 비율(%) — -99999.9999 ~ 99999.9999.
# DB 에는 항상 퍼센트 값(15.0)을 넣는다. 비율(0.15)이 아니다 (규약 4.2).
PCT = dict(max_digits=9, decimal_places=4)

# 기준가(NAV) — 대회 시작 자본을 1000 으로 놓은 지수.
NAV = dict(max_digits=12, decimal_places=4)

# HHI(허핀달 지수) — 0~1 사이라 소수부를 더 넓게 잡는다 (E-02 9장).
HHI = dict(max_digits=9, decimal_places=6)
