"""대회 규칙 엔진 테스트 (F-04).

`trading/tests.py` 가 **주문 경로가 규칙을 부르는지**를 본다면, 여기는
**규칙 자체가 맞는지**를 본다. `Portfolio` 를 손으로 지어 넣으므로 포지션·시세를
DB 에 만들 필요가 없다 — 규칙 엔진이 `trading` 을 참조하지 않게 설계한 덕이다
(`contests/rules.py` 모듈 docstring).

    python manage.py test contests
"""

from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from contests import jobs as contest_jobs, rules, scoring, services
from contests.models import (
    Contest,
    ContestRanking,
    ContestResult,
    ContestSectorWeight,
    ContestStatus,
    ContestUniverse,
    DailySnapshot,
    IntradaySnapshot,
    Participation,
    ParticipationStatus,
    RuleViolation,
    SnapshotHolding,
    ViolationRule,
    WeeklyTurnover,
    default_rule_set,
)
from core.constants import AccountMode, AssetClass, OrderSide
from market.models import Market, QuoteCache, StockMaster, StockType, TradingCalendar

SAMSUNG = "005930"
HYNIX = "000660"
NAVER = "035420"
SMALL = "123450"

ONE_EOK = 100_000_000


def make_contest(**overrides):
    today = timezone.localdate()
    params = dict(
        name="테스트 대회",
        slug=f"rules-{Contest.objects.count()}",
        status=ContestStatus.ONGOING,
        start_date=today - timezone.timedelta(days=1),
        end_date=today + timezone.timedelta(days=30),
        initial_capital=ONE_EOK,
        rule_set=default_rule_set(),
    )
    params.update(overrides)
    return Contest.objects.create(**params)


def make_stock(symbol=SAMSUNG, *, name="삼성전자", market_cap=400_000_000_000_000,
               turnover=1_000_000_000_000, stock_type=StockType.COMMON, **extra):
    return StockMaster.objects.create(
        symbol=symbol, name=name, market=Market.KOSPI, stock_type=stock_type,
        sector_code="1013", sector_name="전기전자",
        market_cap=market_cap, avg_turnover_5d=turnover, close_price=Decimal(74_000),
        **extra,
    )


def put_in_universe(contest, symbol, *, sector="1013", sector_name="전기전자"):
    ContestUniverse.objects.create(
        contest=contest, symbol=symbol, name=symbol, market=Market.KOSPI,
        sector_code=sector, sector_name=sector_name, frozen_at=timezone.now(),
    )


def portfolio(cash, *holdings):
    """`portfolio(9000만, ("005930", 1000만))` 처럼 쓴다."""
    return rules.Portfolio(
        cash=cash,
        holdings=[rules.Holding(symbol=symbol, value=value) for symbol, value in holdings],
    )


# ─────────────────────────────────────────────────────────────────
# 1. 참가 상태 (F-04 4.1 ①)
# ─────────────────────────────────────────────────────────────────


class ParticipationCheckTests(TestCase):
    def setUp(self):
        from accounts.models import Member

        self.contest = make_contest()
        self.member = Member.objects.create_user(
            username="p1", email="p1@example.com", password="pass-1234!"
        )

    def _participation(self, **overrides):
        params = dict(
            contest=self.contest, member=self.member, nickname="닉",
            status=ParticipationStatus.APPROVED,
        )
        params.update(overrides)
        return Participation.objects.create(**params)

    def test_참가자가_아니면_거부(self):
        with self.assertRaises(rules.RuleRejection) as ctx:
            rules.check_participation(None)
        self.assertEqual(ctx.exception.rule, "NOT_PARTICIPANT")

    def test_대회가_진행중이_아니면_거부(self):
        self.contest.status = ContestStatus.CLOSED
        self.contest.save(update_fields=["status"])
        with self.assertRaises(rules.RuleRejection) as ctx:
            rules.check_participation(self._participation())
        self.assertEqual(ctx.exception.rule, "CONTEST_NOT_ONGOING")

    def test_실격자는_주문할_수_없다(self):
        """★ 실격은 랭킹에서만 빠지는 게 아니라 거래도 멈춘다 (F-02 4.3)."""
        with self.assertRaises(rules.RuleRejection) as ctx:
            rules.check_participation(
                self._participation(status=ParticipationStatus.DISQUALIFIED)
            )
        self.assertEqual(ctx.exception.rule, "NOT_PARTICIPANT")

    def test_승인_대기도_주문할_수_없다(self):
        with self.assertRaises(rules.RuleRejection):
            rules.check_participation(self._participation(status=ParticipationStatus.PENDING))

    def test_승인된_참가는_통과(self):
        rules.check_participation(self._participation())      # 예외가 없으면 통과


# ─────────────────────────────────────────────────────────────────
# 2. 거래 불가 종목 (F-04 2장)
# ─────────────────────────────────────────────────────────────────


class SymbolTradableTests(TestCase):
    def setUp(self):
        self.contest = make_contest()
        put_in_universe(self.contest, SAMSUNG)

    def _context(self, symbol=SAMSUNG):
        return rules.RuleContext.load(self.contest, symbol)

    def _reasons(self, symbol=SAMSUNG):
        with self.assertRaises(rules.RuleRejection) as ctx:
            rules.check_symbol_tradable(self._context(symbol), symbol, OrderSide.BUY)
        return {item["code"] for item in ctx.exception.detail["reasons"]}

    def test_정상_종목은_통과(self):
        make_stock()
        rules.check_symbol_tradable(self._context(), SAMSUNG, OrderSide.BUY)

    def test_종목_마스터에_없으면_거부(self):
        self.assertIn("UNKNOWN_SYMBOL", self._reasons())

    def test_보통주가_아니면_거부(self):
        make_stock(stock_type=StockType.PREFERRED)
        self.assertIn("NOT_COMMON_STOCK", self._reasons())

    def test_거래대금이_적으면_거부(self):
        make_stock(turnover=1_200_000_000)        # 12억 < 기준 30억
        reasons = self._reasons()
        self.assertIn("LOW_TURNOVER", reasons)

    def test_시가총액이_작으면_거부(self):
        make_stock(market_cap=50_000_000_000)     # 500억 < 기준 1,000억
        self.assertIn("SMALL_MARKET_CAP", self._reasons())

    def test_관리종목은_거부(self):
        make_stock(is_supervised=True)
        self.assertIn("SUPERVISED", self._reasons())

    def test_시장경보도_거부(self):
        make_stock(alert_level="투자경고")
        self.assertIn("ALERT", self._reasons())

    def test_상장폐지는_거부(self):
        make_stock(is_delisted=True)
        self.assertIn("DELISTED", self._reasons())

    def test_신규상장은_영업일을_세어_거부한다(self):
        today = timezone.localdate()
        TradingCalendar.objects.bulk_create([
            TradingCalendar(
                date=today - timezone.timedelta(days=offset),
                is_open=(today - timezone.timedelta(days=offset)).weekday() < 5,
            )
            for offset in range(20)
        ], ignore_conflicts=True)
        make_stock(listing_date=today - timezone.timedelta(days=2))
        self.assertIn("NEW_LISTING", self._reasons())

    def test_달력이_없으면_신규상장_판정을_건너뛴다(self):
        """★ 달력이 비었다고 정상 종목을 막는 것이 더 나쁜 오류다."""
        make_stock(listing_date=timezone.localdate() - timezone.timedelta(days=2))
        with self.assertLogs("contests.rules", level="INFO"):
            rules.check_symbol_tradable(self._context(), SAMSUNG, OrderSide.BUY)

    def test_업종이_없으면_유니버스에서_제외된다(self):
        """★ 변경노트 E-31 — 섹터를 모르면 섹터 한도를 통째로 우회할 수 있다."""
        contest = make_contest()
        put_in_universe(contest, SAMSUNG, sector="", sector_name="")
        make_stock()
        with self.assertRaises(rules.RuleRejection) as ctx:
            rules.check_symbol_tradable(
                rules.RuleContext.load(contest, SAMSUNG), SAMSUNG, OrderSide.BUY
            )
        codes = {item["code"] for item in ctx.exception.detail["reasons"]}
        self.assertIn("NO_SECTOR", codes)

    def test_걸린_사유를_전부_알려준다(self):
        """★ 종목의 성질이라 하나를 고쳐도 다음이 걸린다 — 한 번에 보여준다 (A-03 1.6)."""
        make_stock(market_cap=50_000_000_000, turnover=1_200_000_000, is_supervised=True)
        reasons = self._reasons()
        self.assertEqual(reasons, {"LOW_TURNOVER", "SMALL_MARKET_CAP", "SUPERVISED"})

    def test_매도는_언제나_허용한다(self):
        """★★ F-04 2.2 — 막으면 참가자가 물린 채 대회를 끝내야 한다."""
        make_stock(is_supervised=True, is_delisted=True, market_cap=1)
        rules.check_symbol_tradable(self._context(), SAMSUNG, OrderSide.SELL)


# ─────────────────────────────────────────────────────────────────
# 3. 편입 한도 (F-04 3장)
# ─────────────────────────────────────────────────────────────────


class PositionLimitTests(TestCase):
    def setUp(self):
        self.contest = make_contest()
        put_in_universe(self.contest, SAMSUNG)
        put_in_universe(self.contest, HYNIX)
        make_stock()
        make_stock(HYNIX, name="SK하이닉스")
        self.context = rules.RuleContext.load(self.contest, SAMSUNG)

    def _check(self, book, amount, symbol=SAMSUNG, side=OrderSide.BUY, context=None):
        rules.check_limits(
            context or self.context, book, symbol=symbol, side=side, amount_krw=amount
        )

    def test_한도_안이면_통과(self):
        self._check(portfolio(ONE_EOK), 30_000_000)          # 30% → 삼성전자 예외 40%

    def test_예외_한도가_적용된다(self):
        """★ 삼성전자 40% · SK하이닉스 30% — 대회마다 다르게 줄 수 있다 (F-04 3.1)."""
        # 기본 15% 라면 20% 는 막혀야 하지만, 삼성전자는 예외라 통과한다
        self._check(portfolio(ONE_EOK), 20_000_000)

    def test_예외가_없는_종목은_기본_15퍼센트(self):
        contest = make_contest(rule_set={**default_rule_set(), "position_limit_exceptions": {}})
        put_in_universe(contest, SAMSUNG)
        context = rules.RuleContext.load(contest, SAMSUNG)
        with self.assertRaises(rules.RuleRejection) as ctx:
            self._check(portfolio(ONE_EOK), 20_000_000, context=context)
        self.assertEqual(ctx.exception.rule, "POSITION_LIMIT")

    def test_한도를_넘기면_거부(self):
        with self.assertRaises(rules.RuleRejection) as ctx:
            self._check(portfolio(ONE_EOK), 50_000_000)      # 50% > 40%
        self.assertEqual(ctx.exception.rule, "POSITION_LIMIT")
        self.assertEqual(ctx.exception.field, "weight_pct")

    def test_기존_보유를_합쳐서_본다(self):
        book = portfolio(70_000_000, (SAMSUNG, 30_000_000))  # 이미 30%
        with self.assertRaises(rules.RuleRejection):
            self._check(book, 20_000_000)                     # 30% + 20% = 50% > 40%

    def test_여기까지는_됩니다_를_함께_준다(self):
        """★★ F-04 4.2 — '안 됩니다'만 말하면 참가자가 비중을 바꿔가며 계속 시도한다."""
        book = portfolio(70_000_000, (SAMSUNG, 30_000_000))
        with self.assertRaises(rules.RuleRejection) as ctx:
            self._check(book, 20_000_000)
        detail = ctx.exception.detail
        self.assertEqual(detail["current_pct"], 30.0)
        self.assertEqual(detail["limit_pct"], 40.0)
        # 한도 40% = 4,000만. 이미 3,000만 → 1,000만까지 더 살 수 있다
        self.assertEqual(detail["max_additional_krw"], 10_000_000)

    def test_이미_초과한_상태면_0원까지_살_수_있다고_답한다(self):
        """★ 음수를 그대로 주면 '-320만원까지 살 수 있습니다' 가 화면에 뜬다."""
        book = portfolio(50_000_000, (SAMSUNG, 50_000_000))   # 이미 50% > 40%
        with self.assertRaises(rules.RuleRejection) as ctx:
            self._check(book, 1_000_000)
        self.assertEqual(ctx.exception.detail["max_additional_krw"], 0)

    def test_초과_상태에서도_매도는_허용한다(self):
        """★★ 가격 변동에 의한 초과는 허용하고, 해소 행위는 막지 않는다 (F-04 3.4)."""
        book = portfolio(50_000_000, (SAMSUNG, 50_000_000))
        self._check(book, 10_000_000, side=OrderSide.SELL)    # 예외가 없으면 통과

    def test_순자산이_0_이면_판정하지_않는다(self):
        self._check(portfolio(0), 1_000_000)


class SectorLimitTests(TestCase):
    def setUp(self):
        self.contest = make_contest()
        put_in_universe(self.contest, SAMSUNG)
        put_in_universe(self.contest, HYNIX)
        make_stock()
        make_stock(HYNIX, name="SK하이닉스")
        ContestSectorWeight.objects.create(
            contest=self.contest, sector_code="1013", sector_name="전기전자",
            market_weight_pct=Decimal("15.0"), limit_pct=Decimal("30.0"),
        )
        self.context = rules.RuleContext.load(self.contest, SAMSUNG)

    def test_같은_섹터_보유를_합산한다(self):
        """삼성전자를 사는데 SK하이닉스 보유가 섹터 합계에 들어간다."""
        book = portfolio(75_000_000, (HYNIX, 25_000_000))     # 전기전자 25%
        with self.assertRaises(rules.RuleRejection) as ctx:
            rules.check_limits(
                self.context, book, symbol=SAMSUNG, side=OrderSide.BUY,
                amount_krw=10_000_000,                        # 25% + 10% = 35% > 30%
            )
        self.assertEqual(ctx.exception.rule, "SECTOR_LIMIT")
        self.assertEqual(ctx.exception.detail["sector_code"], "1013")

    def test_다른_섹터_보유는_합산하지_않는다(self):
        put_in_universe(self.contest, "035720", sector="1015", sector_name="서비스업")
        book = portfolio(75_000_000, ("035720", 25_000_000))
        rules.check_limits(
            self.context, book, symbol=SAMSUNG, side=OrderSide.BUY, amount_krw=10_000_000
        )

    def test_섹터_비중_스냅샷이_없으면_검사하지_않는다(self):
        """★ 우리 쪽 준비가 덜 된 것으로 참가자의 주문을 막지 않는다.

        같은 섹터를 25% 들고 있고 10% 를 더 사면 35% 라 섹터 한도(30%)를 넘는데,
        스냅샷이 없으므로 통과해야 한다. 삼성전자 종목 한도는 40% 라 걸리지 않는다.
        """
        ContestSectorWeight.objects.all().delete()
        context = rules.RuleContext.load(self.contest, SAMSUNG)
        book = portfolio(75_000_000, (HYNIX, 25_000_000))
        rules.check_limits(
            context, book, symbol=SAMSUNG, side=OrderSide.BUY, amount_krw=10_000_000
        )


class SmallCapLimitTests(TestCase):
    """소형주 합산 한도 (F-04 3.3).

    ★ **검증 순서 때문에 시나리오를 조심해서 짜야 한다** — F-04 4.1 은
      종목 → 섹터 → 소형주 순으로 보고 **첫 번째 것만** 알린다. 소형주 한도를
      시험하려면 앞의 둘을 통과하는 금액이어야 하므로, 한 종목에 몰지 않고
      **소형주 두 종목**에 나눠 담는다.
    """

    SMALL2 = "123460"

    def setUp(self):
        self.contest = make_contest()
        for symbol in (SMALL, self.SMALL2, SAMSUNG):
            put_in_universe(self.contest, symbol)
        # 시총 5,000억 — 1,000억 이상이라 편입은 되지만 1조 미만이라 소형주다
        make_stock(SMALL, name="소형주", market_cap=500_000_000_000)
        make_stock(self.SMALL2, name="소형주2", market_cap=500_000_000_000)
        make_stock()
        self.context = rules.RuleContext.load(self.contest, SMALL)

    def test_소형주_합산이_30퍼센트를_넘으면_거부(self):
        # 소형주2 를 25% 들고 소형주1 을 10% 더 산다 →
        #   종목 한도 10% < 15% 통과 · 소형주 합산 35% > 30% 위반
        book = portfolio(75_000_000, (self.SMALL2, 25_000_000))
        with self.assertRaises(rules.RuleRejection) as ctx:
            rules.check_limits(
                self.context, book, symbol=SMALL, side=OrderSide.BUY, amount_krw=10_000_000
            )
        self.assertEqual(ctx.exception.rule, "SMALL_CAP_LIMIT")
        self.assertEqual(ctx.exception.detail["limit_pct"], 30.0)

    def test_대형주를_사면_소형주_합계는_안_본다(self):
        context = rules.RuleContext.load(self.contest, SAMSUNG)
        book = portfolio(70_000_000, (SMALL, 30_000_000))     # 소형주 이미 30%
        rules.check_limits(
            context, book, symbol=SAMSUNG, side=OrderSide.BUY, amount_krw=10_000_000
        )

    def test_한도_안이면_통과(self):
        rules.check_limits(
            self.context, portfolio(ONE_EOK), symbol=SMALL, side=OrderSide.BUY,
            amount_krw=10_000_000,                             # 10% — 종목·소형주 둘 다 통과
        )


# ─────────────────────────────────────────────────────────────────
# 4. 거래 세션 (F-03 8장)
# ─────────────────────────────────────────────────────────────────


class SessionTests(TestCase):
    def test_영업일_09시_전은_BEFORE_OPEN(self):
        from datetime import datetime

        from market.sessions import SessionState, session_state
        from core.time import KST

        TradingCalendar.objects.create(date=timezone.localdate(), is_open=True)
        moment = datetime.combine(
            timezone.localdate(), datetime.min.time(), tzinfo=KST
        ).replace(hour=8, minute=30)
        self.assertEqual(session_state(moment), SessionState.BEFORE_OPEN)

    def test_장중은_OPEN(self):
        from datetime import datetime

        from market.sessions import SessionState, session_state
        from core.time import KST

        TradingCalendar.objects.create(date=timezone.localdate(), is_open=True)
        moment = datetime.combine(
            timezone.localdate(), datetime.min.time(), tzinfo=KST
        ).replace(hour=11, minute=0)
        self.assertEqual(session_state(moment), SessionState.OPEN)

    def test_15시_30분_이후는_AFTER_CLOSE(self):
        from datetime import datetime

        from market.sessions import SessionState, session_state
        from core.time import KST

        TradingCalendar.objects.create(date=timezone.localdate(), is_open=True)
        moment = datetime.combine(
            timezone.localdate(), datetime.min.time(), tzinfo=KST
        ).replace(hour=15, minute=45)
        self.assertEqual(session_state(moment), SessionState.AFTER_CLOSE)

    def test_휴장일은_HOLIDAY(self):
        from market.sessions import SessionState, session_state

        TradingCalendar.objects.create(date=timezone.localdate(), is_open=False)
        self.assertEqual(session_state(), SessionState.HOLIDAY)

    def test_달력에_없으면_요일로_판정한다(self):
        """★ 없으면 휴장으로 보면 달력이 하루만 뒤처져도 대회가 멈춘다."""
        from datetime import date

        from market.sessions import is_business_day

        with self.assertLogs("market.sessions", level="INFO"):
            self.assertTrue(is_business_day(date(2026, 8, 13)))    # 목요일
            self.assertFalse(is_business_day(date(2026, 8, 15)))   # 토요일

    def test_장외에는_MarketClosed_를_던진다(self):
        """★ RuleRejection 이 아니다 — 상속하면 호출부가 장외 주문까지 거부한다."""
        TradingCalendar.objects.create(date=timezone.localdate(), is_open=False)
        with self.assertRaises(rules.MarketClosed):
            rules.check_session()
        self.assertFalse(issubclass(rules.MarketClosed, rules.RuleRejection))


# ═════════════════════════════════════════════════════════════════
#  정산 (F-05 · F-04 5·6장) — 세션 11
# ═════════════════════════════════════════════════════════════════
#
#  아래는 잡 4·7·9·10 의 테스트다. 위쪽(사전 차단)과 성격이 다르다:
#
#      사전 차단   `Portfolio` 를 손으로 지어 규칙만 본다 — DB 가 거의 없다
#      정산        계좌·포지션·체결이 실제로 있어야 한다 — DB 를 짓는다
#
#  ★ 그래서 `scoring.py`(순수 산식)를 따로 분리했다. 아래 `ScoringTests` 는
#    DB 없이 즉시 돌고, 산식을 조정할 때 가장 먼저 보는 곳이다 (F-04 6.2).

from datetime import date, datetime, timedelta                  # noqa: E402
from unittest.mock import patch                                 # noqa: E402

from core.time import KST                                       # noqa: E402


# ─────────────────────────────────────────────────────────────────
# 6. 순수 산식 (contests/scoring.py)
# ─────────────────────────────────────────────────────────────────


class ScoringTests(TestCase):
    """DB 를 건드리지 않는다. 산식만 본다."""

    def test_NAV_는_시작자본을_1000_으로_놓는다(self):
        self.assertEqual(scoring.nav_for(ONE_EOK, ONE_EOK), Decimal("1000.0000"))
        self.assertEqual(scoring.nav_for(110_000_000, ONE_EOK), Decimal("1100.0000"))

    def test_시작자본_0_이면_1000_으로_둔다(self):
        """★ 예외를 던지면 대회 하나의 설정 실수로 정산 잡 전체가 멈춘다."""
        self.assertEqual(scoring.nav_for(50_000_000, 0), Decimal("1000.0000"))

    def test_누적수익률은_NAV_에서_바로_나온다(self):
        self.assertEqual(scoring.cumulative_return_pct(Decimal(1100)), Decimal("10.0000"))
        self.assertEqual(scoring.cumulative_return_pct(Decimal(880)), Decimal("-12.0000"))

    def test_첫날_일간수익률은_누적과_같다(self):
        """전일 NAV 가 없으면 기준은 시작 자본이다."""
        self.assertEqual(
            scoring.daily_return_pct(Decimal(1050), None), Decimal("5.0000")
        )
        # 전일 순자산이 0 인 계좌도 같은 취급 — 0 을 분모로 쓸 수 없다
        self.assertEqual(
            scoring.daily_return_pct(Decimal(1050), Decimal(0)), Decimal("5.0000")
        )

    def test_회전율_산식(self):
        # (매수 2000만 + 매도 1000만) / 1억 × 0.5 × 100 = 15%
        self.assertEqual(
            scoring.turnover_pct(20_000_000, 10_000_000, ONE_EOK), Decimal("15.0000")
        )

    def test_회전율_경계는_미만이_위반이다(self):
        """★ '5% 이상 유지' 이므로 정확히 5.0 은 통과다."""
        self.assertFalse(scoring.is_turnover_violation(Decimal("5.0000"), 5.0))
        self.assertTrue(scoring.is_turnover_violation(Decimal("4.9999"), 5.0))

    def test_HHI_는_몰빵이_1_이다(self):
        self.assertEqual(scoring.herfindahl([100]), Decimal("1.000000"))
        self.assertEqual(scoring.herfindahl([50, 50]), Decimal("0.500000"))
        self.assertEqual(scoring.herfindahl([25, 25, 25, 25]), Decimal("0.250000"))

    def test_HHI_는_빈_입력을_최악으로_본다(self):
        """★ 0 을 주면 '아무것도 안 한 사람이 완전 분산' 이 되어 만점을 받는다."""
        self.assertEqual(scoring.herfindahl([]), Decimal("1.000000"))
        self.assertEqual(scoring.herfindahl([0, -5]), Decimal("1.000000"))

    def test_평균비중은_평가액_합의_비중이다(self):
        """★ 현금으로 있던 날이 유리해지지도 불리해지지도 않아야 한다."""
        weights = scoring.average_weights([
            {"A": 100, "B": 100},
            {},                       # 전액 현금이었던 날
            {"A": 300, "B": 100},
        ])
        self.assertEqual(weights["A"], Decimal(400) / Decimal(600))
        self.assertEqual(weights["B"], Decimal(200) / Decimal(600))
        self.assertEqual(sum(weights.values()), Decimal(1))

    def test_보유가_한_번도_없으면_평균비중이_비어_있다(self):
        self.assertEqual(scoring.average_weights([{}, {}]), {})

    def test_수익분산_점수는_수익종목이_적으면_깎인다(self):
        """★ min(수익종목수/5, 1) — 1종목이면 1/5 만 받는다 (F-04 6.1)."""
        full = scoring.pnl_dispersion_score(Decimal("0.2"), 5)
        one = scoring.pnl_dispersion_score(Decimal("0.2"), 1)
        self.assertEqual(full, Decimal("80.0000"))
        self.assertEqual(one, Decimal("16.0000"))
        self.assertEqual(scoring.pnl_dispersion_score(Decimal("0.2"), 0), Decimal("0.0000"))

    def test_경쟁순위는_동점을_묶고_다음을_건너뛴다(self):
        ranks = scoring.competition_ranks([Decimal(10), Decimal(10), Decimal(5), Decimal(1)])
        self.assertEqual(ranks, [1, 1, 3, 4])

    def test_경쟁순위는_입력_순서를_지킨다(self):
        """★ 정렬해 돌려주면 호출부가 참가자와 짝을 다시 맞춰야 하고, 어긋나면
        남의 순위가 내 화면에 뜬다."""
        ranks = scoring.competition_ranks([Decimal(1), Decimal(99), Decimal(50)])
        self.assertEqual(ranks, [3, 1, 2])

    def test_백분위는_동점자에게_같은_값을_준다(self):
        values = [Decimal(10), Decimal(10), Decimal(1)]
        pcts = scoring.percentiles(values)
        self.assertEqual(pcts[0], pcts[1])
        self.assertGreater(pcts[0], pcts[2])

    def test_백분위는_100_도_0_도_아니다(self):
        pcts = scoring.percentiles([Decimal(1), Decimal(2), Decimal(3)])
        self.assertLess(max(pcts), Decimal(100))
        self.assertGreater(min(pcts), Decimal(0))

    def test_참가자가_하나면_백분위는_50(self):
        self.assertEqual(scoring.percentiles([Decimal(7)]), [Decimal("50.0000")])

    def test_등급_구간(self):
        self.assertEqual(scoring.grade_for(Decimal("97.5")), "A+")
        self.assertEqual(scoring.grade_for(Decimal("95")), "A+")      # 경계 포함
        self.assertEqual(scoring.grade_for(Decimal("94.9")), "A")
        self.assertEqual(scoring.grade_for(Decimal("0")), "F")

    def test_최종점수는_백분위를_가중합한다(self):
        self.assertEqual(
            scoring.final_score(Decimal(100), Decimal(0)), Decimal("70.0000")
        )
        self.assertEqual(
            scoring.final_score(Decimal(0), Decimal(100)), Decimal("30.0000")
        )


# ─────────────────────────────────────────────────────────────────
# 7. 정산 공통 — 대회·참가자·포지션 짓기
# ─────────────────────────────────────────────────────────────────


def make_member(username: str):
    from accounts.models import Member

    return Member.objects.create_user(
        username=username, email=f"{username}@example.com", password="pass-1234!"
    )


def join(contest, username: str, *, cash=ONE_EOK, nickname=None, is_ranked=True,
         status=ParticipationStatus.APPROVED):
    """참가 + 대회 계좌를 함께 만든다. 승인 시점에 계좌가 생긴다 (Account docstring)."""
    from accounts.models import Account

    member = make_member(username)
    account = Account.objects.create(
        member=member, contest=contest, mode=AccountMode.CONTEST,
        cash=cash, initial_capital=contest.initial_capital,
    )
    return Participation.objects.create(
        contest=contest, member=member, account=account,
        nickname=nickname or username, status=status, is_ranked=is_ranked,
    )


def hold(participation, symbol, qty, avg_price):
    from trading.models import Position

    return Position.objects.create(
        account=participation.account, symbol=symbol, asset_class=AssetClass.STOCK,
        qty=Decimal(qty), avg_price=Decimal(avg_price),
        principal=int(Decimal(qty) * Decimal(avg_price)),
    )


def quote(symbol, price):
    now = timezone.now()
    return QuoteCache.objects.create(
        asset_class=AssetClass.STOCK, symbol=symbol, price=Decimal(price),
        prev_close=Decimal(price), fetched_at=now, expires_at=now + timedelta(seconds=10),
    )


def fill(participation, symbol, side, qty, price, *, at, fee=0, tax=0):
    """체결된 주문 하나 — 회전율·수수료 집계의 재료."""
    from trading.models import Execution, Order, OrderStatus, OrderType

    gross = int(Decimal(qty) * Decimal(price))
    order = Order.objects.create(
        account=participation.account, symbol=symbol, asset_class=AssetClass.STOCK,
        side=side, order_type=OrderType.MARKET,
        requested_qty=Decimal(qty), filled_qty=Decimal(qty),
        avg_fill_price=Decimal(price), gross_amount=gross, fee=fee, tax=tax,
        status=OrderStatus.FILLED, filled_at=at,
    )
    Execution.objects.create(
        order=order, seq=1, qty=Decimal(qty), price=Decimal(price),
        amount=gross, executed_at=at,
    )
    return order


class SettlementBase(TestCase):
    """정산 테스트가 공유하는 무대 — 대회 1개 · 종목 2개 · 참가자 3명."""

    def setUp(self):
        self.today = date(2026, 8, 13)          # 목요일
        TradingCalendar.objects.create(date=self.today, is_open=True)
        self.contest = make_contest(
            slug="settle-1",
            start_date=self.today - timedelta(days=10),
            end_date=self.today + timedelta(days=10),
        )
        make_stock(SAMSUNG, name="삼성전자")
        make_stock(HYNIX, name="SK하이닉스", market_cap=200_000_000_000_000)
        # ★ 예외 한도가 없는 종목을 하나 둔다 — 삼성전자(40%)·하이닉스(30%)는
        #   `default_rule_set` 의 `position_limit_exceptions` 에 걸려 있어
        #   **기본 한도 15% 를 시험할 수 없다** (F-04 3.1).
        make_stock(NAVER, name="NAVER", market_cap=30_000_000_000_000)
        put_in_universe(self.contest, SAMSUNG)
        put_in_universe(self.contest, HYNIX)
        put_in_universe(self.contest, NAVER)
        quote(SAMSUNG, 80_000)
        quote(HYNIX, 200_000)
        quote(NAVER, 200_000)

        # 삼성전자 100주(평단 7만) → 평가 800만 · 손익 +100만
        self.alice = join(self.contest, "alice", cash=92_000_000)
        hold(self.alice, SAMSUNG, 100, 70_000)
        # 하이닉스 50주(평단 20만) → 평가 1000만 · 손익 0
        self.bob = join(self.contest, "bob", cash=90_000_000)
        hold(self.bob, HYNIX, 50, 200_000)
        # 전액 현금
        self.carol = join(self.contest, "carol", cash=ONE_EOK)


# ─────────────────────────────────────────────────────────────────
# 8. 잡 7 — 일별 정산 (F-05 4.1)
# ─────────────────────────────────────────────────────────────────


class SettleDailyTests(SettlementBase):
    def test_참가자마다_스냅샷이_하나씩_생긴다(self):
        result = contest_jobs.settle_daily(target_date=self.today)

        self.assertEqual(result.created, 3)
        self.assertEqual(result.updated, 0)
        self.assertEqual(DailySnapshot.objects.filter(date=self.today).count(), 3)

    def test_평가액과_NAV(self):
        contest_jobs.settle_daily(target_date=self.today)

        snapshot = DailySnapshot.objects.get(participation=self.alice, date=self.today)
        self.assertEqual(snapshot.position_value, 8_000_000)     # 100주 × 8만
        self.assertEqual(snapshot.total_asset, ONE_EOK)          # 현금 9200만 + 800만
        self.assertEqual(snapshot.nav, Decimal("1000.0000"))
        self.assertEqual(snapshot.position_count, 1)
        self.assertEqual(snapshot.invested_ratio_pct, Decimal("8.0000"))

    def test_JSONB_원본과_정규화_파생이_일치한다(self):
        """★ E-02 6.2 이중 저장 규약 — 같은 트랜잭션에서 한 원본으로 두 표현을 만든다."""
        contest_jobs.settle_daily(target_date=self.today)

        snapshot = DailySnapshot.objects.get(participation=self.alice, date=self.today)
        rows = list(SnapshotHolding.objects.filter(snapshot=snapshot))
        self.assertEqual(len(rows), len(snapshot.holdings))
        self.assertEqual(rows[0].symbol, snapshot.holdings[0]["symbol"])
        self.assertEqual(rows[0].value, snapshot.holdings[0]["value"])
        self.assertEqual(str(rows[0].weight_pct), snapshot.holdings[0]["weight_pct"])
        # 조회 편의를 위한 비정규 복제도 채워져 있어야 한다 (Top Pick 매트릭스용)
        self.assertEqual(rows[0].contest_id, self.contest.pk)
        self.assertEqual(rows[0].date, self.today)

    def test_평가손익은_평단_대비다(self):
        contest_jobs.settle_daily(target_date=self.today)

        row = SnapshotHolding.objects.get(participation=self.alice, symbol=SAMSUNG)
        self.assertEqual(row.pnl, 1_000_000)                     # (8만-7만) × 100주
        self.assertEqual(row.pnl_pct, Decimal("14.2857"))

    def test_두_번_돌려도_행이_늘지_않는다(self):
        """★ 멱등성 — 재실행인데 created 가 0 이 아니면 upsert 키가 잘못 잡힌 것이다."""
        contest_jobs.settle_daily(target_date=self.today)
        second = contest_jobs.settle_daily(target_date=self.today)

        self.assertEqual(second.created, 0)
        self.assertEqual(second.updated, 3)
        self.assertEqual(DailySnapshot.objects.filter(date=self.today).count(), 3)
        self.assertEqual(SnapshotHolding.objects.filter(date=self.today).count(), 2)

    def test_판_종목은_파생에서_사라진다(self):
        """★ 파생을 부분 갱신하면 어제 팔아 없앤 종목이 오늘 날짜로 남는다."""
        contest_jobs.settle_daily(target_date=self.today)
        self.alice.account.positions.all().delete()
        contest_jobs.settle_daily(target_date=self.today)

        self.assertEqual(SnapshotHolding.objects.filter(participation=self.alice).count(), 0)
        snapshot = DailySnapshot.objects.get(participation=self.alice, date=self.today)
        self.assertEqual(snapshot.holdings, [])

    def test_순위는_누적수익률_내림차순이다(self):
        """★ 진행 중 순위는 단순 수익률이다. 관리 점수는 최종 정산에만 (F-05 3.1)."""
        # alice 는 +0%, bob 은 +0%, carol 은 0% — 값을 벌려 놓는다
        self.alice.account.cash += 5_000_000
        self.alice.account.save(update_fields=["cash"])
        contest_jobs.settle_daily(target_date=self.today)

        rankings = {
            row.participation_id: row.rank
            for row in ContestRanking.objects.filter(contest=self.contest, date=self.today)
        }
        self.assertEqual(rankings[self.alice.pk], 1)
        self.assertEqual(rankings[self.bob.pk], 2)      # 동점이라 2위 두 명
        self.assertEqual(rankings[self.carol.pk], 2)

    def test_실격자는_행은_있고_순위는_비어_있다(self):
        """★ 목록에서 지우면 '내가 왜 없지?' 라는 문의가 온다 (F-05 3.2)."""
        self.carol.status = ParticipationStatus.DISQUALIFIED
        self.carol.is_ranked = False
        self.carol.save(update_fields=["status", "is_ranked"])

        contest_jobs.settle_daily(target_date=self.today)

        row = ContestRanking.objects.get(participation=self.carol, date=self.today)
        self.assertIsNone(row.rank)
        self.assertTrue(DailySnapshot.objects.filter(participation=self.carol).exists())

    def test_전일_순위가_등락_표시용으로_남는다(self):
        yesterday = self.today - timedelta(days=1)
        contest_jobs.settle_daily(target_date=yesterday)
        contest_jobs.settle_daily(target_date=self.today)

        row = ContestRanking.objects.get(participation=self.alice, date=self.today)
        self.assertIsNotNone(row.prev_rank)

    def test_당일_거래는_체결시각으로_센다(self):
        """★ 접수일이 아니라 **체결일**이다 — 장외 주문은 다음 영업일에 체결된다."""
        at = datetime(2026, 8, 13, 10, 0, tzinfo=KST)
        fill(self.alice, SAMSUNG, OrderSide.BUY, 10, 80_000, at=at, fee=800)
        # 전날 체결분은 오늘 집계에 들어오면 안 된다
        fill(self.alice, SAMSUNG, OrderSide.SELL, 5, 79_000,
             at=at - timedelta(days=1), fee=400, tax=800)

        contest_jobs.settle_daily(target_date=self.today)

        snapshot = DailySnapshot.objects.get(participation=self.alice, date=self.today)
        self.assertEqual(snapshot.buy_amount, 800_000)
        self.assertEqual(snapshot.sell_amount, 0)
        self.assertEqual(snapshot.fee_amount, 800)

    def test_휴장일에는_아무것도_하지_않는다(self):
        TradingCalendar.objects.create(date=date(2026, 8, 15), is_open=False)
        with patch("contests.jobs.today_kst", return_value=date(2026, 8, 15)):
            result = contest_jobs.settle_daily()

        self.assertEqual(result.rows, 0)
        self.assertFalse(DailySnapshot.objects.exists())
        self.assertTrue(any("영업일이 아니" in note for note in result.notes))

    def test_날짜를_명시하면_휴장일_판정을_건너뛴다(self):
        """★ 운영자가 날짜를 찍는 것은 '그날을 정산하라' 는 지시다."""
        holiday = date(2026, 8, 15)
        TradingCalendar.objects.create(date=holiday, is_open=False)
        self.contest.end_date = holiday + timedelta(days=5)
        self.contest.save(update_fields=["end_date"])

        result = contest_jobs.settle_daily(target_date=holiday)
        self.assertEqual(result.created, 3)

    def test_dry_run_은_아무것도_쓰지_않는다(self):
        result = contest_jobs.settle_daily(target_date=self.today, dry_run=True)

        self.assertFalse(DailySnapshot.objects.exists())
        self.assertFalse(ContestRanking.objects.exists())
        self.assertEqual(result.updated, 3)

    def test_시세가_없으면_평단으로_평가하고_알린다(self):
        """★ 0 으로 두면 순자산이 줄어 엉뚱한 종목이 한도 위반으로 기록된다."""
        QuoteCache.objects.all().delete()
        StockMaster.objects.update(close_price=0)

        result = contest_jobs.settle_daily(target_date=self.today)

        snapshot = DailySnapshot.objects.get(participation=self.alice, date=self.today)
        self.assertEqual(snapshot.position_value, 7_000_000)      # 평단 7만 × 100주
        self.assertTrue(any("매입단가로 평가" in note for note in result.notes))

    def test_이번주_회전율_잠정치가_함께_갱신된다(self):
        contest_jobs.settle_daily(target_date=self.today)

        row = WeeklyTurnover.objects.get(participation=self.alice)
        self.assertEqual(row.week_start, date(2026, 8, 10))       # 그 주 월요일
        self.assertFalse(row.is_confirmed)


class ViolationTests(SettlementBase):
    def test_종목한도_초과는_경고로_기록된다(self):
        """★ 가격 급등에 의한 초과는 강제 매도하지 않는다. 위반일수만 쌓는다 (F-04 3.4)."""
        hold(self.carol, NAVER, 120, 150_000)        # 평가 2400만 = 24% > 한도 15%
        self.carol.account.cash = 76_000_000
        self.carol.account.save(update_fields=["cash"])

        contest_jobs.settle_daily(target_date=self.today)

        violation = RuleViolation.objects.get(
            participation=self.carol, rule=ViolationRule.POSITION_LIMIT, date=self.today
        )
        self.assertFalse(violation.is_resolved)
        self.assertEqual(violation.detail["items"][0]["symbol"], NAVER)

        snapshot = DailySnapshot.objects.get(participation=self.carol, date=self.today)
        self.assertEqual(len(snapshot.violations), 1)

    def test_예외_한도가_적용된다(self):
        """삼성전자는 40% 까지 허용된다 (F-04 3.1)."""
        hold(self.carol, SAMSUNG, 400, 70_000)       # 3200만 = 32% < 40%
        self.carol.account.cash = 68_000_000
        self.carol.account.save(update_fields=["cash"])

        contest_jobs.settle_daily(target_date=self.today)

        self.assertFalse(
            RuleViolation.objects.filter(participation=self.carol).exists()
        )

    def test_해소되면_is_resolved_로_표시하고_지우지_않는다(self):
        """★ 지우면 '3일 연속 위반했다가 풀었다' 는 이력이 사라진다."""
        hold(self.carol, NAVER, 120, 150_000)
        self.carol.account.cash = 76_000_000
        self.carol.account.save(update_fields=["cash"])
        contest_jobs.settle_daily(target_date=self.today)

        self.carol.account.positions.all().delete()
        contest_jobs.settle_daily(target_date=self.today)

        violation = RuleViolation.objects.get(participation=self.carol, date=self.today)
        self.assertTrue(violation.is_resolved)

    def test_섹터_한도는_스냅샷이_없으면_검사하지_않는다(self):
        """★ 준비가 덜 된 대회에서 규칙만 먼저 발동하면 모든 섹터가 위반이 된다."""
        hold(self.carol, SAMSUNG, 900, 70_000)       # 7200만 = 72%
        self.carol.account.cash = 28_000_000
        self.carol.account.save(update_fields=["cash"])

        contest_jobs.settle_daily(target_date=self.today)

        self.assertFalse(
            RuleViolation.objects.filter(rule=ViolationRule.SECTOR_LIMIT).exists()
        )

    def test_섹터_한도가_있으면_판정한다(self):
        ContestSectorWeight.objects.create(
            contest=self.contest, sector_code="1013", sector_name="전기전자",
            market_weight_pct=Decimal(20), limit_pct=Decimal(40),
        )
        hold(self.carol, SAMSUNG, 900, 70_000)       # 7200만 = 72% > 40%
        self.carol.account.cash = 28_000_000
        self.carol.account.save(update_fields=["cash"])

        contest_jobs.settle_daily(target_date=self.today)

        violation = RuleViolation.objects.get(
            participation=self.carol, rule=ViolationRule.SECTOR_LIMIT
        )
        self.assertEqual(violation.detail["items"][0]["sector_code"], "1013")


# ─────────────────────────────────────────────────────────────────
# 9. 잡 4 — 장중 스냅샷 (F-05 2.3)
# ─────────────────────────────────────────────────────────────────


class IntradayTests(SettlementBase):
    def test_시각은_10분_단위로_내려간다(self):
        """★ 내림이 곧 멱등성이다 — 09:07 과 09:09 가 같은 칸에 떨어진다."""
        moment = datetime(2026, 8, 13, 9, 7, 31, tzinfo=KST)
        self.assertEqual(
            services.intraday_slot(moment), datetime(2026, 8, 13, 9, 0, tzinfo=KST)
        )
        self.assertEqual(
            services.intraday_slot(datetime(2026, 8, 13, 15, 29, tzinfo=KST)),
            datetime(2026, 8, 13, 15, 20, tzinfo=KST),
        )

    def test_장중_판정(self):
        self.assertTrue(
            services.is_intraday_window(datetime(2026, 8, 13, 9, 0, tzinfo=KST))
        )
        self.assertTrue(
            services.is_intraday_window(datetime(2026, 8, 13, 15, 30, tzinfo=KST))
        )
        self.assertFalse(
            services.is_intraday_window(datetime(2026, 8, 13, 8, 59, tzinfo=KST))
        )
        self.assertFalse(
            services.is_intraday_window(datetime(2026, 8, 13, 15, 31, tzinfo=KST))
        )

    def test_장외에는_아무것도_하지_않는다(self):
        with patch("contests.services.is_intraday_window", return_value=False):
            result = contest_jobs.snapshot_intraday()

        self.assertEqual(result.rows, 0)
        self.assertFalse(IntradaySnapshot.objects.exists())
        self.assertTrue(any("장중이 아니" in note for note in result.notes))

    def test_장중에는_참가자마다_한_행(self):
        slot = datetime(2026, 8, 13, 10, 30, tzinfo=KST)
        with patch("contests.services.is_intraday_window", return_value=True), \
             patch("contests.services.intraday_slot", return_value=slot), \
             patch("contests.jobs.today_kst", return_value=self.today):
            result = contest_jobs.snapshot_intraday()

        self.assertEqual(result.created, 3)
        self.assertEqual(IntradaySnapshot.objects.filter(at=slot).count(), 3)
        row = IntradaySnapshot.objects.get(participation=self.alice, at=slot)
        self.assertEqual(row.nav, Decimal("1000.0000"))
        self.assertEqual(row.return_pct, Decimal("0.0000"))

    def test_같은_칸에_두_번_돌면_덮어쓴다(self):
        slot = datetime(2026, 8, 13, 10, 30, tzinfo=KST)
        with patch("contests.services.is_intraday_window", return_value=True), \
             patch("contests.services.intraday_slot", return_value=slot), \
             patch("contests.jobs.today_kst", return_value=self.today):
            contest_jobs.snapshot_intraday()
            second = contest_jobs.snapshot_intraday()

        self.assertEqual(second.created, 0)
        self.assertEqual(second.updated, 3)
        self.assertEqual(IntradaySnapshot.objects.count(), 3)


# ─────────────────────────────────────────────────────────────────
# 10. 잡 9 — 주간 정산 (F-04 5장)
# ─────────────────────────────────────────────────────────────────


class SettleWeeklyTests(SettlementBase):
    def _snapshot(self, participation, day, *, buy=0, sell=0, asset=ONE_EOK):
        return DailySnapshot.objects.create(
            participation=participation, date=day,
            cash=asset, position_value=0, total_asset=asset,
            nav=Decimal(1000), buy_amount=buy, sell_amount=sell,
        )

    def test_회전율을_확정한다(self):
        week = date(2026, 8, 3)          # 월요일
        for offset in range(5):
            self._snapshot(self.alice, week + timedelta(days=offset),
                           buy=4_000_000 if offset == 0 else 0)

        result = contest_jobs.settle_weekly(week_start=week)

        row = WeeklyTurnover.objects.get(participation=self.alice, week_start=week)
        self.assertTrue(row.is_confirmed)
        self.assertEqual(row.buy_amount, 4_000_000)
        # (400만 + 0) / 1억 × 0.5 × 100 = 2% → 5% 미만이라 위반
        self.assertEqual(row.turnover_pct, Decimal("2.0000"))
        self.assertTrue(row.is_violation)
        self.assertEqual(result.created, 1)

    def test_기준을_넘기면_위반이_아니다(self):
        week = date(2026, 8, 3)
        self._snapshot(self.alice, week, buy=20_000_000)
        contest_jobs.settle_weekly(week_start=week)

        row = WeeklyTurnover.objects.get(participation=self.alice, week_start=week)
        self.assertEqual(row.turnover_pct, Decimal("10.0000"))
        self.assertFalse(row.is_violation)

    def test_평균운용금액은_스냅샷이_있는_날로만_나눈다(self):
        """★ 주 5일로 고정해 나누면 공휴일이 낀 주에서 없던 위반이 생긴다."""
        week = date(2026, 8, 3)
        self._snapshot(self.alice, week, buy=10_000_000, asset=ONE_EOK)
        contest_jobs.settle_weekly(week_start=week)

        row = WeeklyTurnover.objects.get(participation=self.alice, week_start=week)
        self.assertEqual(row.avg_asset, ONE_EOK)
        self.assertEqual(row.turnover_pct, Decimal("5.0000"))
        self.assertFalse(row.is_violation)       # 정확히 5% 는 통과

    def test_4회_위반이면_자동_정지한다(self):
        """★ 회전율만이 유일한 자동 실격이다 (F-04 5.4)."""
        for index in range(3):
            week = date(2026, 7, 13) + timedelta(weeks=index)
            WeeklyTurnover.objects.create(
                participation=self.alice, week_start=week, avg_asset=ONE_EOK,
                turnover_pct=Decimal(0), is_violation=True, is_confirmed=True,
                violation_seq=index + 1,
            )
        fourth = date(2026, 8, 3)
        self._snapshot(self.alice, fourth)        # 매매 없음 → 회전율 0%

        result = contest_jobs.settle_weekly(week_start=fourth)

        self.alice.refresh_from_db()
        self.assertEqual(self.alice.status, ParticipationStatus.DISQUALIFIED)
        self.assertFalse(self.alice.is_ranked)
        self.assertIn("4회", self.alice.disqualified_reason)
        self.assertTrue(any("자동 정지" in note for note in result.notes))
        # ★ 계좌·주문 이력은 그대로 둔다
        self.assertTrue(self.alice.account.positions.exists())

    def test_3회까지는_실격이_아니다(self):
        for index in range(2):
            WeeklyTurnover.objects.create(
                participation=self.alice, week_start=date(2026, 7, 20) + timedelta(weeks=index),
                avg_asset=ONE_EOK, turnover_pct=Decimal(0),
                is_violation=True, is_confirmed=True, violation_seq=index + 1,
            )
        week = date(2026, 8, 3)
        self._snapshot(self.alice, week)

        contest_jobs.settle_weekly(week_start=week)

        self.alice.refresh_from_db()
        self.assertEqual(self.alice.status, ParticipationStatus.APPROVED)

    def test_위반을_되돌리면_번호가_다시_매겨진다(self):
        """★ 운영자가 Admin 에서 되돌릴 수 있어야 한다 (F-04 5.4)."""
        first = WeeklyTurnover.objects.create(
            participation=self.alice, week_start=date(2026, 7, 27), avg_asset=ONE_EOK,
            turnover_pct=Decimal(0), is_violation=True, is_confirmed=True, violation_seq=1,
        )
        week = date(2026, 8, 3)
        self._snapshot(self.alice, week)
        contest_jobs.settle_weekly(week_start=week)
        self.assertEqual(
            WeeklyTurnover.objects.get(participation=self.alice, week_start=week).violation_seq, 2
        )

        first.is_violation = False
        first.save(update_fields=["is_violation"])
        contest_jobs.settle_weekly(week_start=week)

        self.assertEqual(
            WeeklyTurnover.objects.get(participation=self.alice, week_start=week).violation_seq, 1
        )

    def test_스냅샷이_없는_주는_건너뛴다(self):
        result = contest_jobs.settle_weekly(week_start=date(2026, 6, 1))

        self.assertEqual(result.rows, 0)
        self.assertFalse(WeeklyTurnover.objects.exists())

    def test_dry_run_은_아무것도_저장하지_않는다(self):
        """★ 커맨드가 'DB 에 아무것도 쓰지 않았습니다' 라고 말하는데 회전율 행이
        늘어나 있으면 그 약속이 거짓이 된다."""
        for index in range(3):
            WeeklyTurnover.objects.create(
                participation=self.alice, week_start=date(2026, 7, 13) + timedelta(weeks=index),
                avg_asset=ONE_EOK, turnover_pct=Decimal(0),
                is_violation=True, is_confirmed=True, violation_seq=index + 1,
            )
        week = date(2026, 8, 3)
        self._snapshot(self.alice, week)

        result = contest_jobs.settle_weekly(week_start=week, dry_run=True)

        self.alice.refresh_from_db()
        self.assertEqual(self.alice.status, ParticipationStatus.APPROVED)
        self.assertFalse(
            WeeklyTurnover.objects.filter(participation=self.alice, week_start=week).exists()
        )
        # ★ 대신 **누가 잘릴 뻔했는지**는 읽기만으로 알려준다
        self.assertTrue(any("자동 정지될 예정" in note for note in result.notes))


# ─────────────────────────────────────────────────────────────────
# 11. 잡 10 — 상태 전이 · 최종 정산 (F-05 4.3)
# ─────────────────────────────────────────────────────────────────


class SettleContestTests(SettlementBase):
    def test_시작일이_되면_ONGOING_이_되고_유니버스를_얼린다(self):
        upcoming = make_contest(
            slug="upcoming-1", status=ContestStatus.UPCOMING,
            start_date=self.today, end_date=self.today + timedelta(days=30),
        )

        result = contest_jobs.settle_contest(target_date=self.today)

        upcoming.refresh_from_db()
        self.assertEqual(upcoming.status, ContestStatus.ONGOING)
        self.assertIsNotNone(upcoming.universe_frozen_at)
        self.assertEqual(ContestUniverse.objects.filter(contest=upcoming).count(), 3)
        self.assertTrue(ContestSectorWeight.objects.filter(contest=upcoming).exists())
        self.assertTrue(any("유니버스 고정" in note for note in result.notes))

    def test_종료일_당일에는_아직_ONGOING_이다(self):
        """★ 그날 15:40 의 마지막 정산이 남아 있다."""
        self.contest.end_date = self.today
        self.contest.save(update_fields=["end_date"])

        contest_jobs.settle_contest(target_date=self.today)

        self.contest.refresh_from_db()
        self.assertEqual(self.contest.status, ContestStatus.ONGOING)

    def test_다음날_06시에_SETTLING_을_거쳐_CLOSED_까지_간다(self):
        self.contest.end_date = self.today - timedelta(days=1)
        self.contest.save(update_fields=["end_date"])
        contest_jobs.settle_daily(target_date=self.today - timedelta(days=1))

        contest_jobs.settle_contest(target_date=self.today)

        self.contest.refresh_from_db()
        self.assertEqual(self.contest.status, ContestStatus.CLOSED)
        self.assertEqual(ContestResult.objects.filter(contest=self.contest).count(), 3)

    def test_최종_정산은_계좌를_동결한다(self):
        self.contest.status = ContestStatus.SETTLING
        self.contest.save(update_fields=["status"])
        contest_jobs.settle_contest(target_date=self.today)

        self.alice.account.refresh_from_db()
        self.assertTrue(self.alice.account.is_frozen)

    def test_등급과_백분위가_매겨진다(self):
        day = self.today - timedelta(days=1)
        for participation, pct in ((self.alice, 30), (self.bob, 10), (self.carol, -5)):
            DailySnapshot.objects.create(
                participation=participation, date=day,
                cash=ONE_EOK, position_value=0, total_asset=ONE_EOK,
                nav=Decimal(1000) + Decimal(pct) * 10,
                cumulative_return_pct=Decimal(pct),
            )
        self.contest.status = ContestStatus.SETTLING
        self.contest.save(update_fields=["status"])

        contest_jobs.settle_contest(target_date=self.today)

        winner = ContestResult.objects.get(participation=self.alice)
        loser = ContestResult.objects.get(participation=self.carol)
        self.assertEqual(winner.final_rank, 1)
        self.assertEqual(loser.final_rank, 3)
        self.assertEqual(winner.return_score, Decimal("30.0000"))
        self.assertTrue(winner.grade)
        self.assertGreater(winner.final_score, loser.final_score)

    def test_실격자는_최종순위를_받지_않는다(self):
        self.carol.is_ranked = False
        self.carol.status = ParticipationStatus.DISQUALIFIED
        self.carol.save(update_fields=["is_ranked", "status"])
        contest_jobs.settle_daily(target_date=self.today)
        self.contest.status = ContestStatus.SETTLING
        self.contest.save(update_fields=["status"])

        contest_jobs.settle_contest(target_date=self.today)

        result = ContestResult.objects.get(participation=self.carol)
        self.assertIsNone(result.final_rank)
        self.assertEqual(result.grade, "")
        # 수익 원점수는 남는다 — "내 최종 수익률이 얼마였나" 는 보여줘야 한다
        self.assertIsNotNone(result.return_score)

    def test_바꿀_대회가_없으면_조용히_끝난다(self):
        result = contest_jobs.settle_contest(target_date=self.today)

        self.assertEqual(result.rows, 0)
        self.assertTrue(any("바꿀 대회가 없" in note for note in result.notes))

    def test_dry_run_은_상태를_바꾸지_않는다(self):
        self.contest.status = ContestStatus.SETTLING
        self.contest.save(update_fields=["status"])

        contest_jobs.settle_contest(target_date=self.today, dry_run=True)

        self.contest.refresh_from_db()
        self.assertEqual(self.contest.status, ContestStatus.SETTLING)
        self.assertFalse(ContestResult.objects.exists())


class SettleContestWeeklyGateTests(SettlementBase):
    """★★ 최종 확정 **전에** 주간 회전율을 마감하는가 (2026-08-16 추가).

    `settle_weekly` 는 주 1회(월요일 06:00 KST)이고 `settle_contest` 는 매일
    06:10 이다. 두 잡의 10분 간격이 순서를 만들어 주는 것은 **월요일에 끝난
    대회뿐**이다. 금요일에 끝나면 마지막 주가 미확정인 채 등급이 굳는다.

    회전율 4회 위반 자동 실격은 `apply_turnover_disqualification` 이 유일한 경로이고,
    `finalize_contest` 는 계좌를 얼리고 `CLOSED` 로 닫아 앱 안에 되돌릴 길이 없다.
    → 그래서 `_finalize_settling` 이 **스스로** 마감한다.
    """

    def _snapshot(self, participation, day, *, buy=0, sell=0, asset=ONE_EOK):
        return DailySnapshot.objects.create(
            participation=participation, date=day,
            cash=asset, position_value=0, total_asset=asset,
            nav=Decimal(1000), buy_amount=buy, sell_amount=sell,
        )

    def setUp(self):
        super().setUp()
        # 2026-07-13(월) ~ 2026-08-14(**금**) — 월요일에 끝나지 **않는** 대회다.
        self.contest.start_date = date(2026, 7, 13)
        self.contest.end_date = date(2026, 8, 14)
        self.contest.save(update_fields=["start_date", "end_date"])
        # 대회가 **온전히 덮는** 네 주. 마지막 08-10 주는 금요일에 잘리는 토막이라
        # 확정 대상이 아니다 (아래 test_토막_주는_확정하지_않는다 참조).
        self.weeks = [date(2026, 7, 13), date(2026, 7, 20),
                      date(2026, 7, 27), date(2026, 8, 3)]
        self.stub_week = date(2026, 8, 10)
        # alice 는 내내 매매가 없다 → 회전율 0% → 4회 위반 → 허용 3회 초과
        for week in [*self.weeks, self.stub_week]:
            self._snapshot(self.alice, week)

    def test_금요일에_끝난_대회도_마지막_주까지_확정하고_실격시킨다(self):
        """★ 이 테스트는 수정 전 코드에서 **실패한다** — 그게 요점이다.

        수정 전에는 `settle_weekly` 가 돌지 않은 주가 미확정으로 남아
        alice 가 실격되지 않은 채 등급을 받았다.
        """
        result = contest_jobs.settle_contest(target_date=date(2026, 8, 15))  # 토요일

        self.contest.refresh_from_db()
        self.alice.refresh_from_db()

        self.assertEqual(self.contest.status, ContestStatus.CLOSED)
        self.assertEqual(self.alice.status, ParticipationStatus.DISQUALIFIED)
        self.assertFalse(self.alice.is_ranked)
        self.assertIn("4회", self.alice.disqualified_reason)
        self.assertTrue(any("실격" in note for note in result.notes))

    def test_실격자도_결과행은_남기되_순위는_비운다(self):
        """계좌·이력은 살아 있고 랭킹에서만 빠진다 (F-04 5.4 · F-05 3.2)."""
        contest_jobs.settle_contest(target_date=date(2026, 8, 15))

        row = ContestResult.objects.get(participation=self.alice)
        self.assertIsNone(row.final_rank)
        # 나머지 둘은 정상적으로 순위를 받는다
        others = ContestResult.objects.filter(contest=self.contest).exclude(
            participation=self.alice
        )
        self.assertEqual(others.count(), 2)
        self.assertTrue(all(r.final_rank is not None for r in others))

    def test_스냅샷이_없는_주는_위반으로_세지_않는다(self):
        """★ 대회 전체 주를 훑는다고 **없던 위반이 생기면** 안 된다.

        bob·carol 은 스냅샷이 하루도 없다. `compute_weekly_turnover` 의
        `day_count > 0` 조건이 그들을 지켜 줘야 한다.
        """
        contest_jobs.settle_contest(target_date=date(2026, 8, 15))

        self.bob.refresh_from_db()
        self.carol.refresh_from_db()
        self.assertEqual(self.bob.status, ParticipationStatus.APPROVED)
        self.assertEqual(self.carol.status, ParticipationStatus.APPROVED)
        self.assertFalse(
            WeeklyTurnover.objects.filter(participation=self.bob, is_violation=True).exists()
        )

    def test_두_번_돌려도_결과가_같다(self):
        """`settle_weekly` 가 이미 확정한 주를 다시 훑어도 같은 값이 덮인다.

        월요일에 끝난 대회에서 실제로 일어나는 순서다 — `settle_weekly` 가 06:00 에
        네 주를 모두 확정해 alice 를 실격시키고, 10분 뒤 `settle_contest` 가 같은
        주들을 다시 훑는다. **도장을 두 번 찍으면 안 된다.**
        """
        for week in self.weeks:
            contest_jobs.settle_weekly(week_start=week)
        self.alice.refresh_from_db()
        self.assertEqual(self.alice.status, ParticipationStatus.DISQUALIFIED)
        already = self.alice.disqualified_at
        self.assertIsNotNone(already)

        contest_jobs.settle_contest(target_date=date(2026, 8, 15))

        self.alice.refresh_from_db()
        self.assertEqual(self.alice.status, ParticipationStatus.DISQUALIFIED)
        # 이미 실격이면 다시 도장을 찍지 않는다 (apply_… 가 False 를 돌려준다)
        self.assertEqual(self.alice.disqualified_at, already)
        self.assertEqual(
            WeeklyTurnover.objects.filter(participation=self.alice).count(), len(self.weeks)
        )

    def test_토막_주는_확정하지_않는다(self):
        """★★ 대회가 주 전체를 덮지 않는 주는 **건드리지 않는다**.

        `compute_weekly_turnover` 의 분모는 **평균** 운용금액이라, 하루짜리 토막
        주도 5일 주와 같은 절대 매매금액을 요구한다. 마지막 날 매매를 멈추는 것은
        정상인데 그 주를 확정하면 자동으로 위반이 붙는다.

        확정을 앞당기는 것이 이 함수의 일이지 **판정 기준을 바꾸는 것은 아니다.**
        토막 주는 예전처럼 대회가 닫힌 뒤 `settle_weekly` 가 맡는다.
        """
        contest_jobs.settle_contest(target_date=date(2026, 8, 15))

        self.assertFalse(
            WeeklyTurnover.objects.filter(
                participation=self.alice, week_start=self.stub_week, is_confirmed=True
            ).exists(),
            "금요일에 잘린 토막 주가 확정됐다 — 자동 위반이 된다",
        )
        # 온전한 네 주는 확정됐다
        self.assertEqual(
            WeeklyTurnover.objects.filter(
                participation=self.alice, is_confirmed=True
            ).count(),
            len(self.weeks),
        )

    def test_한_명이라도_확정에_실패하면_대회를_닫지_않는다(self):
        """★★ 백분위는 **모수가 맞아야** 뜻이 있다.

        실격됐어야 할 사람이 미확정으로 랭킹에 남으면 그 사람만 틀리는 게 아니라
        **아래 참가자 전원의 등급이 이동한다.** 그런데 CLOSED 는 되돌릴 수 없다.
        미루는 비용은 0 이다 — SETTLING 으로 남아 내일 다시 시도된다.
        """
        from unittest.mock import patch

        real = services.refresh_weekly_turnover

        def flaky(participation, week_start, *, confirm):
            if participation.pk == self.alice.pk:
                raise RuntimeError("DB 일시 장애")
            return real(participation, week_start, confirm=confirm)

        with patch.object(services, "refresh_weekly_turnover", side_effect=flaky):
            result = contest_jobs.settle_contest(target_date=date(2026, 8, 15))

        self.contest.refresh_from_db()
        self.assertEqual(self.contest.status, ContestStatus.SETTLING)   # CLOSED 가 아니다
        self.assertFalse(ContestResult.objects.filter(contest=self.contest).exists())
        self.assertTrue(any("미룹니다" in note for note in result.notes))

    def test_실패가_없으면_그대로_닫는다(self):
        """미루는 것은 실패했을 때뿐이다 — 정상 경로를 막지 않는다."""
        contest_jobs.settle_contest(target_date=date(2026, 8, 15))

        self.contest.refresh_from_db()
        self.assertEqual(self.contest.status, ContestStatus.CLOSED)

    def test_이미_확정된_주는_다시_계산하지_않는다(self):
        """★ 확정의 주인은 `settle_weekly` 다. 뒤늦게 덮어쓰지 않는다.

        비용 문제이기도 하다 — 13주 × 100명이면 확정 직전에 1,300 집계 쿼리가 몰린다.
        """
        for week in self.weeks:
            contest_jobs.settle_weekly(week_start=week)
        # 확정된 행을 밖에서 손본 것처럼 표시해 둔다 (운영자 shell 교정 시나리오)
        touched = WeeklyTurnover.objects.filter(
            participation=self.alice, week_start=self.weeks[0]
        )
        touched.update(turnover_pct=Decimal("99.99"))

        contest_jobs.settle_contest(target_date=date(2026, 8, 15))

        # 다시 계산했다면 0% 로 돌아갔을 값이다. 건드리지 않았으므로 그대로 남는다.
        self.assertEqual(
            WeeklyTurnover.objects.get(
                participation=self.alice, week_start=self.weeks[0]
            ).turnover_pct,
            Decimal("99.99"),
        )


class ManagementScoreTests(SettlementBase):
    def test_몰빵과_분산의_관리점수가_갈린다(self):
        """★ 수익률만으로 순위를 매기면 '한 종목에 몰빵해 운 좋게 오른 사람' 이 이긴다."""
        day = self.today
        DailySnapshot.objects.create(
            participation=self.alice, date=day, cash=0, position_value=ONE_EOK,
            total_asset=ONE_EOK, nav=Decimal(1000),
            holdings=[{"symbol": SAMSUNG, "value": ONE_EOK}],
        )
        DailySnapshot.objects.create(
            participation=self.bob, date=day, cash=0, position_value=ONE_EOK,
            total_asset=ONE_EOK, nav=Decimal(1000),
            holdings=[
                {"symbol": SAMSUNG, "value": 25_000_000},
                {"symbol": HYNIX, "value": 25_000_000},
                {"symbol": "111111", "value": 25_000_000},
                {"symbol": "222222", "value": 25_000_000},
            ],
        )

        concentrated, port_hhi_a, _ = services.management_metrics(self.alice)
        diversified, port_hhi_b, _ = services.management_metrics(self.bob)

        self.assertEqual(port_hhi_a, Decimal("1.000000"))
        self.assertEqual(port_hhi_b, Decimal("0.250000"))
        self.assertGreater(diversified, concentrated)

    def test_실현손익과_평가손익을_합쳐_본다(self):
        """★ 실현만 보면 끝까지 들고 있어 이익이 난 종목이 통째로 빠진다."""
        at = datetime(2026, 8, 12, 10, 0, tzinfo=KST)
        order = fill(self.alice, HYNIX, OrderSide.SELL, 10, 210_000, at=at)
        order.realized_pnl = 500_000
        order.save(update_fields=["realized_pnl"])
        contest_jobs.settle_daily(target_date=self.today)

        profits = services.symbol_profits(self.alice)
        self.assertEqual(profits[HYNIX], 500_000)          # 실현손익
        self.assertEqual(profits[SAMSUNG], 1_000_000)      # 평가손익


class FreezeUniverseTests(TestCase):
    def test_섹터_한도는_작은_섹터에만_하한_10퍼센트를_준다(self):
        """★ F-04 3.2 의 괄호 — '에너지 2% → 2×2=4% 가 아니라 하한 10%'."""
        contest = make_contest(slug="freeze-1")
        # 전기전자 80% · 에너지 20% 가 되도록 시총을 잡는다
        make_stock(SAMSUNG, name="삼성전자", market_cap=800_000_000_000_000)
        StockMaster.objects.create(
            symbol="096770", name="SK이노베이션", market=Market.KOSPI,
            stock_type=StockType.COMMON, sector_code="1015", sector_name="에너지",
            market_cap=200_000_000_000_000, avg_turnover_5d=1_000_000_000_000,
            close_price=Decimal(100_000),
        )

        symbols, sectors = services.freeze_universe(contest)

        self.assertEqual((symbols, sectors), (2, 2))
        big = ContestSectorWeight.objects.get(contest=contest, sector_code="1013")
        small = ContestSectorWeight.objects.get(contest=contest, sector_code="1015")
        self.assertEqual(big.market_weight_pct, Decimal("80.0000"))
        self.assertEqual(big.limit_pct, Decimal("160.0000"))      # 80 × 2
        self.assertEqual(small.market_weight_pct, Decimal("20.0000"))
        self.assertEqual(small.limit_pct, Decimal("40.0000"))     # 20 × 2 (하한 미적용)

    def test_업종이_없는_종목은_유니버스에_넣지_않는다(self):
        """★ 면제하면 미분류 종목만 담아 섹터 한도를 통째로 우회할 수 있다 (E-31)."""
        contest = make_contest(slug="freeze-2")
        make_stock(SAMSUNG)
        StockMaster.objects.create(
            symbol="999990", name="업종없음", market=Market.KOSDAQ,
            stock_type=StockType.COMMON, sector_code="", sector_name="",
            market_cap=1_000_000_000_000, avg_turnover_5d=10_000_000_000,
            close_price=Decimal(5_000),
        )

        symbols, _sectors = services.freeze_universe(contest)

        self.assertEqual(symbols, 1)
        self.assertFalse(
            ContestUniverse.objects.filter(contest=contest, symbol="999990").exists()
        )

    def test_다시_얼려도_행이_중복되지_않는다(self):
        contest = make_contest(slug="freeze-3")
        make_stock(SAMSUNG)
        services.freeze_universe(contest)
        services.freeze_universe(contest)

        self.assertEqual(ContestUniverse.objects.filter(contest=contest).count(), 1)
        self.assertEqual(ContestSectorWeight.objects.filter(contest=contest).count(), 1)


# ─────────────────────────────────────────────────────────────────
# 9. 참가 신청 · 승인 · 포기 (F-02 6장)
# ─────────────────────────────────────────────────────────────────


PASSWORD = "pass-1234!"


class JoinContestTests(TestCase):
    """참가 신청 서비스 (contests.services.join_contest)."""

    def setUp(self):
        self.member = make_member("joiner")

    def test_자동승인_대회는_즉시_승인되고_계좌가_생긴다(self):
        contest = make_contest(slug="join-auto", status=ContestStatus.UPCOMING)

        participation = services.join_contest(
            contest=contest, member=self.member, nickname="드래곤"
        )

        self.assertEqual(participation.status, ParticipationStatus.APPROVED)
        self.assertIsNotNone(participation.approved_at)
        self.assertIsNotNone(participation.account)
        # ★ 시작 자본은 대회 값이어야 한다. Account 의 기본값(1억)이 아니다.
        self.assertEqual(participation.account.cash, contest.initial_capital)
        self.assertEqual(participation.account.initial_capital, contest.initial_capital)
        self.assertEqual(participation.account.mode, AccountMode.CONTEST)
        self.assertEqual(participation.account.contest_id, contest.pk)

    def test_운영자승인_대회는_대기_상태이고_계좌가_없다(self):
        from contests.models import ApprovalMode

        contest = make_contest(
            slug="join-manual", status=ContestStatus.UPCOMING,
            approval_mode=ApprovalMode.MANUAL,
        )

        participation = services.join_contest(contest=contest, member=self.member)

        self.assertEqual(participation.status, ParticipationStatus.PENDING)
        self.assertIsNone(participation.account_id)

    def test_승인하면_계좌가_생기고_두_번_승인해도_계좌는_하나다(self):
        from contests.models import ApprovalMode
        from accounts.models import Account

        contest = make_contest(
            slug="join-approve", status=ContestStatus.UPCOMING,
            approval_mode=ApprovalMode.MANUAL,
        )
        participation = services.join_contest(contest=contest, member=self.member)

        services.approve_participation(participation)
        services.approve_participation(participation)

        participation.refresh_from_db()
        self.assertEqual(participation.status, ParticipationStatus.APPROVED)
        self.assertEqual(Account.objects.filter(contest=contest, member=self.member).count(), 1)

    def test_별칭을_비우면_참가자N_이_자동_부여된다(self):
        contest = make_contest(slug="join-nick", status=ContestStatus.UPCOMING)

        participation = services.join_contest(contest=contest, member=self.member)

        self.assertTrue(participation.nickname.startswith("참가자"))

    def test_중복_참가는_거절된다(self):
        contest = make_contest(slug="join-dup", status=ContestStatus.UPCOMING)
        services.join_contest(contest=contest, member=self.member, nickname="가")

        with self.assertRaises(services.JoinRejected) as caught:
            services.join_contest(contest=contest, member=self.member, nickname="나")

        self.assertEqual(caught.exception.rule, "ALREADY_JOINED")

    def test_별칭_중복은_거절되고_어느_칸인지_알려준다(self):
        contest = make_contest(slug="join-nick-dup", status=ContestStatus.UPCOMING)
        join(contest, "other", nickname="드래곤")

        with self.assertRaises(services.JoinRejected) as caught:
            services.join_contest(contest=contest, member=self.member, nickname="드래곤")

        self.assertEqual(caught.exception.rule, "NICKNAME_TAKEN")
        self.assertEqual(caught.exception.field, "nickname")

    def test_정원은_승인_대기까지_세어_막는다(self):
        """★ APPROVED 만 세면 승인 대기가 정원을 넘겨 쌓인다."""
        from contests.models import ApprovalMode

        contest = make_contest(
            slug="join-cap", status=ContestStatus.UPCOMING, capacity=1,
            approval_mode=ApprovalMode.MANUAL,
        )
        services.join_contest(contest=contest, member=make_member("first"))

        with self.assertRaises(services.JoinRejected) as caught:
            services.join_contest(contest=contest, member=self.member)

        self.assertEqual(caught.exception.rule, "CAPACITY_FULL")

    def test_신청_마감일이_지나면_거절된다(self):
        contest = make_contest(
            slug="join-closed", status=ContestStatus.ONGOING,
            entry_deadline=timezone.localdate() - timezone.timedelta(days=1),
        )

        with self.assertRaises(services.JoinRejected) as caught:
            services.join_contest(contest=contest, member=self.member)

        self.assertEqual(caught.exception.rule, "ENTRY_CLOSED")

    def test_준비중_대회는_신청할_수_없다(self):
        contest = make_contest(slug="join-draft", status=ContestStatus.DRAFT)

        with self.assertRaises(services.JoinRejected) as caught:
            services.join_contest(contest=contest, member=self.member)

        self.assertEqual(caught.exception.rule, "ENTRY_CLOSED")

    def test_학습_포인트_조건을_못_채우면_거절된다(self):
        contest = make_contest(
            slug="join-req", status=ContestStatus.UPCOMING,
            entry_requirement={"min_learning_points": 30},
        )

        with self.assertRaises(services.JoinRejected) as caught:
            services.join_contest(contest=contest, member=self.member)

        self.assertEqual(caught.exception.rule, "REQUIREMENT_NOT_MET")

    def test_참가_조건이_비어_있으면_아무도_막지_않는다(self):
        contest = make_contest(slug="join-noreq", status=ContestStatus.UPCOMING)

        participation = services.join_contest(contest=contest, member=self.member)

        self.assertEqual(participation.status, ParticipationStatus.APPROVED)

    def test_포기하면_랭킹에서만_빠지고_계좌는_남는다(self):
        contest = make_contest(slug="join-withdraw", status=ContestStatus.ONGOING)
        participation = services.join_contest(contest=contest, member=self.member)
        account_id = participation.account_id

        services.withdraw_participation(participation)

        participation.refresh_from_db()
        self.assertEqual(participation.status, ParticipationStatus.WITHDRAWN)
        self.assertFalse(participation.is_ranked)
        self.assertEqual(participation.account_id, account_id)


# ─────────────────────────────────────────────────────────────────
# 10. 포트폴리오 공개 범위 (F-05 3.3)
# ─────────────────────────────────────────────────────────────────


class PortfolioVisibilityTests(TestCase):
    """`can_view_portfolio` — 설정은 존중하되 판정은 가장 관대하게."""

    def setUp(self):
        self.contest = make_contest(slug="vis-1")
        self.target = join(self.contest, "target", nickname="대상")
        self.viewer = make_member("viewer")

    def _can(self, *, viewer, rank):
        return services.can_view_portfolio(
            contest=self.contest, viewer=viewer, participation=self.target, rank=rank
        )

    def test_ALL_이면_전원_공개다(self):
        self.assertTrue(self._can(viewer=self.viewer, rank=999))

    def test_SELF_ONLY_이면_남의_것은_막힌다(self):
        from contests.models import PortfolioVisibility

        self.contest.portfolio_visibility = PortfolioVisibility.SELF_ONLY
        self.assertFalse(self._can(viewer=self.viewer, rank=1))

    def test_SELF_ONLY_라도_본인_것은_보인다(self):
        from contests.models import PortfolioVisibility

        self.contest.portfolio_visibility = PortfolioVisibility.SELF_ONLY
        self.assertTrue(self._can(viewer=self.target.member, rank=1))

    def test_TOP_N_은_상위_N명만_공개한다(self):
        from contests.models import PortfolioVisibility

        self.contest.portfolio_visibility = PortfolioVisibility.TOP_N
        self.contest.portfolio_visible_top_n = 10

        self.assertTrue(self._can(viewer=self.viewer, rank=10))
        self.assertFalse(self._can(viewer=self.viewer, rank=11))

    def test_TOP_N_이라도_순위가_없는_사람은_공개한다(self):
        """★ 실격·포기자와 정산 전 참가자. 숨기면 회고 자료가 사라진다."""
        from contests.models import PortfolioVisibility

        self.contest.portfolio_visibility = PortfolioVisibility.TOP_N
        self.contest.portfolio_visible_top_n = 10

        self.assertTrue(self._can(viewer=self.viewer, rank=None))

    def test_운영자는_어떤_설정에서도_볼_수_있다(self):
        from contests.models import PortfolioVisibility

        self.contest.portfolio_visibility = PortfolioVisibility.SELF_ONLY
        staff = make_member("staff")
        staff.is_staff = True

        self.assertTrue(self._can(viewer=staff, rank=99))

    def test_비로그인은_SELF_ONLY_에서_막힌다(self):
        from django.contrib.auth.models import AnonymousUser
        from contests.models import PortfolioVisibility

        self.contest.portfolio_visibility = PortfolioVisibility.SELF_ONLY
        self.assertFalse(self._can(viewer=AnonymousUser(), rank=1))

    def test_차단_사유_문구가_비어_있지_않다(self):
        from contests.models import PortfolioVisibility

        self.contest.portfolio_visibility = PortfolioVisibility.TOP_N
        self.contest.portfolio_visible_top_n = 7

        self.assertIn("7", services.portfolio_block_reason(self.contest))


# ─────────────────────────────────────────────────────────────────
# 11. 대회 화면군 (U-02)
# ─────────────────────────────────────────────────────────────────


def rank_row(participation, *, rank, date=None, prev_rank=None, nav="1050.0000",
             cumulative="5.0000", daily="1.0000", positions=3, invested="88.3000"):
    """랭킹 한 줄 — 화면 테스트는 정산을 돌리지 않고 결과만 심는다."""
    return ContestRanking.objects.create(
        contest=participation.contest,
        participation=participation,
        date=date or timezone.localdate(),
        rank=rank,
        prev_rank=prev_rank,
        nav=Decimal(nav),
        cumulative_return_pct=Decimal(cumulative),
        daily_return_pct=Decimal(daily),
        position_count=positions,
        invested_ratio_pct=Decimal(invested),
    )


class ContestListScreenTests(TestCase):
    """대회 목록 `/contests/` (U-02 4.1)."""

    def setUp(self):
        from django.urls import reverse

        self.url = reverse("contests:list")

    def test_비로그인도_목록을_볼_수_있다(self):
        make_contest(slug="list-1", name="공개 대회")

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "contests/list.html")
        self.assertContains(response, "공개 대회")

    def test_비공개_대회는_목록에_뜨지_않는다(self):
        from contests.models import Visibility

        make_contest(slug="list-private", name="비밀 대회", visibility=Visibility.PRIVATE)

        response = self.client.get(self.url)

        self.assertNotContains(response, "비밀 대회")

    def test_탭으로_상태를_거른다(self):
        make_contest(slug="list-ongoing", name="진행 대회", status=ContestStatus.ONGOING)
        make_contest(slug="list-upcoming", name="모집 대회", status=ContestStatus.UPCOMING)

        ongoing = self.client.get(self.url, {"tab": "ongoing"})
        upcoming = self.client.get(self.url, {"tab": "upcoming"})

        self.assertContains(ongoing, "진행 대회")
        self.assertNotContains(ongoing, "모집 대회")
        self.assertContains(upcoming, "모집 대회")
        self.assertNotContains(upcoming, "진행 대회")

    def test_모르는_탭은_기본_탭으로_되돌린다(self):
        make_contest(slug="list-fallback", name="진행 대회", status=ContestStatus.ONGOING)

        response = self.client.get(self.url, {"tab": "없는탭"})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "진행 대회")

    def test_참가_중이면_내_순위가_카드에_보인다(self):
        contest = make_contest(slug="list-mine", name="내 대회")
        participation = join(contest, "me", nickname="나")
        rank_row(participation, rank=7, prev_rank=8, cumulative="8.2500")

        self.client.login(username="me", password=PASSWORD)
        response = self.client.get(self.url)

        self.assertContains(response, "참가 중")
        self.assertContains(response, "7위")
        self.assertContains(response, "+8.25%")

    def test_대회가_없으면_빈_상태_문구가_뜬다(self):
        response = self.client.get(self.url)

        self.assertContains(response, "이 상태의 대회가 아직 없습니다")


class ContestDetailScreenTests(TestCase):
    """대회 상세 `/contests/<slug>/` (U-02 4.2)."""

    def setUp(self):
        self.contest = make_contest(slug="detail-1", name="상세 대회")

    def _url(self, contest=None):
        from django.urls import reverse

        return reverse("contests:detail", args=[(contest or self.contest).slug])

    def test_규칙_요약과_데이터_출처가_보인다(self):
        response = self.client.get(self._url())

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "contests/detail.html")
        self.assertContains(response, "규칙 요약")
        self.assertContains(response, "시작 자본")
        # ★ 문서가 그대로 넣으라고 못박은 문구 (U-02 4.2)
        self.assertContains(response, "KRX 업종분류 (GICS 아님)")
        self.assertContains(response, "한국투자증권 KIS Developers")

    def test_준비중_대회는_404_다(self):
        draft = make_contest(slug="detail-draft", status=ContestStatus.DRAFT)

        self.assertEqual(self.client.get(self._url(draft)).status_code, 404)

    def test_비공개_대회는_참가자가_아니면_404_다(self):
        from contests.models import Visibility

        private = make_contest(slug="detail-private", visibility=Visibility.PRIVATE)

        self.assertEqual(self.client.get(self._url(private)).status_code, 404)

    def test_비공개_대회도_참가자는_볼_수_있다(self):
        from contests.models import Visibility

        private = make_contest(slug="detail-private-ok", name="비밀 대회",
                               visibility=Visibility.PRIVATE)
        join(private, "insider")

        self.client.login(username="insider", password=PASSWORD)
        response = self.client.get(self._url(private))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "비밀 대회")

    def test_참가_중이면_상태가_보이고_신청_버튼이_사라진다(self):
        join(self.contest, "joined", nickname="별칭이다")

        self.client.login(username="joined", password=PASSWORD)
        response = self.client.get(self._url())

        self.assertContains(response, "참가 중")
        self.assertContains(response, "별칭이다")
        self.assertNotContains(response, "참가 신청</a>")

    def test_실격자에게는_기록이_남는다는_안내가_보인다(self):
        participation = join(self.contest, "dq")
        participation.status = ParticipationStatus.DISQUALIFIED
        participation.disqualified_reason = "회전율 4회 위반"
        participation.save()

        self.client.login(username="dq", password=PASSWORD)
        response = self.client.get(self._url())

        self.assertContains(response, "실격")
        self.assertContains(response, "회전율 4회 위반")
        self.assertContains(response, "계좌와 거래 이력은 그대로 남습니다")


class ContestRankingScreenTests(TestCase):
    """대회 랭킹 `/contests/<slug>/ranking/` (U-02 6장 · F-05 3장)."""

    def setUp(self):
        self.contest = make_contest(slug="rank-1")
        self.first = join(self.contest, "first", nickname="우잔")
        self.me = join(self.contest, "me", nickname="나야")
        rank_row(self.first, rank=1, prev_rank=1, cumulative="34.0200")
        rank_row(self.me, rank=7, prev_rank=8, cumulative="8.2500")

    def _url(self):
        from django.urls import reverse

        return reverse("contests:ranking", args=[self.contest.slug])

    def test_순위표가_그려진다(self):
        response = self.client.get(self._url())

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "contests/ranking.html")
        self.assertContains(response, "우잔")
        self.assertContains(response, "+34.02%")

    def test_안내문_3줄이_그대로_들어간다(self):
        """U-02 6.2 가 화면에 그대로 넣으라고 못박은 문구."""
        response = self.client.get(self._url())

        self.assertContains(response, "※ 전 참가자의 보유 종목과 수익 종목이 공개됩니다.")
        self.assertContains(response, "※ [순위] : 수익률 순위. 최종 운영 능력 평가와는 무관")
        self.assertContains(response, "※ [액티브] : 주별 회전율 5% 이상 유지")

    def test_정산_기준_시각을_반드시_적는다(self):
        """★ 없으면 참가자가 '방금 낸 주문이 순위에 없다' 를 버그로 읽는다."""
        response = self.client.get(self._url())

        self.assertContains(response, "15:40")
        self.assertContains(response, "정산 기준")

    def test_실격자는_숨기지_않고_순위를_대시로_표시한다(self):
        """F-05 3.2 — 숨기면 '내가 왜 없지' 문의가 온다."""
        dropped = join(self.contest, "dropped", nickname="포기자", is_ranked=False,
                       status=ParticipationStatus.WITHDRAWN)
        rank_row(dropped, rank=None, cumulative="-3.0000")

        response = self.client.get(self._url())

        self.assertContains(response, "포기자")
        self.assertContains(response, "순위 제외")

    def test_내_행이_강조되고_내_순위로_버튼이_있다(self):
        self.client.login(username="me", password=PASSWORD)
        response = self.client.get(self._url())
        body = response.content.decode()

        self.assertIn('id="my-rank"', body)
        self.assertIn("내 순위로", body)

    def test_진행_중이면_60초_폴링_속성이_붙는다(self):
        body = self.client.get(self._url()).content.decode()

        self.assertIn("every 60s", body)
        self.assertIn("document.visibilityState === 'visible'", body)

    def test_종료된_대회는_폴링하지_않는다(self):
        """HTMX 규약 5.2 — 더 바뀔 값이 없는데 60초마다 당기지 않는다."""
        self.contest.status = ContestStatus.CLOSED
        self.contest.save()

        body = self.client.get(self._url()).content.decode()

        self.assertNotIn("every 60s", body)

    def test_순위가_아직_없으면_안내가_뜬다(self):
        empty = make_contest(slug="rank-empty")
        from django.urls import reverse

        response = self.client.get(reverse("contests:ranking", args=[empty.slug]))

        self.assertContains(response, "아직 정산된 순위가 없습니다")


class RankingFragmentTests(TestCase):
    """랭킹 프래그먼트 — 폴링 규약 (HTMX 규약 3.3)."""

    def setUp(self):
        self.contest = make_contest(slug="frag-1")
        rank_row(join(self.contest, "one", nickname="하나"), rank=1)

    def _url(self):
        from django.urls import reverse

        return reverse("contests:frag_ranking", args=[self.contest.slug])

    def test_껍데기를_상속하지_않는다(self):
        response = self.client.get(self._url())

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "contests/_ranking_table.html")
        self.assertTemplateNotUsed(response, "base.html")

    def test_폴링_속성을_조각_자신이_들고_있다(self):
        """★ outerHTML 로 교체되므로 여기 없으면 한 번 갱신되고 멈춘다."""
        body = self.client.get(self._url()).content.decode()

        self.assertIn(f'hx-get="/contests/{self.contest.slug}/fragments/ranking/"', body)
        self.assertIn("every 60s", body)
        self.assertIn('hx-swap="outerHTML"', body)


class JoinScreenTests(TestCase):
    """참가 신청 화면 `/contests/<slug>/join/`."""

    def setUp(self):
        self.contest = make_contest(slug="joinui-1", status=ContestStatus.UPCOMING)
        self.member = make_member("applicant")
        self.client.login(username="applicant", password=PASSWORD)

    def _url(self):
        from django.urls import reverse

        return reverse("contests:join", args=[self.contest.slug])

    def test_비로그인은_로그인_화면으로_보낸다(self):
        self.client.logout()

        response = self.client.get(self._url())

        self.assertEqual(response.status_code, 302)
        self.assertIn("/account/login/", response.url)

    def test_신청_화면에_규칙과_공개_안내가_보인다(self):
        response = self.client.get(self._url())

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "contests/join.html")
        self.assertContains(response, "대회 규칙")
        self.assertContains(response, "공개")

    def test_동의하지_않으면_신청되지_않는다(self):
        response = self.client.post(self._url(), {"nickname": "테스터"})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "대회 규칙에 동의해야 참가할 수 있습니다")
        self.assertFalse(Participation.objects.filter(contest=self.contest).exists())

    def test_신청하면_참가와_계좌가_만들어진다(self):
        response = self.client.post(
            self._url(), {"nickname": "테스터", "agree_rules": "on"}
        )

        self.assertEqual(response.status_code, 302)
        participation = Participation.objects.get(contest=self.contest, member=self.member)
        self.assertEqual(participation.nickname, "테스터")
        self.assertEqual(participation.status, ParticipationStatus.APPROVED)
        self.assertIsNotNone(participation.account)

    def test_HTMX_신청_성공은_204_와_HX_Redirect_다(self):
        """★ 302 를 주면 HTMX 가 목적지 HTML 을 폼 자리에 쑤셔 넣는다."""
        response = self.client.post(
            self._url(), {"nickname": "테스터", "agree_rules": "on"},
            headers={"HX-Request": "true"},
        )

        self.assertEqual(response.status_code, 204)
        self.assertIn(f"/contests/{self.contest.slug}/", response.headers["HX-Redirect"])

    def test_별칭이_겹치면_그_칸에_에러가_붙는다(self):
        join(self.contest, "other", nickname="겹치는별칭")

        response = self.client.post(
            self._url(), {"nickname": "겹치는별칭", "agree_rules": "on"}
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "이미 사용 중인 별칭입니다")

    def test_이미_참가했으면_상세로_돌려보낸다(self):
        join(self.contest, "applicant2")
        Participation.objects.filter(contest=self.contest).delete()
        services.join_contest(contest=self.contest, member=self.member)

        response = self.client.get(self._url())

        self.assertEqual(response.status_code, 302)


class ParticipantDetailScreenTests(TestCase):
    """참가자 상세 모달 — 공개 범위를 실제로 지키는가 (A-02 4.2)."""

    def setUp(self):
        self.contest = make_contest(slug="pd-1")
        self.target = join(self.contest, "target", nickname="대상자")
        rank_row(self.target, rank=60)
        self.viewer = make_member("viewer")
        self.client.login(username="viewer", password=PASSWORD)

    def _url(self):
        from django.urls import reverse

        return reverse("contests:participant", args=[self.contest.slug, self.target.pk])

    def test_ALL_이면_열린다(self):
        response = self.client.get(self._url())

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "대상자")
        self.assertContains(response, "보유 종목")

    def test_HTMX_요청은_모달_조각만_준다(self):
        response = self.client.get(self._url(), headers={"HX-Request": "true"})

        self.assertTemplateUsed(response, "contests/_participant_modal.html")
        self.assertTemplateNotUsed(response, "base.html")

    def test_주소로_직접_들어오면_전체_페이지가_나온다(self):
        response = self.client.get(self._url())

        self.assertTemplateUsed(response, "contests/participant.html")
        self.assertTemplateUsed(response, "base.html")

    def test_SELF_ONLY_면_남의_것은_사유와_함께_막힌다(self):
        from contests.models import PortfolioVisibility

        self.contest.portfolio_visibility = PortfolioVisibility.SELF_ONLY
        self.contest.save()

        response = self.client.get(self._url())

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "공개되지 않았습니다")
        self.assertContains(response, "본인 포트폴리오만")
        self.assertNotContains(response, "보유 종목 <span")

    def test_TOP_N_밖이면_막히고_안이면_열린다(self):
        from contests.models import PortfolioVisibility

        self.contest.portfolio_visibility = PortfolioVisibility.TOP_N
        self.contest.portfolio_visible_top_n = 50
        self.contest.save()

        blocked = self.client.get(self._url())
        self.assertContains(blocked, "공개되지 않았습니다")

        self.contest.portfolio_visible_top_n = 100
        self.contest.save()
        opened = self.client.get(self._url())
        self.assertContains(opened, "보유 종목")

    def test_현금과_잔고_금액은_어디에도_나오지_않는다(self):
        """★★ A-02 4.2 — 종목·비중·손익률만 공개한다."""
        self.target.account.cash = 87_654_321
        self.target.account.save()

        body = self.client.get(self._url()).content.decode()

        self.assertNotIn("87,654,321", body)
        self.assertNotIn("87654321", body)

    def test_다른_대회의_참가자는_404_다(self):
        from django.urls import reverse

        other = make_contest(slug="pd-other")
        stranger = join(other, "stranger")

        response = self.client.get(
            reverse("contests:participant", args=[self.contest.slug, stranger.pk])
        )

        self.assertEqual(response.status_code, 404)


class MyContestsScreenTests(TestCase):
    """내 대회 `/account/contests/` (U-04 8장)."""

    def setUp(self):
        from django.urls import reverse

        self.url = reverse("account:contests")

    def test_비로그인은_로그인_화면으로_보낸다(self):
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 302)
        self.assertIn("/account/login/", response.url)

    def test_참가_이력이_없으면_대회를_둘러보라고_안내한다(self):
        make_member("empty")
        self.client.login(username="empty", password=PASSWORD)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "아직 참가한 대회가 없습니다")

    def test_진행_중_대회의_순위와_수익률이_보인다(self):
        contest = make_contest(slug="my-1", name="내 첫 대회")
        participation = join(contest, "me", nickname="나")
        rank_row(participation, rank=12, cumulative="-2.1000")

        self.client.login(username="me", password=PASSWORD)
        response = self.client.get(self.url)

        self.assertContains(response, "내 첫 대회")
        self.assertContains(response, "12위")
        self.assertContains(response, "-2.10%")

    def test_종료된_대회는_최종_순위와_등급을_보여준다(self):
        contest = make_contest(slug="my-2", name="끝난 대회", status=ContestStatus.CLOSED)
        participation = join(contest, "me2", nickname="나2")
        ContestResult.objects.create(
            contest=contest, participation=participation,
            final_rank=3, return_score=Decimal("12.5000"), grade="A",
        )

        self.client.login(username="me2", password=PASSWORD)
        response = self.client.get(self.url)

        self.assertContains(response, "끝난 대회")
        self.assertContains(response, "3위")
        self.assertContains(response, "A")

    def test_등급이_비어_있는_이유를_안내한다(self):
        contest = make_contest(slug="my-3")
        join(contest, "me3")

        self.client.login(username="me3", password=PASSWORD)
        response = self.client.get(self.url)

        self.assertContains(response, "참가자 수가 적으면 상위 등급이 나오지 않을 수 있습니다")


class ContestNavigationTests(TestCase):
    """화면을 붙이면 GNB 의 '준비 중' 이 자동으로 풀린다 (web/navigation.py)."""

    def test_대회_메뉴가_더_이상_준비_중이_아니다(self):
        from web.navigation import build_menu

        groups = {group["label"]: group for group in build_menu(is_authenticated=True)}
        items = {item["label"]: item for item in groups["대회"]["items"]}

        self.assertTrue(items["대회 목록"]["ready"])
        self.assertEqual(items["대회 목록"]["url"], "/contests/")
        self.assertTrue(items["내 대회"]["ready"])
        self.assertEqual(items["내 대회"]["url"], "/account/contests/")


class ContestScreenNoExternalCallTests(TestCase):
    """대회 화면은 외부 API 를 절대 부르지 않는다 (F-05 7장)."""

    def test_목록_상세_랭킹이_외부를_부르지_않는다(self):
        from unittest.mock import patch
        from django.urls import reverse

        contest = make_contest(slug="noext-1")
        rank_row(join(contest, "one", nickname="하나"), rank=1)

        urls = [
            reverse("contests:list"),
            reverse("contests:detail", args=[contest.slug]),
            reverse("contests:ranking", args=[contest.slug]),
            reverse("contests:frag_ranking", args=[contest.slug]),
        ]
        with patch("requests.get", side_effect=AssertionError("외부 호출 금지")), \
             patch("requests.post", side_effect=AssertionError("외부 호출 금지")):
            for url in urls:
                with self.subTest(url=url):
                    self.assertEqual(self.client.get(url).status_code, 200)
