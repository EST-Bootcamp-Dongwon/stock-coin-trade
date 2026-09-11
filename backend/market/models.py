"""market — 종목 마스터 · 영업일 · 시세 캐시 · 외부 토큰 (E-04).

모델 13종. 이 앱이 v1.0 결함 **D-9(프로세스 메모리 캐시)** 를 구조적으로 해소한다.

    모든 캐시는 Postgres 테이블에 있다.
    요청을 넘어서 사는 프로세스 메모리 캐시는 만들지 않는다.

v1.0 은 시세·차트·뉴스·랭킹 캐시가 전부 전역 `dict` 였다. 서버리스에서는
인스턴스마다 따로 놀아 히트율이 0 이 되고, 재기동하면 사라진다.

이 앱은 **다른 앱을 참조하지 않는다** (규약 1.1). 캐시 테이블에 FK 가 없는 것도
같은 이유다 — 캐시가 본 데이터를 붙잡고 있으면 정리가 어려워진다.
"""

from django.db import models
from django.utils import timezone

from core.constants import AssetClass, BrokerProvider, QuoteSource
from core.fields import PCT, PRICE, QTY


class Market(models.TextChoices):
    KOSPI = "KOSPI", "코스피"
    KOSDAQ = "KOSDAQ", "코스닥"


class StockType(models.TextChoices):
    """종목 구분. 대회는 **보통주만** 허용한다 (F-04 2.1)."""

    COMMON = "COMMON", "보통주"
    PREFERRED = "PREFERRED", "우선주"
    ETF = "ETF", "ETF"
    ETN = "ETN", "ETN"
    REIT = "REIT", "리츠"
    SPAC = "SPAC", "스팩"


class CalendarSource(models.TextChoices):
    PYKRX = "PYKRX", "pykrx 배치"
    MANUAL = "MANUAL", "운영자 수정"


# ─────────────────────────────────────────────────────────────────
# 1. 마스터 3종
# ─────────────────────────────────────────────────────────────────


class StockMasterQuerySet(models.QuerySet):
    """종목 마스터 조회 — 여러 곳에서 되풀이되면 안 되는 조건만 담는다."""

    def contest_universe_candidates(self):
        """대회 유니버스에 넣을 **후보** (F-04 2장 · 3.2 · 변경노트 E-31).

        여기서 거르는 것은 **대회 규칙과 무관하게 언제나 참인 3가지**뿐이다:

            ① 상장폐지되지 않았을 것
            ② 보통주일 것            (F-04 2.1 — 우선주·ETF·ETN·리츠·스팩 제외)
            ③ **KRX 업종이 매겨져 있을 것** ★

        시가총액·거래대금·관리종목·신규상장 같은 조건은 **대회마다 임계값이 다르다**
        (`Contest.rule_set`). 그것들은 규칙 엔진이 판정한다 — `StockMaster` 에
        `is_tradable` 컬럼을 두지 않는 것과 같은 이유다(아래 모델 docstring).

        ★★ **③ 업종 미부여 종목을 왜 유니버스에서 빼는가** ────────────────────

        F-04 3.2 의 섹터 한도는 `max(시장 섹터 비중 × 2, 10%)` 다. **섹터를 모르면
        이 식에 넣을 값 자체가 없다.** 남는 선택지는 셋뿐인데:

        | 안 | 결과 |
        |---|---|
        | 한도 판정에서 면제 | 미분류 종목만 담아 **섹터 한도를 통째로 우회**할 수 있다 |
        | '미분류' 한 섹터로 묶기 | 서로 무관한 33종이 한 한도를 나눠 쓴다 — 근거 없는 제약 |
        | **유니버스에서 제외** | 규칙에 구멍이 없다. 연습 모드에서는 그대로 거래된다 |

        세 번째를 택했다 (2026-08-13 사용자 확정).

        실측(2026-08-13) — 업종이 비는 종목은 **33종**이고, 그중 12종은 시총
        1,000억을 넘어 다른 조건은 전부 통과한다. 그냥 두면 대회에 들어온다.
        정체는 대부분 **`9xxxxx` 외국기업**(코오롱티슈진·GRT·JTC…)이다.
        KRX 업종지수가 외국주권을 편입하지 않아 업종이 비는 것이고, 이들은
        결산·공시 체계도 국내와 달라 대회 규칙 판정 자체가 애매하다.
        **제외가 데이터 사정에 떠밀린 타협이 아니라 규칙상으로도 맞다.**

        (변경노트 E-25 는 이 33종을 "스팩·신규상장" 으로 추정했는데 **틀렸다.**
         실제로 스팩은 0종이다. E-31 에서 정정했다.)
        """
        return self.filter(
            is_delisted=False,
            stock_type=StockType.COMMON,
        ).exclude(sector_code="")


class StockMaster(models.Model):
    """종목 마스터 — pykrx 일배치(매 영업일 16:00 KST)로 적재한다.

    v1.0 은 14종을 하드코딩하고, 그 밖의 종목은 KRX KIND 페이지를 EUC-KR HTML
    정규식으로 긁었다. 깨지기 쉬운 경로라 pykrx 로 교체한다.

    Django 관점 — `symbol` 을 `primary_key` 로 두면 Django 가 `id` 컬럼을 만들지 않는다.
    FastAPI + SQLAlchemy 에서 자연키를 쓰던 감각과 같지만, Django Admin 의 URL 이
    `/admin/market/stockmaster/005930/` 처럼 종목코드로 나와 운영에도 편하다.

    **`is_tradable` 같은 컬럼은 두지 않는다.** 거래 불가 조건 5가지가 대회
    `rule_set` 의 임계값에 따라 달라지기 때문이다 (E-04 3.2). 판정은 함수로 한다.
    """

    symbol = models.CharField(max_length=6, primary_key=True, verbose_name="종목코드")
    name = models.CharField(max_length=60, db_index=True, verbose_name="종목명")
    market = models.CharField(max_length=10, choices=Market.choices, verbose_name="시장")
    stock_type = models.CharField(
        max_length=15, choices=StockType.choices, default=StockType.COMMON, verbose_name="종목 구분"
    )

    sector_code = models.CharField(max_length=20, blank=True, db_index=True, verbose_name="업종 코드")
    sector_name = models.CharField(
        max_length=40, blank=True, verbose_name="업종명",
        help_text="KRX 업종분류. GICS 는 MSCI·S&P 의 유료 데이터라 사용하지 않는다",
    )

    listing_date = models.DateField(null=True, blank=True, verbose_name="상장일")
    market_cap = models.BigIntegerField(default=0, verbose_name="시가총액")
    shares_outstanding = models.BigIntegerField(default=0, verbose_name="상장주식수")
    avg_turnover_5d = models.BigIntegerField(default=0, verbose_name="5일 평균 거래대금")
    close_price = models.DecimalField(**PRICE, default=0, verbose_name="전일 종가")

    is_supervised = models.BooleanField(default=False, verbose_name="관리종목")
    alert_level = models.CharField(
        max_length=15, blank=True, verbose_name="시장경보",
        help_text="투자주의 / 투자경고 / 투자위험 / 환기",
    )
    is_delisted = models.BooleanField(default=False, verbose_name="상장폐지")
    is_featured = models.BooleanField(
        default=False, verbose_name="수업용 14종",
        help_text="v1.0 이 하드코딩하던 14종. 시세 조회 실패 시 시뮬레이션 폴백 대상",
    )

    updated_at = models.DateTimeField(auto_now=True)

    # Django 관점 — `QuerySet.as_manager()` 는 쿼리셋 메서드를 `objects` 에도
    # 그대로 노출한다. `StockMaster.objects.contest_universe_candidates()` 와
    # `...filter(...).contest_universe_candidates()` 가 **둘 다 된다.**
    # ★ 매니저를 추가해도 마이그레이션은 생기지 않는다 —
    #   `use_in_migrations = True` 인 매니저만 마이그레이션에 직렬화된다.
    objects = StockMasterQuerySet.as_manager()

    class Meta:
        db_table = "stock_master"
        verbose_name = "종목 마스터"
        verbose_name_plural = "종목 마스터"
        indexes = [
            # 거래 가능 종목 검색 — 규칙 엔진이 매 주문마다 본다
            models.Index(fields=["market", "stock_type", "is_delisted"], name="stock_idx_tradable"),
            models.Index(fields=["-market_cap"], name="stock_idx_market_cap"),   # 시총 랭킹
        ]

    def __str__(self):
        return f"{self.symbol} {self.name}"


class TradingCalendar(models.Model):
    """영업일. pykrx 로 연간을 미리 채우고, 임시 휴장은 운영자가 Admin 에서 수정한다.

    `source` 를 남기는 이유는 **배치가 수동 수정을 덮어쓰지 않게** 하기 위해서다::

        TradingCalendar.objects.filter(source=CalendarSource.PYKRX).bulk_create(
            rows, update_conflicts=True, unique_fields=["date"], update_fields=["is_open"],
        )
    """

    date = models.DateField(primary_key=True, verbose_name="날짜(KST)")
    is_open = models.BooleanField(default=True, verbose_name="개장 여부")
    note = models.CharField(max_length=50, blank=True, verbose_name="비고")
    source = models.CharField(
        max_length=10, choices=CalendarSource.choices, default=CalendarSource.PYKRX
    )

    class Meta:
        db_table = "trading_calendar"
        verbose_name = "영업일"
        verbose_name_plural = "영업일"
        ordering = ["-date"]

    def __str__(self):
        return f"{self.date} {'개장' if self.is_open else '휴장'}"


class UpbitMarket(models.Model):
    """업비트 마켓 목록. 매일 18:00 에 **없는 것만 추가**(upsert)한다. v1.0 동작 승계."""

    market = models.CharField(max_length=20, primary_key=True, verbose_name="마켓 코드")
    korean_name = models.CharField(max_length=40, blank=True, verbose_name="한글명")
    english_name = models.CharField(max_length=60, blank=True, verbose_name="영문명")
    is_warning = models.BooleanField(default=False, verbose_name="유의 종목")
    is_active = models.BooleanField(default=True, verbose_name="활성")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "upbit_market"
        verbose_name = "업비트 마켓"
        verbose_name_plural = "업비트 마켓"

    def __str__(self):
        return f"{self.market} {self.korean_name}"


# ─────────────────────────────────────────────────────────────────
# 2. 캐시 5종 (규약 10장)
# ─────────────────────────────────────────────────────────────────


class CacheBase(models.Model):
    """캐시 테이블 공통.

    `TimeStampedModel` 을 상속하지 않는다 — `fetched_at` / `expires_at` 이 그 역할을 하고,
    매 갱신마다 `updated_at` 을 함께 쓰는 것은 낭비다 (규약 10장).

    `expires_at` 에 인덱스를 거는 이유는 `cleanup` 잡이 만료 행을 지울 때 쓰기 때문이다.

    `source` 에 choices 를 걸지 않는 것은 의미가 모델마다 다르기 때문이다 —
    `QuoteCache` 는 시세 출처 라벨이고 `NewsCache` 는 유니크 키다.
    """

    fetched_at = models.DateTimeField(verbose_name="수집 시각")
    expires_at = models.DateTimeField(db_index=True, verbose_name="만료 시각")
    source = models.CharField(max_length=10, blank=True, verbose_name="출처")

    class Meta:
        abstract = True

    @property
    def is_fresh(self) -> bool:
        return self.expires_at > timezone.now()


class QuoteCache(CacheBase):
    """현재가 캐시. TTL 10초(장중).

    `asset_class` 를 키에 포함하는 이유 — 주식 `005930` 과 코인 `KRW-BTC` 가
    한 테이블을 쓴다. 형식이 달라 충돌하지는 않지만, 자산군별 일괄 조회·정리가 필요하다.
    """

    asset_class = models.CharField(max_length=10, choices=AssetClass.choices)
    symbol = models.CharField(max_length=20)

    price = models.DecimalField(**PRICE, default=0, verbose_name="현재가")
    change = models.DecimalField(**PRICE, default=0, verbose_name="전일 대비")
    change_pct = models.DecimalField(**PCT, default=0, verbose_name="등락률 %")
    volume = models.BigIntegerField(default=0, verbose_name="거래량")
    open = models.DecimalField(**PRICE, default=0, verbose_name="시가")
    high = models.DecimalField(**PRICE, default=0, verbose_name="고가")
    low = models.DecimalField(**PRICE, default=0, verbose_name="저가")
    prev_close = models.DecimalField(**PRICE, default=0, verbose_name="전일 종가")

    is_simulated = models.BooleanField(
        default=False, verbose_name="시뮬레이션 가격",
        help_text=(
            "외부 조회가 전부 실패해 수식으로 만든 값. "
            "대회 체결에는 절대 쓰지 않고, 연습 모드는 화면에 배지를 띄운다"
        ),
    )

    class Meta:
        db_table = "quote_cache"
        verbose_name = "현재가 캐시"
        verbose_name_plural = "현재가 캐시"
        constraints = [
            models.UniqueConstraint(fields=["asset_class", "symbol"], name="quote_cache_uniq"),
        ]

    def __str__(self):
        return f"{self.symbol} {self.price}"


class OrderbookCache(CacheBase):
    """호가 10단계. TTL 5초(장중).

    **10단계를 JSONB 한 칸에 넣는 이유** — 호가는 항상 통째로 읽고 통째로 쓴다.
    40개 컬럼으로 펼치거나 자식 테이블로 나누면 갱신마다 10행을 쓰게 되어 손해다.

    **대회 모드에서만 채운다.** 연습 모드는 현재가에서 파생한 5단계를 화면에서
    만들 뿐이라 KIS 를 호출하지 않는다. 이것이 유량 부담을 크게 줄인다.
    """

    symbol = models.CharField(max_length=20, unique=True)
    levels = models.JSONField(
        default=list, verbose_name="호가 10단계",
        help_text='[{"ask_price":…, "ask_qty":…, "bid_price":…, "bid_qty":…} × 10]',
    )
    total_ask_qty = models.DecimalField(**QTY, default=0, verbose_name="총 매도잔량")
    total_bid_qty = models.DecimalField(**QTY, default=0, verbose_name="총 매수잔량")

    class Meta:
        db_table = "orderbook_cache"
        verbose_name = "호가 캐시"
        verbose_name_plural = "호가 캐시"

    def __str__(self):
        return f"{self.symbol} 호가"


class ChartCache(CacheBase):
    """차트 봉 데이터. TTL 300초."""

    asset_class = models.CharField(max_length=10, choices=AssetClass.choices)
    symbol = models.CharField(max_length=20)
    interval = models.CharField(max_length=10, verbose_name="봉 주기", help_text="1m / 1d / 1w 등")
    payload = models.JSONField(default=list, verbose_name="봉 배열")

    class Meta:
        db_table = "chart_cache"
        verbose_name = "차트 캐시"
        verbose_name_plural = "차트 캐시"
        constraints = [
            models.UniqueConstraint(
                fields=["asset_class", "symbol", "interval"], name="chart_cache_uniq"
            ),
        ]

    def __str__(self):
        return f"{self.symbol} {self.interval}"


class IndexCache(CacheBase):
    """지수 현재값 (KOSPI·KOSDAQ). TTL 60초."""

    index_code = models.CharField(max_length=10, unique=True, verbose_name="지수 코드")
    value = models.DecimalField(max_digits=14, decimal_places=4, default=0, verbose_name="지수")
    change = models.DecimalField(max_digits=14, decimal_places=4, default=0)
    change_pct = models.DecimalField(**PCT, default=0)

    class Meta:
        db_table = "index_cache"
        verbose_name = "지수 캐시"
        verbose_name_plural = "지수 캐시"

    def __str__(self):
        return f"{self.index_code} {self.value}"


class NewsCache(CacheBase):
    """뉴스 목록 캐시. TTL 300초.

    **실패해도 마지막 성공분을 남긴다.** v1.0 은 수집 실패 시 빈 배열을 반환해
    화면이 조용히 비었다. v2.0 은 만료돼도 행을 지우지 않고, 수집 실패는
    `core.DataSyncLog` 에 남겨 Admin 에서 보이게 한다.

    그래서 `cleanup` 잡의 삭제 대상에서도 이 테이블만 빠진다 (E-04 11장).
    """

    payload = models.JSONField(default=list, verbose_name="뉴스 목록")

    class Meta:
        db_table = "news_cache"
        verbose_name = "뉴스 캐시"
        verbose_name_plural = "뉴스 캐시"
        constraints = [
            # 상속받은 `source` 를 유니크 키로 쓴다 — 제공처당 1행
            models.UniqueConstraint(fields=["source"], name="news_cache_uniq"),
        ]

    def __str__(self):
        return f"{self.source} 뉴스"


# ─────────────────────────────────────────────────────────────────
# 3. 운영 3종
# ─────────────────────────────────────────────────────────────────


class ApiCallBudget(models.Model):
    """외부 API 유량 예산.

    KIS 모의투자 API 는 **초당 2건** 제한이다(실전 15~20건). 초과하면 `EGW00201` 이 난다.

    ★ 2026-08-14 정정 — 이 주석은 "모의 5건" 이었는데 근거가 없었다. KIS 공식 SDK
      (`open-trading-api`)가 모의에 `sleep 0.5초`(≈2건/초), 실전에 `0.05초`(≈20건/초)를
      두고 있고, 공식 README 도 "모의투자 계좌는 호출 제한이 낮으니 연속 호출이 많으면
      실전 계좌를 권장" 이라고만 적는다. 코드의 `DEFAULT_RATE_LIMIT`(kis.py)은 처음부터
      `MOCK: 2 · REAL: 15` 였으므로 **동작은 옳았고 주석만 낙관적이었다.**
    서버리스에서는 각 인스턴스가 자기 호출 수만 알기 때문에, 전역 예산을 DB 에서
    관리하지 않으면 반드시 터진다.

    **잠금이 아니라 upsert 를 쓴다**::

        INSERT INTO api_call_budget (api_name, window_start, count)
        VALUES ('KIS', date_trunc('second', now()), 1)
        ON CONFLICT (api_name, window_start)
        DO UPDATE SET count = api_call_budget.count + 1
        RETURNING count;          -- count > 5 이면 호출하지 않고 캐시값으로 응답

    잠금은 트랜잭션이 끝날 때까지 다른 요청을 세우는데, 초당 5건짜리 창에서는
    그 대기 자체가 유량을 낭비한다 (E-04 6장).
    """

    api_name = models.CharField(max_length=20, verbose_name="API 이름")
    window_start = models.DateTimeField(verbose_name="윈도우 시작(초 절삭)")
    count = models.PositiveIntegerField(default=0, verbose_name="호출 수")

    class Meta:
        db_table = "api_call_budget"
        verbose_name = "API 유량 예산"
        verbose_name_plural = "API 유량 예산"
        constraints = [
            models.UniqueConstraint(fields=["api_name", "window_start"], name="budget_uniq_window"),
        ]
        indexes = [
            # cleanup 잡이 1시간 지난 윈도우를 지울 때 쓴다
            models.Index(fields=["window_start"], name="budget_idx_window"),
        ]

    def __str__(self):
        return f"{self.api_name} {self.window_start:%H:%M:%S} × {self.count}"


class SubscriptionRegistry(models.Model):
    """시세 폴링 우선순위.

    초당 5건 = 분당 300건이다. 전 종목 2,700여 개를 주기적으로 갱신하는 건 불가능하다.
    **필요한 것만 갱신한다.**

    | 우선순위 | 대상                                   | 주기      |
    |---|---|---|
    | 1 | 대회 참가자가 **미체결 주문을 걸어둔** 종목 | 5초       |
    | 2 | 대회 참가자가 **보유 중인** 종목          | 30초      |
    | 3 | 화면에서 **보고 있는** 종목               | on-demand |
    | 4 | 그 외                                    | 캐시 만료 시 |
    """

    asset_class = models.CharField(max_length=10, choices=AssetClass.choices)
    symbol = models.CharField(max_length=20)
    priority = models.PositiveSmallIntegerField(default=4, verbose_name="우선순위")
    reason = models.CharField(max_length=20, blank=True, verbose_name="근거")

    # ★★ **E-04 8장에 없는 필드다** (2026-08-13 세션 10 추가 · 변경노트 E-44) ──────
    #
    #   호가(`OrderbookCache`)는 **대회 모드에서만** 필요하다 (F-16 2.5).
    #   연습 모드는 현재가에서 파생한 5단계를 화면에서 만들 뿐이라 KIS 를 부르지 않는다.
    #   그런데 우선순위만으로는 그 구분이 서지 않는다 — 연습 주문도 미체결이면
    #   우선순위 1 로 올라오기 때문이다. 구분 없이 폴링하면 **귀한 KIS 유량이
    #   호가를 쓰지도 않는 연습 종목에 낭비된다.**
    #
    #   ★ `market` 앱은 다른 앱을 참조하지 않는다(규약 1.1). "대회인가" 를 여기서
    #     알아낼 방법이 없고, 알아내서도 안 된다. **등록하는 쪽(trading)이 알려준다.**
    #     그래서 컬럼 이름도 `is_contest` 가 아니라 `needs_orderbook` 이다 —
    #     market 이 아는 것은 도메인이 아니라 **캐시 수요**다.
    needs_orderbook = models.BooleanField(
        default=False, verbose_name="호가 필요",
        help_text="실호가로 체결하는 종목(대회)만 참. 잡 2 가 KIS 유량을 여기에만 쓴다",
    )

    last_polled_at = models.DateTimeField(null=True, blank=True, verbose_name="마지막 폴링")
    expires_at = models.DateTimeField(
        null=True, blank=True, verbose_name="만료",
        help_text="화면 열람(우선순위 3)은 일정 시간 후 만료된다",
    )

    class Meta:
        db_table = "subscription_registry"
        verbose_name = "시세 구독"
        verbose_name_plural = "시세 구독"
        constraints = [
            models.UniqueConstraint(fields=["asset_class", "symbol"], name="subs_uniq_symbol"),
        ]
        indexes = [
            # 잡이 "우선순위 높고 가장 오래된 것부터" 뽑는다
            models.Index(fields=["priority", "last_polled_at"], name="subs_idx_priority_polled"),
        ]

    def __str__(self):
        return f"{self.symbol} (P{self.priority})"


class ExternalToken(models.Model):
    """외부 브로커 접근 토큰.

    서버리스라 메모리에 두면 콜드스타트마다 재발급하게 된다.
    토큰 발급 자체에도 유량 제한이 있어 금방 막힌다.

    **보안** — `APP_KEY` / `APP_SECRET` 은 **환경변수에만** 둔다. DB 에 넣지 않는다.
    DB 에 저장하는 것은 **발급된 액세스 토큰뿐**이고, 이것도 24시간 후 만료된다.

    ★ 2026-08-13 — 강사님 원본이 KIS 하나에서 KB증권·Alpaca 까지 넓어져
    `provider` 를 `BrokerProvider` 선택지로 바꾸고 `environment` 를 추가했다.
    KIS 는 모의/실전, Alpaca 는 Paper/Live 로 **같은 브로커에 자격증명이 둘**이라
    유니크 키를 `(provider, environment)` 로 잡는다. 발급 절차는
    `docs/v2-design/api/version2.0/api_발급_가이드.md` 참조.
    """

    class Environment(models.TextChoices):
        MOCK = "MOCK", "모의투자 / Paper"
        REAL = "REAL", "실전 / Live"

    provider = models.CharField(
        max_length=20, choices=BrokerProvider.choices, verbose_name="브로커"
    )
    environment = models.CharField(
        max_length=10, choices=Environment.choices, default=Environment.MOCK, verbose_name="환경"
    )
    access_token = models.TextField(verbose_name="액세스 토큰")
    token_type = models.CharField(max_length=20, default="Bearer", verbose_name="토큰 종류")
    issued_at = models.DateTimeField(verbose_name="발급 시각")
    expires_at = models.DateTimeField(db_index=True, verbose_name="만료 시각")

    class Meta:
        db_table = "external_token"
        verbose_name = "외부 브로커 토큰"
        verbose_name_plural = "외부 브로커 토큰"
        constraints = [
            models.UniqueConstraint(
                fields=["provider", "environment"], name="external_token_uniq"
            ),
        ]

    def __str__(self):
        return f"{self.provider}/{self.environment}"

    @property
    def is_valid(self) -> bool:
        return self.expires_at > timezone.now()


# ─────────────────────────────────────────────────────────────────
# 4. 집계 2종
# ─────────────────────────────────────────────────────────────────


class CryptoRank(models.Model):
    """코인 랭킹 — **KRW 기준으로 재정의** (결함 D-3).

    v1.0 은 CoinMarketCap Top 100(USD 기준)을 매시 `TRUNCATE` 후 재적재했는데
    **어느 화면에서도 쓰지 않았다.** 거래는 KRW 기준이라 데이터가 연결되지도 않았다.

    `TRUNCATE` 를 버리는 이유 — 동기화 중에 조회하면 빈 테이블이 보이는 창이 생긴다.
    CoinMarketCap API 키 의존도 함께 사라진다. 매시 **upsert** 한다.
    """

    market = models.CharField(max_length=20, unique=True, verbose_name="마켓 코드")
    rank = models.PositiveIntegerField(db_index=True, verbose_name="순위")
    trade_price = models.DecimalField(**PRICE, default=0, verbose_name="현재가")
    acc_trade_price_24h = models.BigIntegerField(
        default=0, verbose_name="24시간 거래대금(KRW)", help_text="랭킹 기준값"
    )
    change_rate = models.DecimalField(**PCT, default=0, verbose_name="등락률 %")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "crypto_rank"
        verbose_name = "코인 랭킹"
        verbose_name_plural = "코인 랭킹"
        ordering = ["rank"]

    def __str__(self):
        return f"{self.rank}. {self.market}"


class MarketIndexSnapshot(models.Model):
    """벤치마크 지수 일별 종가.

    NAV 차트에 KOSPI·KOSDAQ 을 겹쳐 그리려면 지수 종가도 함께 쌓아야 한다.

    ★ **`nav` 컬럼을 두지 않는다** (E-04 10.1) — F-05 4.4 는 `nav` 를 포함해 적었지만,
    NAV 는 "대회 시작일을 1000 으로 놓은 지수"인데 이 테이블은 대회와 무관한 전역
    시계열이다. 대회마다 기준일이 달라 한 행에 하나의 NAV 를 담을 수 없다.

        지수 NAV(대회 c, 날짜 d) = 1000 × close(d) / close(대회 c 의 시작일)

    종가만 저장하고 NAV 는 조회 시 환산한다.
    """

    date = models.DateField(verbose_name="영업일(KST)")
    index_code = models.CharField(max_length=10, verbose_name="지수 코드")
    close = models.DecimalField(max_digits=14, decimal_places=4, verbose_name="종가")
    change_pct = models.DecimalField(**PCT, default=0, verbose_name="등락률 %")

    class Meta:
        db_table = "market_index_snapshot"
        verbose_name = "지수 일별 종가"
        verbose_name_plural = "지수 일별 종가"
        constraints = [
            models.UniqueConstraint(fields=["date", "index_code"], name="index_snapshot_uniq"),
        ]
        indexes = [
            models.Index(fields=["index_code", "-date"], name="index_snap_idx_code_date"),
        ]

    def __str__(self):
        return f"{self.index_code} {self.date} {self.close}"
