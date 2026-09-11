"""trading — 주문 · 체결 · 포지션 · 대체자산 (E-03).

**v1.0 의 테이블 5개가 여기서 3개로 합쳐진다.**

| v1.0                                              | v2.0                      |
|---|---|
| `stock_order` · `alternative_order` · (코인 없음)   | `Order` 통합 (결함 D-4)     |
| `stock_position` · `alternative_position` · `hold_crypto` | `Position` 통합    |

v1.0 은 **코인 주문 이력 테이블이 아예 없었다**(D-4). `hold_crypto`(현재 보유)만
있어 "언제 얼마에 샀는지"를 복원할 수 없었다.
"""

from django.db import models

from core.constants import AssetClass, OrderSide
from core.fields import PCT, PRICE, QTY


class OrderStatus(models.TextChoices):
    PENDING_OPEN = "PENDING_OPEN", "장 시작 대기"
    ACCEPTED = "ACCEPTED", "접수"
    PARTIAL = "PARTIAL", "부분체결"
    FILLED = "FILLED", "체결완료"
    CANCELLED = "CANCELLED", "취소"
    REJECTED = "REJECTED", "거부"


class OrderType(models.TextChoices):
    MARKET = "MARKET", "시장가"
    LIMIT = "LIMIT", "지정가"
    RELATIVE = "RELATIVE", "상대호가"    # 매수면 매도n호가 — 공격적
    OWN = "OWN", "자기호가"              # 매수면 매수n호가 — 소극적
    STOP = "STOP", "STOP"


class OrderSource(models.TextChoices):
    WEB = "WEB", "웹"
    PINE = "PINE", "Pine 전략"
    OPENAPI = "OPENAPI", "Open API"
    ADMIN = "ADMIN", "운영자"
    DEMO_SEED = "DEMO_SEED", "샘플"


# 미체결로 보는 상태 — 부분 인덱스 조건과 체결 추종 잡이 함께 쓴다.
# 두 곳에 손으로 적으면 어긋나므로 상수로 뺀다.
OPEN_ORDER_STATUSES = [
    OrderStatus.ACCEPTED,
    OrderStatus.PARTIAL,
    OrderStatus.PENDING_OPEN,
]


class Order(models.Model):
    """주문 — 주식·코인·대체자산 통합.

    Django 관점 — SQLAlchemy 에서 테이블을 나눠 두고 UNION 으로 합치던 조회가
    여기서는 그냥 필터 하나가 된다. 대신 **필드가 자산군마다 다르게 쓰인다**는 부담이
    생기므로(코인은 `price_level` 이 무의미) 어떤 조합이 유효한지 서비스에서 검증한다.

    ★ **`order` 는 SQL 예약어다** (`ORDER BY`). Django ORM 은 항상 `"order"` 로
    따옴표를 붙여 내보내므로 ORM 경로는 문제가 없다. **직접 SQL 을 쓰는 pg_cron 잡에서는
    반드시 따옴표를 친다**::

        -- 틀림:  SELECT * FROM order WHERE ...     → 구문 오류
        -- 맞음:  SELECT * FROM "order" WHERE ...

    ★ **`realized_pnl` 을 저장하는 이유** — 거래이력 상단의 실현손익 합계를
    `SUM(realized_pnl)` 한 줄로 끝내기 위해서다. 사후에 계산하려면 그 시점의 평단을
    알아야 하는데, `Position` 은 현재 상태만 갖고 있어 과거 평단을 복원할 수 없다.

        매도 실현손익 = (체결가 - 매도 직전 평단) × 체결수량 - 수수료 - 매도세
    """

    account = models.ForeignKey(
        "accounts.Account", on_delete=models.PROTECT, related_name="orders", verbose_name="계좌"
    )
    symbol = models.CharField(
        max_length=20, db_index=True, verbose_name="종목",
        help_text="주식 005930 · 코인 KRW-BTC · 대체자산 FUT-K200",
    )
    asset_class = models.CharField(
        max_length=10, choices=AssetClass.choices, verbose_name="자산군",
        help_text="조인 없이 필터하려고 저장한다 (규약 8.1)",
    )
    side = models.CharField(max_length=4, choices=OrderSide.choices, verbose_name="매매 구분")

    order_type = models.CharField(max_length=12, choices=OrderType.choices, verbose_name="주문 유형")
    price_level = models.PositiveSmallIntegerField(
        null=True, blank=True, verbose_name="호가 단계",
        help_text="상대호가·자기호가일 때만 1~10",
    )
    limit_price = models.DecimalField(**PRICE, null=True, blank=True, verbose_name="지정가")
    stop_price = models.DecimalField(**PRICE, null=True, blank=True, verbose_name="STOP 발동가")

    requested_weight_pct = models.DecimalField(
        **PCT, null=True, blank=True, verbose_name="주문 비중 %",
        help_text="대회 모드는 수량이 아니라 순자산 대비 %로 주문한다(F-03 3.1). 연습은 비운다",
    )
    requested_qty = models.DecimalField(**QTY, verbose_name="요청 수량")
    filled_qty = models.DecimalField(**QTY, default=0, verbose_name="체결 수량")
    avg_fill_price = models.DecimalField(**PRICE, default=0, verbose_name="가중평균 체결가")

    gross_amount = models.BigIntegerField(default=0, verbose_name="체결금액(수수료 전)")
    fee = models.BigIntegerField(default=0, verbose_name="수수료")
    tax = models.BigIntegerField(default=0, verbose_name="매도세")
    net_amount = models.BigIntegerField(default=0, verbose_name="정산금액")
    realized_pnl = models.BigIntegerField(
        null=True, blank=True, verbose_name="실현손익",
        help_text="매도 체결 시 계산해 저장한다. 사후 계산 없이 합산할 수 있게 한다",
    )

    status = models.CharField(
        max_length=15, choices=OrderStatus.choices,
        default=OrderStatus.ACCEPTED, db_index=True, verbose_name="상태",
    )
    source = models.CharField(
        max_length=10, choices=OrderSource.choices, default=OrderSource.WEB, verbose_name="주문 경로"
    )
    execution_mode = models.CharField(
        max_length=10, default="IMMEDIATE", verbose_name="체결 방식",
        help_text="SLICED(시간분할)는 v2.1. 컬럼만 미리 만들어 둔다",
    )
    reject_rule = models.CharField(max_length=30, blank=True, verbose_name="위반 규칙")
    reject_message = models.CharField(max_length=200, blank=True, verbose_name="거부 사유")

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    accepted_at = models.DateTimeField(null=True, blank=True)
    filled_at = models.DateTimeField(null=True, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "order"
        verbose_name = "주문"
        verbose_name_plural = "주문"
        indexes = [
            # 거래이력 화면 — 계좌별 최신순
            models.Index(fields=["account", "-created_at"], name="order_idx_account_recent"),
            # 거래이력 필터 — 자산군·매매구분
            models.Index(fields=["account", "asset_class", "side"], name="order_idx_account_class"),
            # 체결 추종 잡 — 미체결 주문만 스캔 (부분 인덱스)
            models.Index(
                fields=["symbol", "status"],
                name="order_idx_open",
                condition=models.Q(status__in=OPEN_ORDER_STATUSES),
            ),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(filled_qty__lte=models.F("requested_qty")),
                name="order_ck_filled_le_requested",
            ),
        ]

    def __str__(self):
        return f"{self.symbol} {self.side} {self.requested_qty} [{self.status}]"


class Execution(models.Model):
    """체결 조각. 호가 단계마다 체결가가 다르므로 조각별로 남긴다.

    `Order.avg_fill_price` 는 이 조각들의 가중평균이고, 검증이 필요할 때 여기서 되짚는다.

    ★ **`is_assumed_depth` — 정직성 컬럼** — 지정가가 상대 10호가를 넘는 경우,
    1~10호가의 평균 잔량이 1틱 간격으로 11호가부터 형성되어 있다고 **가정**하고
    계속 체결한다 (F-03 5.1 4단계 · 실제 시장보다 유리한 가정).

    이 가정으로 체결된 조각은 화면에 표시한다. v2.0 의 관통 원칙 ②
    "화면에 보이지 않는 사실은 없는 것과 같다"의 적용이다.
    """

    order = models.ForeignKey(
        "trading.Order", on_delete=models.CASCADE, related_name="executions", verbose_name="주문"
    )
    seq = models.PositiveIntegerField(verbose_name="조각 순번")
    qty = models.DecimalField(**QTY, verbose_name="체결 수량")
    price = models.DecimalField(**PRICE, verbose_name="체결가")
    amount = models.BigIntegerField(default=0, verbose_name="체결금액")
    price_level = models.PositiveSmallIntegerField(
        null=True, blank=True, verbose_name="체결 호가 단계"
    )
    is_assumed_depth = models.BooleanField(
        default=False, verbose_name="추정 잔량 체결",
        help_text="10호가를 넘어 가정으로 체결된 조각. 체결 상세에 배지를 띄운다",
    )
    executed_at = models.DateTimeField(verbose_name="체결 시각")

    class Meta:
        db_table = "execution"
        verbose_name = "체결"
        verbose_name_plural = "체결"
        ordering = ["order", "seq"]
        constraints = [
            models.UniqueConstraint(fields=["order", "seq"], name="execution_uniq_order_seq"),
        ]

    def __str__(self):
        return f"{self.order_id}#{self.seq} {self.qty}@{self.price}"


class Position(models.Model):
    """현재 보유. 계좌 × 종목당 1행.

    **수량이 0 이 되면 행을 삭제한다** (F-03 7.2). `UNIQUE(account, symbol)` 아래에서
    0 행을 남기면 "보유 종목 수" 집계마다 `qty > 0` 조건을 달아야 하고, 빠뜨리면
    조용히 틀린다.

    잃는 것은 "예전에 이 종목을 들고 있었다"는 사실인데, 두 가지로 보완한다:
      ① `Order.realized_pnl` — 종료 거래의 손익
      ② `contests.SnapshotHolding` — 매 영업일의 보유 전체

    `position_idx_symbol` 의 용도는 시세 폴링 우선순위 결정이다 —
    "대회 참가자가 보유 중인 종목"을 뽑을 때 쓴다 (F-16 2.5 우선순위 2).
    """

    account = models.ForeignKey(
        "accounts.Account", on_delete=models.CASCADE, related_name="positions", verbose_name="계좌"
    )
    symbol = models.CharField(max_length=20, verbose_name="종목")
    asset_class = models.CharField(max_length=10, choices=AssetClass.choices, verbose_name="자산군")
    qty = models.DecimalField(**QTY, default=0, verbose_name="수량")
    avg_price = models.DecimalField(**PRICE, default=0, verbose_name="평균 단가")
    principal = models.BigIntegerField(default=0, verbose_name="매수원금")
    opened_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "position"
        verbose_name = "보유 포지션"
        verbose_name_plural = "보유 포지션"
        constraints = [
            models.UniqueConstraint(
                fields=["account", "symbol"], name="position_uniq_account_symbol"
            ),
            models.CheckConstraint(condition=models.Q(qty__gte=0), name="position_ck_qty_nonneg"),
        ]
        indexes = [models.Index(fields=["symbol"], name="position_idx_symbol")]

    def __str__(self):
        return f"{self.account_id} {self.symbol} × {self.qty}"


class AlternativeProduct(models.Model):
    """대체자산 카탈로그 11종.

    v1.0 은 11종을 **파이썬 상수로 하드코딩**했다. 테이블로 옮겨 운영자가 Admin 에서
    고칠 수 있게 한다. 시드는 데이터 마이그레이션으로 넣는다 (05 문서 5.1).

    `symbol` 을 PK 로 두면 `Order.symbol` · `Position.symbol` 과 문자열로 자연스럽게
    맞물린다 (FK 는 걸지 않는다 — 자산군 통합 테이블이라 FK 를 걸 수 없다).

    ★ **대체자산은 대회 대상이 아니다** — 가격이
    `sin(오늘의 서수 × 0.71 + 심볼문자합)` 기반 결정론적 수식이고 저장소가 Public 이라
    **참가자가 내일 가격을 미리 계산할 수 있다** (F-08 2장).
    그래서 `Contest.asset_class` 는 1차에서 `STOCK` 만 허용한다.
    """

    symbol = models.CharField(max_length=20, primary_key=True, verbose_name="심볼")
    name = models.CharField(max_length=50, verbose_name="상품명")
    category = models.CharField(
        max_length=20, verbose_name="분류", help_text="선물/옵션/파생상품/금/은/부동산"
    )
    description = models.CharField(
        max_length=100, blank=True, verbose_name="설명",
        help_text="시세 표·선택 안내줄에 그대로 노출된다 (v1.0 CATALOG.description)",
    )
    base_price = models.BigIntegerField(default=0, verbose_name="기준가")
    multiplier = models.IntegerField(default=1, verbose_name="승수")
    unit_label = models.CharField(
        max_length=20, blank=True, verbose_name="단위", help_text="1계약 · 1g · 1구좌"
    )
    margin_rate_pct = models.DecimalField(**PCT, default=0, verbose_name="증거금률 %")
    volatility_pct = models.DecimalField(
        **PCT, default=0, verbose_name="일간 진폭 %", help_text="옵션·파생 1.8 / 나머지 0.9"
    )
    point_scale = models.IntegerField(null=True, blank=True, verbose_name="포인트 배율")
    actual_multiplier = models.IntegerField(
        null=True, blank=True, verbose_name="실계약 승수", help_text="FUT-K200 실계약 규모 안내용"
    )
    map_lat = models.DecimalField(
        max_digits=9, decimal_places=6, null=True, blank=True, verbose_name="위도"
    )
    map_lng = models.DecimalField(
        max_digits=9, decimal_places=6, null=True, blank=True, verbose_name="경도"
    )
    map_label = models.CharField(
        max_length=40, blank=True, verbose_name="지도 표시명",
        help_text="'서울 강남구' 등. Leaflet 마커 팝업과 차트 부제에 쓴다",
    )
    is_active = models.BooleanField(default=True, verbose_name="활성")
    sort_order = models.IntegerField(default=0, verbose_name="정렬 순서")

    class Meta:
        db_table = "alternative_product"
        verbose_name = "대체자산 상품"
        verbose_name_plural = "대체자산 상품"
        ordering = ["sort_order", "symbol"]

    def __str__(self):
        return f"{self.symbol} {self.name}"


class AvgDownSimulation(models.Model):
    """물타기 시뮬레이션 기록.

    **1차에는 저장만 하고 회고 화면은 v2.1** 로 미룬다 (F-11 3.4).
    계산 자체는 클라이언트(Alpine.js)에서 하고, 저장 요청만 서버로 온다.
    """

    account = models.ForeignKey(
        "accounts.Account", on_delete=models.CASCADE, related_name="avg_down_simulations"
    )
    symbol = models.CharField(max_length=20, verbose_name="종목")
    params = models.JSONField(
        default=dict, verbose_name="입력값", help_text="보유수량·평단·추가매수가·수량/금액"
    )
    result = models.JSONField(
        default=dict, verbose_name="산출값", help_text="새 평단·본전 상승률·잔여현금"
    )
    executed_order = models.ForeignKey(
        "trading.Order", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="avg_down_simulations", verbose_name="실행된 주문",
        help_text="시뮬레이션이 실제 주문으로 이어졌으면 연결한다",
    )
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        db_table = "avg_down_simulation"
        verbose_name = "물타기 시뮬레이션"
        verbose_name_plural = "물타기 시뮬레이션"

    def __str__(self):
        return f"{self.symbol} 물타기 {self.created_at:%Y-%m-%d}"
