"""체결 엔진 테스트 (F-03).

★ **왜 여기에 테스트를 촘촘히 쓰는가** — 체결은 **눈으로 확인할 수 없는 경로**다.
  적재 잡이 틀리면 종목 수가 이상해져 드러나지만, 체결이 1원 틀리면 아무도 모른 채
  대회가 끝나고 순위가 뒤바뀐다. 증상이 없는 결함은 테스트만이 잡는다.

    python manage.py test trading
"""

from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from accounts.models import Account, Member
from contests.models import (
    Contest,
    ContestSectorWeight,
    ContestStatus,
    ContestUniverse,
    Participation,
    ParticipationStatus,
)
from contests.rules import RuleRejection
from core.constants import AccountMode, AssetClass, OrderSide, SyncStatus
from core.models import AppSetting, DataSyncLog
from market.models import Market, OrderbookCache, QuoteCache, StockMaster, StockType
from market.quotes import Orderbook, PriceUnavailable, tick_size
from trading import jobs as trading_jobs
from trading import services
from trading.models import Execution, Order, OrderStatus, OrderType, Position

SYMBOL = "005930"


class MarketOpenMixin:
    """이 테스트 클래스가 도는 동안 **장이 열려 있다고 본다** (세션 10 · 변경노트 E-47).

    ★★ **왜 필요한가 — 테스트가 시계에 묶여 있었다** ─────────────────────────

    대회 주문은 장외에 `PENDING_OPEN` 으로 접수되고 **체결되지 않는다**(F-03 8장).
    그래서 "주문 → 즉시 체결 → 보유가 생긴다" 를 전제한 테스트는 **평일 09:00~15:30
    에만 통과했다.** 세션 9 는 장중에 작성·검증돼 이 사실이 드러나지 않았고,
    세션 10 이 16:11 KST 에 돌리자 9건이 한꺼번에 실패했다.

        테스트는 **언제 돌려도 같은 답**을 내야 한다. 시계는 입력이지 환경이 아니다.

    장외 동작 자체를 검증하는 테스트(`MarketClosedTests`)는 자기 안에서 다시
    패치하므로 이 믹스인과 충돌하지 않는다 — 안쪽 패치가 이긴다.

    ★★ **`setUp` 이 아니라 `setUpClass` 인 이유** ───────────────────────────

    믹스인에 `setUp` 을 두면 **하위 클래스가 정의한 `setUp` 에 그대로 가려진다.**
    (`class PlaceOrderTests(MarketOpenMixin, TestCase)` 에서 `PlaceOrderTests.setUp`
    이 MRO 앞에 온다.) 하위 클래스마다 `super().setUp()` 을 부르도록 고치는 방법도
    있지만, 한 곳만 빠뜨려도 **그 클래스만 조용히 시계에 다시 묶인다.**
    `setUpClass` 는 하위 클래스가 정의하지 않으므로 가려질 일이 없다.
    """

    @classmethod
    def setUpClass(cls):
        from unittest.mock import patch

        from market.sessions import SessionState

        super().setUpClass()
        # ★★ **두 곳을 함께 잡아야 한다.** 장 시간을 보는 경로가 둘이다.
        #
        #     contests.rules.session_state    주문 접수 — PENDING_OPEN 인가 (F-03 8장)
        #     trading.jobs.is_market_open     추종 체결 — 대회 주문을 건너뛸 것인가
        #
        #   한쪽만 잡으면 "주문은 접수됐는데 잡이 체결하지 않는" 어중간한 상태가 되어
        #   실패 메시지가 원인을 가리키지 못한다.
        #
        # ★ 각 모듈이 `from … import` 로 **가져간 이름**을 갈아끼운다.
        #   원본(`market.sessions.…`)을 패치하면 이미 묶인 참조는 바뀌지 않아
        #   아무 효과가 없다 — 파이썬 import 의 흔한 함정이다.
        for target, value in (
            ("contests.rules.session_state", SessionState.OPEN),
            ("trading.jobs.is_market_open", True),
        ):
            patcher = patch(target, return_value=value)
            patcher.start()
            cls.addClassCleanup(patcher.stop)


# ─────────────────────────────────────────────────────────────────
# 픽스처 도우미
# ─────────────────────────────────────────────────────────────────


def make_book(symbol=SYMBOL, *, asks=None, bids=None, fetched_at=None):
    """호가 캐시를 만든다. `asks` · `bids` 는 `[(가격, 잔량), …]` 오름/내림차순."""
    asks = asks or [(74_300, 60), (74_400, 40), (74_500, 100)]
    bids = bids or [(74_200, 50), (74_100, 80), (74_000, 120)]
    levels = []
    for index in range(max(len(asks), len(bids))):
        ask = asks[index] if index < len(asks) else (0, 0)
        bid = bids[index] if index < len(bids) else (0, 0)
        levels.append({
            "ask_price": ask[0], "ask_qty": ask[1],
            "bid_price": bid[0], "bid_qty": bid[1],
        })
    now = fetched_at or timezone.now()
    return OrderbookCache.objects.create(
        symbol=symbol,
        levels=levels,
        fetched_at=now,
        expires_at=now + timezone.timedelta(seconds=5),
        source="KIS",
    )


def make_quote(symbol=SYMBOL, price=74_300, *, prev_close=74_000, simulated=False, fetched_at=None,
               asset_class=AssetClass.STOCK):
    now = fetched_at or timezone.now()
    return QuoteCache.objects.create(
        asset_class=asset_class,
        symbol=symbol,
        price=Decimal(price),
        prev_close=Decimal(prev_close),
        is_simulated=simulated,
        fetched_at=now,
        expires_at=now + timezone.timedelta(seconds=10),
        source="KIS",
    )


def make_stock(symbol=SYMBOL, *, name="삼성전자", market_cap=400_000_000_000_000,
               turnover=1_000_000_000_000, sector="1013", stock_type=StockType.COMMON):
    return StockMaster.objects.create(
        symbol=symbol, name=name, market=Market.KOSPI, stock_type=stock_type,
        sector_code=sector, sector_name="전기전자",
        market_cap=market_cap, avg_turnover_5d=turnover, close_price=Decimal(74_000),
    )


def make_calendar(days=10):
    """오늘을 포함한 최근 영업일을 달력에 채운다.

    ★ 넣지 않으면 `market.sessions` 가 요일로 폴백하며 INFO 로그를 쏟아 테스트
      출력이 덮인다. 무엇보다 **실제 운영에서는 달력이 채워져 있는 것이 정상**이라,
      그 상태를 기준으로 시험하는 편이 맞다.
    """
    from market.models import TradingCalendar

    today = timezone.localdate()
    TradingCalendar.objects.bulk_create(
        [
            TradingCalendar(
                date=today - timezone.timedelta(days=offset),
                is_open=(today - timezone.timedelta(days=offset)).weekday() < 5,
            )
            for offset in range(days)
        ],
        ignore_conflicts=True,
    )


def make_member(username="tester"):
    return Member.objects.create_user(
        username=username, email=f"{username}@example.com", password="pass-1234!"
    )


def make_practice_account(member, cash=100_000_000, mode=AccountMode.PRACTICE_STOCK):
    return Account.objects.create(
        member=member, contest=None, mode=mode, cash=cash, initial_capital=cash
    )


def make_contest_account(member, cash=100_000_000, *, contest=None, symbol=SYMBOL):
    """대회 + 참가 + 계좌를 한 번에. 유니버스·섹터 한도·영업일까지 채운다."""
    make_calendar()
    if contest is None:
        today = timezone.localdate()
        contest = Contest.objects.create(
            name="테스트 대회", slug=f"test-{Contest.objects.count()}",
            status=ContestStatus.ONGOING,
            start_date=today - timezone.timedelta(days=1),
            end_date=today + timezone.timedelta(days=30),
            initial_capital=cash, fee_bp=10, tax_bp=20,
        )
        ContestUniverse.objects.create(
            contest=contest, symbol=symbol, name="삼성전자", market=Market.KOSPI,
            sector_code="1013", sector_name="전기전자", frozen_at=timezone.now(),
        )
        ContestSectorWeight.objects.create(
            contest=contest, sector_code="1013", sector_name="전기전자",
            market_weight_pct=Decimal("25.0"), limit_pct=Decimal("50.0"),
        )
    account = Account.objects.create(
        member=member, contest=contest, mode=AccountMode.CONTEST,
        cash=cash, initial_capital=cash,
    )
    Participation.objects.create(
        contest=contest, member=member, account=account,
        nickname=f"참가자{member.pk}", status=ParticipationStatus.APPROVED,
    )
    return account


def book_from(symbol=SYMBOL) -> Orderbook:
    from market import quotes as market_quotes

    return market_quotes.get_orderbook(symbol)


# ─────────────────────────────────────────────────────────────────
# 1. 거래 비용 (F-03 6장)
# ─────────────────────────────────────────────────────────────────


class CostTests(TestCase):
    """수수료·세금은 **원 단위 floor 절사**하고, 매도세는 매도에만 붙는다."""

    def test_수수료는_내림한다(self):
        # 7,431,000 × 10bp = 7,431.0 → 7,431
        fee, tax = services.calc_cost(7_431_000, OrderSide.BUY, Decimal(10), Decimal(20))
        self.assertEqual((fee, tax), (7_431, 0))

    def test_소수가_생기면_버린다(self):
        # 1,234,567 × 10bp = 1,234.567 → 1,234 (반올림이면 1,235 다)
        fee, _ = services.calc_cost(1_234_567, OrderSide.BUY, Decimal(10), Decimal(20))
        self.assertEqual(fee, 1_234)

    def test_매도에만_세금이_붙는다(self):
        fee, tax = services.calc_cost(1_000_000, OrderSide.SELL, Decimal(10), Decimal(20))
        self.assertEqual((fee, tax), (1_000, 2_000))

    def test_소수_bp_도_계산된다(self):
        """연습(주식)은 1.5bp = 0.015% 다 — 정수 bp 로는 표현할 수 없다."""
        fee, _ = services.calc_cost(10_000_000, OrderSide.BUY, Decimal("1.5"), Decimal(18))
        self.assertEqual(fee, 1_500)

    def test_대회_요율은_Contest_에서_읽는다(self):
        """★ 전역 설정이 아니다 — 값을 고치면 진행 중인 대회 규칙이 바뀐다."""
        member = make_member()
        account = make_contest_account(member)
        account.contest.fee_bp = 25
        account.contest.tax_bp = 30
        account.contest.save(update_fields=["fee_bp", "tax_bp"])
        account.refresh_from_db()
        self.assertEqual(services.cost_rates(account), (Decimal(25), Decimal(30)))

    def test_연습_요율은_자산군마다_다르다(self):
        """코인에는 매도세가 없다 (F-03 6장)."""
        member = make_member()
        stock = make_practice_account(member, mode=AccountMode.PRACTICE_STOCK)
        crypto = make_practice_account(member, mode=AccountMode.PRACTICE_CRYPTO)

        self.assertEqual(services.cost_rates(stock), (Decimal("1.5"), Decimal("18")))
        self.assertEqual(services.cost_rates(crypto), (Decimal("5"), Decimal("0")))

    def test_요율_설정이_없으면_0_이고_지어내지_않는다(self):
        AppSetting.objects.filter(key__startswith="practice.").delete()
        member = make_member()
        account = make_practice_account(member)
        with self.assertLogs("trading.services", level="WARNING"):
            self.assertEqual(services.cost_rates(account), (Decimal(0), Decimal(0)))


# ─────────────────────────────────────────────────────────────────
# 2. 호가 소진 체결 (F-03 5.1)
# ─────────────────────────────────────────────────────────────────


class MatchOrderbookTests(TestCase):
    def setUp(self):
        make_book()
        self.book = book_from()
        self.quote = None

    def test_최우선호가_잔량_안이면_한_조각(self):
        result = services.match_orderbook(
            book=self.book, side=OrderSide.BUY, limit_price=Decimal(74_500), qty=Decimal(50)
        )
        self.assertEqual(len(result.fills), 1)
        self.assertEqual(result.fills[0].price, Decimal(74_300))
        self.assertEqual(result.remaining_qty, Decimal(0))

    def test_여러_호가를_소진하면_가중평균이_된다(self):
        """★ 큰 주문은 불리한 가격에 체결된다 — 대회를 연습과 가르는 지점이다."""
        result = services.match_orderbook(
            book=self.book, side=OrderSide.BUY, limit_price=Decimal(74_500), qty=Decimal(120)
        )
        # 60@74,300 + 40@74,400 + 20@74,500
        self.assertEqual(len(result.fills), 3)
        self.assertEqual(result.filled_qty, Decimal(120))
        expected = 60 * 74_300 + 40 * 74_400 + 20 * 74_500
        self.assertEqual(result.gross_amount, expected)
        self.assertGreater(result.avg_price, Decimal(74_300))   # 최우선호가보다 비싸다

    def test_지정가를_넘으면_거기서_멈춘다(self):
        result = services.match_orderbook(
            book=self.book, side=OrderSide.BUY, limit_price=Decimal(74_300), qty=Decimal(200)
        )
        self.assertEqual(result.filled_qty, Decimal(60))        # 1호가 잔량만
        self.assertEqual(result.remaining_qty, Decimal(140))    # 나머지는 PARTIAL

    def test_매도는_매수호가를_비싼_것부터_먹는다(self):
        result = services.match_orderbook(
            book=self.book, side=OrderSide.SELL, limit_price=Decimal(74_000), qty=Decimal(60)
        )
        self.assertEqual(result.fills[0].price, Decimal(74_200))   # 가장 비싼 매수호가
        self.assertEqual(result.fills[1].price, Decimal(74_100))

    def test_호가_순서가_뒤집혀_있어도_결과가_같다(self):
        """캐시 JSON 의 배열 순서를 믿지 않는다."""
        OrderbookCache.objects.all().delete()
        make_book(asks=[(74_500, 100), (74_300, 60), (74_400, 40)])
        result = services.match_orderbook(
            book=book_from(), side=OrderSide.BUY, limit_price=Decimal(74_500), qty=Decimal(60)
        )
        self.assertEqual(result.fills[0].price, Decimal(74_300))

    def test_잔량이_0_인_단계는_건너뛴다(self):
        OrderbookCache.objects.all().delete()
        make_book(asks=[(74_300, 0), (74_400, 50)])
        result = services.match_orderbook(
            book=book_from(), side=OrderSide.BUY, limit_price=Decimal(74_500), qty=Decimal(30)
        )
        self.assertEqual(result.fills[0].price, Decimal(74_400))


class AssumedDepthTests(TestCase):
    """10호가를 넘어선 가정 체결 (F-03 5.1 4단계 · E-03 5.1)."""

    def setUp(self):
        # 3단계뿐인 얕은 호가창 — 금방 바닥난다
        make_book(asks=[(74_300, 10), (74_400, 10), (74_500, 10)])
        self.book = book_from()
        self.quote = make_quote(prev_close=74_000)

    def test_호가를_다_먹으면_가정으로_이어간다(self):
        from market import quotes as market_quotes

        quote = market_quotes.get_quote(AssetClass.STOCK, SYMBOL, for_contest=True)
        result = services.match_orderbook(
            book=self.book, side=OrderSide.BUY, limit_price=Decimal(80_000),
            qty=Decimal(60), quote=quote,
        )
        self.assertEqual(result.filled_qty, Decimal(60))
        self.assertTrue(result.has_assumed_depth)

    def test_가정_체결_조각은_표시된다(self):
        """★ 정직성 — 유리한 가정으로 체결된 조각은 화면에 배지를 띄운다."""
        from market import quotes as market_quotes

        quote = market_quotes.get_quote(AssetClass.STOCK, SYMBOL, for_contest=True)
        result = services.match_orderbook(
            book=self.book, side=OrderSide.BUY, limit_price=Decimal(80_000),
            qty=Decimal(60), quote=quote,
        )
        real = [fill for fill in result.fills if not fill.is_assumed_depth]
        assumed = [fill for fill in result.fills if fill.is_assumed_depth]
        self.assertEqual(sum(f.qty for f in real), Decimal(30))     # 실제 호가 3단계
        self.assertTrue(assumed)
        self.assertTrue(all(f.price > Decimal(74_500) for f in assumed))

    def test_가정_체결도_1틱씩_올라간다(self):
        from market import quotes as market_quotes

        quote = market_quotes.get_quote(AssetClass.STOCK, SYMBOL, for_contest=True)
        result = services.match_orderbook(
            book=self.book, side=OrderSide.BUY, limit_price=Decimal(80_000),
            qty=Decimal(60), quote=quote,
        )
        assumed = [f for f in result.fills if f.is_assumed_depth]
        # 74,300원대의 호가단위는 100원
        self.assertEqual(assumed[0].price, Decimal(74_600))
        self.assertEqual(assumed[1].price, Decimal(74_700))

    def test_주식_체결_조각은_정수_주다(self):
        """★ 평균 잔량 (60+40+30)/3 = 43.33 을 그대로 쓰면 **0.33주가 체결된다.**
        실측에서 실제로 나왔던 값이다 (변경노트 E-38)."""
        from market import quotes as market_quotes

        quote = market_quotes.get_quote(AssetClass.STOCK, SYMBOL, for_contest=True)
        result = services.match_orderbook(
            book=self.book, side=OrderSide.BUY, limit_price=Decimal(80_000),
            qty=Decimal(60), quote=quote, asset_class=AssetClass.STOCK,
        )
        for fill in result.fills:
            self.assertEqual(fill.qty, fill.qty.to_integral_value(), f"{fill.qty} 주는 쪼갤 수 없다")

    def test_코인은_소수_수량이_허용된다(self):
        """같은 호가창이라도 자산군에 따라 절사 단위가 달라진다."""
        from market import quotes as market_quotes

        # 평균이 소수가 되는 잔량 (10+10+11)/3 = 10.333…
        OrderbookCache.objects.all().delete()
        make_book(asks=[(74_300, 10), (74_400, 10), (74_500, 11)])
        book = market_quotes.get_orderbook(SYMBOL)
        quote = market_quotes.get_quote(AssetClass.STOCK, SYMBOL, for_contest=True)

        stock = services.match_orderbook(
            book=book, side=OrderSide.BUY, limit_price=Decimal(80_000),
            qty=Decimal(60), quote=quote, asset_class=AssetClass.STOCK,
        )
        crypto = services.match_orderbook(
            book=book, side=OrderSide.BUY, limit_price=Decimal(80_000),
            qty=Decimal(60), quote=quote, asset_class=AssetClass.CRYPTO,
        )
        stock_assumed = [f for f in stock.fills if f.is_assumed_depth]
        crypto_assumed = [f for f in crypto.fills if f.is_assumed_depth]

        self.assertEqual(stock_assumed[0].qty, Decimal(10))            # 10.333 → 10
        self.assertEqual(crypto_assumed[0].qty, Decimal("10.33333333"))

    def test_상한가를_넘어서지_않는다(self):
        """★ 시장가(제한 없음)라도 존재하지 않는 가격까지 파고들면 안 된다."""
        from market import quotes as market_quotes

        quote = market_quotes.get_quote(AssetClass.STOCK, SYMBOL, for_contest=True)
        upper, _ = market_quotes.price_limits(Decimal(74_000))
        result = services.match_orderbook(
            book=self.book, side=OrderSide.BUY, limit_price=None,
            qty=Decimal(100_000), quote=quote,
        )
        self.assertTrue(all(fill.price <= upper for fill in result.fills))
        self.assertGreater(result.remaining_qty, 0)      # 다 못 채우고 멈췄다

    def test_호가창이_비면_가정도_하지_않는다(self):
        OrderbookCache.objects.all().delete()
        make_book(asks=[(0, 0)], bids=[(0, 0)])
        result = services.match_orderbook(
            book=book_from(), side=OrderSide.BUY, limit_price=Decimal(80_000), qty=Decimal(10)
        )
        self.assertEqual(result.fills, [])


class TickSizeTests(TestCase):
    def test_가격대별_호가단위(self):
        self.assertEqual(tick_size(Decimal(1_500)), Decimal(1))
        self.assertEqual(tick_size(Decimal(3_000)), Decimal(5))
        self.assertEqual(tick_size(Decimal(74_300)), Decimal(100))
        self.assertEqual(tick_size(Decimal(300_000)), Decimal(500))
        self.assertEqual(tick_size(Decimal(900_000)), Decimal(1_000))


# ─────────────────────────────────────────────────────────────────
# 3. 가격 유형 (F-03 4장)
# ─────────────────────────────────────────────────────────────────


class LimitPriceTests(TestCase):
    def setUp(self):
        make_book()
        make_quote()
        self.book = book_from()
        from market import quotes as market_quotes

        self.quote = market_quotes.get_quote(AssetClass.STOCK, SYMBOL, for_contest=True)

    def _resolve(self, **kwargs):
        params = dict(
            order_type=OrderType.RELATIVE, side=OrderSide.BUY, price_level=1,
            limit_price=None, book=self.book, quote=self.quote,
        )
        params.update(kwargs)
        return services.resolve_limit_price(**params)

    def test_상대호가_매수는_매도호가다(self):
        """공격적 주문 — 상대를 찾아간다."""
        self.assertEqual(self._resolve(price_level=1), Decimal(74_300))
        self.assertEqual(self._resolve(price_level=2), Decimal(74_400))

    def test_자기호가_매수는_매수호가다(self):
        """소극적 주문 — 내 편 줄에 선다."""
        self.assertEqual(
            self._resolve(order_type=OrderType.OWN, price_level=1), Decimal(74_200)
        )

    def test_매도는_반대다(self):
        self.assertEqual(self._resolve(side=OrderSide.SELL), Decimal(74_200))
        self.assertEqual(
            self._resolve(side=OrderSide.SELL, order_type=OrderType.OWN), Decimal(74_300)
        )

    def test_지정가는_입력값_그대로(self):
        self.assertEqual(
            self._resolve(order_type=OrderType.LIMIT, limit_price=Decimal(70_000)),
            Decimal(70_000),
        )

    def test_시장가와_STOP_은_제한이_없다(self):
        self.assertIsNone(self._resolve(order_type=OrderType.MARKET))
        self.assertIsNone(self._resolve(order_type=OrderType.STOP))

    def test_호가_단계는_1에서_10까지(self):
        with self.assertRaises(services.OrderRejected):
            self._resolve(price_level=11)

    def test_상한가에_갇히면_자기호가_매도는_상한가다(self):
        """F-03 4.1 특례 — 파는 사람이 없어 매도호가가 통째로 빈 상황."""
        OrderbookCache.objects.all().delete()
        make_book(asks=[(0, 0)], bids=[(96_200, 5_000)])
        from market import quotes as market_quotes

        book = market_quotes.get_orderbook(SYMBOL)
        upper, _ = market_quotes.price_limits(Decimal(74_000))
        with self.assertLogs("trading.services", level="INFO"):
            price = services.resolve_limit_price(
                order_type=OrderType.OWN, side=OrderSide.SELL, price_level=1,
                limit_price=None, book=book, quote=self.quote,
            )
        self.assertEqual(price, upper)

    def test_상대호가가_비면_체결하지_않고_남긴다(self):
        """받아 줄 사람이 없는 것은 거부 사유가 아니다."""
        OrderbookCache.objects.all().delete()
        make_book(asks=[(0, 0)], bids=[(74_200, 10)])
        from market import quotes as market_quotes

        with self.assertRaises(PriceUnavailable) as ctx:
            services.resolve_limit_price(
                order_type=OrderType.RELATIVE, side=OrderSide.BUY, price_level=1,
                limit_price=None, book=market_quotes.get_orderbook(SYMBOL), quote=self.quote,
            )
        self.assertTrue(ctx.exception.recoverable)


# ─────────────────────────────────────────────────────────────────
# 4. 포지션·잔고 반영 (F-03 7장)
# ─────────────────────────────────────────────────────────────────


class PositionTests(TestCase):
    def setUp(self):
        self.member = make_member()
        self.account = make_practice_account(self.member, cash=100_000_000)
        make_stock()
        make_quote(price=74_000)

    def _buy(self, qty, price=74_000):
        return services.place_order(
            account=self.account, symbol=SYMBOL, side=OrderSide.BUY,
            order_type=OrderType.MARKET, qty=Decimal(qty),
        )

    def test_매수하면_현금이_수수료까지_빠진다(self):
        make_quote  # noqa: B018 — 위 setUp 에서 이미 만들었다
        order = self._buy(100)
        self.account.refresh_from_db()
        gross = 100 * 74_000
        fee = int(gross * Decimal("1.5") / 10000)
        self.assertEqual(order.status, OrderStatus.FILLED)
        self.assertEqual(self.account.cash, 100_000_000 - gross - fee)

    def test_평단이_재계산된다(self):
        self._buy(100)                     # 74,000 × 100
        QuoteCache.objects.all().delete()
        make_quote(price=76_000)
        self._buy(100)                     # 76,000 × 100
        position = Position.objects.get(account=self.account, symbol=SYMBOL)
        self.assertEqual(position.qty, Decimal(200))
        self.assertEqual(position.avg_price, Decimal(75_000))    # (74,000+76,000)/2

    def test_주식_평단은_정수로_절사한다(self):
        self._buy(3)                       # 74,000 × 3
        QuoteCache.objects.all().delete()
        make_quote(price=74_001)
        self._buy(1)                       # 평단 74,000.25
        position = Position.objects.get(account=self.account, symbol=SYMBOL)
        self.assertEqual(position.avg_price, Decimal(74_000))

    def test_매도하면_평단은_그대로고_원금만_준다(self):
        self._buy(100)
        services.place_order(
            account=self.account, symbol=SYMBOL, side=OrderSide.SELL,
            order_type=OrderType.MARKET, qty=Decimal(40),
        )
        position = Position.objects.get(account=self.account, symbol=SYMBOL)
        self.assertEqual(position.qty, Decimal(60))
        self.assertEqual(position.avg_price, Decimal(74_000))     # 불변
        self.assertEqual(position.principal, 74_000 * 100 - 74_000 * 40)

    def test_전량_매도하면_행이_사라진다(self):
        """★ 0 행을 남기면 '보유 종목 수' 집계마다 qty>0 을 달아야 한다 (F-03 7.2)."""
        self._buy(100)
        services.place_order(
            account=self.account, symbol=SYMBOL, side=OrderSide.SELL,
            order_type=OrderType.MARKET, qty=Decimal(100),
        )
        self.assertFalse(Position.objects.filter(account=self.account, symbol=SYMBOL).exists())

    def test_실현손익을_체결_시점에_저장한다(self):
        """★ Position 은 현재 상태만 갖고 있어 사후에 평단을 복원할 수 없다 (E-03 4.4)."""
        self._buy(100)                                   # 평단 74,000
        QuoteCache.objects.all().delete()
        make_quote(price=80_000)
        order = services.place_order(
            account=self.account, symbol=SYMBOL, side=OrderSide.SELL,
            order_type=OrderType.MARKET, qty=Decimal(50),
        )
        gross = 50 * 80_000
        fee = int(gross * Decimal("1.5") / 10000)
        tax = int(gross * Decimal(18) / 10000)
        self.assertEqual(order.realized_pnl, (80_000 - 74_000) * 50 - fee - tax)

    def test_보유보다_많이_팔면_보유_전량으로_잘린다(self):
        self._buy(100)
        order = services.place_order(
            account=self.account, symbol=SYMBOL, side=OrderSide.SELL,
            order_type=OrderType.MARKET, qty=Decimal(500),
        )
        self.assertEqual(order.requested_qty, Decimal(100))

    def test_보유하지_않은_종목은_팔_수_없다(self):
        with self.assertRaises(services.OrderRejected) as ctx:
            services.place_order(
                account=self.account, symbol=SYMBOL, side=OrderSide.SELL,
                order_type=OrderType.MARKET, qty=Decimal(10),
            )
        self.assertEqual(ctx.exception.rule, "INSUFFICIENT_POSITION")

    def test_현금보다_많이_사면_수수료까지_따져_막는다(self):
        with self.assertRaises(services.OrderRejected) as ctx:
            self._buy(2_000)          # 74,000 × 2,000 = 1.48억 > 1억
        self.assertEqual(ctx.exception.rule, "INSUFFICIENT_CASH")
        self.assertIn("fee_krw", ctx.exception.detail)

    def test_거부된_주문은_행으로_남지_않는다(self):
        with self.assertRaises(services.OrderRejected):
            self._buy(2_000)
        self.assertEqual(Order.objects.count(), 0)


class ExecutionRecordTests(MarketOpenMixin, TestCase):
    """체결 조각 기록 — 가중평균을 되짚을 수 있어야 한다."""

    def setUp(self):
        self.member = make_member()
        self.account = make_contest_account(self.member)
        make_stock()
        make_book()
        make_quote()

    def test_호가_단계마다_조각이_남는다(self):
        order = services.place_order(
            account=self.account, symbol=SYMBOL, side=OrderSide.BUY,
            order_type=OrderType.RELATIVE, price_level=3, weight_pct=Decimal("0.1"),
        )
        rows = list(Execution.objects.filter(order=order).order_by("seq"))
        self.assertTrue(rows)
        self.assertEqual([row.seq for row in rows], list(range(1, len(rows) + 1)))
        # 조각들의 가중평균이 주문의 avg_fill_price 와 같아야 한다
        total_amount = sum(row.amount for row in rows)
        total_qty = sum(row.qty for row in rows)
        self.assertEqual(order.gross_amount, total_amount)
        self.assertEqual(
            order.avg_fill_price,
            (Decimal(total_amount) / total_qty).quantize(Decimal("0.00000001")),
        )


# ─────────────────────────────────────────────────────────────────
# 5. 주문 접수 (F-04 4.1)
# ─────────────────────────────────────────────────────────────────


class PlaceOrderTests(MarketOpenMixin, TestCase):
    def setUp(self):
        self.member = make_member()
        self.account = make_contest_account(self.member, cash=100_000_000)
        make_stock()
        make_book()
        make_quote()

    def test_대회는_비중으로_주문한다(self):
        """★ 수량이 아니라 순자산 대비 % 다 (F-03 3.1)."""
        order = services.place_order(
            account=self.account, symbol=SYMBOL, side=OrderSide.BUY,
            order_type=OrderType.LIMIT, limit_price=Decimal(74_300),
            weight_pct=Decimal("5.0"),
        )
        # floor(1억 × 5% / 74,300) = floor(67.29) = 67
        self.assertEqual(order.requested_qty, Decimal(67))
        self.assertEqual(order.requested_weight_pct, Decimal("5.0"))

    def test_비중이_없으면_거부한다(self):
        with self.assertRaises(services.OrderRejected) as ctx:
            services.place_order(
                account=self.account, symbol=SYMBOL, side=OrderSide.BUY,
                order_type=OrderType.LIMIT, limit_price=Decimal(74_300),
            )
        self.assertEqual(ctx.exception.field, "weight_pct")

    def test_수량이_0_이_되면_이유를_알려준다(self):
        with self.assertRaises(services.OrderRejected) as ctx:
            services.place_order(
                account=self.account, symbol=SYMBOL, side=OrderSide.BUY,
                order_type=OrderType.LIMIT, limit_price=Decimal(74_300),
                weight_pct=Decimal("0.001"),
            )
        self.assertIn("0", ctx.exception.message)

    def test_STOP_은_즉시_체결하지_않는다(self):
        # 손절 STOP 을 걸려면 먼저 갖고 있어야 한다
        services.place_order(
            account=self.account, symbol=SYMBOL, side=OrderSide.BUY,
            order_type=OrderType.LIMIT, limit_price=Decimal(74_300),
            weight_pct=Decimal("5.0"),
        )
        order = services.place_order(
            account=self.account, symbol=SYMBOL, side=OrderSide.SELL,
            order_type=OrderType.STOP, stop_price=Decimal(70_000),
            weight_pct=Decimal("1.0"),
        )
        self.assertEqual(order.status, OrderStatus.ACCEPTED)
        self.assertEqual(order.filled_qty, Decimal(0))

    def test_보유가_없으면_매도_STOP_도_걸_수_없다(self):
        """손절은 갖고 있는 것에만 건다."""
        with self.assertRaises(services.OrderRejected) as ctx:
            services.place_order(
                account=self.account, symbol=SYMBOL, side=OrderSide.SELL,
                order_type=OrderType.STOP, stop_price=Decimal(70_000),
                weight_pct=Decimal("1.0"),
            )
        self.assertEqual(ctx.exception.rule, "INSUFFICIENT_POSITION")

    def test_STOP_가격이_없으면_거부한다(self):
        with self.assertRaises(services.OrderRejected) as ctx:
            services.place_order(
                account=self.account, symbol=SYMBOL, side=OrderSide.BUY,
                order_type=OrderType.STOP, weight_pct=Decimal("1.0"),
            )
        self.assertEqual(ctx.exception.field, "stop_price")

    def test_호가가_없으면_거부하지_않고_접수만_한다(self):
        """★ F-16 3.4 — 우리 쪽 장애로 참가자가 기회를 잃으면 안 된다."""
        OrderbookCache.objects.all().delete()
        order = services.place_order(
            account=self.account, symbol=SYMBOL, side=OrderSide.BUY,
            order_type=OrderType.LIMIT, limit_price=Decimal(74_300),
            weight_pct=Decimal("5.0"),
        )
        self.assertEqual(order.status, OrderStatus.ACCEPTED)
        self.assertEqual(order.filled_qty, Decimal(0))

    def test_호가가_없는_대회_주문은_현재가로_체결되지_않는다(self):
        """★ 그러면 연습 모드와 똑같아진다 — 대회를 대회로 만드는 것이 호가 소진이다."""
        OrderbookCache.objects.all().delete()
        order = services.place_order(
            account=self.account, symbol=SYMBOL, side=OrderSide.BUY,
            order_type=OrderType.LIMIT, limit_price=Decimal(80_000),
            weight_pct=Decimal("5.0"),
        )
        self.assertEqual(order.filled_qty, Decimal(0))

    def test_시뮬레이션_가격으로는_대회_체결을_하지_않는다(self):
        """v1.0 의 판단을 승계한다 — 가짜 가격으로 주문이 나가면 안 된다 (F-16 4.1)."""
        QuoteCache.objects.all().delete()
        make_quote(simulated=True)
        with self.assertRaises(PriceUnavailable):
            services.place_order(
                account=self.account, symbol=SYMBOL, side=OrderSide.BUY,
                order_type=OrderType.LIMIT, limit_price=Decimal(74_300),
                weight_pct=Decimal("5.0"),
            )

    def test_연습은_시뮬레이션_가격도_쓴다(self):
        member = make_member("practice")
        account = make_practice_account(member)
        QuoteCache.objects.all().delete()
        make_quote(simulated=True, price=74_000)
        order = services.place_order(
            account=account, symbol=SYMBOL, side=OrderSide.BUY,
            order_type=OrderType.MARKET, qty=Decimal(10),
        )
        self.assertEqual(order.status, OrderStatus.FILLED)

    def test_동결된_계좌는_주문할_수_없다(self):
        self.account.is_frozen = True
        self.account.save(update_fields=["is_frozen"])
        with self.assertRaises(services.OrderRejected) as ctx:
            services.place_order(
                account=self.account, symbol=SYMBOL, side=OrderSide.BUY,
                order_type=OrderType.LIMIT, limit_price=Decimal(74_300),
                weight_pct=Decimal("5.0"),
            )
        self.assertEqual(ctx.exception.rule, "ACCOUNT_FROZEN")

    def test_미체결_주문의_종목은_폴링_우선순위에_오른다(self):
        """★ 등록하지 않으면 호가가 안 갱신돼 체결이 하염없이 미뤄진다 (F-16 2.5)."""
        from market.models import SubscriptionRegistry

        OrderbookCache.objects.all().delete()
        services.place_order(
            account=self.account, symbol=SYMBOL, side=OrderSide.BUY,
            order_type=OrderType.LIMIT, limit_price=Decimal(74_300),
            weight_pct=Decimal("5.0"),
        )
        row = SubscriptionRegistry.objects.get(symbol=SYMBOL)
        self.assertEqual(row.priority, 1)


class MarketClosedTests(TestCase):
    """장외 접수 — `PENDING_OPEN` (F-03 8장)."""

    def setUp(self):
        self.member = make_member()
        self.account = make_contest_account(self.member)
        make_stock()
        make_book()
        make_quote()

    def test_장외에는_PENDING_OPEN_으로_접수된다(self):
        from unittest.mock import patch

        with patch("contests.rules.session_state", return_value="AFTER_CLOSE"):
            order = services.place_order(
                account=self.account, symbol=SYMBOL, side=OrderSide.BUY,
                order_type=OrderType.LIMIT, limit_price=Decimal(74_300),
                weight_pct=Decimal("5.0"),
            )
        self.assertEqual(order.status, OrderStatus.PENDING_OPEN)
        self.assertEqual(order.filled_qty, Decimal(0))

    def test_장_시작_잡이_접수로_올린다(self):
        from unittest.mock import patch

        with patch("contests.rules.session_state", return_value="BEFORE_OPEN"):
            services.place_order(
                account=self.account, symbol=SYMBOL, side=OrderSide.BUY,
                order_type=OrderType.LIMIT, limit_price=Decimal(74_300),
                weight_pct=Decimal("5.0"),
            )
        result = trading_jobs.open_pending_orders(triggered_by="CLI")
        self.assertEqual(result.updated, 1)
        self.assertEqual(Order.objects.get().status, OrderStatus.ACCEPTED)


# ─────────────────────────────────────────────────────────────────
# 6. 취소 (A-03 4장)
# ─────────────────────────────────────────────────────────────────


class CancelTests(MarketOpenMixin, TestCase):
    def setUp(self):
        self.member = make_member()
        self.account = make_practice_account(self.member)
        make_stock()
        make_quote(price=74_000)

    def _accepted_order(self):
        """체결되지 않는 지정가 주문 — 현재가보다 훨씬 싸게 사겠다는 주문."""
        return services.place_order(
            account=self.account, symbol=SYMBOL, side=OrderSide.BUY,
            order_type=OrderType.LIMIT, limit_price=Decimal(50_000), qty=Decimal(10),
        )

    def test_미체결_주문은_취소된다(self):
        order = self._accepted_order()
        self.assertEqual(order.status, OrderStatus.ACCEPTED)
        cancelled = services.cancel_order(order)
        self.assertEqual(cancelled.status, OrderStatus.CANCELLED)
        self.assertIsNotNone(cancelled.cancelled_at)

    def test_체결된_주문은_취소할_수_없다(self):
        order = services.place_order(
            account=self.account, symbol=SYMBOL, side=OrderSide.BUY,
            order_type=OrderType.MARKET, qty=Decimal(10),
        )
        with self.assertRaises(services.OrderNotCancellable):
            services.cancel_order(order)

    def test_장_마감_잡은_대회_주문만_취소한다(self):
        """연습은 상시 거래라 '장 마감'이 없다."""
        self._accepted_order()                       # 연습 계좌
        contest_member = make_member("contestant")
        contest_account = make_contest_account(contest_member)
        make_book()
        services.place_order(
            account=contest_account, symbol=SYMBOL, side=OrderSide.BUY,
            order_type=OrderType.LIMIT, limit_price=Decimal(50_000),
            weight_pct=Decimal("1.0"),
        )
        result = trading_jobs.cancel_stale_orders(triggered_by="CLI")
        self.assertEqual(result.updated, 1)
        self.assertEqual(
            Order.objects.filter(status=OrderStatus.CANCELLED).count(), 1
        )


# ─────────────────────────────────────────────────────────────────
# 7. 잡 3 — 체결 추종 (F-20 잡 3 · F-03 5.2)
# ─────────────────────────────────────────────────────────────────


class MatchPendingOrdersTests(MarketOpenMixin, TestCase):
    def setUp(self):
        self.member = make_member()
        self.account = make_practice_account(self.member, cash=100_000_000)
        make_stock()
        make_quote(price=74_000)

    def _limit_buy(self, price):
        return services.place_order(
            account=self.account, symbol=SYMBOL, side=OrderSide.BUY,
            order_type=OrderType.LIMIT, limit_price=Decimal(price), qty=Decimal(10),
        )

    def test_조건이_안_맞으면_체결하지_않는다(self):
        self._limit_buy(70_000)                  # 현재가 74,000 보다 싸게 사겠다
        result = trading_jobs.match_pending_orders(triggered_by="CLI")
        self.assertEqual(result.rows, 0)
        self.assertEqual(result.skipped, 1)

    def test_시장이_지정가에_닿으면_체결한다(self):
        order = self._limit_buy(70_000)
        QuoteCache.objects.all().delete()
        make_quote(price=69_000)                 # 가격이 내려왔다
        result = trading_jobs.match_pending_orders(triggered_by="CLI")
        order.refresh_from_db()
        self.assertEqual(result.created, 1)
        self.assertEqual(order.status, OrderStatus.FILLED)
        # ★ 시장 체결가로 체결한다 — 내 지정가(70,000)가 아니라 69,000 이다
        self.assertEqual(order.avg_fill_price, Decimal(69_000))

    def test_두_번_돌려도_결과가_같다(self):
        """★ 멱등성 — 잡은 5초마다 같은 주문을 다시 본다 (F-20 6장)."""
        order = self._limit_buy(70_000)
        QuoteCache.objects.all().delete()
        make_quote(price=69_000)

        trading_jobs.match_pending_orders(triggered_by="CLI")
        self.account.refresh_from_db()
        cash_after_first = self.account.cash
        position_after_first = Position.objects.get(account=self.account).qty

        second = trading_jobs.match_pending_orders(triggered_by="CLI")
        self.account.refresh_from_db()

        self.assertEqual(second.rows, 0)                       # 할 일이 없다
        self.assertEqual(self.account.cash, cash_after_first)  # 현금이 또 빠지지 않았다
        self.assertEqual(Position.objects.get(account=self.account).qty, position_after_first)
        self.assertEqual(Execution.objects.filter(order=order).count(), 1)

    def test_매도_STOP_은_현재가가_내려오면_발동한다(self):
        services.place_order(
            account=self.account, symbol=SYMBOL, side=OrderSide.BUY,
            order_type=OrderType.MARKET, qty=Decimal(100),
        )
        stop = services.place_order(
            account=self.account, symbol=SYMBOL, side=OrderSide.SELL,
            order_type=OrderType.STOP, stop_price=Decimal(70_000), qty=Decimal(50),
        )
        # 아직 안 내려왔다
        trading_jobs.match_pending_orders(triggered_by="CLI")
        stop.refresh_from_db()
        self.assertEqual(stop.status, OrderStatus.ACCEPTED)

        QuoteCache.objects.all().delete()
        make_quote(price=69_000)
        trading_jobs.match_pending_orders(triggered_by="CLI")
        stop.refresh_from_db()
        self.assertEqual(stop.status, OrderStatus.FILLED)
        # ★ order_type 은 STOP 그대로 — 참가자가 낸 주문의 정체는 바뀌지 않는다
        self.assertEqual(stop.order_type, OrderType.STOP)

    def test_매수_STOP_은_현재가가_올라가면_발동한다(self):
        stop = services.place_order(
            account=self.account, symbol=SYMBOL, side=OrderSide.BUY,
            order_type=OrderType.STOP, stop_price=Decimal(80_000), qty=Decimal(10),
        )
        QuoteCache.objects.all().delete()
        make_quote(price=81_000)
        trading_jobs.match_pending_orders(triggered_by="CLI")
        stop.refresh_from_db()
        self.assertEqual(stop.status, OrderStatus.FILLED)

    def test_부분_체결된_주문은_남은_잔량만_채운다(self):
        make_book(asks=[(74_000, 4)])            # 잔량 4주뿐
        contest_member = make_member("partial")
        account = make_contest_account(contest_member, cash=10_000_000)
        order = services.place_order(
            account=account, symbol=SYMBOL, side=OrderSide.BUY,
            order_type=OrderType.LIMIT, limit_price=Decimal(74_000),
            weight_pct=Decimal("7.4"),           # 10주가량
        )
        self.assertEqual(order.status, OrderStatus.PARTIAL)
        filled_first = order.filled_qty

        trading_jobs.match_pending_orders(triggered_by="CLI")
        order.refresh_from_db()
        self.assertEqual(order.status, OrderStatus.FILLED)
        self.assertEqual(order.filled_qty, order.requested_qty)
        self.assertGreater(order.filled_qty, filled_first)
        # 조각 번호가 이어진다 — UNIQUE(order, seq) 를 넘지 않는다
        seqs = list(Execution.objects.filter(order=order).values_list("seq", flat=True))
        self.assertEqual(sorted(seqs), list(range(1, len(seqs) + 1)))

    def test_취소된_주문은_스캔하지_않는다(self):
        order = self._limit_buy(70_000)
        services.cancel_order(order)
        QuoteCache.objects.all().delete()
        make_quote(price=69_000)
        result = trading_jobs.match_pending_orders(triggered_by="CLI")
        self.assertEqual(result.rows, 0)
        order.refresh_from_db()
        self.assertEqual(order.filled_qty, Decimal(0))

    def test_dry_run_은_아무것도_바꾸지_않는다(self):
        self._limit_buy(70_000)
        QuoteCache.objects.all().delete()
        make_quote(price=69_000)
        result = trading_jobs.match_pending_orders(triggered_by="CLI", dry_run=True)
        self.assertEqual(result.updated, 1)                    # 체결될 것이라고 보고
        self.assertEqual(Execution.objects.count(), 0)         # 실제로는 안 했다
        self.assertEqual(DataSyncLog.objects.count(), 0)       # 이력도 안 남긴다


class ContestSessionGuardTests(MarketOpenMixin, TestCase):
    """대회 주문은 장중에만 체결한다 — 로컬 `--loop` 는 24시간 돈다."""

    def setUp(self):
        self.member = make_member()
        self.account = make_contest_account(self.member)
        make_stock()
        make_book()
        make_quote(price=74_000)

    def test_장외에는_대회_주문을_건너뛴다(self):
        from unittest.mock import patch

        # 체결되지 않는 지정가로 접수해 둔다 (장중 기준)
        services.place_order(
            account=self.account, symbol=SYMBOL, side=OrderSide.BUY,
            order_type=OrderType.LIMIT, limit_price=Decimal(50_000),
            weight_pct=Decimal("1.0"),
        )
        QuoteCache.objects.all().delete()
        make_quote(price=49_000)                 # 조건은 충족한다

        with patch("trading.jobs.is_market_open", return_value=False):
            result = trading_jobs.match_pending_orders(triggered_by="CLI")
        self.assertEqual(result.rows, 0)
        self.assertEqual(result.skipped, 1)

    def test_장중에는_체결한다(self):
        from unittest.mock import patch

        services.place_order(
            account=self.account, symbol=SYMBOL, side=OrderSide.BUY,
            order_type=OrderType.LIMIT, limit_price=Decimal(50_000),
            weight_pct=Decimal("1.0"),
        )
        QuoteCache.objects.all().delete()
        make_quote(price=49_000)

        with patch("trading.jobs.is_market_open", return_value=True):
            result = trading_jobs.match_pending_orders(triggered_by="CLI")
        self.assertEqual(result.created, 1)

    def test_낡은_시세로는_대회_체결을_하지_않는다(self):
        from unittest.mock import patch

        services.place_order(
            account=self.account, symbol=SYMBOL, side=OrderSide.BUY,
            order_type=OrderType.LIMIT, limit_price=Decimal(50_000),
            weight_pct=Decimal("1.0"),
        )
        QuoteCache.objects.all().delete()
        make_quote(price=49_000, fetched_at=timezone.now() - timezone.timedelta(minutes=30))

        with patch("trading.jobs.is_market_open", return_value=True):
            result = trading_jobs.match_pending_orders(triggered_by="CLI")
        self.assertEqual(result.rows, 0)
        self.assertEqual(result.skipped, 1)


# ─────────────────────────────────────────────────────────────────
# 8. 고빈도 잡의 이력 정책 (변경노트 E-36)
# ─────────────────────────────────────────────────────────────────


class IdleLogCoalescingTests(TestCase):
    """조용한 실행은 새 행을 만들지 않고 직전 행의 구간을 늘린다."""

    def setUp(self):
        self.member = make_member()
        self.account = make_practice_account(self.member)
        make_stock()
        make_quote(price=74_000)

    def test_조용한_실행은_한_행으로_합쳐진다(self):
        for _ in range(5):
            trading_jobs.match_pending_orders(triggered_by="CLI")
        logs = DataSyncLog.objects.filter(job_name="match_pending_orders")
        self.assertEqual(logs.count(), 1)

    def test_합쳐진_행은_시작을_유지하고_끝만_민다(self):
        trading_jobs.match_pending_orders(triggered_by="CLI")
        first = DataSyncLog.objects.get(job_name="match_pending_orders")
        started, finished = first.started_at, first.finished_at

        trading_jobs.match_pending_orders(triggered_by="CLI")
        first.refresh_from_db()
        self.assertEqual(first.started_at, started)          # 구간의 시작은 그대로
        self.assertGreater(first.finished_at, finished)      # 끝만 밀린다

    def test_체결이_일어나면_새_행을_남긴다(self):
        trading_jobs.match_pending_orders(triggered_by="CLI")     # 조용한 회차
        services.place_order(
            account=self.account, symbol=SYMBOL, side=OrderSide.BUY,
            order_type=OrderType.LIMIT, limit_price=Decimal(70_000), qty=Decimal(10),
        )
        QuoteCache.objects.all().delete()
        make_quote(price=69_000)
        trading_jobs.match_pending_orders(triggered_by="CLI")     # 체결된 회차

        logs = list(
            DataSyncLog.objects.filter(job_name="match_pending_orders").order_by("started_at")
        )
        self.assertEqual(len(logs), 2)
        self.assertEqual(logs[1].rows_affected, 1)

    def test_체결_뒤의_조용한_구간은_다시_새_행에서_시작한다(self):
        services.place_order(
            account=self.account, symbol=SYMBOL, side=OrderSide.BUY,
            order_type=OrderType.LIMIT, limit_price=Decimal(70_000), qty=Decimal(10),
        )
        QuoteCache.objects.all().delete()
        make_quote(price=69_000)
        trading_jobs.match_pending_orders(triggered_by="CLI")     # 체결
        trading_jobs.match_pending_orders(triggered_by="CLI")     # 조용
        trading_jobs.match_pending_orders(triggered_by="CLI")     # 조용

        logs = list(
            DataSyncLog.objects.filter(job_name="match_pending_orders").order_by("started_at")
        )
        self.assertEqual(len(logs), 2)                  # 체결 1행 + 조용한 구간 1행
        self.assertEqual(logs[1].rows_affected, 0)

    def test_실패는_언제나_자기_행을_갖는다(self):
        """★ 조용한 구간에 섞이면 실패가 사라진다."""
        from unittest.mock import patch

        trading_jobs.match_pending_orders(triggered_by="CLI")     # 조용한 행 1개
        with patch("trading.jobs._run", side_effect=RuntimeError("터짐")):
            with self.assertRaises(RuntimeError):
                trading_jobs.match_pending_orders(triggered_by="CLI")

        logs = list(
            DataSyncLog.objects.filter(job_name="match_pending_orders").order_by("started_at")
        )
        self.assertEqual(len(logs), 2)
        self.assertEqual(logs[1].status, SyncStatus.FAILED)
        self.assertIn("터짐", logs[1].error)


# ─────────────────────────────────────────────────────────────────
# 9. 대회 규칙이 주문 경로에 실제로 걸리는가 (F-04 + F-03 결합)
# ─────────────────────────────────────────────────────────────────


class ContestRuleIntegrationTests(MarketOpenMixin, TestCase):
    """규칙 엔진 단위 테스트는 `contests/tests.py` 에 있다.
    여기서는 **주문 경로가 그것을 실제로 부르는지**만 본다."""

    def setUp(self):
        self.member = make_member()
        self.account = make_contest_account(self.member, cash=100_000_000)
        make_stock()
        make_book(asks=[(74_300, 100_000)])      # 잔량을 넉넉히
        make_quote()

    def test_종목_한도를_넘기면_거부된다(self):
        with self.assertRaises(RuleRejection) as ctx:
            services.place_order(
                account=self.account, symbol=SYMBOL, side=OrderSide.BUY,
                order_type=OrderType.LIMIT, limit_price=Decimal(74_300),
                weight_pct=Decimal("50.0"),      # 기본 한도 15% (삼성전자 예외 40%)
            )
        self.assertEqual(ctx.exception.rule, "POSITION_LIMIT")
        self.assertIn("max_additional_krw", ctx.exception.detail)

    def test_거래_불가_종목은_매수가_막힌다(self):
        StockMaster.objects.filter(symbol=SYMBOL).update(is_supervised=True)
        with self.assertRaises(RuleRejection) as ctx:
            services.place_order(
                account=self.account, symbol=SYMBOL, side=OrderSide.BUY,
                order_type=OrderType.LIMIT, limit_price=Decimal(74_300),
                weight_pct=Decimal("5.0"),
            )
        self.assertEqual(ctx.exception.rule, "SYMBOL_NOT_TRADABLE")

    def test_거래_불가_종목이어도_매도는_된다(self):
        """★ 막으면 참가자가 물린 채 대회를 끝내야 한다 (F-04 2.2)."""
        services.place_order(
            account=self.account, symbol=SYMBOL, side=OrderSide.BUY,
            order_type=OrderType.LIMIT, limit_price=Decimal(74_300),
            weight_pct=Decimal("5.0"),
        )
        StockMaster.objects.filter(symbol=SYMBOL).update(is_supervised=True)
        order = services.place_order(
            account=self.account, symbol=SYMBOL, side=OrderSide.SELL,
            order_type=OrderType.LIMIT, limit_price=Decimal(74_000),
            weight_pct=Decimal("1.0"),
        )
        self.assertIn(order.status, (OrderStatus.FILLED, OrderStatus.PARTIAL))

    def test_실격자는_주문할_수_없다(self):
        Participation.objects.filter(account=self.account).update(
            status=ParticipationStatus.DISQUALIFIED
        )
        with self.assertRaises(RuleRejection) as ctx:
            services.place_order(
                account=self.account, symbol=SYMBOL, side=OrderSide.BUY,
                order_type=OrderType.LIMIT, limit_price=Decimal(74_300),
                weight_pct=Decimal("5.0"),
            )
        self.assertEqual(ctx.exception.rule, "NOT_PARTICIPANT")
