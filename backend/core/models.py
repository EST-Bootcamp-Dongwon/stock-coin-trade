"""core — 배치 이력 · 감사 로그 · 설정 · 공통 추상모델 (E-07).

모델 3종 + 추상 모델. 다른 앱이 참조하지만 **core 는 아무것도 참조하지 않는다**
(규약 1.1 의존 방향). `AdminAuditLog.actor` 만 예외적으로 회원을 가리키는데,
이건 `settings.AUTH_USER_MODEL` 문자열이라 앱 의존이 생기지 않는다.
"""

from django.conf import settings
from django.db import models

from core.constants import SyncStatus, TriggeredBy


class TimeStampedModel(models.Model):
    """생성·수정 시각을 자동 기록한다. 대부분의 모델이 이걸 상속한다.

    Django 관점 — `abstract = True` 면 이 클래스로는 테이블이 생기지 않고,
    상속한 자식 테이블에 컬럼만 복사된다. SQLAlchemy 의 declarative mixin 과 같은 역할이다.

    **캐시 테이블은 상속하지 않는다** — `fetched_at` / `expires_at` 이 이미 있고,
    매 갱신마다 `updated_at` 을 함께 쓰는 것은 낭비다 (규약 7장).
    """

    created_at = models.DateTimeField(auto_now_add=True, verbose_name="생성 시각")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="수정 시각")

    class Meta:
        abstract = True   # ← 이게 없으면 core_timestampedmodel 테이블이 진짜 생긴다


class DataSyncLog(models.Model):
    """배치 실행 이력 — 관측성.

    v1.0 스케줄러는 실패 시 로그만 남겼다. 서버리스에서는 그 로그를 보기도 어렵다.
    Django Admin 대시보드가 이 테이블을 읽어 최근 실패 잡을 경고 배너로 띄운다.
    "조용히 실패하는 배치" 를 없애는 게 목적이다.
    """

    job_name = models.CharField(max_length=40, db_index=True, verbose_name="잡 이름")
    started_at = models.DateTimeField(db_index=True, verbose_name="시작 시각")
    finished_at = models.DateTimeField(null=True, blank=True, verbose_name="종료 시각")
    status = models.CharField(
        max_length=10, choices=SyncStatus.choices, default=SyncStatus.RUNNING, verbose_name="상태"
    )
    rows_affected = models.IntegerField(default=0, verbose_name="처리 행 수")
    error = models.TextField(blank=True, verbose_name="스택트레이스")
    triggered_by = models.CharField(
        max_length=10, choices=TriggeredBy.choices, default=TriggeredBy.CRON, verbose_name="실행 주체"
    )

    class Meta:
        db_table = "data_sync_log"
        verbose_name = "배치 실행 이력"
        verbose_name_plural = "배치 실행 이력"
        indexes = [
            # 잡별 최근 실행 — 대시보드가 잡마다 마지막 1건씩 뽑는다
            models.Index(fields=["job_name", "-started_at"], name="sync_log_idx_job_recent"),
            # 실패만 훑기 (부분 인덱스)
            models.Index(
                fields=["-started_at"],
                name="sync_log_idx_failed",
                condition=models.Q(status="FAILED"),
            ),
        ]

    def __str__(self):
        return f"{self.job_name} [{self.status}] {self.started_at:%Y-%m-%d %H:%M}"


class AdminAuditLog(models.Model):
    """운영자 조치 감사 로그.

    **운영자가 대회 데이터를 손대는 일은 반드시 기록되어야 한다.**
    순위에 영향을 주는 조작이 흔적 없이 일어나면 안 된다.

    Django 관점 — Django 에는 `django.contrib.admin.models.LogEntry` 가 이미 있다.
    그걸 쓰지 않는 이유는 ① 변경 전후 값을 담지 않고 ② **사유를 강제할 수 없으며**
    ③ Admin 밖(정산 재실행 API 등)의 조치를 못 잡기 때문이다.
    """

    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,      # 조치한 사람을 지울 수 없게 한다
        related_name="audit_logs",
        verbose_name="조치자",
    )
    action = models.CharField(
        max_length=40, db_index=True, verbose_name="조치",
        help_text="DISQUALIFY / RESTORE / RESETTLE / RULE_CHANGE / ORDER_EDIT",
    )
    target_model = models.CharField(max_length=40, verbose_name="대상 모델")
    target_id = models.CharField(
        max_length=40, verbose_name="대상 PK",
        help_text="StockMaster 처럼 문자열 PK 인 모델도 있어서 varchar 로 둔다",
    )
    before = models.JSONField(null=True, blank=True, verbose_name="변경 전")
    after = models.JSONField(null=True, blank=True, verbose_name="변경 후")
    reason = models.CharField(
        max_length=200, verbose_name="사유",
        help_text="실격·주문수정·정산재실행·규칙변경은 사유 입력이 필수다",
    )
    ip = models.GenericIPAddressField(null=True, blank=True, verbose_name="IP")
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        db_table = "admin_audit_log"
        verbose_name = "운영자 감사 로그"
        verbose_name_plural = "운영자 감사 로그"
        indexes = [
            models.Index(fields=["target_model", "target_id"], name="audit_idx_target"),
        ]

    def __str__(self):
        return f"{self.action} {self.target_model}#{self.target_id}"


class AppSetting(models.Model):
    """런타임 설정값 — 하드코딩 제거.

    `ai.daily_quota` · `ai.model_basic` · `webfetch.daily_quota` ·
    `portfolio.advice_thresholds` · `practice.fee_bp` · `openapi.rate_limit_per_min` 등.

    **대회 요율은 여기 없다.** `Contest.fee_bp` / `tax_bp` 로 대회마다 다르게 준다.
    전역 설정으로 두면 진행 중인 대회의 규칙이 바뀌어 버린다.
    """

    key = models.CharField(max_length=50, unique=True, verbose_name="키")
    value = models.JSONField(verbose_name="값")
    description = models.CharField(max_length=200, blank=True, verbose_name="설명")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "app_setting"
        verbose_name = "앱 설정"
        verbose_name_plural = "앱 설정"
        ordering = ["key"]

    def __str__(self):
        return self.key


class CronJobRunDetail(models.Model):
    """pg_cron 의 실행 이력 (읽기 전용).

    Django 관점 — `managed = False` 는 "이 테이블의 마이그레이션을 만들지 마라" 는 뜻이다.
    이미 존재하는 테이블을 ORM 으로 읽기만 할 때 쓴다.

    우리 잡의 성공·실패 판정은 `DataSyncLog` 를 본다. `cron.job_run_details` 는
    "HTTP 호출 자체가 나갔는가"만 알려주므로, **호출은 성공했는데 잡 내부가 실패한 경우**를
    구분하지 못한다. 두 테이블을 나란히 보여주는 게 Admin 화면의 역할이다.

    로컬 개발 DB 에는 pg_cron 이 없다. 이 모델을 조회하면 에러가 나는 게 정상이고,
    Admin 에서도 운영 환경에서만 노출한다.
    """

    jobid = models.BigIntegerField(primary_key=True)
    runid = models.BigIntegerField()
    command = models.TextField()
    status = models.TextField()
    return_message = models.TextField()
    start_time = models.DateTimeField()
    end_time = models.DateTimeField(null=True)

    class Meta:
        managed = False                          # 마이그레이션을 만들지 않는다
        db_table = 'cron"."job_run_details'      # 스키마 포함 인용 표기
        verbose_name = "pg_cron 실행 이력"
        verbose_name_plural = "pg_cron 실행 이력"
