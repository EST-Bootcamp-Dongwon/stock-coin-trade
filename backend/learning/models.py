"""learning — 레슨 · 진행률 · 가이드 (E-05).

**모델 3종.** 가장 단순하지만 결함 D-2 와 확정 사항 3번이 여기서 해소된다.
"""

from django.db import models

from core.models import TimeStampedModel


class Lesson(TimeStampedModel):
    """투자분석 학습 레슨 14종.

    v1.0 은 `analysis.js` **650줄(단일 JS 최대)** 에 레슨 텍스트·예제 코드·데이터 표·
    렌더링 로직이 뒤엉켜 있었다. 그대로 옮기면 유지보수가 안 된다.

    `content`(jsonb) 구조 — v1.0 의 레슨 한 개가 갖던 구성 요소를 그대로 담는다::

        {"theory": [...3줄], "tip": "...", "code": "...python...",
         "sample_data": {...표...}, "explain": "...결과 해설..."}

    Django 관점 — 텍스트를 마크다운 파일로 두는 방법도 있다. DB 를 택한 이유는
    Django Admin 이 편집 화면을 공짜로 주기 때문이다. 파일이면 배포가 필요하다.
    **강사님이 수업 자료를 갱신할 때 코드 배포 없이 반영된다.**

    인터랙티브 3종(백테스트 성과 지표·시장 계절성·Pine Script 기초)은 DB 에 넣지 않는다.
    사용자가 값을 바꿀 때마다 즉시 반응해야 하는 계산이라 클라이언트 계산을 유지하고,
    여기에는 설명과 초기 파라미터만 둔다 (E-05 2.1).
    """

    key = models.SlugField(
        max_length=50, unique=True, verbose_name="레슨 키",
        help_text="macro-indicators 등. URL 과 진행률 이관에 쓴다",
    )
    group = models.CharField(
        max_length=30, db_index=True, verbose_name="그룹",
        help_text="매크로 / 산업 / 기본적 / 기술적 / 일자별 / 도구",
    )
    title = models.CharField(max_length=100, verbose_name="제목")
    order = models.PositiveSmallIntegerField(default=0, verbose_name="그룹 내 정렬")
    content = models.JSONField(default=dict, verbose_name="본문")
    max_points = models.PositiveSmallIntegerField(default=10, verbose_name="최대 획득 포인트")
    is_published = models.BooleanField(default=True, verbose_name="공개", help_text="초안 숨김")

    class Meta:
        db_table = "lesson"
        verbose_name = "학습 레슨"
        verbose_name_plural = "학습 레슨"
        ordering = ["group", "order"]

    def __str__(self):
        return f"[{self.group}] {self.title}"


class LearningProgress(models.Model):
    """레슨별 진행률. 전역 목표 100포인트 (v1.0 승계).

    ★ v1.0 은 `localStorage` 키 `edumgt-investment-academy-progress-v1` 에만 저장했다.
    기기를 바꾸면 사라지고, 브라우저 데이터를 지우면 사라지고,
    **대회 참가 조건으로 쓸 수 없었다.**

    ★ **`lesson_key` 문자열이 아니라 FK 로 둔 이유** (F-17 4.2 조정) —
    문자열 키는 레슨을 지우거나 이름을 바꾸면 진행률이 조용히 고아가 된다.
    FK 는 `PROTECT` 가 삭제를 막고, "총 진행률 / 총 가능 포인트" 집계가 조인 하나로 나온다.

    저장 시점 — 입력칸 변경·실행 버튼 클릭 시 디바운스 후 서버에 쓴다.
    HTMX 로는 `hx-trigger="change delay:1s"` 한 줄이다.
    """

    member = models.ForeignKey(
        "accounts.Member", on_delete=models.CASCADE, related_name="learning_progress"
    )
    lesson = models.ForeignKey(
        "learning.Lesson", on_delete=models.PROTECT, related_name="progress_rows"
    )
    progress_points = models.PositiveSmallIntegerField(default=0, verbose_name="획득 포인트")
    completed_at = models.DateTimeField(null=True, blank=True, verbose_name="완료 시각")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "learning_progress"
        verbose_name = "학습 진행률"
        verbose_name_plural = "학습 진행률"
        constraints = [
            models.UniqueConstraint(fields=["member", "lesson"], name="learning_progress_uniq"),
        ]

    def __str__(self):
        return f"{self.member} / {self.lesson} {self.progress_points}p"


class Guide(TimeStampedModel):
    """학습가이드 4종 — `kb-securities` · `kis-developers` · `tradingview-pine` · `pine-basics`.

    ★ **결함 D-2 해소** — v1.0 은 `/trade/pine-guide.html` 이 `common.js` 의
    `navGroups` 에 없어 **사실상 발견되지 않는 기능**이었다. v2.0 은 학습 콘텐츠를
    `/learn/` 아래로 통합하고 GNB 에 "학습" 메뉴로 정식 노출한다.
    `pine-basics` 가 다른 가이드와 나란히 이 테이블에 들어가는 것이 그 반영이다.

    ★ **`last_verified_at` 이 필요한 이유** — 외부 서비스의 UI 를 설명하는 문서라
    원본이 바뀌면 낡는다. 각 가이드 상단에 "2026-08-13 기준" 을 표시하고,
    6개월이 지나면 Admin 대시보드에 갱신 알림 배너를 띄운다.

    강사님이 실제로 이 자산을 계속 보강한다 — upstream `08a23a7` 이후에도
    `591086d`(TradingView Pine 확장) · `179b946`(KB증권 SVG) · `8798d1b`(KIS 모의투자
    SVG) 가 들어왔다. 낡은 채로 방치되지 않게 하는 장치다.
    """

    slug = models.SlugField(max_length=50, unique=True, verbose_name="URL 식별자")
    title = models.CharField(max_length=100, verbose_name="제목")
    content = models.TextField(blank=True, verbose_name="본문(마크다운)")
    last_verified_at = models.DateField(
        null=True, blank=True, verbose_name="최종 확인일",
        help_text="화면 상단에 'YYYY-MM-DD 기준' 으로 표시한다",
    )
    order = models.PositiveSmallIntegerField(default=0, verbose_name="정렬")
    is_published = models.BooleanField(default=True, verbose_name="공개")

    class Meta:
        db_table = "guide"
        verbose_name = "학습 가이드"
        verbose_name_plural = "학습 가이드"
        ordering = ["order", "slug"]

    def __str__(self):
        return self.title
