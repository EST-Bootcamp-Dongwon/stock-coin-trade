"""홈 대시보드 · GNB 테스트 (F-21 · U-01).

    python manage.py test web

★★ **가장 중요한 테스트가 무엇인가** ────────────────────────────────────────

`test_홈은_외부_API를_부르지_않는다` 다. F-21 4장이 홈에 건 유일한 절대 조건이고,
어기면 **방문자 수만큼 KIS·업비트 호출이 나가** 초당 5건 유량이 즉시 마른다.
게다가 조용히 어긋난다 — 개발 중에는 방문자가 나 하나라 아무 문제가 없어 보인다.

그래서 "네트워크로 나가는 함수" 를 통째로 막아 놓고 홈을 그려 본다.
"""

import os
import subprocess
import sys
import textwrap
from datetime import timedelta
from decimal import Decimal
from unittest import mock

from django.conf import settings
from django.test import SimpleTestCase, TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import Account
from accounts.services import register_member
from contests.models import (
    Contest,
    ContestRanking,
    ContestStatus,
    DailySnapshot,
    Participation,
    ParticipationStatus,
    ViolationRule,
    Visibility,
    WeeklyTurnover,
)
from core.constants import AccountMode, AssetClass
from core.time import today_kst, week_start_kst
from market.models import CryptoRank, IndexCache, QuoteCache
from trading.models import Position
from web import navigation, services

PASSWORD = "kospi-2026-test"
SAMSUNG = "005930"
ONE_EOK = 100_000_000


def make_quote(symbol=SAMSUNG, *, price="74300", change_pct="4.80", simulated=False, volume=1000):
    now = timezone.now()
    return QuoteCache.objects.create(
        asset_class=AssetClass.STOCK,
        symbol=symbol,
        price=Decimal(price),
        change_pct=Decimal(change_pct),
        prev_close=Decimal(price),
        volume=volume,
        is_simulated=simulated,
        fetched_at=now,
        expires_at=now + timedelta(seconds=10),
    )


class HomeAnonymousTests(TestCase):
    """비로그인 홈 (F-21 2.1)."""

    def test_비로그인도_홈을_볼_수_있다(self):
        """라우팅 문서 5장 — 홈은 공개 화면이다."""
        response = self.client.get(reverse("home"))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "web/home.html")

    def test_공개_대회만_홈에_뜬다(self):
        """`LINK`·`PRIVATE` 대회가 홈에 뜨면 비공개의 의미가 없다."""
        today = today_kst()
        common = dict(
            status=ContestStatus.ONGOING,
            start_date=today - timedelta(days=1),
            end_date=today + timedelta(days=30),
        )
        Contest.objects.create(name="공개 대회", slug="pub", visibility=Visibility.PUBLIC, **common)
        Contest.objects.create(name="비공개 대회", slug="priv", visibility=Visibility.PRIVATE, **common)

        cards = services.public_contest_cards()
        self.assertEqual([card.contest.name for card in cards], ["공개 대회"])

    def test_참가자_수는_승인된_참가만_센다(self):
        today = today_kst()
        contest = Contest.objects.create(
            name="대회", slug="c1", visibility=Visibility.PUBLIC,
            status=ContestStatus.ONGOING,
            start_date=today - timedelta(days=1), end_date=today + timedelta(days=30),
        )
        for index, status in enumerate(
            [ParticipationStatus.APPROVED, ParticipationStatus.PENDING, ParticipationStatus.APPROVED]
        ):
            member = register_member(
                username=f"m{index}", email=f"m{index}@example.com", password=PASSWORD
            )
            Participation.objects.create(
                contest=contest, member=member, nickname=f"별칭{index}", status=status
            )

        card = services.public_contest_cards()[0]
        self.assertEqual(card.participant_count, 2)

    def test_캐시가_비면_비었다고_화면에_적는다(self):
        """★ v1.0 은 조용히 빈 배열을 보여줬다 (규약 7.2 의 교훈)."""
        response = self.client.get(reverse("home"))
        self.assertTrue(services.market_overview().is_empty)
        self.assertContains(response, "아직 시세를 받지 못했습니다")


class MarketOverviewTests(TestCase):
    """시장 현황 (F-21 3.2)."""

    def test_상승과_하락을_나눠서_준다(self):
        """★ v1.0 은 등락률 **절대값** 정렬이라 상승·하락이 섞여 나왔다."""
        make_quote("000001", change_pct="9.50")
        make_quote("000002", change_pct="3.20")
        make_quote("000003", change_pct="-8.10")
        make_quote("000004", change_pct="-2.40")

        overview = services.market_overview()
        self.assertEqual([q.symbol for q in overview.gainers][:2], ["000001", "000002"])
        self.assertEqual([q.symbol for q in overview.losers][:2], ["000003", "000004"])

    def test_거래량_0인_종목은_제외한다(self):
        """거래가 없는데 등락률만 큰 종목(관리·정리매매)이 올라오면 오해를 부른다."""
        make_quote("000001", change_pct="29.90", volume=0)
        make_quote("000002", change_pct="3.20", volume=500)

        overview = services.market_overview()
        self.assertEqual([q.symbol for q in overview.gainers], ["000002"])

    def test_시뮬레이션_가격이_섞이면_화면에_알린다(self):
        """U-01 5장 — 화면에 보이지 않는 사실은 없는 것과 같다."""
        make_quote("000001", change_pct="5.00", simulated=True)

        self.assertTrue(services.market_overview().has_simulated)
        response = self.client.get(reverse("frag_market"))
        self.assertContains(response, "시뮬레이션 가격 포함")

    def test_지수와_코인도_캐시에서_읽는다(self):
        now = timezone.now()
        IndexCache.objects.create(
            index_code="KOSPI", value=Decimal("3182.11"), change_pct=Decimal("0.03"),
            fetched_at=now, expires_at=now + timedelta(seconds=60),
        )
        CryptoRank.objects.create(
            market="KRW-BTC", rank=1, trade_price=Decimal("95000000"),
            acc_trade_price_24h=1_000_000_000, change_rate=Decimal("1.50"),
        )
        overview = services.market_overview()
        self.assertEqual([i.index_code for i in overview.indices], ["KOSPI"])
        self.assertEqual([c.market for c in overview.cryptos], ["KRW-BTC"])


class HomeMemberTests(TestCase):
    """로그인 홈 — 내 대회 현황 · 내 계좌 (F-21 2.1 · 3.1)."""

    def setUp(self):
        self.member = register_member(
            username="trader", email="trader@example.com", password=PASSWORD
        )
        self.client.login(username="trader", password=PASSWORD)
        self.today = today_kst()

    def _join_contest(self):
        contest = Contest.objects.create(
            name="RFM 1회", slug="rfm-1", visibility=Visibility.PUBLIC,
            status=ContestStatus.ONGOING, initial_capital=ONE_EOK,
            start_date=self.today - timedelta(days=10),
            end_date=self.today + timedelta(days=12),
        )
        account = Account.objects.create(
            member=self.member, contest=contest, mode=AccountMode.CONTEST,
            cash=ONE_EOK, initial_capital=ONE_EOK,
        )
        participation = Participation.objects.create(
            contest=contest, member=self.member, account=account,
            nickname="트레이더", status=ParticipationStatus.APPROVED,
        )
        return contest, participation

    def _card_for(self, account):
        return next(
            card for card in services.account_cards(self.member)
            if card.account.pk == account.pk
        )

    def test_연습_계좌_3종이_홈에_뜬다(self):
        cards = services.account_cards(self.member)
        self.assertEqual(len(cards), 3)
        self.assertEqual(sum(card.total_asset for card in cards), 3 * ONE_EOK)

    def test_순위는_ContestRanking_을_그대로_읽는다(self):
        """★ F-05 7장 — 조회 시점에 계산하지 않는다.

        일부러 **말이 안 되는 값**을 넣는다. 화면이 스스로 계산한다면 이 값이
        나올 수 없으므로, 그대로 나오면 "읽기만 한다" 가 증명된다.
        """
        contest, participation = self._join_contest()
        ContestRanking.objects.create(
            contest=contest, participation=participation, date=self.today,
            rank=7, prev_rank=10, nav=Decimal("1082.5"),
            cumulative_return_pct=Decimal("8.25"), daily_return_pct=Decimal("1.10"),
        )

        card = services.my_contest_cards(self.member)[0]
        self.assertEqual(card.rank_delta, 3)                      # 10위 → 7위
        self.assertEqual(card.ranking.cumulative_return_pct, Decimal("8.25"))
        self.assertEqual(card.d_day, 12)

        response = self.client.get(reverse("home"))
        self.assertContains(response, "RFM 1회")
        self.assertContains(response, "+8.25%")

    def test_회전율_미달이면_경고를_띄운다(self):
        """F-21 3.1 — 참가자가 대회 화면까지 들어가야 알 수 있으면 늦는다."""
        _, participation = self._join_contest()
        WeeklyTurnover.objects.create(
            participation=participation, week_start=week_start_kst(self.today),
            buy_amount=0, sell_amount=0, avg_asset=ONE_EOK,
            turnover_pct=Decimal("2.40"), is_violation=True, is_confirmed=False,
        )

        card = services.my_contest_cards(self.member)[0]
        self.assertTrue(card.turnover_warning)

        response = self.client.get(reverse("home"))
        self.assertContains(response, "금주 회전율")
        # 잠정치임을 함께 적는다 (WeeklyTurnover 모델 주석의 요구)
        self.assertContains(response, "예상")

    def test_한도_위반은_스냅샷에_굳은_것을_읽는다(self):
        """★ 위반 판정은 정산 시점에 끝났다. 화면은 다시 판정하지 않는다 (F-05 7장)."""
        _, participation = self._join_contest()
        DailySnapshot.objects.create(
            participation=participation, date=self.today,
            cash=ONE_EOK, position_value=0, total_asset=ONE_EOK, nav=Decimal("1000"),
            # `contests.services.evaluate_violations` 가 실제로 만드는 모양이다
            violations=[{
                "rule": ViolationRule.POSITION_LIMIT,
                "symbol": SAMSUNG, "name": "삼성전자",
                "current_pct": "18.2000", "limit_pct": "15.0000",
                "excess_krw": 3_200_000,
            }],
        )
        card = services.my_contest_cards(self.member)[0]
        self.assertTrue(card.has_limit_violation)
        self.assertEqual(
            card.violation_lines,
            ["종목 비중 한도 — 삼성전자 18.2000% (한도 15.0000%)"],
        )
        self.assertContains(self.client.get(reverse("home")), "한도 초과 상태입니다")

    def test_키가_다른_위반_종류도_렌더된다(self):
        """★ 위반 JSONB 는 규칙마다 키가 다르다 — 소형주 한도에는 종목도 섹터도 없다.

        템플릿에서 대체 키를 찾는 방식이면 여기서 `VariableDoesNotExist` 로
        **홈 전체가 500** 이 된다. 실제로 겪은 회귀라 테스트로 못 박는다.
        """
        _, participation = self._join_contest()
        DailySnapshot.objects.create(
            participation=participation, date=self.today,
            cash=ONE_EOK, position_value=0, total_asset=ONE_EOK, nav=Decimal("1000"),
            violations=[
                {"rule": ViolationRule.SMALL_CAP_LIMIT,
                 "current_pct": "22.0000", "limit_pct": "20.0000", "excess_krw": 2_000_000},
                {"rule": ViolationRule.SECTOR_LIMIT, "sector_code": "G45", "sector_name": "반도체",
                 "current_pct": "31.0000", "limit_pct": "30.0000", "excess_krw": 1_000_000},
            ],
        )
        card = services.my_contest_cards(self.member)[0]
        self.assertEqual(len(card.violation_lines), 2)
        self.assertIn("반도체", card.violation_lines[1])
        self.assertEqual(self.client.get(reverse("home")).status_code, 200)

    def test_참가_중인_대회가_없으면_모집중_대회를_보여준다(self):
        """F-21 2.1 세 번째 경우."""
        Contest.objects.create(
            name="모집 중 대회", slug="upcoming", visibility=Visibility.PUBLIC,
            status=ContestStatus.UPCOMING,
            start_date=self.today + timedelta(days=3),
            end_date=self.today + timedelta(days=33),
        )
        response = self.client.get(reverse("home"))
        self.assertContains(response, "참가할 수 있는 대회")
        self.assertContains(response, "모집 중 대회")

    def test_시세가_없는_보유는_평단으로_평가하고_배지를_단다(self):
        """★ 0 으로 두면 순자산이 줄어 비중이 부풀고 엉뚱한 위반이 기록된다.

        평가액은 평단으로 채우되, **그 사실을 화면에 밝힌다** (U-01 5장).
        """
        account = self.member.accounts.get(mode=AccountMode.PRACTICE_STOCK)
        Position.objects.create(
            account=account, symbol=SAMSUNG, asset_class=AssetClass.STOCK,
            qty=Decimal("100"), avg_price=Decimal("70000"), principal=7_000_000,
        )
        card = self._card_for(account)

        self.assertIn(SAMSUNG, card.estimated_symbols)
        self.assertEqual(card.total_asset, ONE_EOK + 100 * 70_000)
        self.assertTrue(card.has_caveat)

        self.assertContains(self.client.get(reverse("home")), "평단 평가")

    def test_시뮬레이션_가격으로_평가되면_배지를_단다(self):
        account = self.member.accounts.get(mode=AccountMode.PRACTICE_STOCK)
        Position.objects.create(
            account=account, symbol=SAMSUNG, asset_class=AssetClass.STOCK,
            qty=Decimal("10"), avg_price=Decimal("70000"), principal=700_000,
        )
        make_quote(SAMSUNG, price="80000", simulated=True)

        card = self._card_for(account)
        self.assertIn(SAMSUNG, card.simulated_symbols)
        # ★ 시뮬레이션이어도 **평가에는 쓴다** — 연습 모드는 그 값으로 체결까지 하므로
        #   빼면 화면의 숫자와 계좌의 현실이 어긋난다.
        self.assertEqual(card.total_asset, ONE_EOK + 10 * 80_000)


class NoExternalCallTests(TestCase):
    """★★ F-21 4장 — 홈은 외부 API 를 부르지 않는다."""

    def test_홈은_외부_API를_부르지_않는다(self):
        """네트워크로 나가는 입구를 전부 막고 홈을 그린다.

        ★ `requests` 는 업비트·KIS 가, `_load_pykrx` 는 종목/지수 배치가 쓰는 통로다.
          하나라도 불리면 이 테스트가 실패한다.
        """
        register_member(username="trader", email="trader@example.com", password=PASSWORD)
        self.client.login(username="trader", password=PASSWORD)
        make_quote()

        with (
            mock.patch("requests.get", side_effect=AssertionError("홈에서 외부 HTTP 호출")),
            mock.patch("requests.post", side_effect=AssertionError("홈에서 외부 HTTP 호출")),
            mock.patch(
                "market.services._load_pykrx", side_effect=AssertionError("홈에서 pykrx 호출")
            ),
        ):
            for url_name in ["home", "frag_market", "frag_my_contests", "frag_my_accounts"]:
                with self.subTest(url=url_name):
                    self.assertEqual(self.client.get(reverse(url_name)).status_code, 200)


class SlimBundleTests(SimpleTestCase):
    """★★ 배포 번들(slim 의존성)만으로 앱이 뜨는지 (VERCEL.md 1장).

    Vercel 에 올리는 `requirements.txt`(**`api/requirements.txt`** — 2026-09-11 저장소
    루트에서 옮겼다. 루트는 v3.0 Streamlit 이 쓴다 → ADR-SC-0008 ②)는
    `pykrx` 와 `fastembed` 를
    **뺀다.** 둘이 pandas·numpy·onnxruntime·matplotlib 을 끌고 와 260MB 를 차지하는데,
    웹 요청 경로에서는 한 번도 쓰이지 않기 때문이다.

    성립 조건은 하나다 — **어느 모듈도 최상단에서 이 둘을 import 하지 않을 것.**
    누군가 `market/services.py` 맨 위에 `from pykrx import stock` 을 적으면
    로컬 테스트는 전부 통과하고 **배포만 조용히 죽는다.** 그 회귀를 여기서 잡는다.

    ★ 별도 프로세스로 돌린다. 이 테스트가 실행될 시점에는 이미 모든 모듈이
      import 된 뒤라, 같은 프로세스에서 막아 봐야 의미가 없다.
    """

    def test_pykrx_fastembed_없이도_기동한다(self):
        script = textwrap.dedent(
            """
            import os, sys

            BLOCKED = {"pykrx", "fastembed", "onnxruntime", "pandas", "matplotlib", "numpy"}

            class Blocker:
                def find_spec(self, name, path=None, target=None):
                    if name.split(".")[0] in BLOCKED:
                        raise ImportError(f"[차단됨] {name}")
                    return None

            sys.meta_path.insert(0, Blocker())

            os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
            import django
            django.setup()

            from django.core.management import call_command
            call_command("check")

            # URLConf 를 실제로 해석해 모든 뷰 모듈을 불러온다
            from django.urls import get_resolver
            get_resolver().url_patterns
            # 잡 레지스트리 — market · insight · contests · trading 서비스를 전부 import 한다
            import config.internal_urls
            from config.wsgi import application

            print("OK")
            """
        )
        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd=str(settings.BASE_DIR),
            env={**os.environ, "PYTHONPATH": str(settings.BASE_DIR)},
            capture_output=True,
            text=True,
            timeout=120,
        )
        self.assertIn("OK", result.stdout, msg=f"기동 실패:\n{result.stderr[-3000:]}")


class FragmentTests(TestCase):
    """HTMX 프래그먼트 (규약 2장)."""

    def test_프래그먼트는_페이지_껍데기를_상속하지_않는다(self):
        """★ base.html 을 상속하면 `<html>` 통째로 응답해 화면 속에 화면이 겹친다."""
        response = self.client.get(reverse("frag_market"))
        self.assertTemplateUsed(response, "web/_market.html")
        self.assertTemplateNotUsed(response, "base.html")
        self.assertNotContains(response, "<html")

    def test_프래그먼트가_폴링_속성을_스스로_갖는다(self):
        """★ `outerHTML` 로 교체되므로 조각 자신에 hx 속성이 없으면
        **한 번 갱신되고 폴링이 멈춘다.**"""
        body = self.client.get(reverse("frag_market")).content.decode()
        self.assertIn('hx-get="/fragments/market/"', body)
        self.assertIn("every 30s", body)

    def test_비로그인_폴링에는_빈_조각을_준다(self):
        """세션이 만료된 채 폴링이 이어질 때 에러 조각이 박히지 않게 한다."""
        for url_name in ["frag_my_contests", "frag_my_accounts"]:
            with self.subTest(url=url_name):
                self.assertEqual(self.client.get(reverse(url_name)).status_code, 200)


class NavigationTests(TestCase):
    """GNB (U-01 2장)."""

    def test_아직_없는_화면은_준비중으로_표시된다(self):
        """★ 감추지 않는다 — 무엇이 있고 무엇이 아직 없는지가 화면에 드러나야 한다."""
        groups = navigation.build_menu(is_authenticated=True)
        labels = {item["label"]: item for group in groups for item in group["items"]}

        # 이번 세션에 만들지 않은 화면 — URL 이 없으므로 링크가 걸리지 않는다
        self.assertFalse(labels["주식"]["ready"])
        self.assertIsNone(labels["주식"]["url"])

        response = self.client.get(reverse("home"))
        self.assertContains(response, "준비 중")

    def test_Pine_전략이_메뉴에_있다(self):
        """★ v1.0 결함 D-2 — 화면은 있는데 메뉴에 없어 아무도 갈 수 없었다."""
        groups = navigation.build_menu(is_authenticated=True)
        labels = [item["label"] for group in groups for item in group["items"]]
        self.assertIn("Pine 전략", labels)

    def test_비로그인에게도_메뉴_구조는_보인다(self):
        """U-01 2.3 — 잠금 화면만 띄우지 않고 무엇을 할 수 있는지 알려준다."""
        groups = navigation.build_menu(is_authenticated=False)
        practice = next(group for group in groups if group["label"] == "연습")
        self.assertTrue(practice["locked"])
        self.assertTrue(all(item["locked"] for item in practice["items"]))

    def test_진행중_대회_배지는_종료가_임박한_것을_보여준다(self):
        member = register_member(
            username="trader", email="trader@example.com", password=PASSWORD
        )
        today = today_kst()
        for index, days in enumerate([30, 5]):
            contest = Contest.objects.create(
                name=f"대회{days}", slug=f"c{index}", visibility=Visibility.PUBLIC,
                status=ContestStatus.ONGOING,
                start_date=today - timedelta(days=1), end_date=today + timedelta(days=days),
            )
            account = Account.objects.create(
                member=member, contest=contest, mode=AccountMode.CONTEST,
                cash=ONE_EOK, initial_capital=ONE_EOK,
            )
            Participation.objects.create(
                contest=contest, member=member, account=account,
                nickname=f"별칭{index}", status=ParticipationStatus.APPROVED,
            )

        self.client.login(username="trader", password=PASSWORD)
        response = self.client.get(reverse("home"))
        self.assertEqual(response.context["nav_contest_badge"]["name"], "대회5")


class TemplateFilterTests(TestCase):
    """표시 필터 (core/templatetags/form_extras.py · U-01 4장)."""

    def test_등락률은_부호를_항상_붙인다(self):
        from core.templatetags.form_extras import d_day, krw_short, pnl_class, signed_pct

        self.assertEqual(signed_pct(Decimal("8.2")), "+8.20%")
        self.assertEqual(signed_pct(Decimal("-3.15")), "-3.15%")
        self.assertEqual(signed_pct(Decimal("0")), "0.00%")
        self.assertEqual(signed_pct(None), "—")

        # ★ 상승 빨강 / 하락 파랑 — 국내 관행이고 미국과 반대다
        self.assertEqual(pnl_class(Decimal("1")), "text-up")
        self.assertEqual(pnl_class(Decimal("-1")), "text-down")
        self.assertEqual(pnl_class(Decimal("0")), "text-flat")

        self.assertEqual(krw_short(123_456_789), "1억 2,346만")
        self.assertEqual(krw_short(-50_000), "-5만")
        self.assertEqual(krw_short(1_234), "1,234")

        self.assertEqual(d_day(12), "D-12")
        self.assertEqual(d_day(0), "D-DAY")
        self.assertEqual(d_day(-3), "D+3")
