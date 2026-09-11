"""앱 설정 기본값 시드 (05 문서 5.1 · E-07 4장).

**하드코딩 제거가 목적이다.** v1.0 은 AI 모델명·쿼터·수수료율이 코드 곳곳에
문자열로 박혀 있어, 바꾸려면 재배포해야 했다.

여기 없는 것 — **대회 요율**(`fee_bp` · `tax_bp`)은 `contests.Contest` 의 컬럼이다.
전역 설정으로 두면 값을 고치는 순간 **진행 중인 대회의 규칙이 바뀌어 버린다.**
이 시드에 `practice.*` 만 있고 `contest.*` 가 없는 이유다.
"""

from django.db import migrations

# (key, value, description)
#
# `value` 는 jsonb 다. 스칼라도 JSON 값이라 그대로 들어간다 —
# Postgres 의 jsonb 는 숫자·불리언·문자열 단독도 유효한 문서로 본다.
SETTINGS = [
    # ── AI 분석 (F-14) ────────────────────────────────────────────
    ("ai.daily_quota", 20, "회원 1인당 AI 분석 1일 한도 (실패 호출은 차감하지 않는다)"),
    ("ai.model_basic", "claude-haiku-4-5-20251001", "기본 분석 모델명 — 코드에 하드코딩하지 않는다"),
    ("ai.model_deep", "claude-sonnet-5", "심층 분석 모델명. 쿼터를 더 크게 차감한다"),
    ("ai.deep_quota_cost", 3, "심층 분석 1회가 차감하는 쿼터 수"),

    # ── 웹 데이터 수집기 (F-18) ───────────────────────────────────
    ("webfetch.daily_quota", 50, "회원 1인당 외부 URL 수집 1일 한도"),
    ("webfetch.max_bytes", 2_097_152, "응답 크기 상한(2MB). 초과하면 SIZE 사유로 차단한다"),

    # ── 포트폴리오 조언 임계값 (F-09 4.2) ─────────────────────────
    (
        "portfolio.advice_thresholds",
        {
            "single_position_pct": 30.0,   # 한 종목이 이 비중을 넘으면 집중 경고
            "cash_ratio_low_pct": 5.0,     # 현금이 이보다 적으면 유동성 경고
            "cash_ratio_high_pct": 70.0,   # 현금이 이보다 많으면 미운용 안내
            "loss_alert_pct": -15.0,       # 평가손실이 이보다 크면 점검 안내
        },
        "규칙 기반 포트폴리오 조언 임계값 4종. AI 호출 없이 계산한다",
    ),

    # ── 연습 모드 요율 (F-03 6장) ─────────────────────────────────
    ("practice.fee_bp", 10, "연습 모드 매매 수수료(bp). 10bp = 0.10%"),
    ("practice.tax_bp", 20, "연습 모드 매도세(bp). 20bp = 0.20%"),

    # ── Open API (F-13 3장) ───────────────────────────────────────
    ("openapi.rate_limit_per_min", 60, "API 키당 분당 호출 한도 (고정 윈도우)"),

    # ── 학습 (F-17) ───────────────────────────────────────────────
    ("learning.total_target_points", 100, "학습 전역 목표 포인트. v1.0 승계"),

    # ── 가이드 신선도 (E-05 4.1) ──────────────────────────────────
    ("guide.stale_after_days", 180, "가이드 최종 확인일이 이만큼 지나면 Admin 에 갱신 배너를 띄운다"),
]

KEYS = [row[0] for row in SETTINGS]


def seed_settings(apps, schema_editor):
    """기본 설정값을 넣는다.

    ★ **이미 있는 키는 덮어쓰지 않는다.** 운영자가 Admin 에서 쿼터를 20 → 50 으로
    올려 뒀는데 재배포가 20 으로 되돌리면, "고쳐도 자꾸 원복된다"는 최악의 버그가 된다.
    `ignore_conflicts=True` 가 그 방어선이다 — `key` 에 UNIQUE 가 걸려 있어 동작한다.
    """
    AppSetting = apps.get_model("core", "AppSetting")
    AppSetting.objects.bulk_create(
        [
            AppSetting(key=key, value=value, description=description)
            for key, value, description in SETTINGS
        ],
        ignore_conflicts=True,
    )


def unseed_settings(apps, schema_editor):
    """되돌리기 — 이 마이그레이션이 넣은 키만 지운다.

    운영자가 나중에 추가한 다른 키는 건드리지 않는다.
    """
    apps.get_model("core", "AppSetting").objects.filter(key__in=KEYS).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(seed_settings, unseed_settings),
    ]
