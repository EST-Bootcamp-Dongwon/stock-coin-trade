"""KIS 호가·시세 파이프라인 테스트 (F-16 · F-20 잡 1·2).

    python manage.py test market

★★ **무엇을 목으로 하고 무엇을 진짜로 도는가** ───────────────────────────────

    목(mock)   HTTP 호출만            — KIS·네이버·업비트에 실제로 붙지 않는다
    진짜       나머지 전부            — 캐시 갱신 · 폴백 판정 · 예산 · 토큰 수명

이 구분이 중요하다. 외부 호출까지 진짜로 하면 테스트가 **장 시간과 남의 서버 상태에
묶여** 아무것도 보장하지 못한다. 반대로 캐시 갱신까지 목으로 하면 정작 우리가 쓴
로직은 한 줄도 검증되지 않는다.

★ **`requests` 를 모듈 안에서 지연 import 하는 구조가 여기서 값을 한다.**
  각 모듈의 `_requests()` 를 갈아끼우면 HTTP 만 정확히 끊어낼 수 있다.
"""

import json
from datetime import timedelta
from decimal import Decimal
from unittest import mock

from django.test import TestCase
from django.utils import timezone

from core.constants import AssetClass, BrokerProvider, QuoteSource, SyncStatus
from core.jobs import ExternalDataError
from core.models import DataSyncLog
from market import jobs as market_jobs
from market import kis, naver
from market.models import (
    ExternalToken,
    Market,
    OrderbookCache,
    QuoteCache,
    StockMaster,
    StockType,
    SubscriptionRegistry,
)
from market.quotes import mark_priority
from market.services import charge_api_budget, spend_api_budget

SYMBOL = "005930"


# ─────────────────────────────────────────────────────────────────
# 픽스처 도우미
# ─────────────────────────────────────────────────────────────────


class FakeResponse:
    """`requests` 응답 흉내. 필요한 것은 세 가지뿐이다."""

    def __init__(self, payload, *, status_code: int = 200, raw: str | None = None):
        self.status_code = status_code
        self._payload = payload
        self._raw = raw

    def json(self):
        if self._raw is not None:
            return json.loads(self._raw)      # 일부러 터뜨리고 싶을 때
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise ValueError(f"HTTP {self.status_code}")


class FakeRequests:
    """`get` · `post` 를 각각 대본대로 돌려주고 호출 이력을 남긴다."""

    def __init__(self, *, get=None, post=None):
        self._get = get
        self._post = post
        self.get_calls: list[tuple[str, dict]] = []
        self.post_calls: list[str] = []

    def get(self, url, **kwargs):
        self.get_calls.append((url, kwargs))
        result = self._get(url, **kwargs) if callable(self._get) else self._get
        if isinstance(result, Exception):
            raise result
        return result

    def post(self, url, **kwargs):
        self.post_calls.append(url)
        result = self._post(url, **kwargs) if callable(self._post) else self._post
        if isinstance(result, Exception):
            raise result
        return result


def kis_env(**overrides):
    """`.env` 를 대신한다. 실제 키가 없어도 코드 경로 전체를 돌 수 있다."""
    env = {
        "KIS_MODE": "mock",
        "KIS_APP_KEY": "TEST-APP-KEY-0123456789",
        "KIS_APP_SECRET": "TEST-APP-SECRET-0123456789",
        # ★ 테스트에서는 유량을 넉넉히 준다. 좁게 잡으면 `spend_api_budget` 이
        #   1초 창이 열릴 때까지 **실제로 잠들어** 테스트가 몇 초씩 늘어진다.
        "KIS_RATE_LIMIT": "1000",
    }
    env.update(overrides)
    return mock.patch.dict("os.environ", env, clear=False)


def token_payload(*, expires_hours: int = 24) -> dict:
    expired_at = (timezone.localtime() + timedelta(hours=expires_hours)).strftime("%Y-%m-%d %H:%M:%S")
    return {
        "access_token": "test-access-token",
        "token_type": "Bearer",
        "expires_in": expires_hours * 3600,
        "access_token_token_expired": expired_at,
    }


def orderbook_payload(*, base_ask: int = 74_300, base_bid: int = 74_200) -> dict:
    """KIS 호가 응답. **`askp1`~`askp10` 처럼 컬럼으로 펼쳐진** 진짜 형식."""
    output1 = {"total_askp_rsqn": "1000", "total_bidp_rsqn": "1200"}
    for step in range(1, 11):
        output1[f"askp{step}"] = str(base_ask + (step - 1) * 100)
        output1[f"askp_rsqn{step}"] = str(10 * step)
        output1[f"bidp{step}"] = str(base_bid - (step - 1) * 100)
        output1[f"bidp_rsqn{step}"] = str(20 * step)
    return {"rt_cd": "0", "msg_cd": "MCA00000", "msg1": "정상처리", "output1": output1, "output2": {}}


def quote_payload(*, price: int = 74_300, prev: int = 73_000, sign: str = "2") -> dict:
    return {
        "rt_cd": "0",
        "output": {
            "stck_prpr": str(price),
            "stck_sdpr": str(prev),
            "prdy_vrss": str(abs(price - prev)),
            "prdy_vrss_sign": sign,
            "prdy_ctrt": "1.78",
            "acml_vol": "12345678",
            "stck_oprc": str(prev + 100),
            "stck_hgpr": str(price + 200),
            "stck_lwpr": str(prev - 100),
        },
    }


def subscribe(symbol=SYMBOL, *, needs_orderbook=True, priority=1, asset_class=AssetClass.STOCK):
    return SubscriptionRegistry.objects.create(
        asset_class=asset_class,
        symbol=symbol,
        priority=priority,
        reason="OPEN_ORDER",
        needs_orderbook=needs_orderbook,
    )


def make_master(symbol=SYMBOL, *, is_featured=False, close_price=73_000):
    return StockMaster.objects.create(
        symbol=symbol,
        name=f"테스트{symbol}",
        market=Market.KOSPI,
        stock_type=StockType.COMMON,
        close_price=Decimal(close_price),
        is_featured=is_featured,
    )


# ─────────────────────────────────────────────────────────────────
# 1. 설정 · 자격증명
# ─────────────────────────────────────────────────────────────────


class KisConfigTests(TestCase):
    def test_자격증명이_없으면_무엇을_해야_하는지_알려준다(self):
        """규약 8.5 — 환경 가드는 막다른 길로 만들지 않는다."""
        with mock.patch.dict("os.environ", {"KIS_APP_KEY": "", "KIS_APP_SECRET": ""}, clear=False):
            self.assertFalse(kis.is_configured())
            with self.assertRaises(kis.KisNotConfigured) as ctx:
                kis.load_config()

        # 발급 절차·확인 커맨드가 힌트에 들어 있어야 한다.
        self.assertIn("apiportal.koreainvestment.com", ctx.exception.hint)
        self.assertIn("kis_probe", ctx.exception.hint)

    def test_모드에_따라_도메인과_예산_카운터가_갈린다(self):
        with kis_env(KIS_MODE="mock"):
            config = kis.load_config()
        self.assertEqual(config.environment, ExternalToken.Environment.MOCK)
        self.assertIn("openapivts", config.host)
        self.assertEqual(config.budget_name, "KIS")

        with kis_env(KIS_MODE="real"):
            config = kis.load_config()
        self.assertEqual(config.environment, ExternalToken.Environment.REAL)
        self.assertIn("openapi.koreainvestment.com:9443", config.host)
        # ★ 모의와 **다른 카운터**를 써야 한다 (한도가 다르다)
        self.assertEqual(config.budget_name, "KIS_REAL")

    def test_유량_한도는_env_로_조절된다(self):
        """자료가 엇갈려 실측 전까지 조절 가능해야 한다 (변경노트 E-43)."""
        with kis_env():
            self.assertEqual(kis.load_config().rate_limit, 1000)
        with kis_env(KIS_RATE_LIMIT="이상한값"):
            # 숫자가 아니면 기본값으로 떨어진다 — 터지지 않는다.
            self.assertEqual(kis.load_config().rate_limit, kis.DEFAULT_RATE_LIMIT["MOCK"])


# ─────────────────────────────────────────────────────────────────
# 2. 접근토큰
# ─────────────────────────────────────────────────────────────────


class KisTokenTests(TestCase):
    def test_토큰을_발급해_DB_에_저장한다(self):
        fake = FakeRequests(post=FakeResponse(token_payload()))
        with kis_env(), mock.patch.object(kis, "_requests", return_value=fake):
            token = kis.get_access_token()

        self.assertEqual(token, "test-access-token")
        row = ExternalToken.objects.get(provider=BrokerProvider.KIS)
        self.assertEqual(row.environment, ExternalToken.Environment.MOCK)
        self.assertTrue(row.is_valid)

    def test_유효한_토큰이_있으면_다시_발급하지_않는다(self):
        """서버리스 콜드스타트마다 재발급하면 1분 제한(EGW00133)에 걸린다."""
        fake = FakeRequests(post=FakeResponse(token_payload()))
        with kis_env(), mock.patch.object(kis, "_requests", return_value=fake):
            kis.get_access_token()
            kis.get_access_token()
            kis.get_access_token()

        self.assertEqual(len(fake.post_calls), 1, "토큰은 한 번만 발급돼야 한다")

    def test_만료가_임박하면_새로_받는다(self):
        fake = FakeRequests(post=FakeResponse(token_payload()))
        with kis_env(), mock.patch.object(kis, "_requests", return_value=fake):
            kis.get_access_token()
            # 만료를 10분 뒤로 당긴다 — 갱신 여유(30분)보다 짧다.
            ExternalToken.objects.update(expires_at=timezone.now() + timedelta(minutes=10))
            kis.get_access_token()

        self.assertEqual(len(fake.post_calls), 2)

    def test_만료시각을_KST_로_읽는다(self):
        """★ `access_token_token_expired` 는 naive 문자열이다.

        UTC 로 읽으면 만료가 9시간 뒤로 보여 **죽은 토큰을 계속 쓴다.**
        """
        fake = FakeRequests(post=FakeResponse(token_payload(expires_hours=24)))
        with kis_env(), mock.patch.object(kis, "_requests", return_value=fake):
            kis.get_access_token()

        row = ExternalToken.objects.get(provider=BrokerProvider.KIS)
        gap = row.expires_at - timezone.now()
        # 24시간 ± 1분. 9시간 밀리면 33시간이 되어 여기서 걸린다.
        self.assertAlmostEqual(gap.total_seconds(), 24 * 3600, delta=60)

    def test_발급_실패는_예외에_자격증명을_담지_않는다(self):
        """모듈 docstring — 예외 메시지는 Admin 까지 흘러간다."""
        fake = FakeRequests(post=FakeResponse(
            {"error_code": "EGW00121", "error_description": "appkey 오류", "appkey": "TEST-APP-KEY-0123456789"},
            status_code=403,
        ))
        with kis_env(), mock.patch.object(kis, "_requests", return_value=fake):
            with self.assertRaises(ExternalDataError) as ctx:
                kis.get_access_token()

        message = f"{ctx.exception} {ctx.exception.hint}"
        self.assertNotIn("TEST-APP-KEY-0123456789", message)
        self.assertNotIn("TEST-APP-SECRET", message)
        self.assertIn("EGW00121", message)

    def test_발급_실패_직후에는_연타하지_않는다(self):
        """KIS 는 토큰 발급을 1분에 1회로 제한한다 (EGW00133)."""
        fake = FakeRequests(post=FakeResponse({"error_code": "EGW00133"}, status_code=403))
        with kis_env(), mock.patch.object(kis, "_requests", return_value=fake):
            with self.assertRaises(ExternalDataError):
                kis.get_access_token()
            # 두 번째 호출은 **HTTP 를 부르지 않고** 쿨다운으로 거절돼야 한다.
            with self.assertRaises(ExternalDataError) as ctx:
                kis.get_access_token()

        self.assertEqual(len(fake.post_calls), 1, "쿨다운 중에는 다시 두드리지 않는다")
        self.assertIn("초 뒤에 다시 시도", str(ctx.exception))

    def test_발급_실패가_살아있는_토큰을_지우지_않는다(self):
        """★ 두 프로세스가 동시에 첫 발급을 시도하면 한쪽은 EGW00133 을 맞는다.

        그때 실패한 쪽이 무조건 덮어쓰면 **방금 받은 멀쩡한 토큰이 지워진다.**
        """
        ExternalToken.objects.create(
            provider=BrokerProvider.KIS,
            environment=ExternalToken.Environment.MOCK,
            access_token="살아있는-토큰",
            issued_at=timezone.now(),
            expires_at=timezone.now() + timedelta(hours=20),
        )
        fake = FakeRequests(post=FakeResponse({"error_code": "EGW00133"}, status_code=403))
        with kis_env(), mock.patch.object(kis, "_requests", return_value=fake):
            with self.assertRaises(ExternalDataError):
                # --refresh 로 강제 재발급을 시도했는데 실패한 상황
                kis.get_access_token(force_refresh=True)

        row = ExternalToken.objects.get(provider=BrokerProvider.KIS)
        self.assertEqual(row.access_token, "살아있는-토큰")
        self.assertTrue(row.is_valid)

    def test_실패_기록이_롤백되지_않는다(self):
        """★★ 변경노트 E-46 — `atomic()` 안에서 기록하면 예외와 함께 사라진다."""
        fake = FakeRequests(post=FakeResponse({"error_code": "EGW00121"}, status_code=403))
        with kis_env(), mock.patch.object(kis, "_requests", return_value=fake):
            with self.assertRaises(ExternalDataError):
                kis.get_access_token()

        row = ExternalToken.objects.get(provider=BrokerProvider.KIS)
        self.assertEqual(row.access_token, "", "실패 표시가 DB 에 남아야 쿨다운이 켜진다")


# ─────────────────────────────────────────────────────────────────
# 3. 호가 · 현재가 파싱
# ─────────────────────────────────────────────────────────────────


class KisFetchTests(TestCase):
    def setUp(self):
        ExternalToken.objects.create(
            provider=BrokerProvider.KIS,
            environment=ExternalToken.Environment.MOCK,
            access_token="cached-token",
            issued_at=timezone.now(),
            expires_at=timezone.now() + timedelta(hours=20),
        )

    def test_호가_10단계를_캐시_형식으로_옮긴다(self):
        fake = FakeRequests(get=FakeResponse(orderbook_payload()))
        with kis_env(), mock.patch.object(kis, "_requests", return_value=fake):
            book = kis.fetch_orderbook(SYMBOL)

        self.assertEqual(len(book.levels), 10)
        self.assertEqual(book.levels[0]["ask_price"], 74_300)
        self.assertEqual(book.levels[0]["ask_qty"], 10)
        self.assertEqual(book.levels[0]["bid_price"], 74_200)
        self.assertEqual(book.levels[9]["ask_price"], 75_200)
        self.assertEqual(book.total_ask_qty, Decimal("1000"))

        # ★ `market/quotes.py` 의 `Level` 이 읽는 키와 같아야 한다 — 형식이 어긋나면
        #   체결이 전 단계를 잔량 0 으로 보고 조용히 아무것도 채우지 않는다.
        from market.quotes import _to_level

        level = _to_level(book.levels[0])
        self.assertEqual(level.ask_price, Decimal("74300"))
        self.assertEqual(level.bid_qty, Decimal("20"))

    def test_rt_cd_가_0이_아니면_실패다(self):
        """★★ KIS 는 **HTTP 200 으로 실패를 알린다.**"""
        fake = FakeRequests(get=FakeResponse({
            "rt_cd": "1", "msg_cd": "EGW00201", "msg1": "초당 거래건수를 초과하였습니다.",
        }))
        with kis_env(), mock.patch.object(kis, "_requests", return_value=fake), \
                mock.patch("core.jobs.time.sleep"):
            with self.assertRaises(ExternalDataError) as ctx:
                kis.fetch_orderbook(SYMBOL)

        self.assertIn("EGW00201", str(ctx.exception))
        # 유량 초과는 재시도 대상이다 (기본 3회).
        self.assertEqual(len(fake.get_calls), 3)

    def test_인증_오류는_재시도하지_않는다(self):
        """잘못된 키로 세 번 두드려 봐야 결과가 같고, 그 사이 유량만 태운다."""
        fake = FakeRequests(get=FakeResponse({"rt_cd": "1", "msg_cd": "EGW00123", "msg1": "토큰 만료"}))
        with kis_env(), mock.patch.object(kis, "_requests", return_value=fake):
            with self.assertRaises(ExternalDataError):
                kis.fetch_orderbook(SYMBOL)

        self.assertEqual(len(fake.get_calls), 1)

    def test_전_단계가_0이면_호가로_보지_않는다(self):
        """장 시작 전·거래정지 종목. 그대로 캐시에 넣으면 잔량 0 짜리 호가가 된다."""
        payload = orderbook_payload()
        for step in range(1, 11):
            payload["output1"][f"askp{step}"] = "0"
            payload["output1"][f"bidp{step}"] = "0"
        fake = FakeRequests(get=FakeResponse(payload))
        with kis_env(), mock.patch.object(kis, "_requests", return_value=fake):
            with self.assertRaises(ExternalDataError) as ctx:
                kis.fetch_orderbook(SYMBOL)
        self.assertIn("전 단계 0", str(ctx.exception))
        self.assertIn("장중", ctx.exception.hint)

    def test_현재가는_기준가를_전일종가로_쓴다(self):
        fake = FakeRequests(get=FakeResponse(quote_payload(price=74_300, prev=73_000)))
        with kis_env(), mock.patch.object(kis, "_requests", return_value=fake):
            quote = kis.fetch_quote(SYMBOL)

        self.assertEqual(quote.price, Decimal("74300"))
        self.assertEqual(quote.prev_close, Decimal("73000"))
        self.assertEqual(quote.change, Decimal("1300"))

    def test_하락일_때_부호를_바로잡는다(self):
        """`prdy_vrss` 가 절댓값으로 오는 경우가 있다. 부호는 `prdy_vrss_sign` 에 있다."""
        fake = FakeRequests(get=FakeResponse(quote_payload(price=72_000, prev=73_000, sign="5")))
        with kis_env(), mock.patch.object(kis, "_requests", return_value=fake):
            quote = kis.fetch_quote(SYMBOL)

        self.assertEqual(quote.change, Decimal("-1000"))
        self.assertEqual(quote.prev_close, Decimal("73000"))

    def test_유량_예산을_차감한다(self):
        fake = FakeRequests(get=FakeResponse(quote_payload()))
        with kis_env(), mock.patch.object(kis, "_requests", return_value=fake):
            kis.fetch_quote(SYMBOL)

        from market.models import ApiCallBudget

        self.assertEqual(
            ApiCallBudget.objects.filter(api_name="KIS").count(), 1,
            "호출 1건이 예산에 기록돼야 한다",
        )


# ─────────────────────────────────────────────────────────────────
# 4. 유량 예산 — limit 인자
# ─────────────────────────────────────────────────────────────────


class BudgetLimitTests(TestCase):
    def test_limit_인자가_모듈_상수를_이긴다(self):
        """KIS 한도는 실행 환경에 달려 있어 인자로 받아야 한다 (변경노트 E-43)."""
        for _ in range(3):
            charge_api_budget("KIS")
        # 모듈 기본값(2)이면 이미 넘었다. 넉넉한 limit 을 주면 기다리지 않고 통과한다.
        with mock.patch("market.services.time.sleep") as slept:
            spend_api_budget("KIS", limit=50)
        slept.assert_not_called()

    def test_한도를_넘으면_기다리다_포기한다(self):
        charge_api_budget("KIS")            # 이미 1건 썼다 → 한도 1 이면 다음은 막힌다
        with mock.patch("market.services.time.sleep"):
            with self.assertRaises(ExternalDataError) as ctx:
                spend_api_budget("KIS", limit=1, max_wait=0)
        self.assertIn("초당 1건", str(ctx.exception))


# ─────────────────────────────────────────────────────────────────
# 5. 잡 2 — poll_orderbook
# ─────────────────────────────────────────────────────────────────


class PollOrderbookTests(TestCase):
    def setUp(self):
        ExternalToken.objects.create(
            provider=BrokerProvider.KIS,
            environment=ExternalToken.Environment.MOCK,
            access_token="cached-token",
            issued_at=timezone.now(),
            expires_at=timezone.now() + timedelta(hours=20),
        )

    def test_대회_종목만_조회한다(self):
        """★ 연습 종목에 KIS 유량을 쓰지 않는다 (F-16 2.5 · 변경노트 E-44)."""
        subscribe("005930", needs_orderbook=True)
        subscribe("000660", needs_orderbook=False)          # 연습
        subscribe("KRW-BTC", needs_orderbook=True, asset_class=AssetClass.CRYPTO)   # 코인

        fake = FakeRequests(get=FakeResponse(orderbook_payload()))
        with kis_env(), mock.patch.object(kis, "_requests", return_value=fake):
            result = market_jobs.poll_orderbook()

        self.assertEqual(len(fake.get_calls), 1, "대회 주식 1종만 조회해야 한다")
        self.assertEqual(result.created, 1)
        self.assertEqual(OrderbookCache.objects.count(), 1)
        self.assertEqual(OrderbookCache.objects.get().symbol, "005930")

    def test_캐시를_TTL_과_함께_쓴다(self):
        subscribe()
        fake = FakeRequests(get=FakeResponse(orderbook_payload()))
        with kis_env(), mock.patch.object(kis, "_requests", return_value=fake):
            market_jobs.poll_orderbook()

        row = OrderbookCache.objects.get()
        self.assertEqual(row.source, QuoteSource.KIS)
        self.assertTrue(row.is_fresh)
        self.assertAlmostEqual(
            (row.expires_at - row.fetched_at).total_seconds(),
            market_jobs.ORDERBOOK_TTL.total_seconds(), delta=0.1,
        )
        # 폴링 시각이 기록돼야 다음 회차의 정렬이 의미를 갖는다.
        self.assertIsNotNone(SubscriptionRegistry.objects.get().last_polled_at)

    def test_두번째_회차는_created_가_0이다(self):
        """멱등성 — 두 번째부터 created 가 0 이 아니면 upsert 키가 잘못됐다 (F-20 6장)."""
        subscribe()
        fake = FakeRequests(get=FakeResponse(orderbook_payload()))
        with kis_env(), mock.patch.object(kis, "_requests", return_value=fake):
            market_jobs.poll_orderbook()
            second = market_jobs.poll_orderbook()

        self.assertEqual(second.created, 0)
        self.assertEqual(second.updated, 1)
        self.assertEqual(OrderbookCache.objects.count(), 1)

    def test_한_종목이_실패해도_나머지는_갱신한다(self):
        subscribe("005930")
        subscribe("000660")

        def _get(url, **kwargs):
            if kwargs["params"]["FID_INPUT_ISCD"] == "005930":
                return FakeResponse({"rt_cd": "1", "msg_cd": "EGW00123", "msg1": "토큰 만료"})
            return FakeResponse(orderbook_payload())

        fake = FakeRequests(get=_get)
        with kis_env(), mock.patch.object(kis, "_requests", return_value=fake):
            result = market_jobs.poll_orderbook()

        self.assertEqual(result.skipped, 1)
        self.assertEqual(result.created, 1)
        self.assertEqual(OrderbookCache.objects.get().symbol, "000660")

    def test_실패한_종목의_낡은_캐시는_지우지_않는다(self):
        """F-16 3.4 — 5분 안이면 낡은 호가로도 체결한다. 지우면 그 여지가 사라진다."""
        subscribe()
        old = timezone.now() - timedelta(minutes=1)
        OrderbookCache.objects.create(
            symbol=SYMBOL, levels=[{"ask_price": 100, "ask_qty": 1, "bid_price": 99, "bid_qty": 1}],
            fetched_at=old, expires_at=old, source=QuoteSource.KIS,
        )
        fake = FakeRequests(get=FakeResponse({"rt_cd": "1", "msg_cd": "EGW00123", "msg1": "만료"}))
        with kis_env(), mock.patch.object(kis, "_requests", return_value=fake):
            market_jobs.poll_orderbook()

        row = OrderbookCache.objects.get()
        self.assertEqual(row.fetched_at, old, "실패했다고 캐시를 건드리면 안 된다")

    def test_KIS_미설정이면_실패로_기록하지_않는다(self):
        """★ 5초 잡이 매번 FAILED 를 남기면 자동 비활성화가 걸리고 배너가 도배된다."""
        subscribe()
        with mock.patch.dict("os.environ", {"KIS_APP_KEY": "", "KIS_APP_SECRET": ""}, clear=False):
            result = market_jobs.poll_orderbook()

        self.assertEqual(result.rows, 0)
        self.assertTrue(any("KIS 자격증명이 없어" in note for note in result.notes))
        self.assertFalse(
            DataSyncLog.objects.filter(job_name="poll_orderbook", status=SyncStatus.FAILED).exists()
        )

    def test_상한을_넘으면_잘라내고_남긴다(self):
        """조용히 자르면 '다 갱신했다'로 읽힌다."""
        for index in range(market_jobs.MAX_ORDERBOOK_PER_RUN + 3):
            subscribe(f"{index:06d}")
        fake = FakeRequests(get=FakeResponse(orderbook_payload()))
        with kis_env(), mock.patch.object(kis, "_requests", return_value=fake):
            result = market_jobs.poll_orderbook()

        self.assertEqual(len(fake.get_calls), market_jobs.MAX_ORDERBOOK_PER_RUN)
        self.assertTrue(any("잘라 처리" in note for note in result.notes))

    def test_한번도_폴링되지_않은_종목이_먼저다(self):
        """★ Postgres 는 오름차순에서 NULL 을 마지막에 둔다 — 그대로 두면 새 종목이 밀린다."""
        old = subscribe("000660")
        old.last_polled_at = timezone.now() - timedelta(hours=1)
        old.save(update_fields=["last_polled_at"])
        subscribe("005930")     # last_polled_at 이 NULL

        fake = FakeRequests(get=FakeResponse(orderbook_payload()))
        with kis_env(), \
                mock.patch.object(market_jobs, "MAX_ORDERBOOK_PER_RUN", 1), \
                mock.patch.object(kis, "_requests", return_value=fake):
            market_jobs.poll_orderbook()

        self.assertEqual(OrderbookCache.objects.get().symbol, "005930")


# ─────────────────────────────────────────────────────────────────
# 6. 잡 1 — poll_quotes · 3단 폴백
# ─────────────────────────────────────────────────────────────────


class PollQuotesTests(TestCase):
    def setUp(self):
        ExternalToken.objects.create(
            provider=BrokerProvider.KIS,
            environment=ExternalToken.Environment.MOCK,
            access_token="cached-token",
            issued_at=timezone.now(),
            expires_at=timezone.now() + timedelta(hours=20),
        )

    def test_대회는_KIS_연습은_네이버로_간다(self):
        """F-16 4.2 — 비싼 자원(KIS)을 꼭 필요한 곳에만 쓴다."""
        subscribe("005930", needs_orderbook=True)       # 대회
        subscribe("000660", needs_orderbook=False)      # 연습

        kis_fake = FakeRequests(get=FakeResponse(quote_payload(price=74_300)))
        naver_fake = FakeRequests(get=FakeResponse({
            "result": {"areas": [{"datas": [
                {"cd": "000660", "nv": "180000", "pcv": "178000", "cv": "2000", "cr": "1.12", "aq": "500"},
            ]}]},
        }))
        with kis_env(), \
                mock.patch.object(kis, "_requests", return_value=kis_fake), \
                mock.patch.object(naver, "_requests", return_value=naver_fake):
            result = market_jobs.poll_quotes()

        self.assertEqual(len(kis_fake.get_calls), 1, "대회 1종만 KIS 로")
        self.assertEqual(result.created, 2)
        self.assertEqual(QuoteCache.objects.get(symbol="005930").source, QuoteSource.KIS)
        self.assertEqual(QuoteCache.objects.get(symbol="000660").source, QuoteSource.NAVER)
        self.assertFalse(QuoteCache.objects.filter(is_simulated=True).exists())

    def test_KIS_가_실패하면_네이버로_내려간다(self):
        """3단 폴백 2순위 (F-16 4.2)."""
        subscribe("005930", needs_orderbook=True)

        kis_fake = FakeRequests(get=FakeResponse({"rt_cd": "1", "msg_cd": "EGW00123", "msg1": "만료"}))
        naver_fake = FakeRequests(get=FakeResponse({
            "result": {"areas": [{"datas": [
                {"cd": "005930", "nv": "74000", "pcv": "73000", "cv": "1000", "cr": "1.37", "aq": "9999"},
            ]}]},
        }))
        with kis_env(), \
                mock.patch.object(kis, "_requests", return_value=kis_fake), \
                mock.patch.object(naver, "_requests", return_value=naver_fake):
            market_jobs.poll_quotes()

        row = QuoteCache.objects.get(symbol="005930")
        self.assertEqual(row.source, QuoteSource.NAVER)
        self.assertEqual(row.price, Decimal("74000"))
        self.assertFalse(row.is_simulated)

    def test_대회_종목은_시뮬레이션으로_채우지_않는다(self):
        """★★ F-16 4.1 — 가짜 가격으로 대회를 체결하지 않는다.

        `is_featured`(수업용 14종)여도 **대회 종목이면 만들지 않는다.**
        캐시 한 행을 대회와 연습이 공유하기 때문이다.
        """
        make_master("005930", is_featured=True)
        subscribe("005930", needs_orderbook=True)

        dead = FakeRequests(get=ValueError("네트워크 끊김"))
        with kis_env(), \
                mock.patch.object(kis, "_requests", return_value=dead), \
                mock.patch.object(naver, "_requests", return_value=dead), \
                mock.patch("core.jobs.time.sleep"):
            result = market_jobs.poll_quotes()

        self.assertEqual(QuoteCache.objects.count(), 0)
        self.assertEqual(result.skipped, 1)
        self.assertTrue(any("시뮬레이션 가격으로 대회를 체결하지 않습니다" in n for n in result.notes))

    def test_연습_수업용_14종만_시뮬레이션을_만든다(self):
        """F-16 4.1 — v1.0 의 안전장치를 승계한다."""
        make_master("005930", is_featured=True, close_price=73_000)
        make_master("000660", is_featured=False)
        subscribe("005930", needs_orderbook=False)
        subscribe("000660", needs_orderbook=False)

        dead = FakeRequests(get=ValueError("네트워크 끊김"))
        with kis_env(), \
                mock.patch.object(naver, "_requests", return_value=dead), \
                mock.patch("core.jobs.time.sleep"):
            market_jobs.poll_quotes()

        self.assertEqual(QuoteCache.objects.count(), 1)
        row = QuoteCache.objects.get()
        self.assertEqual(row.symbol, "005930")
        self.assertTrue(row.is_simulated)
        self.assertEqual(row.source, QuoteSource.SIM)
        # 기준가는 `StockMaster.close_price` 다 — v1.0 의 하드코딩을 없앴다.
        self.assertEqual(row.prev_close, Decimal("73000"))
        self.assertGreater(row.price, 0)

    def test_시뮬레이션_가격은_대회_체결에서_거부된다(self):
        """캐시를 채우는 쪽과 읽는 쪽이 실제로 맞물리는지 확인한다."""
        make_master("005930", is_featured=True)
        subscribe("005930", needs_orderbook=False)

        dead = FakeRequests(get=ValueError("끊김"))
        with kis_env(), \
                mock.patch.object(naver, "_requests", return_value=dead), \
                mock.patch("core.jobs.time.sleep"):
            market_jobs.poll_quotes()

        from market.quotes import PriceUnavailable, get_quote

        # 연습은 쓴다.
        quote = get_quote(AssetClass.STOCK, "005930", for_contest=False)
        self.assertTrue(quote.is_simulated)
        # 대회는 거부한다.
        with self.assertRaises(PriceUnavailable) as ctx:
            get_quote(AssetClass.STOCK, "005930", for_contest=True)
        self.assertTrue(ctx.exception.recoverable)

    def test_코인은_업비트_다종목으로_받는다(self):
        subscribe("KRW-BTC", needs_orderbook=False, asset_class=AssetClass.CRYPTO)
        subscribe("KRW-ETH", needs_orderbook=False, asset_class=AssetClass.CRYPTO)

        tickers = [
            {"market": "KRW-BTC", "trade_price": 95_000_000, "prev_closing_price": 94_000_000,
             "signed_change_price": 1_000_000, "signed_change_rate": 0.0106,
             "acc_trade_volume_24h": 1234, "opening_price": 94_100_000,
             "high_price": 95_500_000, "low_price": 93_900_000},
            {"market": "KRW-ETH", "trade_price": 5_000_000, "prev_closing_price": 4_900_000,
             "signed_change_price": 100_000, "signed_change_rate": 0.0204,
             "acc_trade_volume_24h": 999, "opening_price": 4_950_000,
             "high_price": 5_100_000, "low_price": 4_880_000},
        ]
        with mock.patch.object(market_jobs, "_fetch_upbit_tickers", return_value=tickers):
            market_jobs.poll_quotes()

        self.assertEqual(QuoteCache.objects.filter(asset_class=AssetClass.CRYPTO).count(), 2)
        btc = QuoteCache.objects.get(symbol="KRW-BTC")
        # ★ 업비트는 등락률을 **비율**로 준다. 우리 규약은 퍼센트다 (규약 4.2).
        self.assertAlmostEqual(float(btc.change_pct), 1.06, places=2)

    def test_대체자산은_조용히_빠지지_않는다(self):
        subscribe("GOLD", needs_orderbook=False, asset_class=AssetClass.ALT)
        result = market_jobs.poll_quotes()
        self.assertTrue(any("대체자산" in note for note in result.notes))

    def test_두번째_회차는_created_가_0이다(self):
        subscribe("000660", needs_orderbook=False)
        naver_fake = FakeRequests(get=FakeResponse({
            "result": {"areas": [{"datas": [
                {"cd": "000660", "nv": "180000", "pcv": "178000", "cv": "2000", "cr": "1.12", "aq": "500"},
            ]}]},
        }))
        with kis_env(), mock.patch.object(naver, "_requests", return_value=naver_fake):
            market_jobs.poll_quotes()
            second = market_jobs.poll_quotes()

        self.assertEqual(second.created, 0)
        self.assertEqual(second.updated, 1)
        self.assertEqual(QuoteCache.objects.count(), 1)


# ─────────────────────────────────────────────────────────────────
# 7. 네이버 파싱
# ─────────────────────────────────────────────────────────────────


class NaverTests(TestCase):
    def test_다종목을_한_번에_받는다(self):
        """★ 이 다종목 API 가 연습 모드 폴링을 성립시킨다 (변경노트 E-45)."""
        naver_fake = FakeRequests(get=FakeResponse({
            "result": {"areas": [{"datas": [
                {"cd": "005930", "nv": "74300", "pcv": "73000", "cv": "1300", "cr": "1.78", "aq": "100"},
                {"cd": "000660", "nv": "180000", "pcv": "178000", "cv": "2000", "cr": "1.12", "aq": "200"},
            ]}]},
        }))
        with mock.patch.object(naver, "_requests", return_value=naver_fake):
            quotes = naver.fetch_quotes(["005930", "000660"])

        self.assertEqual(len(quotes), 2)
        self.assertEqual(len(naver_fake.get_calls), 1, "N종목이 1회 호출이어야 한다")
        self.assertEqual(quotes["005930"].price, Decimal("74300"))

    def test_6자리가_아니면_거른다(self):
        with mock.patch.object(naver, "_requests") as requests:
            self.assertEqual(naver.fetch_quotes(["AAPL", ""]), {})
        requests.assert_not_called()

    def test_가격이_0인_종목은_넣지_않는다(self):
        """0원은 '아직 안 받음' 과 구분되지 않는다. 체결에 쓰면 0원 거래가 된다."""
        naver_fake = FakeRequests(get=FakeResponse({
            "result": {"areas": [{"datas": [
                {"cd": "005930", "nv": "0", "pcv": "73000"},
                {"cd": "000660", "nv": "180000", "pcv": "178000"},
            ]}]},
        }))
        with mock.patch.object(naver, "_requests", return_value=naver_fake):
            quotes = naver.fetch_quotes(["005930", "000660"])
        self.assertEqual(list(quotes), ["000660"])

    def test_응답이_통째로_비면_실패로_본다(self):
        naver_fake = FakeRequests(get=FakeResponse({"result": {"areas": []}}))
        with mock.patch.object(naver, "_requests", return_value=naver_fake):
            with self.assertRaises(ExternalDataError):
                naver.fetch_quotes(["005930"])

    def test_단건_하락_부호를_바로잡는다(self):
        """v1.0 이 잡아둔 함정 — 하락인데 change 가 양수로 온다."""
        naver_fake = FakeRequests(get=FakeResponse({
            "closePrice": "72,000", "compareToPreviousClosePrice": "1,000",
            "fluctuationsRatio": "-1.37", "accumulatedTradingVolume": "1,234",
        }))
        with mock.patch.object(naver, "_requests", return_value=naver_fake):
            quote = naver.fetch_quote("005930")

        self.assertEqual(quote.change, Decimal("-1000"))
        self.assertEqual(quote.prev_close, Decimal("73000"))


# ─────────────────────────────────────────────────────────────────
# 8. 구독 등록 — needs_orderbook
# ─────────────────────────────────────────────────────────────────


class MarkPriorityTests(TestCase):
    def test_우선순위는_올리기만_한다(self):
        mark_priority(AssetClass.STOCK, SYMBOL, 1, "OPEN_ORDER")
        mark_priority(AssetClass.STOCK, SYMBOL, 3, "WATCHING")
        self.assertEqual(SubscriptionRegistry.objects.get().priority, 1)

    def test_needs_orderbook_은_한번_참이면_내리지_않는다(self):
        """★ 나중에 온 연습 주문이 덮으면 **대회 종목의 호가 갱신이 멈춘다.**"""
        mark_priority(AssetClass.STOCK, SYMBOL, 1, "OPEN_ORDER", needs_orderbook=True)
        mark_priority(AssetClass.STOCK, SYMBOL, 1, "OPEN_ORDER", needs_orderbook=False)
        self.assertTrue(SubscriptionRegistry.objects.get().needs_orderbook)

    def test_연습이_먼저여도_대회가_오면_참이_된다(self):
        mark_priority(AssetClass.STOCK, SYMBOL, 2, "OPEN_ORDER", needs_orderbook=False)
        mark_priority(AssetClass.STOCK, SYMBOL, 1, "OPEN_ORDER", needs_orderbook=True)
        row = SubscriptionRegistry.objects.get()
        self.assertTrue(row.needs_orderbook)
        self.assertEqual(row.priority, 1)


# ─────────────────────────────────────────────────────────────────
# 9. 통합 루프 커맨드 — 순서와 장애 격리
# ─────────────────────────────────────────────────────────────────


class RunTradingLoopTests(TestCase):
    """`run_trading_loop` 는 잡 2 → 잡 1 → 잡 3 을 **이 순서로** 돌린다 (F-20 2.1)."""

    def _run(self, **patches):
        from django.core.management import call_command

        return call_command("run_trading_loop", **patches)

    def test_호가_시세_체결_순서로_돈다(self):
        """★ 순서가 뒤집히면 **옛 시세로 체결하는 창**이 생긴다 — 통합한 이유 자체다."""
        called: list[str] = []

        def record(name):
            def _fn(**kwargs):
                called.append(name)
                from core.jobs import SyncResult
                return SyncResult()
            return _fn

        with mock.patch("trading.management.commands.run_trading_loop.STEPS", [
            ("poll_orderbook", record("호가"), "호가"),
            ("poll_quotes", record("시세"), "시세"),
            ("match_pending_orders", record("체결"), "체결"),
        ]):
            self._run()

        self.assertEqual(called, ["호가", "시세", "체결"])

    def test_잡_하나가_실패해도_나머지는_돈다(self):
        """KIS 가 죽어도 체결은 계속돼야 한다 — 낡은 호가도 5분 안이면 쓴다."""
        called: list[str] = []

        def boom(**kwargs):
            raise ExternalDataError("KIS 장애")

        def ok(name):
            def _fn(**kwargs):
                called.append(name)
                from core.jobs import SyncResult
                return SyncResult()
            return _fn

        with mock.patch("trading.management.commands.run_trading_loop.STEPS", [
            ("poll_orderbook", boom, "호가"),
            ("poll_quotes", ok("시세"), "시세"),
            ("match_pending_orders", ok("체결"), "체결"),
        ]):
            self._run()

        self.assertEqual(called, ["시세", "체결"])


# ═════════════════════════════════════════════════════════════════
#  벤치마크 지수 적재 (F-05 4.4) — 세션 11
# ═════════════════════════════════════════════════════════════════
#
#  ★ pykrx 호출만 목으로 끊는다. upsert·등락률 계산·형식 붕괴 판정은 진짜로 돈다.
#    KRX 자격증명이 없어도 이 테스트는 통과해야 한다 — 없는 것이 기본 상태다.

class MarketIndexTests(TestCase):
    def _frame(self, rows):
        """pykrx 가 돌려주는 모양의 DataFrame — 날짜 인덱스 + '종가' 컬럼."""
        import pandas as pd

        return pd.DataFrame(
            {"종가": [value for _day, value in rows]},
            index=pd.to_datetime([day for day, _value in rows]),
        )

    def _run(self, frames, *, dry_run=False):
        """`{티커: DataFrame}` 을 돌려주는 가짜 pykrx 로 잡을 돌린다."""
        from market import services

        class FakeStock:
            def get_index_ohlcv(self, _start, _end, ticker):
                return frames[ticker]

        with mock.patch.object(services, "_load_pykrx", return_value=FakeStock()), \
             mock.patch.object(services, "spend_api_budget", lambda *a, **k: None):
            return services.sync_market_index(dry_run=dry_run, on_progress=lambda _m: None)

    def test_KOSPI_와_KOSDAQ_를_적재한다(self):
        from market.models import MarketIndexSnapshot

        result = self._run({
            "1001": self._frame([("2026-08-12", 2500.0), ("2026-08-13", 2525.0)]),
            "2001": self._frame([("2026-08-12", 800.0), ("2026-08-13", 792.0)]),
        })

        self.assertEqual(result.created, 4)
        self.assertEqual(
            MarketIndexSnapshot.objects.filter(index_code="KOSPI").count(), 2
        )
        row = MarketIndexSnapshot.objects.get(index_code="KOSDAQ", date="2026-08-13")
        self.assertEqual(row.close, Decimal("792.0000"))

    def test_등락률은_직전_종가로_계산한다(self):
        """★ pykrx 응답에 등락률 컬럼이 없어 직접 낸다."""
        from market.models import MarketIndexSnapshot

        self._run({
            "1001": self._frame([("2026-08-12", 2500.0), ("2026-08-13", 2525.0)]),
            "2001": self._frame([("2026-08-12", 800.0)]),
        })

        first = MarketIndexSnapshot.objects.get(index_code="KOSPI", date="2026-08-12")
        second = MarketIndexSnapshot.objects.get(index_code="KOSPI", date="2026-08-13")
        self.assertEqual(first.change_pct, Decimal("0.0000"))      # 비교 대상이 없다
        self.assertEqual(second.change_pct, Decimal("1.0000"))     # 2500 → 2525

    def test_구간_첫날은_DB_의_직전_행과_비교한다(self):
        from market.models import MarketIndexSnapshot

        MarketIndexSnapshot.objects.create(
            date="2026-08-12", index_code="KOSPI", close=Decimal(2500)
        )
        self._run({
            "1001": self._frame([("2026-08-13", 2525.0)]),
            "2001": self._frame([]),
        })

        row = MarketIndexSnapshot.objects.get(index_code="KOSPI", date="2026-08-13")
        self.assertEqual(row.change_pct, Decimal("1.0000"))

    def test_두_번_돌려도_행이_늘지_않는다(self):
        from market.models import MarketIndexSnapshot

        frames = {
            "1001": self._frame([("2026-08-13", 2525.0)]),
            "2001": self._frame([("2026-08-13", 792.0)]),
        }
        self._run(frames)
        second = self._run(frames)

        self.assertEqual(second.created, 0)
        self.assertEqual(second.updated, 2)
        self.assertEqual(MarketIndexSnapshot.objects.count(), 2)

    def test_빈_응답은_실패가_아니라_notes_로_알린다(self):
        """★ pykrx 는 형식이 깨져도 예외 대신 빈 DataFrame 을 준다."""
        result = self._run({
            "1001": self._frame([]),
            "2001": self._frame([]),
        })

        self.assertEqual(result.rows, 0)
        self.assertEqual(len(result.notes), 2)
        self.assertTrue(all("비었습니다" in note for note in result.notes))

    def test_종가_0_은_건너뛴다(self):
        """0 을 저장하면 다음 날 등락률이 -100% 가 된다."""
        from market.models import MarketIndexSnapshot

        self._run({
            "1001": self._frame([("2026-08-12", 0.0), ("2026-08-13", 2525.0)]),
            "2001": self._frame([]),
        })

        self.assertEqual(MarketIndexSnapshot.objects.filter(index_code="KOSPI").count(), 1)

    def test_응답_형식이_깨지면_외부_장애로_던진다(self):
        """★ 500(우리 버그)이 아니라 503(남의 서버)으로 나가야 원인을 바로 짚는다."""
        import pandas as pd

        from market import services

        # 값은 있는데 인덱스가 날짜가 아니다 — `stamp.date()` 에서 터진다.
        # (컬럼명만 바뀐 경우는 빈 결과로 떨어져 notes 로 알린다 — 위 테스트 참조)
        broken = pd.DataFrame({"종가": [2500.0]}, index=["2026-08-13"])

        class FakeStock:
            def get_index_ohlcv(self, _start, _end, _ticker):
                return broken

        with mock.patch.object(services, "_load_pykrx", return_value=FakeStock()), \
             mock.patch.object(services, "spend_api_budget", lambda *a, **k: None), \
             self.assertRaises(ExternalDataError) as ctx:
            services.sync_market_index(on_progress=lambda _m: None)

        self.assertIn("해석하지 못했습니다", str(ctx.exception))
        self.assertIn("종가", ctx.exception.hint)

    def test_dry_run_은_쓰지_않는다(self):
        from market.models import MarketIndexSnapshot

        result = self._run({
            "1001": self._frame([("2026-08-13", 2525.0)]),
            "2001": self._frame([]),
        }, dry_run=True)

        self.assertEqual(result.updated, 1)
        self.assertFalse(MarketIndexSnapshot.objects.exists())
