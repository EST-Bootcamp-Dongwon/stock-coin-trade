"""contests — 대회 · 참가 · 스냅샷 · 랭킹 · 정산 · 위반 (E-02).

가장 큰 앱이다. **모델 11종.**

관통 원칙은 **정산 멱등성** — 모든 정산 잡은 두 번 돌아도 결과가 같아야 한다.
그 근거가 각 테이블의 `UniqueConstraint` 다 (E-02 12장).
"""

from django.db import models

from core.constants import AssetClass
from core.fields import HHI, NAV, PCT, PRICE, QTY
from core.models import TimeStampedModel


class ContestStatus(models.TextChoices):
    DRAFT = "DRAFT", "준비중"
    UPCOMING = "UPCOMING", "모집중"
    ONGOING = "ONGOING", "진행중"
    SETTLING = "SETTLING", "정산중"
    CLOSED = "CLOSED", "종료"
    CANCELLED = "CANCELLED", "취소"


class ApprovalMode(models.TextChoices):
    AUTO = "AUTO", "자동 승인"
    MANUAL = "MANUAL", "운영자 승인"


class Visibility(models.TextChoices):
    PUBLIC = "PUBLIC", "공개"
    LINK = "LINK", "링크 있는 사람만"
    PRIVATE = "PRIVATE", "비공개"


class PortfolioVisibility(models.TextChoices):
    ALL = "ALL", "전체 공개"
    TOP_N = "TOP_N", "상위 N명만"
    SELF_ONLY = "SELF_ONLY", "본인만"


class ParticipationStatus(models.TextChoices):
    PENDING = "PENDING", "승인 대기"
    APPROVED = "APPROVED", "승인"
    REJECTED = "REJECTED", "거절"
    DISQUALIFIED = "DISQUALIFIED", "실격"
    WITHDRAWN = "WITHDRAWN", "포기"


class ViolationRule(models.TextChoices):
    POSITION_LIMIT = "POSITION_LIMIT", "종목 비중 한도"
    SECTOR_LIMIT = "SECTOR_LIMIT", "섹터 비중 한도"
    SMALL_CAP_LIMIT = "SMALL_CAP_LIMIT", "소형주 합계 한도"
    WEEKLY_TURNOVER = "WEEKLY_TURNOVER", "주간 회전율"


class ViolationSeverity(models.TextChoices):
    WARN = "WARN", "경고(가격 변동에 의한 초과)"
    BLOCK = "BLOCK", "차단(주문 거부)"


def default_rule_set() -> dict:
    """기본 규칙. 대회 생성 시 이 값을 **복사해** 넣는다.

    복사하는 게 핵심이다. 참조로 두면 기본값을 고쳤을 때
    진행 중인 대회의 규칙이 흔들린다.

    Django 관점 — `JSONField(default=...)` 에는 **호출 가능한 것**을 넘겨야 한다.
    `default={...}` 처럼 딕셔너리 리터럴을 주면 모든 행이 같은 객체를 공유해
    한 대회의 규칙을 고치면 다른 대회까지 바뀌는 사고가 난다.
    마이그레이션도 함수 참조를 그대로 기록하므로 이 함수는 **모듈 최상위**에 있어야 한다.
    """
    return {
        "position_limit_pct": 15.0,
        "position_limit_exceptions": {"005930": 40.0, "000660": 30.0},
        "sector_limit_multiplier": 2.0,
        "sector_limit_floor_pct": 10.0,
        "sector_limit_floor_threshold_pct": 5.0,
        "small_cap_threshold_krw": 1_000_000_000_000,
        "small_cap_total_limit_pct": 30.0,
        "min_market_cap_krw": 100_000_000_000,
        "min_avg_turnover_krw": 3_000_000_000,
        "new_listing_block_days": 6,
        "block_supervised": True,
        "weekly_turnover_min_pct": 5.0,
        "weekly_turnover_violation_limit": 3,
        "score_weight_return": 0.7,
        "score_weight_management": 0.3,
    }


# ─────────────────────────────────────────────────────────────────
# 1. 대회 본체와 참가
# ─────────────────────────────────────────────────────────────────


class Contest(TimeStampedModel):
    """대회 본체.

    상태 전이는 pg_cron 의 `settle_contest` 잡이 자동으로 한다 (F-02 2장).
    운영자가 매일 손으로 바꾸는 구조는 주말·공휴일에 깨진다.

    **`fee_bp` · `tax_bp` 만 컬럼이고 나머지 규칙은 JSON 인 이유** — 수수료·세금은
    주문 한 건마다 곱해지는 값이라 조회가 잦고 집계에도 쓰인다. JSON 안에 있으면
    SQL 집계에서 매번 꺼내야 한다. **자주 읽히는 값만 컬럼으로 승격**한다.
    """

    name = models.CharField(max_length=100, verbose_name="대회명")
    slug = models.SlugField(
        max_length=50, unique=True, verbose_name="URL 식별자",
        help_text="/contests/rfm-1/ — id 노출을 피한다",
    )
    description = models.TextField(blank=True, verbose_name="규칙 안내(마크다운)")

    asset_class = models.CharField(
        max_length=10, choices=AssetClass.choices, default=AssetClass.STOCK,
        verbose_name="자산군",
        help_text="1차는 STOCK 만 허용한다. 코인·대체자산 대회는 v2.2",
    )
    status = models.CharField(
        max_length=10, choices=ContestStatus.choices,
        default=ContestStatus.DRAFT, db_index=True, verbose_name="상태",
    )

    start_date = models.DateField(verbose_name="시작일")
    end_date = models.DateField(verbose_name="종료일")
    entry_deadline = models.DateField(
        null=True, blank=True, verbose_name="참가 신청 마감", help_text="비우면 종료까지"
    )
    capacity = models.PositiveIntegerField(
        null=True, blank=True, verbose_name="정원", help_text="비우면 무제한"
    )

    approval_mode = models.CharField(
        max_length=10, choices=ApprovalMode.choices, default=ApprovalMode.AUTO, verbose_name="승인 방식"
    )
    visibility = models.CharField(
        max_length=10, choices=Visibility.choices, default=Visibility.PUBLIC, verbose_name="공개 범위"
    )
    portfolio_visibility = models.CharField(
        max_length=10, choices=PortfolioVisibility.choices, default=PortfolioVisibility.ALL,
        verbose_name="포트폴리오 공개 범위",
        help_text="동아리 대회는 ALL 이 기본. 벤치마크의 '50등 이후 조회 불가' 결함에 대한 대응",
    )
    portfolio_visible_top_n = models.PositiveIntegerField(default=50, verbose_name="공개 상위 N명")

    initial_capital = models.BigIntegerField(default=100_000_000, verbose_name="시작 자본(원)")
    fee_bp = models.PositiveSmallIntegerField(default=10, verbose_name="매매 수수료(bp)")
    tax_bp = models.PositiveSmallIntegerField(default=20, verbose_name="매도세(bp)")

    rule_set = models.JSONField(default=default_rule_set, verbose_name="규칙")
    entry_requirement = models.JSONField(
        default=dict, blank=True, verbose_name="참가 조건",
        help_text='{"min_learning_points": 30} 처럼. 1차 기본값은 제한 없음',
    )

    universe_frozen_at = models.DateTimeField(
        null=True, blank=True, verbose_name="유니버스 고정 시각"
    )
    created_by = models.ForeignKey(
        "accounts.Member", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="created_contests", verbose_name="만든 운영자",
    )

    class Meta:
        db_table = "contest"
        verbose_name = "대회"
        verbose_name_plural = "대회"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(end_date__gte=models.F("start_date")),
                name="contest_ck_date_order",
            ),
        ]
        indexes = [models.Index(fields=["status", "start_date"], name="contest_idx_status_start")]

    def __str__(self):
        return self.name


class Participation(TimeStampedModel):
    """회원 × 대회.

    실격(`DISQUALIFIED`)은 `is_ranked=False` 로만 만든다. 계좌·주문 이력은 그대로
    둔다 (F-02 4.3). 지우면 회고가 불가능하고, 판정이 잘못됐을 때 되돌릴 수 없다.

    **`account` 를 `OneToOneField` 로 둔 이유** — `Account` 에 이미
    `UNIQUE(member, contest)` 가 걸려 있어 참가당 계좌는 논리적으로 하나다.
    O2O 로 선언하면 `participation.account` 와 `account.participation` 양방향이
    단수로 잡혀서 코드가 읽기 쉬워진다.
    """

    contest = models.ForeignKey(
        "contests.Contest", on_delete=models.PROTECT, related_name="participations"
    )
    member = models.ForeignKey(
        "accounts.Member", on_delete=models.PROTECT, related_name="participations"
    )
    account = models.OneToOneField(
        "accounts.Account", on_delete=models.PROTECT,
        null=True, blank=True, related_name="participation",
        help_text="승인 시점에 생성된다. PENDING 상태에서는 비어 있다",
    )
    nickname = models.CharField(
        max_length=20, verbose_name="별칭", help_text="랭킹 표시명. 실명은 노출하지 않는다"
    )
    status = models.CharField(
        max_length=15, choices=ParticipationStatus.choices,
        default=ParticipationStatus.PENDING, db_index=True, verbose_name="상태",
    )
    is_ranked = models.BooleanField(
        default=True, verbose_name="랭킹 포함", help_text="실격은 이 값을 False 로만 만든다"
    )
    joined_at = models.DateTimeField(auto_now_add=True, verbose_name="신청 시각")
    approved_at = models.DateTimeField(null=True, blank=True, verbose_name="승인 시각")
    disqualified_at = models.DateTimeField(null=True, blank=True, verbose_name="실격 시각")
    disqualified_reason = models.CharField(max_length=200, blank=True, verbose_name="실격 사유")

    class Meta:
        db_table = "participation"
        verbose_name = "대회 참가"
        verbose_name_plural = "대회 참가"
        constraints = [
            models.UniqueConstraint(fields=["contest", "member"], name="participation_uniq_member"),
            models.UniqueConstraint(
                fields=["contest", "nickname"], name="participation_uniq_nickname"
            ),
        ]
        indexes = [
            # 인덱스 이름은 Django 제한(30자)에 맞춰 줄였다.
            # E-02 4장의 participation_idx_contest_status(32자)는 그대로 쓰면 검증에 걸린다.
            models.Index(fields=["contest", "status"], name="part_idx_contest_status"),
        ]

    def __str__(self):
        return f"{self.contest} / {self.nickname}"


# ─────────────────────────────────────────────────────────────────
# 2. 규칙 스냅샷 2종 — 대회 시작 시점에 얼린다
# ─────────────────────────────────────────────────────────────────


class ContestUniverse(models.Model):
    """대회 시작 시점의 종목 업종 스냅샷.

    기간 중에 업종이 바뀌면 어제 합법이던 포트가 오늘 위반이 된다. 그래서 얼린다 (F-02 3.4).

    **시총·거래대금·관리종목 지정은 여기에 넣지 않는다.** 그건 "지금 살 수 있는가"의
    판정이라 최신값이 맞고, `market.StockMaster` 가 매일 갱신한다.
    """

    contest = models.ForeignKey(
        "contests.Contest", on_delete=models.CASCADE, related_name="universe"
    )
    symbol = models.CharField(max_length=6, verbose_name="종목코드")
    name = models.CharField(max_length=60, verbose_name="시작 시점 종목명")
    market = models.CharField(max_length=10, verbose_name="시장")
    sector_code = models.CharField(max_length=20, blank=True, verbose_name="업종 코드")
    sector_name = models.CharField(
        max_length=40, blank=True, verbose_name="업종명", help_text="KRX 업종분류 (GICS 아님)"
    )
    is_tradable_at_start = models.BooleanField(
        default=True, verbose_name="시작 시점 거래 가능", help_text="참고용. 실제 판정은 최신값으로 한다"
    )
    frozen_at = models.DateTimeField(verbose_name="고정 시각")

    class Meta:
        db_table = "contest_universe"
        verbose_name = "대회 종목 스냅샷"
        verbose_name_plural = "대회 종목 스냅샷"
        constraints = [
            models.UniqueConstraint(fields=["contest", "symbol"], name="universe_uniq_symbol"),
        ]

    def __str__(self):
        return f"{self.contest_id} {self.symbol}"


class ContestSectorWeight(models.Model):
    """대회 시작 시점의 시장 섹터 비중 스냅샷.

    **`limit_pct` 를 미리 계산해 저장한다.** 주문마다 `rule_set` 을 읽어 재계산하면
    규칙을 바꿨을 때 과거 판정과 어긋난다. 시작 시점의 판정 기준을 그대로 굳힌다.

        limit_pct = max(시장비중 × sector_limit_multiplier, sector_limit_floor_pct)
    """

    contest = models.ForeignKey(
        "contests.Contest", on_delete=models.CASCADE, related_name="sector_weights"
    )
    sector_code = models.CharField(max_length=20, verbose_name="업종 코드")
    sector_name = models.CharField(max_length=40, blank=True, verbose_name="업종명")
    market_weight_pct = models.DecimalField(**PCT, verbose_name="시장 섹터 비중 %")
    limit_pct = models.DecimalField(**PCT, verbose_name="허용 한도 %")

    class Meta:
        db_table = "contest_sector_weight"
        verbose_name = "대회 섹터 비중"
        verbose_name_plural = "대회 섹터 비중"
        constraints = [
            models.UniqueConstraint(fields=["contest", "sector_code"], name="sector_weight_uniq"),
        ]

    def __str__(self):
        return f"{self.sector_name} ≤ {self.limit_pct}%"


# ─────────────────────────────────────────────────────────────────
# 3. 스냅샷 — JSONB 원본 + 정규화 파생 이중 저장 ★★
# ─────────────────────────────────────────────────────────────────


class DailySnapshot(models.Model):
    """참가자별 일별 성과. 매 영업일 15:40 KST 에 1행씩 기록한다.

    v1.0 의 투자랭킹은 요청이 올 때마다 전 회원의 전 포지션을 재계산했다.
    참가자가 늘면 무너지는 구조다. 이 테이블 하나가 랭킹·성과지표·회전율·
    관리점수를 전부 가능하게 만든다 (F-05 1장).

    ★ **이중 저장** — `holdings`(jsonb)가 **감사용 원본**이고,
    `SnapshotHolding`(행)이 **조회·집계용 파생**이다. 정합성 규약은 E-02 6.2:

      1. 같은 트랜잭션에서만 쓴다 (파생은 delete + bulk_create, 부분 갱신 금지)
      2. 어긋나면 **JSONB 를 정본**으로 보고 파생을 재생성한다
      3. `python manage.py rebuild_snapshot_holdings` 로 복구 경로를 미리 둔다
    """

    participation = models.ForeignKey(
        "contests.Participation", on_delete=models.CASCADE, related_name="daily_snapshots"
    )
    date = models.DateField(verbose_name="영업일(KST)")

    cash = models.BigIntegerField(verbose_name="현금")
    position_value = models.BigIntegerField(verbose_name="보유 평가액")
    total_asset = models.BigIntegerField(verbose_name="순자산")
    nav = models.DecimalField(**NAV, verbose_name="기준가(시작=1000)")
    daily_return_pct = models.DecimalField(**PCT, default=0, verbose_name="전일 대비 %")
    cumulative_return_pct = models.DecimalField(**PCT, default=0, verbose_name="누적 수익률 %")
    position_count = models.PositiveIntegerField(default=0, verbose_name="보유 종목 수")
    invested_ratio_pct = models.DecimalField(**PCT, default=0, verbose_name="편입비 %")
    buy_amount = models.BigIntegerField(default=0, verbose_name="당일 매수")
    sell_amount = models.BigIntegerField(default=0, verbose_name="당일 매도")
    fee_amount = models.BigIntegerField(default=0, verbose_name="당일 수수료")
    tax_amount = models.BigIntegerField(default=0, verbose_name="당일 매도세")

    holdings = models.JSONField(default=list, verbose_name="보유 종목 원본")
    sector_weights = models.JSONField(default=list, verbose_name="섹터 비중")
    violations = models.JSONField(default=list, verbose_name="한도 위반 상태")

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "daily_snapshot"
        verbose_name = "일별 스냅샷"
        verbose_name_plural = "일별 스냅샷"
        constraints = [
            # 정산이 두 번 돌아도 한 행. upsert 의 충돌 대상이 된다
            models.UniqueConstraint(
                fields=["participation", "date"], name="daily_snapshot_uniq_pd"
            ),
        ]
        indexes = [models.Index(fields=["date"], name="daily_snapshot_idx_date")]

    def __str__(self):
        return f"{self.participation_id} {self.date}"


class SnapshotHolding(models.Model):
    """일별 스냅샷의 종목별 보유 내역 — `DailySnapshot.holdings`(jsonb)의 정규화 파생.

    JSONB 가 정본이고 이 테이블은 조회 편의를 위한 사본이다 (E-02 6.2).
    Top Pick 매트릭스·수익 종목 집계·섹터 집계가 여기서 나온다.

    행 수는 걱정할 규모가 아니다 — 참가자 100명 × 종목 15개 × 20영업일 ≈ 3만 행.

    ★ **`contest` · `participation` · `date` 를 복제하는 이유** — Top Pick 매트릭스는
    "이 대회의 이 날짜, 상위 참가자들의 종목별 비중"을 묻는다. 복제가 없으면
    `snapshot_holding → daily_snapshot → participation → contest` 3중 조인이 된다.
    **스냅샷은 한 번 쓰고 다시 고치지 않는 불변 데이터**라 갱신 이상의 위험이 없다.
    """

    snapshot = models.ForeignKey(
        "contests.DailySnapshot", on_delete=models.CASCADE, related_name="holding_rows"
    )
    # ↓ 조회 성능을 위한 비정규 복제. related_name="+" 는 "역참조를 만들지 마라"는 뜻이다 —
    #   contest.snapshotholding_set 같은 접근자는 3만 행을 통째로 끌어올 위험만 있다
    contest = models.ForeignKey("contests.Contest", on_delete=models.CASCADE, related_name="+")
    participation = models.ForeignKey(
        "contests.Participation", on_delete=models.CASCADE, related_name="+"
    )
    date = models.DateField()

    symbol = models.CharField(max_length=20)
    name = models.CharField(max_length=60, blank=True)
    sector_code = models.CharField(max_length=20, blank=True)
    qty = models.DecimalField(**QTY)
    avg_price = models.DecimalField(**PRICE)
    close_price = models.DecimalField(**PRICE, verbose_name="그날 종가")
    value = models.BigIntegerField(verbose_name="평가액")
    weight_pct = models.DecimalField(**PCT, verbose_name="순자산 대비 비중 %")
    pnl = models.BigIntegerField(default=0, verbose_name="평가손익")
    pnl_pct = models.DecimalField(**PCT, default=0, verbose_name="평가손익률 %")

    class Meta:
        db_table = "snapshot_holding"
        verbose_name = "스냅샷 보유 종목"
        verbose_name_plural = "스냅샷 보유 종목"
        constraints = [
            models.UniqueConstraint(fields=["snapshot", "symbol"], name="snapshot_holding_uniq"),
        ]
        indexes = [
            # Top Pick 매트릭스 — 대회 + 날짜 + 종목
            models.Index(fields=["contest", "date", "symbol"], name="snap_hold_idx_contest_date"),
            # 참가자 시계열 — "이 사람이 이 종목을 언제부터 들고 있었나"
            models.Index(
                fields=["participation", "symbol", "date"], name="snap_hold_idx_part_symbol"
            ),
        ]

    def __str__(self):
        return f"{self.date} {self.symbol} {self.weight_pct}%"


class IntradaySnapshot(models.Model):
    """장중 10분 간격 수익률. 09:00~15:30 → 하루 40행.

    **당일분만 보관한다.** `cleanup` 잡(매일 04:00)이 전일분을 지운다.
    과거 장중 데이터는 `DailySnapshot` 으로 충분하다 (F-05 2.3).
    """

    participation = models.ForeignKey(
        "contests.Participation", on_delete=models.CASCADE, related_name="intraday_snapshots"
    )
    at = models.DateTimeField(verbose_name="시각(10분 간격)")
    return_pct = models.DecimalField(**PCT, default=0, verbose_name="수익률 %")
    nav = models.DecimalField(**NAV, verbose_name="기준가")

    class Meta:
        db_table = "intraday_snapshot"
        verbose_name = "장중 스냅샷"
        verbose_name_plural = "장중 스냅샷"
        constraints = [
            models.UniqueConstraint(
                fields=["participation", "at"], name="intraday_uniq_part_at"
            ),
        ]
        indexes = [models.Index(fields=["at"], name="intraday_idx_at")]

    def __str__(self):
        return f"{self.participation_id} {self.at:%H:%M}"


# ─────────────────────────────────────────────────────────────────
# 4. 랭킹 · 결과 · 위반 · 회전율
# ─────────────────────────────────────────────────────────────────


class ContestRanking(models.Model):
    """일자별 순위.

    **랭킹 조회는 이 테이블을 그대로 읽는다.** 요청 시점에 정렬·계산하지 않는다 (F-05 7장).

    `nav` 를 `DailySnapshot` 에서 또 복제하는 이유 — 랭킹 화면은 참가자 100명 × 1일을
    한 번에 읽는다. `DailySnapshot` 을 조인하면 넓은 행(JSONB 3칸 포함)을 100개 끌어온다.
    `ContestRanking` 은 좁은 행이라 인덱스만으로 응답할 수 있다.
    """

    contest = models.ForeignKey(
        "contests.Contest", on_delete=models.CASCADE, related_name="rankings"
    )
    participation = models.ForeignKey(
        "contests.Participation", on_delete=models.CASCADE, related_name="rankings"
    )
    date = models.DateField(verbose_name="영업일(KST)")
    rank = models.PositiveIntegerField(
        null=True, blank=True, verbose_name="순위", help_text="비우면 실격·포기. 화면에는 '-' 로 표시"
    )
    prev_rank = models.PositiveIntegerField(
        null=True, blank=True, verbose_name="전일 순위", help_text="등락(3↑) 표시용"
    )
    nav = models.DecimalField(**NAV, verbose_name="기준가")
    cumulative_return_pct = models.DecimalField(**PCT, default=0, verbose_name="누적 수익률 %")
    daily_return_pct = models.DecimalField(**PCT, default=0, verbose_name="일간 수익률 %")
    position_count = models.PositiveIntegerField(default=0, verbose_name="보유 종목 수")
    invested_ratio_pct = models.DecimalField(**PCT, default=0, verbose_name="편입비 %")

    class Meta:
        db_table = "contest_ranking"
        verbose_name = "대회 랭킹"
        verbose_name_plural = "대회 랭킹"
        constraints = [
            models.UniqueConstraint(
                fields=["contest", "participation", "date"], name="ranking_uniq_cpd"
            ),
        ]
        indexes = [
            # 랭킹 목록의 유일한 조회 패턴이다
            models.Index(fields=["contest", "date", "rank"], name="ranking_idx_contest_date"),
        ]

    def __str__(self):
        return f"{self.date} {self.rank}위"


class ContestResult(models.Model):
    """최종 확정 결과.

    ★ **`port_hhi` · `pnl_hhi` 를 저장하는 이유** — 관리 점수 산식은 v2.0 의 제안이며
    1회 대회 후 조정을 전제한다 (F-04 6.2). **중간 값을 남겨두면 산식을 바꿨을 때
    과거 대회를 재계산해 비교할 수 있다.** 점수만 저장하면 왜 그 점수가 나왔는지
    되짚을 수 없다.
    """

    contest = models.ForeignKey(
        "contests.Contest", on_delete=models.CASCADE, related_name="results"
    )
    participation = models.ForeignKey(
        "contests.Participation", on_delete=models.CASCADE, related_name="result"
    )
    final_rank = models.PositiveIntegerField(null=True, blank=True, verbose_name="최종 순위")
    return_score = models.DecimalField(**PCT, default=0, verbose_name="수익 원점수(누적 수익률)")
    return_percentile = models.DecimalField(**PCT, default=0, verbose_name="수익 백분위")
    management_score = models.DecimalField(**PCT, default=0, verbose_name="관리 원점수")
    management_percentile = models.DecimalField(**PCT, default=0, verbose_name="관리 백분위")
    port_hhi = models.DecimalField(**HHI, default=0, verbose_name="포트 HHI")
    pnl_hhi = models.DecimalField(**HHI, default=0, verbose_name="손익 HHI")
    final_score = models.DecimalField(**PCT, default=0, verbose_name="최종 점수(백분위 가중합)")
    grade = models.CharField(max_length=3, blank=True, verbose_name="등급", help_text="A+ ~ F")
    confirmed_at = models.DateTimeField(null=True, blank=True, verbose_name="확정 시각")

    class Meta:
        db_table = "contest_result"
        verbose_name = "대회 최종 결과"
        verbose_name_plural = "대회 최종 결과"
        constraints = [
            models.UniqueConstraint(fields=["contest", "participation"], name="result_uniq_cp"),
        ]
        indexes = [models.Index(fields=["contest", "final_rank"], name="result_idx_contest_rank")]

    def __str__(self):
        return f"{self.participation_id} {self.final_rank}위 {self.grade}"


class RuleViolation(models.Model):
    """위반 기록.

    **자동 실격 처리는 하지 않는다** (회전율 4회 제외).
    "적극적으로 해소하려 노력했는가"는 정성 판단이라, 시스템은 **운영자에게 근거를
    주는 선까지**가 역할이다 (F-04 3.4).
    """

    participation = models.ForeignKey(
        "contests.Participation", on_delete=models.CASCADE, related_name="violations"
    )
    rule = models.CharField(max_length=30, choices=ViolationRule.choices, verbose_name="규칙")
    date = models.DateField(verbose_name="발생 영업일")
    severity = models.CharField(
        max_length=10, choices=ViolationSeverity.choices, default=ViolationSeverity.WARN
    )
    detail = models.JSONField(default=dict, verbose_name="상세", help_text="현재 비중·한도·초과분")
    is_resolved = models.BooleanField(default=False, verbose_name="해소 여부")
    resolved_at = models.DateTimeField(null=True, blank=True, verbose_name="해소 시각")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "rule_violation"
        verbose_name = "규칙 위반"
        verbose_name_plural = "규칙 위반"
        constraints = [
            # 하루에 같은 규칙은 1행
            models.UniqueConstraint(
                fields=["participation", "rule", "date"], name="violation_uniq_prd"
            ),
        ]
        indexes = [models.Index(fields=["date", "rule"], name="violation_idx_date_rule")]

    def __str__(self):
        return f"{self.date} {self.rule}"


class WeeklyTurnover(models.Model):
    """주간 회전율.

        turnover_pct = (매수 + 매도) / 평균운용금액 × 0.5 × 100

    주는 **월요일 00:00 ~ 일요일 24:00 KST** 다 (F-04 5.1 확정).
    `week_start` 에는 그 주 월요일의 KST 날짜를 넣는다 (`core.time.week_start_kst`).

    ★ **`is_confirmed` 가 필요한 이유** — 화면이 "확정 위반"과 "예상 위반"을 나눠 보여준다.

      · `True`  — 지난 주들. 더 이상 변하지 않는다
      · `False` — **이번 주 현재 시점 기준** 잠정치. 매일 `settle_daily` 가 덮어쓴다

    "새로운 주가 시작하는 월요일 아침 장시작 전에는 매매가 있을 수 없으므로 금주의
    예상값은 1회 위반으로 보이는 게 정상입니다." — 이 안내를 화면에 그대로 넣는다.
    안 그러면 월요일마다 문의가 들어온다.
    """

    participation = models.ForeignKey(
        "contests.Participation", on_delete=models.CASCADE, related_name="weekly_turnovers"
    )
    week_start = models.DateField(verbose_name="주 시작(월요일 KST)")
    buy_amount = models.BigIntegerField(default=0, verbose_name="매수 합계")
    sell_amount = models.BigIntegerField(default=0, verbose_name="매도 합계")
    avg_asset = models.BigIntegerField(default=0, verbose_name="평균 운용금액")
    turnover_pct = models.DecimalField(**PCT, default=0, verbose_name="회전율 %")
    is_violation = models.BooleanField(default=False, verbose_name="기준 미달")
    is_confirmed = models.BooleanField(
        default=False, verbose_name="확정 여부", help_text="False 면 이번 주 잠정치"
    )
    violation_seq = models.PositiveSmallIntegerField(default=0, verbose_name="확정 위반 누적")

    class Meta:
        db_table = "weekly_turnover"
        verbose_name = "주간 회전율"
        verbose_name_plural = "주간 회전율"
        constraints = [
            models.UniqueConstraint(
                fields=["participation", "week_start"], name="turnover_uniq_part_week"
            ),
        ]

    def __str__(self):
        return f"{self.week_start} {self.turnover_pct}%"
