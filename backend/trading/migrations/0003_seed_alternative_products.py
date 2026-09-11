"""대체자산 카탈로그 11종 시드 (05 문서 5.1 · E-03 7장).

v1.0 은 `python-stock-backend/alternatives.py` 의 `CATALOG` **파이썬 상수**였다.
테이블로 옮겨 운영자가 Admin 에서 고칠 수 있게 한다.

**없으면 대체자산 연습 모드가 통째로 동작하지 않는다.** 그래서 운영 환경에도 들어가는
"필수 기준 데이터"이고, management command 가 아니라 데이터 마이그레이션이다.

`volatility_pct` 는 v1.0 `_quote()` 의 진폭 계수를 옮긴 것이다::

    rate = round(wave * (0.018 if category in {"옵션", "파생상품"} else 0.009), 2)
                          └ 1.8%                                      └ 0.9%
"""

from decimal import Decimal

from django.db import migrations

# (symbol, name, category, base_price, multiplier, unit_label, margin_rate_pct,
#  point_scale, actual_multiplier, map_lat, map_lng, map_label, description)
CATALOG = [
    ("FUT-K200", "KOSPI 200 선물", "선물", 372_500, 1, "1계약", 15,
     1_000, 250_000, None, None, "", "KOSPI 200 지수 선물 축소 모의계약"),
    ("FUT-USD", "미국 달러 선물", "선물", 13_850, 10, "10 USD", 12,
     None, None, None, None, "", "원/달러 환율 선물 모의계약"),
    ("OPT-K200-C", "KOSPI 200 콜옵션", "옵션", 12_800, 25, "1계약", 100,
     None, None, None, None, "", "상승 전망을 연습하는 콜옵션"),
    ("OPT-K200-P", "KOSPI 200 풋옵션", "옵션", 10_400, 25, "1계약", 100,
     None, None, None, None, "", "하락 위험 헤지를 연습하는 풋옵션"),
    ("DRV-LEV", "KOSPI 200 레버리지", "파생상품", 18_450, 1, "1좌", 100,
     None, None, None, None, "", "지수 수익률 2배 추종형 모의 ETN"),
    ("DRV-INV", "KOSPI 200 인버스", "파생상품", 7_920, 1, "1좌", 100,
     None, None, None, None, "", "지수 하락 방향 모의 ETN"),
    ("MET-GOLD", "금 (순금 99.99%)", "금", 178_300, 1, "1g", 100,
     None, None, None, None, "", "국내 금 현물 기준 모의가격"),
    ("MET-SILVER", "은 (99.9%)", "은", 2_480, 1, "1g", 100,
     None, None, None, None, "", "국내 은 현물 기준 모의가격"),
    ("RE-SEOUL", "서울 강남 아파트 지분", "부동산", 12_850_000, 1, "1구좌", 100,
     None, None, "37.497900", "127.027600", "서울 강남구",
     "전용 84㎡ 대표 단지 시세를 분할한 교육용 지분"),
    ("RE-PANGYO", "판교 아파트 지분", "부동산", 9_720_000, 1, "1구좌", 100,
     None, None, "37.394700", "127.111200", "경기 성남시 판교",
     "전용 84㎡ 대표 단지 시세를 분할한 교육용 지분"),
    ("RE-BUSAN", "부산 해운대 아파트 지분", "부동산", 6_380_000, 1, "1구좌", 100,
     None, None, "35.163100", "129.163600", "부산 해운대구",
     "전용 84㎡ 대표 단지 시세를 분할한 교육용 지분"),
]

# 옵션·파생상품은 진폭이 2배다 (v1.0 `_quote()` 승계)
HIGH_VOLATILITY_CATEGORIES = {"옵션", "파생상품"}

SYMBOLS = [row[0] for row in CATALOG]


def seed_products(apps, schema_editor):
    """대체자산 11종을 넣는다.

    ★ Django 관점 — 데이터 마이그레이션에서는 **반드시 `apps.get_model()`** 로 모델을
    얻는다. `from trading.models import AlternativeProduct` 처럼 직접 import 하면
    "이 마이그레이션 시점의 모델"이 아니라 "현재 코드의 모델"을 쓰게 된다.
    나중에 필드를 하나 추가하는 순간 과거 마이그레이션이 통째로 깨진다.

    FastAPI + Alembic 에서도 같은 이유로 데이터 마이그레이션에 ORM 모델을 쓰지 말고
    `sa.table(...)` 로 그 시점의 스키마를 다시 적으라고 했던 것과 같은 규칙이다.
    Django 는 `apps` 인자가 그 일을 대신해 준다.

    `ignore_conflicts=True` 로 **이미 있는 행은 건드리지 않는다** — 운영자가 Admin 에서
    기준가를 고쳤는데 재배포가 되돌려 놓으면 안 된다.
    """
    AlternativeProduct = apps.get_model("trading", "AlternativeProduct")

    rows = []
    for order, (symbol, name, category, base_price, multiplier, unit_label,
                margin_rate, point_scale, actual_multiplier,
                lat, lng, map_label, description) in enumerate(CATALOG, start=1):
        rows.append(AlternativeProduct(
            symbol=symbol,
            name=name,
            category=category,
            description=description,
            base_price=base_price,
            multiplier=multiplier,
            unit_label=unit_label,
            margin_rate_pct=Decimal(margin_rate),
            volatility_pct=(
                Decimal("1.8") if category in HIGH_VOLATILITY_CATEGORIES else Decimal("0.9")
            ),
            point_scale=point_scale,
            actual_multiplier=actual_multiplier,
            map_lat=Decimal(lat) if lat else None,
            map_lng=Decimal(lng) if lng else None,
            map_label=map_label,
            is_active=True,
            sort_order=order * 10,   # 사이에 끼워 넣을 자리를 남긴다
        ))

    AlternativeProduct.objects.bulk_create(rows, ignore_conflicts=True)


def unseed_products(apps, schema_editor):
    """되돌리기.

    **`.all().delete()` 를 쓰지 않는다.** 운영자가 Admin 에서 추가한 상품까지
    지워 버리기 때문이다. 이 마이그레이션이 넣은 11개만 심볼로 콕 집어 지운다.

    되돌리기를 짝으로 넣지 않으면 `migrate trading 0002` 가 실패한다 (05 문서 5.1).
    """
    AlternativeProduct = apps.get_model("trading", "AlternativeProduct")
    AlternativeProduct.objects.filter(symbol__in=SYMBOLS).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("trading", "0002_alternativeproduct_description_maplabel"),
    ]

    operations = [
        migrations.RunPython(seed_products, unseed_products),
    ]
