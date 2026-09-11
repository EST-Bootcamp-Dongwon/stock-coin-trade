"""투자분석 레슨 13종 · 학습가이드 4종 시드 (05 문서 5.1 · E-05).

★ **명세는 "14레슨"이지만 실제는 13레슨이다** ────────────────────────────
[F-17](docs/.../F-17-학습콘텐츠.md) 2장은 "투자분석 학습 14레슨"이라고 적었으나,
같은 문서의 커리큘럼 표와 v1.0 `frontend/js/analysis.js` 의 `lessons` 배열을
세어 보면 **13개**다:

    매크로 2 + 산업 2 + 기본적 3 + 기술적 4 + 일자별 1 + 도구 1 = 13

**코드를 정본으로 본다.** 없는 레슨을 지어내면 시드가 거짓이 되고,
`LearningProgress` 의 "총 가능 포인트" 집계가 처음부터 틀린다.
→ 06-기능명세-정합성-변경노트 E-12 에 기록. 다음 버전업 때 F-17·E-05 를 고친다.

★ **본문은 손으로 옮기지 않았다** ────────────────────────────────────────
`data/lessons_v1_2026-08-13.json` 은 `analysis.js` 의 `lessons` 배열을 **기계적으로
추출**한 것이다. 이론 3줄·팁·파이썬 예제·데이터 표·결과 카드가 원문 그대로다.
파일명에 날짜를 박은 이유는 **이 마이그레이션이 얼린 시점의 사본**임을 드러내기
위해서다. 강사님이 나중에 `analysis.js` 를 고쳐도 이 파일은 바뀌지 않는다.

★ **포인트 배분** — 전역 목표 100포인트(v1.0 `globalProgressTarget`) 승계.
본문이 있는 11레슨 × 8p + 인터랙티브 2레슨 × 6p = **정확히 100**.
"""

import json
from pathlib import Path
from datetime import date

from django.db import migrations

# 마이그레이션 파일 옆의 data/ 에서 읽는다.
# `BASE_DIR` 같은 설정에 기대지 않는다 — 마이그레이션은 어느 작업 디렉터리에서든 돌아야 한다.
LESSON_FIXTURE = Path(__file__).resolve().parent / "data" / "lessons_v1_2026-08-13.json"

# 학습가이드 4종 (E-05 4장)
# `last_verified_at` 은 **강사님이 그 파일을 마지막으로 고친 커밋 날짜**다.
# 지어낸 날짜를 넣으면 "6개월 지나면 갱신 배너" 장치가 처음부터 거짓말을 한다.
GUIDES = [
    ("kb-securities", "KB증권 모의투자 시작하기", date(2026, 8, 11), 10),
    ("kis-developers", "한국투자증권 KIS Developers 신청", date(2026, 8, 11), 20),
    ("tradingview-pine", "TradingView Pine 전략 연동", date(2026, 8, 11), 30),
    # ★ 결함 D-2 해소 — v1.0 은 /trade/pine-guide.html 이 common.js 의 navGroups 에
    #   없어 **사실상 발견되지 않는 기능**이었다. 다른 가이드와 나란히 등록해
    #   GNB "학습" 메뉴에 정식 노출한다 (E-05 5장).
    ("pine-basics", "Pine Script 기초 안내", date(2026, 8, 11), 40),
]

GUIDE_SLUGS = [row[0] for row in GUIDES]


def _load_lessons() -> list[dict]:
    return json.loads(LESSON_FIXTURE.read_text(encoding="utf-8"))


def seed_lessons_and_guides(apps, schema_editor):
    """레슨 13종 + 가이드 4종을 넣는다.

    ★ Django 관점 — `TimeStampedModel` 을 상속한 모델이라 `created_at` 이
    `auto_now_add=True` 다. **`bulk_create` 에서도 `auto_now_add` 는 정상 동작한다**
    (`pre_save` 가 인스턴스마다 호출된다). `bulk_update` 에서는 `auto_now` 가
    동작하지 않는다는 것과 헷갈리기 쉬운 지점이다.

    `ignore_conflicts=True` — `Lesson.key` · `Guide.slug` 에 UNIQUE 가 걸려 있어
    **이미 있는 행은 건드리지 않는다.** 운영자가 Admin 에서 레슨 문구를 고쳤는데
    재배포가 되돌려 놓으면 안 된다 (콘텐츠를 DB 로 뺀 목적 자체가 사라진다).

    `Guide.content` 는 **비워 둔다.** 원본이 HTML 3종 합계 55KB 라 마크다운 변환이
    별도 작업이다. `is_published=False` 로 넣어 **본문 없는 가이드가 화면에 노출되는
    사고를 막는다** — 변환을 마친 뒤 Admin 에서 공개로 바꾼다.
    """
    Lesson = apps.get_model("learning", "Lesson")
    Guide = apps.get_model("learning", "Guide")

    Lesson.objects.bulk_create(
        [
            Lesson(
                key=row["key"],
                group=row["group"],
                title=row["title"],
                order=row["order"],
                content=row["content"],
                max_points=row["max_points"],
                is_published=True,
            )
            for row in _load_lessons()
        ],
        ignore_conflicts=True,
    )

    Guide.objects.bulk_create(
        [
            Guide(
                slug=slug,
                title=title,
                content="",                    # 마크다운 변환은 별도 작업
                last_verified_at=verified_at,
                order=order,
                is_published=False,            # ★ 본문이 들어오기 전까지 숨긴다
            )
            for slug, title, verified_at, order in GUIDES
        ],
        ignore_conflicts=True,
    )


def unseed_lessons_and_guides(apps, schema_editor):
    """되돌리기.

    ★ **레슨은 `LearningProgress` 가 `PROTECT` 로 붙잡고 있다.** 회원이 이미 학습한
    레슨을 지우려 하면 `ProtectedError` 가 난다 — 그게 맞는 동작이다(진행률이 조용히
    고아가 되는 것을 막는 게 `PROTECT` 의 목적이다).

    되돌리기가 여기서 멈추면 **먼저 진행률을 어떻게 할지 정하라**는 신호다.
    시드 마이그레이션이 마음대로 남의 학습 기록을 지워서는 안 된다.
    """
    Lesson = apps.get_model("learning", "Lesson")
    Guide = apps.get_model("learning", "Guide")

    keys = [row["key"] for row in _load_lessons()]
    Lesson.objects.filter(key__in=keys).delete()
    Guide.objects.filter(slug__in=GUIDE_SLUGS).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("learning", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(seed_lessons_and_guides, unseed_lessons_and_guides),
    ]
