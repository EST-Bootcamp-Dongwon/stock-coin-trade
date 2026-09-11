"""데모·테스트 데이터 생성 — **결함 D-7 해소** (05 문서 5.4).

> v1.0 README 는 `test1@test.com` · `test2@test.com` 이 앱 시작 시 자동 생성된다고
> 적었지만 **코드에는 존재하지 않았다.** 문서만 보고 로그인을 시도하면 실패했다.
>
> v2.0 은 **README 를 코드에 맞추는 게 아니라, 코드를 만들고 README 를 거기 맞춘다.**

사용법::

    python manage.py seed_demo                    # demo1 · demo2 + 연습 계좌 3개씩
    python manage.py seed_demo --users            # + 샘플 투자자 30명 (v1.0 승계)
    python manage.py seed_demo --users 5          # + 샘플 투자자 5명
    python manage.py seed_demo --users --contest  # + 진행 중 대회 1개 · 스냅샷 20영업일

★★ **안전장치 2개** (05 문서 5.4) ────────────────────────────────────────

  ① **운영 환경에서는 절대 돌지 않는다.** `DEBUG=False` 이면 거부하고,
     정말 필요하면 `ALLOW_DEMO_SEED=1` 을 명시적으로 켜야 한다
  ② **앱 기동 시 자동 실행하지 않는다.** v1.0 은 `app.py` 가 부팅 때
     `seed_demo_investors()` 를 불렀고, 실패해도 조용히 넘어갔다.
     `AppConfig.ready()` 에 아무것도 붙이지 않는 원칙(05 문서 6장)과 같은 이유다

**모든 주문에 `source="DEMO_SEED"`** 를 박아 실제 주문과 섞이지 않게 한다.
v1.0 이 이미 쓰던 라벨이라 그대로 승계한다.

**난수는 고정 시드를 쓴다** — 같은 인자로 두 번 돌리면 같은 데이터가 나온다.
디버깅 중에 데이터가 매번 달라지면 재현이 불가능해진다.
"""

import os
from datetime import date, timedelta
from decimal import Decimal
from random import Random

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from accounts.models import Account, Member
from accounts.services import PRACTICE_MODES, register_member
from contests.models import (
    Contest,
    ContestRanking,
    ContestStatus,
    ContestUniverse,
    DailySnapshot,
    Participation,
    ParticipationStatus,
    SnapshotHolding,
)
from core.constants import AccountMode, AssetClass, OrderSide
from core.time import today_kst
from market.models import CryptoRank, IndexCache, Market, QuoteCache, StockMaster
from trading.models import Order, OrderSource, OrderStatus, OrderType, Position

# 재현 가능한 난수. 바꾸면 데모 데이터 전체가 달라진다.
RANDOM_SEED = 20260813

DEMO_PASSWORD = "demo1234!"          # 개발용. 운영에서는 이 커맨드 자체가 막혀 있다
DEMO_EMAIL_DOMAIN = "sample-investor.local"
SAMPLE_USER_DEFAULT = 30             # v1.0 `DEMO_NAMES` 30명 승계
CONTEST_SNAPSHOT_DAYS = 20           # 스냅샷 20영업일치
CONTEST_SLUG = "demo-contest"

# v1.0 `stock_market.STOCKS` + `BASE_PRICES` 의 수업용 14종.
# (symbol, name, market, sector_code, sector_name, base_price, shares_outstanding)
#
# ★★ **2026-08-13 전면 교정 (변경노트 E-31)** ─────────────────────────────────
#
# 첫 `sync_stock_master` 실적재가 이 표의 오류 3종을 드러냈다. 셋 다 v1.0 의
# 하드코딩을 검증 없이 승계하면서 넘어온 것이다.
#
#   ① **상장폐지 종목** — `091990` 셀트리온헬스케어는 셀트리온에 흡수합병되어
#      존재하지 않는다. 배치가 `is_delisted=True` 로 잡아냈다.
#      → `028300` HLB(KOSDAQ 제약)로 교체했다. `is_featured` 의 용도가
#        "시세 조회 실패 시 시뮬레이션 폴백 대상" 인데(E-04 5.1), **없는 종목은
#        폴백 대상이 될 수 없다.** 14종이라는 숫자보다 그 쓸모가 먼저다.
#
#   ② **존재하지 않는 업종코드** — `G45` · `G25` 같은 값은 GICS 를 흉내 낸
#      창작이다. GICS 는 MSCI·S&P 의 유료 데이터라 쓸 수 없어 v2.0 은 **KRX
#      업종분류**를 쓴다(F-16 5.2). 시드가 가짜 코드를 넣어두면 배치를 돌리기 전
#      로컬에서는 **F-04 섹터 한도가 존재하지 않는 섹터 위에서 계산된다.**
#      → KRX 실제 코드로 바꿨다 (KOSPI 10xx · KOSDAQ 20xx).
#
#   ③ **3년쯤 묵은 가격** — 삼성전자 74,000원. 실제 종가는 270,000원이다.
#      배치가 돈 DB 에서는 데모 포지션이 +265% 수익으로 보인다.
#      → 2026-08-13 종가·상장주식수 실측값으로 맞췄다.
#
# 이 값들은 **네트워크 없이 데모를 돌릴 때만** 쓰인다. `_ensure_featured_stocks`
# 는 이미 있는 종목을 건너뛰므로, 배치가 돈 DB 의 진짜 값을 덮어쓰지 않는다.
FEATURED_STOCKS = [
    ("005930", "삼성전자", Market.KOSPI, "1013", "전기전자", 270_000, 5_846_278_608),
    ("000660", "SK하이닉스", Market.KOSPI, "1013", "전기전자", 1_629_000, 730_492_365),
    ("005380", "현대차", Market.KOSPI, "1015", "운송장비·부품", 421_500, 204_757_766),
    ("035420", "NAVER", Market.KOSPI, "1046", "IT 서비스", 225_000, 156_977_585),
    ("035720", "카카오", Market.KOSPI, "1046", "IT 서비스", 39_950, 442_981_070),
    ("068270", "셀트리온", Market.KOSPI, "1009", "제약", 202_500, 232_617_176),
    ("207940", "삼성바이오로직스", Market.KOSPI, "1009", "제약", 1_555_000, 46_290_951),
    ("373220", "LG에너지솔루션", Market.KOSPI, "1013", "전기전자", 366_500, 234_000_000),
    ("005490", "POSCO홀딩스", Market.KOSPI, "1011", "금속", 324_000, 79_241_527),
    ("247540", "에코프로비엠", Market.KOSDAQ, "2072", "전기전자", 115_100, 97_830_434),
    ("196170", "알테오젠", Market.KOSDAQ, "2012", "일반서비스", 316_500, 53_586_343),
    # ↓ 091990 셀트리온헬스케어(상장폐지) 자리. KOSDAQ 제약 시총 상위로 채웠다.
    ("028300", "HLB", Market.KOSDAQ, "2066", "제약", 42_350, 133_198_223),
    # ↓ 지주회사는 KRX 가 '금융' 으로 분류한다. 사업 내용(2차전지)과 다르지만
    #   **섹터 한도는 KRX 분류를 따르므로** 실제 값을 그대로 적는다.
    ("086520", "에코프로", Market.KOSDAQ, "2031", "금융", 92_900, 135_776_152),
    ("263750", "펄어비스", Market.KOSDAQ, "2118", "IT 서비스", 31_425, 62_843_910),
]

# ── 시세 캐시 시드용 (F-21 3.2 의 홈 위젯을 채운다) ────────────
#
# ★★ **왜 시세까지 시드가 필요한가** ────────────────────────────────────────
#
# `QuoteCache` · `IndexCache` · `CryptoRank` 는 원래 배치 잡(F-20 잡 1·11 ·
# `sync_market_index`)이 채운다. 그런데 그 잡들은 KIS·KRX·업비트를 부르므로
# **API 키가 없으면 한 번도 돌지 않고, 홈의 시장 현황이 통째로 빈다.**
#
# 화면 개발·시연 단계에서 그 상태로는 상승/하락 TOP 도, 코인 TOP 도 확인할 수 없다.
# 그래서 여기서 **결정론적인 가짜 값**을 넣되, 전부 `is_simulated=True` 로 표시한다.
#
# ★ **이 표시가 핵심이다.** 화면은 시뮬레이션 가격에 배지를 달고(U-01 5장),
#   대회 체결은 시뮬레이션 가격을 **아예 거부한다**(F-16 4.1 · `quotes.get_quote`).
#   즉 가짜 값이 진짜인 척 흘러 들어갈 경로가 구조적으로 막혀 있다.
#
# 진짜 시세가 들어오면 잡이 같은 행을 `is_simulated=False` 로 덮어쓴다.
CRYPTO_MARKETS = (
    ("KRW-BTC", 95_000_000), ("KRW-ETH", 4_850_000), ("KRW-XRP", 3_120),
    ("KRW-SOL", 285_000), ("KRW-DOGE", 268), ("KRW-ADA", 1_180),
    ("KRW-LINK", 32_400), ("KRW-AVAX", 48_900), ("KRW-DOT", 7_250),
    ("KRW-TRX", 412),
)

MARKET_INDICES = (("KOSPI", Decimal("3182.11")), ("KOSDAQ", Decimal("802.34")))

# ★ 시드 시세의 만료를 길게 잡는다(7일). 캐시 TTL 은 "이 값을 갱신하는 주체가
#   있다"는 전제 위의 개념인데, 시드 값에는 그 주체가 없다. 10초 TTL 로 넣으면
#   **넣자마자 '낡은 시세' 배지**가 붙어, 정작 중요한 '시뮬레이션 가격' 배지가
#   묻힌다. 신선도가 아니라 **출처**가 이 값의 진짜 성질이다.
SEED_QUOTE_TTL = timedelta(days=7)

# v1.0 `demo_seed.DEMO_NAMES` 30명 그대로
SAMPLE_NAMES = (
    "김가온", "이도윤", "박서연", "최민준", "정하린", "윤지후", "한예린", "오현우", "서유진", "강시우",
    "임나연", "조태윤", "신채원", "권도현", "문서진", "배지민", "송예준", "남유나", "류건우", "안수빈",
    "노재현", "장다은", "백준서", "허지안", "전민서", "고은찬", "황유진", "차도윤", "양하은", "서건호",
)


class Command(BaseCommand):
    help = "데모 계정·샘플 투자자·데모 대회를 만든다 (운영 환경에서는 실행되지 않는다)"

    def add_arguments(self, parser):
        parser.add_argument(
            "--users",
            nargs="?", type=int, const=SAMPLE_USER_DEFAULT, default=0,
            metavar="N",
            help=f"샘플 투자자 N명 (값 없이 주면 {SAMPLE_USER_DEFAULT}명 — v1.0 승계)",
        )
        parser.add_argument(
            "--contest", action="store_true",
            help=f"진행 중 데모 대회 1개 + 참가·주문·스냅샷 {CONTEST_SNAPSHOT_DAYS}영업일치",
        )
        parser.add_argument(
            "--market", action="store_true",
            help=(
                "홈의 시장 현황용 시세·지수·코인 캐시를 시뮬레이션 값으로 채운다 "
                "(API 키가 없을 때. 전부 '시뮬레이션 가격' 으로 표시된다)"
            ),
        )

    # ── 안전장치 ① ─────────────────────────────────────────────
    def _guard_environment(self):
        """운영 환경에서는 실행 자체를 막는다.

        `DEBUG` 만 보지 않고 `ALLOW_DEMO_SEED` 탈출구를 둔 이유 — 스테이징처럼
        `DEBUG=False` 지만 데모 데이터가 필요한 환경이 실제로 있다.
        **명시적으로 켜야만 열린다**는 것이 요점이다.

        규약 8.5 — "환경 가드는 막다른 길로 만들지 않는다." 무엇을 해야 하는지까지 적는다.
        """
        if settings.DEBUG:
            return
        if os.getenv("ALLOW_DEMO_SEED", "").strip().lower() in ("1", "true", "yes", "on"):
            self.stdout.write(self.style.WARNING(
                "⚠ DEBUG=False 인데 ALLOW_DEMO_SEED 가 켜져 있어 진행합니다."
            ))
            return
        raise CommandError(
            "운영 환경(DEBUG=False)에서는 실행할 수 없습니다.\n"
            "  · 로컬 개발이라면  backend/.env 의 DJANGO_DEBUG=True 를 확인하십시오.\n"
            "  · 스테이징에 정말 필요하다면  ALLOW_DEMO_SEED=1 을 설정하고 다시 실행하십시오.\n"
            "  · 운영 DB 라면 실행하지 마십시오 — 실제 회원과 섞입니다."
        )

    def handle(self, *args, **options):
        self._guard_environment()

        self.rng = Random(RANDOM_SEED)
        user_count = options["users"]
        with_contest = options["contest"]
        with_market = options["market"]

        self.stdout.write(self.style.MIGRATE_HEADING("데모 데이터 생성"))

        demo_members = self._create_demo_accounts()
        sample_members = self._create_sample_investors(user_count) if user_count else []

        if with_contest:
            self._create_demo_contest(demo_members + sample_members)

        if with_market:
            self._seed_market_cache()

        self.stdout.write(self.style.SUCCESS("\n완료. 로그인 정보:"))
        self.stdout.write(f"  demo1 / demo2  ·  비밀번호 {DEMO_PASSWORD}")
        if not with_contest:
            self.stdout.write("  대회까지 만들려면 --contest 를 붙여 다시 실행하십시오.")
        if not with_market:
            self.stdout.write("  홈의 시장 현황을 채우려면 --market 을 붙여 다시 실행하십시오.")

    # ── 데모 계정 2개 ───────────────────────────────────────────
    def _create_demo_accounts(self) -> list[Member]:
        """`demo1` · `demo2` + 연습 계좌 3개씩.

        `register_member()` 를 쓴다 — 계좌 생성 규칙을 커맨드가 다시 적으면
        서비스 계층과 어긋난다. **시드도 실제 가입 경로를 그대로 탄다.**
        """
        created = []
        for index in (1, 2):
            username = f"demo{index}"
            member = Member.objects.filter(username=username).first()
            if member:
                self.stdout.write(f"  · {username} 이미 있음 — 건너뜀")
            else:
                member = register_member(
                    username=username,
                    email=f"{username}@{DEMO_EMAIL_DOMAIN}",
                    password=DEMO_PASSWORD,
                    display_name=f"데모 사용자 {index}",
                )
                self.stdout.write(self.style.SUCCESS(
                    f"  · {username} 생성 (연습 계좌 {len(PRACTICE_MODES)}개)"
                ))
            created.append(member)
        return created

    # ── 샘플 투자자 N명 ─────────────────────────────────────────
    def _create_sample_investors(self, count: int) -> list[Member]:
        """샘플 투자자와 주식 포지션 2종씩.

        v1.0 `demo_seed._portfolio_for()` 의 의도를 그대로 옮긴다 —
        **투자자마다 평균 매입가를 다르게 해 수익·손실 사례가 함께 나타나게** 한다.
        전원이 수익이면 랭킹 화면이 무의미해진다.
        """
        self._ensure_featured_stocks()

        members = []
        for index in range(1, min(count, len(SAMPLE_NAMES)) + 1):
            username = f"sample{index:02d}"
            member = Member.objects.filter(username=username).first()
            if member:
                members.append(member)
                continue

            member = register_member(
                username=username,
                email=f"sample-investor-{index:02d}@{DEMO_EMAIL_DOMAIN}",
                password=DEMO_PASSWORD,
                display_name=SAMPLE_NAMES[index - 1],
            )
            self._fill_practice_portfolio(member, index)
            members.append(member)

        if count > len(SAMPLE_NAMES):
            self.stdout.write(self.style.WARNING(
                f"  ⚠ 이름이 {len(SAMPLE_NAMES)}개뿐이라 {len(SAMPLE_NAMES)}명만 만들었습니다."
            ))
        self.stdout.write(self.style.SUCCESS(f"  · 샘플 투자자 {len(members)}명"))
        return members

    @transaction.atomic
    def _fill_practice_portfolio(self, member: Member, index: int):
        """주식 연습 계좌에 매수 주문 2건 + 포지션 2건을 넣는다."""
        account = member.accounts.get(mode=AccountMode.PRACTICE_STOCK)
        now = timezone.now()

        for offset in range(2):
            symbol, name, _market, _sc, _sn, base_price, _shares = FEATURED_STOCKS[
                (index * 3 + offset * 5) % len(FEATURED_STOCKS)
            ]
            # 평단을 기준가의 86~114% 사이에 흩뿌린다 → 수익·손실이 섞인다
            avg_price = int(base_price * (0.86 + ((index + offset * 2) % 8) * 0.04))
            qty = max(1, 1_500_000 // avg_price)
            amount = qty * avg_price

            order = Order.objects.create(
                account=account,
                symbol=symbol,
                asset_class=AssetClass.STOCK,
                side=OrderSide.BUY,
                order_type=OrderType.MARKET,
                requested_qty=Decimal(qty),
                filled_qty=Decimal(qty),
                avg_fill_price=Decimal(avg_price),
                gross_amount=amount,
                net_amount=amount,
                status=OrderStatus.FILLED,
                source=OrderSource.DEMO_SEED,       # ★ 실제 주문과 섞이지 않게 한다
            )
            # created_at 은 auto_now_add 라 생성 후에만 바꿀 수 있다.
            # 거래이력 화면이 하루 안에 뭉치지 않게 과거로 흩뿌린다.
            Order.objects.filter(pk=order.pk).update(
                created_at=now - timedelta(days=8 + index + offset * 4),
                filled_at=now - timedelta(days=8 + index + offset * 4),
            )

            Position.objects.update_or_create(
                account=account, symbol=symbol,
                defaults=dict(
                    asset_class=AssetClass.STOCK,
                    qty=Decimal(qty),
                    avg_price=Decimal(avg_price),
                    principal=amount,
                ),
            )
            account.cash -= amount

        account.save(update_fields=["cash", "updated_at"])

    def _ensure_featured_stocks(self):
        """수업용 14종을 `StockMaster` 에 넣는다.

        원래 `sync_stock_master` 배치(pykrx)가 채우는 테이블이지만, **네트워크 없이도
        데모가 돌아야 한다.** 이미 있는 종목은 `is_featured` 만 켜고 시세는 건드리지
        않는다 — 배치가 적재한 진짜 값을 데모가 덮어쓰면 안 된다.
        """
        existing = set(StockMaster.objects.values_list("symbol", flat=True))
        new_rows = []
        for symbol, name, market, sector_code, sector_name, base_price, shares in FEATURED_STOCKS:
            if symbol in existing:
                continue
            new_rows.append(StockMaster(
                symbol=symbol, name=name, market=market,
                sector_code=sector_code, sector_name=sector_name,
                market_cap=base_price * shares,
                shares_outstanding=shares,
                avg_turnover_5d=base_price * shares // 500,
                close_price=Decimal(base_price),
                is_featured=True,
            ))
        if new_rows:
            StockMaster.objects.bulk_create(new_rows)

        symbols = [s[0] for s in FEATURED_STOCKS]
        # 이미 있던 14종에도 수업용 표시를 켠다
        StockMaster.objects.filter(symbol__in=symbols, is_featured=False).update(is_featured=True)

        # ★ **목록에서 빠진 종목은 표시를 끈다.** 이게 없으면 `FEATURED_STOCKS` 를
        #   고쳐도 예전 종목이 켜진 채 남는다 — 091990(상장폐지)을 교체하고도
        #   DB 에는 15종이 featured 로 남아 있던 것이 실제 사례다(E-31).
        #   "이 목록이 곧 수업용 종목" 이라는 뜻이 성립하려면 양방향이어야 한다.
        stale = StockMaster.objects.filter(is_featured=True).exclude(symbol__in=symbols)
        cleared = stale.update(is_featured=False)
        if cleared:
            self.stdout.write(f"  · 목록에서 빠진 종목 {cleared}건의 수업용 표시를 껐습니다")

    # ── 시장 현황 캐시 ──────────────────────────────────────────
    @transaction.atomic
    def _seed_market_cache(self):
        """홈의 시장 현황(F-21 3.2)을 채운다 — **전부 시뮬레이션 표시로.**

        원래 주인은 배치 잡이다:

            QuoteCache  ← `poll_quotes`        (F-20 잡 1 · KIS→네이버→시뮬)
            IndexCache  ← `poll_quotes`        (지수 부분)
            CryptoRank  ← `sync_upbit_markets` (F-20 잡 11)

        전부 외부 API 키가 있어야 도는 잡이라, 키 발급 전에는 홈이 통째로 빈다.
        여기서 넣는 값은 **화면을 확인하기 위한 자리표시자**다.

        ★★ **`is_simulated=True` 를 빠뜨리면 안 된다.** 이 플래그 하나가
          ① 화면에 '시뮬레이션 가격' 배지를 띄우고 (U-01 5장)
          ② **대회 체결에서 이 가격을 거부한다** (`quotes.get_quote(for_contest=True)`)
          가짜 값이 대회 순위에 영향을 줄 경로를 구조가 막는다.

        ★ `update_or_create` 라 **멱등하다.** 진짜 시세가 들어오기 시작하면 잡이
          같은 행을 `is_simulated=False` 로 덮어쓰고, 이 커맨드를 다시 돌리지만
          않으면 되돌아가지 않는다.
        """
        now = timezone.now()
        expires = now + SEED_QUOTE_TTL

        # ── 주식 현재가 ────────────────────────────────────────
        # 수업용 14종 + 이미 마스터에 있는 종목 중 일부까지 채운다.
        # 상승·하락 TOP 을 보려면 등락률이 **양쪽으로 퍼져 있어야** 한다.
        rows = list(
            StockMaster.objects.filter(is_featured=True).values_list("symbol", "close_price")
        )
        if not rows:
            rows = [(symbol, Decimal(price)) for symbol, _, _, _, _, price, _ in FEATURED_STOCKS]

        quote_count = 0
        for symbol, close_price in rows:
            base = Decimal(close_price or 0)
            if base <= 0:
                continue
            # -9% ~ +9% 를 결정론적으로 흩뿌린다 (RANDOM_SEED 고정 → 매번 같은 결과)
            change_pct = Decimal(self.rng.randint(-900, 900)) / 100
            price = (base * (1 + change_pct / 100)).quantize(Decimal("1"))
            QuoteCache.objects.update_or_create(
                asset_class=AssetClass.STOCK,
                symbol=symbol,
                defaults={
                    "price": price,
                    "prev_close": base,
                    "change": price - base,
                    "change_pct": change_pct,
                    "open": base,
                    "high": max(price, base),
                    "low": min(price, base),
                    # 거래량 0 인 종목은 홈에서 제외된다(web/services.py) — 넣어 준다
                    "volume": self.rng.randint(50_000, 5_000_000),
                    "is_simulated": True,
                    "source": "SEED",
                    "fetched_at": now,
                    "expires_at": expires,
                },
            )
            quote_count += 1

        # ── 지수 ───────────────────────────────────────────────
        for code, value in MARKET_INDICES:
            change_pct = Decimal(self.rng.randint(-150, 150)) / 100
            IndexCache.objects.update_or_create(
                index_code=code,
                defaults={
                    "value": value,
                    "change": (value * change_pct / 100).quantize(Decimal("0.0001")),
                    "change_pct": change_pct,
                    "source": "SEED",
                    "fetched_at": now,
                    "expires_at": expires,
                },
            )

        # ── 코인 랭킹 ──────────────────────────────────────────
        # ★ 24시간 거래대금(KRW) 기준이다 — v1.0 의 CoinMarketCap USD Top 100 은
        #   거래가 KRW 기준이라 화면과 연결되지 않았다(결함 D-3).
        for rank, (market, price) in enumerate(CRYPTO_MARKETS, start=1):
            CryptoRank.objects.update_or_create(
                market=market,
                defaults={
                    "rank": rank,
                    "trade_price": Decimal(price),
                    "acc_trade_price_24h": self.rng.randint(10, 5000) * 100_000_000,
                    "change_rate": Decimal(self.rng.randint(-800, 800)) / 100,
                },
            )

        self.stdout.write(
            f"  · 시장 캐시: 시세 {quote_count}종목 · 지수 {len(MARKET_INDICES)}개 · "
            f"코인 {len(CRYPTO_MARKETS)}개 "
            + self.style.WARNING("(전부 시뮬레이션 가격 — 화면에 배지가 뜬다)")
        )

    # ── 데모 대회 ───────────────────────────────────────────────
    @transaction.atomic
    def _create_demo_contest(self, members: list[Member]):
        """진행 중 대회 1개 + 참가·계좌·주문·스냅샷·랭킹.

        ★ **스냅샷은 `DailySnapshot`(JSONB 원본) 과 `SnapshotHolding`(정규화 파생)을
        같은 트랜잭션에서 함께 쓴다** (A-2 · E-02 6.2). 파생만 따로 만들면 정합성
        규약을 시드가 먼저 어기는 셈이 된다.
        """
        if Contest.objects.filter(slug=CONTEST_SLUG).exists():
            self.stdout.write("  · 데모 대회 이미 있음 — 건너뜀")
            return

        business_days = self._recent_business_days(CONTEST_SNAPSHOT_DAYS)
        start_date, end_date = business_days[0], business_days[-1] + timedelta(days=30)

        contest = Contest.objects.create(
            name="데모 모의투자 대회",
            slug=CONTEST_SLUG,
            description="`seed_demo --contest` 가 만든 데모 대회입니다. 실제 대회가 아닙니다.",
            asset_class=AssetClass.STOCK,
            status=ContestStatus.ONGOING,
            start_date=start_date,
            end_date=end_date,
            initial_capital=100_000_000,
        )

        # 유니버스 — 대회 시작 시점의 업종을 얼린다 (F-02 3.4)
        frozen_at = timezone.now()
        ContestUniverse.objects.bulk_create([
            ContestUniverse(
                contest=contest, symbol=symbol, name=name, market=market,
                sector_code=sector_code, sector_name=sector_name, frozen_at=frozen_at,
            )
            for symbol, name, market, sector_code, sector_name, _bp, _sh in FEATURED_STOCKS
        ])
        contest.universe_frozen_at = frozen_at
        contest.save(update_fields=["universe_frozen_at", "updated_at"])

        participations = [
            self._join_contest(contest, member, seq)
            for seq, member in enumerate(members, start=1)
        ]
        self._build_snapshots(contest, participations, business_days)

        self.stdout.write(self.style.SUCCESS(
            f"  · 데모 대회 1개 · 참가자 {len(participations)}명 · "
            f"스냅샷 {len(business_days)}영업일"
        ))

    def _join_contest(self, contest: Contest, member: Member, seq: int) -> Participation:
        """참가 승인 + 대회 계좌 생성.

        **대회 계좌는 참가 승인 시점에 만든다** (E-01 4장). 가입 시 만드는
        연습 계좌 3종과 생성 시점이 다르다는 것이 v2.0 계좌 모델의 핵심이다.
        """
        account = Account.objects.create(
            member=member,
            contest=contest,
            mode=AccountMode.CONTEST,
            cash=contest.initial_capital,
            initial_capital=contest.initial_capital,
        )
        return Participation.objects.create(
            contest=contest,
            member=member,
            account=account,
            nickname=(member.display_name or member.username)[:20] or f"참가자{seq}",
            status=ParticipationStatus.APPROVED,
            approved_at=timezone.now(),
        )

    def _recent_business_days(self, count: int) -> list[date]:
        """오늘부터 거슬러 올라가 영업일 `count` 개.

        `market.TradingCalendar` 가 채워져 있으면 그것을 쓰고, 비어 있으면
        **주말만 뺀 근사치**로 간다. `sync_trading_calendar` 는 네트워크가 필요한
        배치라 데모가 그것에 묶이면 안 된다 (05 문서 5.3).
        """
        from market.models import TradingCalendar

        today = today_kst()
        calendar_days = list(
            TradingCalendar.objects.filter(date__lte=today, is_open=True)
            .order_by("-date")
            .values_list("date", flat=True)[:count]
        )
        if len(calendar_days) == count:
            return sorted(calendar_days)

        days, cursor = [], today
        while len(days) < count:
            if cursor.weekday() < 5:      # 월(0)~금(4)
                days.append(cursor)
            cursor -= timedelta(days=1)
        return sorted(days)

    def _build_snapshots(self, contest, participations, business_days):
        """참가자별 20영업일 스냅샷 + 일자별 랭킹.

        NAV 는 시작 자본을 1000 으로 놓은 지수다. 참가자마다 다른 드리프트를 주어
        순위가 실제로 움직이게 만든다 — 순위가 고정이면 랭킹 화면을 검증할 수 없다.
        """
        prev_ranks: dict[int, int] = {}

        for day_index, day in enumerate(business_days):
            rows = []
            for participation in participations:
                rows.append(self._snapshot_for(contest, participation, day, day_index))

            snapshots = DailySnapshot.objects.bulk_create([r["snapshot"] for r in rows])

            holdings = []
            for snapshot, row in zip(snapshots, rows):
                for holding in row["holdings"]:
                    holding.snapshot = snapshot
                    holdings.append(holding)
            SnapshotHolding.objects.bulk_create(holdings)

            # 랭킹 — NAV 내림차순. **조회 시점에 정렬하지 않고 미리 써 둔다** (F-05 7장)
            rows.sort(key=lambda r: r["snapshot"].nav, reverse=True)
            ContestRanking.objects.bulk_create([
                ContestRanking(
                    contest=contest,
                    participation=row["snapshot"].participation,
                    date=day,
                    rank=rank,
                    prev_rank=prev_ranks.get(row["snapshot"].participation_id),
                    nav=row["snapshot"].nav,
                    cumulative_return_pct=row["snapshot"].cumulative_return_pct,
                    daily_return_pct=row["snapshot"].daily_return_pct,
                    position_count=row["snapshot"].position_count,
                    invested_ratio_pct=row["snapshot"].invested_ratio_pct,
                )
                for rank, row in enumerate(rows, start=1)
            ])
            prev_ranks = {
                row["snapshot"].participation_id: rank
                for rank, row in enumerate(rows, start=1)
            }

    def _snapshot_for(self, contest, participation, day: date, day_index: int) -> dict:
        """하루치 스냅샷 1행 + 보유 종목 행들을 만든다 (아직 저장하지 않는다)."""
        seed_base = participation.id * 31 + day_index
        rng = Random(RANDOM_SEED + seed_base)

        # 참가자별 고유 드리프트 — 누구는 꾸준히 오르고 누구는 내린다
        drift = ((participation.id % 7) - 3) * 0.0015
        cumulative = drift * (day_index + 1) + rng.uniform(-0.004, 0.004)
        nav = Decimal(1000) * (Decimal(1) + Decimal(str(round(cumulative, 6))))

        total_asset = int(contest.initial_capital * (1 + cumulative))
        picks = rng.sample(FEATURED_STOCKS, k=rng.randint(3, 6))
        invested_ratio = rng.uniform(0.6, 0.95)
        position_value = int(total_asset * invested_ratio)
        cash = total_asset - position_value

        holdings_json, holding_rows = [], []
        remaining = position_value
        for pick_index, (symbol, name, _mkt, sector_code, _sn, base_price, _sh) in enumerate(picks):
            # 마지막 종목이 잔여를 전부 가져가 합계가 정확히 맞는다
            value = remaining if pick_index == len(picks) - 1 else int(
                position_value * rng.uniform(0.1, 1.0 / len(picks) * 1.4)
            )
            value = max(0, min(value, remaining))
            remaining -= value

            close_price = int(base_price * (1 + rng.uniform(-0.08, 0.08)))
            avg_price = int(base_price * (1 + rng.uniform(-0.10, 0.06)))
            qty = max(1, value // max(close_price, 1))
            pnl = int((close_price - avg_price) * qty)
            weight_pct = round(value / total_asset * 100, 4) if total_asset else 0

            holdings_json.append({
                "symbol": symbol, "name": name, "qty": qty,
                "avg_price": avg_price, "close_price": close_price,
                "value": value, "weight_pct": weight_pct,
            })
            holding_rows.append(SnapshotHolding(
                snapshot=None,               # bulk_create 직전에 채운다
                contest=contest,
                participation=participation,
                date=day,
                symbol=symbol,
                name=name,
                sector_code=sector_code,
                qty=Decimal(qty),
                avg_price=Decimal(avg_price),
                close_price=Decimal(close_price),
                value=value,
                weight_pct=Decimal(str(weight_pct)),
                pnl=pnl,
                pnl_pct=Decimal(str(round(pnl / max(avg_price * qty, 1) * 100, 4))),
            ))

        snapshot = DailySnapshot(
            participation=participation,
            date=day,
            cash=cash,
            position_value=position_value,
            total_asset=total_asset,
            nav=nav.quantize(Decimal("0.0001")),
            daily_return_pct=Decimal(str(round(rng.uniform(-1.5, 1.5), 4))),
            cumulative_return_pct=Decimal(str(round(cumulative * 100, 4))),
            position_count=len(picks),
            invested_ratio_pct=Decimal(str(round(invested_ratio * 100, 4))),
            holdings=holdings_json,
            sector_weights=[],
            violations=[],
        )
        return {"snapshot": snapshot, "holdings": holding_rows}
