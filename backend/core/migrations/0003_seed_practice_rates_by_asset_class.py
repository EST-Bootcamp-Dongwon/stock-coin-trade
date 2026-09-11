"""연습 모드 요율을 **자산군별로** 나눈다 + 체결 주기 설정 추가 (F-03 6장 · F-20 잡 3).

★★ **0002 의 `practice.fee_bp` · `practice.tax_bp` 는 두 가지가 틀렸다** ─────

세션 6 이 넣은 시드는 이랬다::

    practice.fee_bp = 10      # 0.10%
    practice.tax_bp = 20      # 0.20%

**① 값이 대회 요율이다.** F-03 6장의 표는 연습 모드를 따로 정해 두었다:

| 모드 | 매매 수수료 | 매도세 | 근거 |
|---|---|---|---|
| 대회 | 10bp | 20bp | 타임폴리오 대회 규칙값 |
| **연습(주식)** | **1.5bp** (0.015%) | **18bp** (0.18%) | 실제 국내 위탁수수료 + 증권거래세·농특세 |
| **연습(코인)** | **5bp** (0.05%) | **없음** | 업비트 기준 |
| **연습(대체자산)** | **없음** | **없음** | v1.0 유지 — 증거금 개념만 |

대회 값을 연습에 그대로 쓰면 **연습에서 실제보다 7배 비싼 수수료**를 물게 되고,
코인·대체자산에는 있지도 않은 매도세가 붙는다.

**② 키가 하나라 자산군을 구분할 수 없다.** 위 표는 자산군마다 다른데 키가
`practice.fee_bp` 하나뿐이면 셋 중 하나만 맞출 수 있다.

그래서 **키를 자산군별로 나누고 값을 명세대로 넣는다.** 옛 키는 지운다 —
남겨 두면 "어느 쪽이 진짜인가"를 코드마다 다르게 답하게 된다.

★ **소수 bp 를 그대로 넣는다.** `AppSetting.value` 는 jsonb 라 `1.5` 가 들어간다.
  `Contest.fee_bp` 는 `PositiveSmallIntegerField` 라 정수만 되지만, 그건 대회
  요율이고 대회는 정수 bp 만 쓴다 (10 · 20). 서비스 계층은 양쪽을 `Decimal` 로
  받아 계산하므로 차이가 드러나지 않는다 (`trading/services.py` 의 `cost_rates`).
"""

from django.db import migrations

# (key, value, description)
NEW_SETTINGS = [
    # ── 연습 모드 요율 — 자산군별 (F-03 6장) ──────────────────────
    ("practice.stock.fee_bp", 1.5, "연습(주식) 매매 수수료(bp). 1.5bp = 0.015% — 실제 국내 위탁수수료"),
    ("practice.stock.tax_bp", 18, "연습(주식) 매도세(bp). 18bp = 0.18% — 증권거래세 + 농어촌특별세"),
    ("practice.crypto.fee_bp", 5, "연습(코인) 매매 수수료(bp). 5bp = 0.05% — 업비트 기준"),
    ("practice.crypto.tax_bp", 0, "연습(코인) 매도세. 코인에는 없다"),
    ("practice.alt.fee_bp", 0, "연습(대체자산) 수수료. v1.0 대로 없다 — 증거금 개념만 다룬다"),
    ("practice.alt.tax_bp", 0, "연습(대체자산) 매도세. 없다"),

    # ── 체결 추종 주기 (F-20 잡 3) ────────────────────────────────
    #
    # ★ 이 값이 **코드가 아니라 설정에 있는 이유** — 잡 3 을 어디서 돌릴지가
    #   아직 정해지지 않았고(변경노트 E-28), 실행처에 따라 감당할 수 있는 주기가
    #   다르다. 로컬 상시기동이면 5초, 서버리스 + pg_cron 이면 30초로 늦춘다.
    #   **체결 코드는 어느 쪽이든 고치지 않는다.**
    (
        "trading.match_interval_seconds",
        5,
        "미체결 주문 체결 추종 주기(초). `manage.py match_pending_orders --loop` 와 "
        "pg_cron 등록 SQL 이 함께 참조한다 (F-20 잡 3)",
    ),
]

# 0002 가 넣은 잘못된 키. 위 설명 참조.
RETIRED_KEYS = ["practice.fee_bp", "practice.tax_bp"]


def seed(apps, schema_editor):
    AppSetting = apps.get_model("core", "AppSetting")
    AppSetting.objects.bulk_create(
        [
            AppSetting(key=key, value=value, description=description)
            for key, value, description in NEW_SETTINGS
        ],
        # ★ 0002 와 같은 원칙 — 운영자가 이미 고쳐 둔 값을 재배포가 되돌리지 않는다.
        ignore_conflicts=True,
    )
    AppSetting.objects.filter(key__in=RETIRED_KEYS).delete()


def unseed(apps, schema_editor):
    """되돌리기 — 새 키를 지우고 옛 키를 되살린다.

    되살리는 값은 **0002 가 넣었던 그대로**다. 되돌리기가 다른 상태를 만들면
    마이그레이션을 앞뒤로 오갈 때마다 값이 달라진다.
    """
    AppSetting = apps.get_model("core", "AppSetting")
    AppSetting.objects.filter(key__in=[key for key, _, _ in NEW_SETTINGS]).delete()
    AppSetting.objects.bulk_create(
        [
            AppSetting(key="practice.fee_bp", value=10, description="연습 모드 매매 수수료(bp). 10bp = 0.10%"),
            AppSetting(key="practice.tax_bp", value=20, description="연습 모드 매도세(bp). 20bp = 0.20%"),
        ],
        ignore_conflicts=True,
    )


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0002_seed_app_settings"),
    ]

    operations = [
        migrations.RunPython(seed, unseed),
    ]
