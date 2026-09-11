# E-07. `core` — 배치 이력 · 감사 로그 · 설정

> **문서 버전** v2.0 · **작성일** 2026-08-13 (KST) · **전제** [02-공통-설계규약](02-공통-설계규약.md)
> **기능 명세** [F-19 관리자](../../features/version2.0/F-19-관리자.md) ·
> [F-20 스케줄러](../../features/version2.0/F-20-스케줄러.md)

**모델 3종 + 추상 모델·공통 상수.** 다른 앱이 참조하지만 **`core` 는 아무것도 참조하지 않는다**
(규약 1.1 의존 방향).

---

## 1. 이 앱이 갖는 것

| 종류 | 이름 | 역할 |
|---|---|---|
| 모델 | `DataSyncLog` | 배치 실행 이력 — **관측성** |
| 모델 | `AdminAuditLog` | 운영자 조치 감사 로그 |
| 모델 | `AppSetting` | 런타임 설정값 (하드코딩 제거) |
| 추상 모델 | `TimeStampedModel` | `created_at` / `updated_at` |
| 상수 | `AssetClass` · `AccountMode` · `OrderSide` | 앱 경계를 넘는 선택지 |
| 헬퍼 | `core/fields.py` (`QTY`·`PRICE`·`PCT`) · `today_kst()` | 값 타입 규약 |

**테이블이 없는 코드가 절반이다.** 그래도 앱으로 두는 이유는
`makemigrations` 가 추상 모델·상수를 앱 단위로 추적하기 때문이고,
`core` 를 `INSTALLED_APPS` 에 넣어두면 마이그레이션 의존 순서가 명확해진다.

---

## 2. `DataSyncLog` — 배치 관측성 ★

v1.0 스케줄러는 실패 시 로그만 남겼다. **서버리스에서는 그 로그를 보기도 어렵다.**

| 필드 | 타입 | 설명 |
|---|---|---|
| `job_name` | varchar(40) | `sync_stock_master` 등 14종 |
| `started_at` | timestamptz | index |
| `finished_at` | timestamptz | null |
| `status` | varchar(10) | `RUNNING` / `SUCCESS` / `FAILED` |
| `rows_affected` | int | |
| `error` | text | 스택트레이스 |
| `triggered_by` | varchar(10) | `CRON` / `ADMIN` / `CLI` |

```python
class DataSyncLog(models.Model):
    """배치 실행 이력.

    Django Admin 대시보드가 이 테이블을 읽어 최근 실패 잡을 경고 배너로 띄운다.
    "조용히 실패하는 배치" 를 없애는 게 목적이다.
    """

    job_name = models.CharField(max_length=40, db_index=True)
    started_at = models.DateTimeField(db_index=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=10, choices=SyncStatus.choices, default=SyncStatus.RUNNING)
    rows_affected = models.IntegerField(default=0)
    error = models.TextField(blank=True)
    triggered_by = models.CharField(max_length=10, default="CRON")

    class Meta:
        db_table = "data_sync_log"
        indexes = [
            # 잡별 최근 실행 — 대시보드가 잡마다 마지막 1건씩 뽑는다
            models.Index(fields=["job_name", "-started_at"], name="sync_log_idx_job_recent"),
            # 실패만 훑기 (부분 인덱스)
            models.Index(fields=["-started_at"], name="sync_log_idx_failed",
                         condition=models.Q(status="FAILED")),
        ]
```

### 2.1 연속 실패 시 자동 비활성화

**3회 연속 실패하면 잡을 끄고 운영자에게 알린다.**
외부 API 장애 때 무한 재시도로 유량을 태우는 것을 막는다.

```sql
-- 개념: 최근 3건이 전부 FAILED 인가
SELECT count(*) = 3 FROM (
  SELECT status FROM data_sync_log
   WHERE job_name = %s ORDER BY started_at DESC LIMIT 3
) t WHERE status = 'FAILED';
```

### 2.2 잡 실행 래퍼

모든 잡이 같은 방식으로 기록되게 컨텍스트 매니저를 둔다.

```python
@contextmanager
def job_run(job_name: str, triggered_by: str = "CRON"):
    """잡 실행을 감싸 시작·종료·실패를 자동 기록한다.

    Django 관점 — FastAPI + APScheduler 에서는 데코레이터로 감싸는 게 흔했다.
    여기서는 HTTP 엔드포인트(pg_cron 호출)와 management command 양쪽에서
    같은 함수를 쓰므로, 데코레이터보다 컨텍스트 매니저가 재사용에 유리하다.
    """
    log = DataSyncLog.objects.create(
        job_name=job_name, started_at=timezone.now(), triggered_by=triggered_by
    )
    try:
        result = {"rows": 0}
        yield result
    except Exception as exc:
        log.status, log.error = SyncStatus.FAILED, traceback.format_exc()
        raise
    else:
        log.status, log.rows_affected = SyncStatus.SUCCESS, result["rows"]
    finally:
        log.finished_at = timezone.now()
        log.save()
```

---

## 3. `AdminAuditLog` — 감사 로그 ★

**운영자가 대회 데이터를 손대는 일은 반드시 기록되어야 한다.**
순위에 영향을 주는 조작이 흔적 없이 일어나면 안 된다.

| 필드 | 타입 | 설명 |
|---|---|---|
| `actor_id` | FK **PROTECT** | 조치한 운영자 |
| `action` | varchar(40) | `DISQUALIFY` / `RESTORE` / `RESETTLE` / `RULE_CHANGE` / `ORDER_EDIT` |
| `target_model` | varchar(40) | `Participation` 등 |
| `target_id` | varchar(40) | PK (문자열 PK 도 있어서 varchar) |
| `before` / `after` | jsonb | 변경 전후 |
| **`reason`** | varchar(200) | **사유 입력 필수** |
| `ip` | inet | null |
| `created_at` | timestamptz | index |

### 3.1 사유가 필수인 조치

| 조치 | 사유 |
|---|---|
| 참가자 실격 · 실격 해제 | 필수 |
| 주문 수정 · 삭제 | 필수 |
| 정산 재실행 | 필수 |
| `rule_set` 변경 | 필수 |

**대회 상세 화면에 "운영자 조치 이력"을 참가자에게도 공개할지는 대회 설정으로 둔다.**
투명성이 필요한 대회라면 켠다.

> **Django 관점** — Django 에는 `django.contrib.admin.models.LogEntry` 가 이미 있다.
> 그걸 쓰지 않는 이유는 ① 변경 전후 값을 담지 않고 ② **사유를 강제할 수 없으며**
> ③ Admin 밖(정산 재실행 API 등)의 조치를 못 잡기 때문이다. 별도로 둔다.

---

## 4. `AppSetting` — 하드코딩 제거

| 필드 | 타입 | 설명 |
|---|---|---|
| `key` | varchar(50) | **UNIQUE** |
| `value` | jsonb | |
| `description` | varchar(200) | |
| `updated_at` | timestamptz | |

담는 값들:

| key | 용도 | 출처 |
|---|---|---|
| `ai.daily_quota` | AI 분석 1일 한도 (기본 20) | [F-14](../../features/version2.0/F-14-AI분석.md) 2.3 |
| `ai.model_basic` / `ai.model_deep` | 모델명 | F-14 2.4 — **하드코딩 금지** |
| `webfetch.daily_quota` | 수집기 1일 한도 (기본 50) | [F-18](../../features/version2.0/F-18-웹데이터-수집기.md) 6장 |
| `portfolio.advice_thresholds` | 규칙 기반 조언 임계값 4종 | [F-09](../../features/version2.0/F-09-보유자산-포트폴리오.md) 4.2 |
| `practice.fee_bp` / `practice.tax_bp` | 연습 모드 요율 | [F-03](../../features/version2.0/F-03-주문-체결엔진.md) 6장 |
| `openapi.rate_limit_per_min` | 기본 60 | [F-13](../../features/version2.0/F-13-OpenAPI.md) 3장 |

**대회 요율은 여기 없다.** `Contest.fee_bp` / `tax_bp` 로 대회마다 다르게 준다.
전역 설정으로 두면 진행 중인 대회의 규칙이 바뀌어 버린다.

### 4.1 읽기 방식

```python
def get_setting(key: str, default=None):
    """설정값 조회. 요청 단위 캐시만 쓴다 —
    프로세스 메모리에 담으면 다른 인스턴스가 옛 값을 계속 본다(D-9 재발 방지선).
    """
    ...
```

---

## 5. 추상 모델과 공통 상수

→ 정의는 [02-공통-설계규약](02-공통-설계규약.md) 7장 · 8.1 참조.

| 파일 | 내용 |
|---|---|
| `core/models.py` | `TimeStampedModel` |
| `core/fields.py` | `QTY` · `PRICE` · `PCT` |
| `core/constants.py` | `AssetClass` · `AccountMode` · `OrderSide` · `SyncStatus` |
| `core/time.py` | `today_kst()` · `KST` |
| `core/jobs.py` | `job_run()` 컨텍스트 매니저 |

---

## 6. pg_cron 이 읽는 것 — `cron.job_run_details`

pg_cron 자체의 실행 이력은 **Postgres 의 `cron.job_run_details` 테이블**에 쌓인다.
우리 모델이 아니다.

Django 에서는 **관리되지 않는 모델**(`managed = False`)로 읽기 전용 매핑을 하나 둬서
Admin 화면에 띄운다.

```python
class CronJobRunDetail(models.Model):
    """pg_cron 의 실행 이력 (읽기 전용).

    Django 관점 — managed = False 는 "이 테이블의 마이그레이션을 만들지 마라" 는 뜻이다.
    이미 존재하는 테이블을 ORM 으로 읽기만 할 때 쓴다.
    db_table 에 스키마를 포함해 'cron.job_run_details' 로 적는다.
    """

    jobid = models.BigIntegerField(primary_key=True)
    runid = models.BigIntegerField()
    command = models.TextField()
    status = models.TextField()
    return_message = models.TextField()
    start_time = models.DateTimeField()
    end_time = models.DateTimeField(null=True)

    class Meta:
        managed = False
        db_table = 'cron"."job_run_details'      # 스키마 포함 인용 표기
```

**우리 잡의 성공·실패 판정은 `DataSyncLog` 를 본다.** `cron.job_run_details` 는
"HTTP 호출 자체가 나갔는가"만 알려주므로, **호출은 성공했는데 잡 내부가 실패한 경우**를
구분하지 못한다. 두 테이블을 나란히 보여주는 게 Admin 화면의 역할이다.

---

## 7. 관련 문서

- 잡 목록 14종 → [F-20 스케줄러](../../features/version2.0/F-20-스케줄러.md) 2장
- Admin 화면 구성 → [F-19 관리자](../../features/version2.0/F-19-관리자.md)
- 규약 → [02-공통-설계규약](02-공통-설계규약.md)
