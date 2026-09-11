"""홈 대시보드 데이터 조립 (F-21).

★★ **이 파일의 유일한 규칙: 외부 API 를 부르지 않는다** ────────────────────

F-21 4장 — 홈은 **가장 많이 열리는 화면**이다. 여기서 KIS·업비트·pykrx 를 한 번이라도
부르면 방문자 수만큼 외부 호출이 나가고, KIS 의 초당 5건 유량이 순식간에 마른다.

    시세  → `market.QuoteCache` (잡 1 이 채운다)
    지수  → `market.IndexCache` (잡 1 이 채운다)
    코인  → `market.CryptoRank` (매시 잡이 채운다)
    순위  → `contests.ContestRanking` (잡 4·7 이 채운다)

**전부 우리 DB 의 테이블이다.** 비어 있으면 비어 있다고 화면에 적는다 —
빈 값을 채우려고 여기서 외부를 부르지 않는다 (그 일은 잡의 몫이다).

★ 순위·수익률을 **조회 시점에 계산하지 않는다** (F-05 7장). 랭킹은 잡이 만든
  스냅샷이 정본이고, 화면이 제 나름대로 계산하면 홈과 랭킹 화면이 서로 다른
  숫자를 말하게 된다. 여기서는 `ContestRanking` 을 **읽기만** 한다.

Django 관점 ─────────────────────────────────────────────────────────────────

Next.js 였다면 각 위젯이 자기 `fetch` 를 갖고 Suspense 로 따로 로딩됐다. HTMX 도
결과는 같지만(위젯별 독립 갱신) **조립은 서버에서** 한다. 그래서 "쿼리를 몇 번
날렸는가" 가 그대로 응답 시간이 된다 — 아래 함수들이 `select_related` 와
"한 번에 읽어 딕셔너리로 나누기" 를 반복하는 이유다.
"""

from dataclasses import dataclass, field
from decimal import Decimal

from django.db.models import Count, Q

from accounts.models import Account
from contests import scoring
from contests.models import (
    Contest,
    ContestRanking,
    ContestStatus,
    DailySnapshot,
    Participation,
    ParticipationStatus,
    WeeklyTurnover,
)
from contests.services import evaluate_account
from core.constants import AccountMode, AssetClass
from core.time import today_kst, week_start_kst
from market.models import CryptoRank, IndexCache, QuoteCache
from market.quotes import get_quotes

# 홈에 띄우는 개수. 화면이 길어지지 않게 자른다.
CONTEST_CARD_LIMIT = 4
MOVER_LIMIT = 5
CRYPTO_LIMIT = 10

# 지수 코드 — `sync_market_index` 가 넣는 값과 같아야 한다.
INDEX_CODES = ("KOSPI", "KOSDAQ")

# 비로그인 홈의 기능 소개 카드 (F-21 2.1). `(제목, 설명, 아이콘)`.
# ★ 템플릿에 하드코딩하지 않고 여기 두는 이유 — 문구가 화면 여러 곳(홈·소개·
#   빈 상태 안내)에서 반복되고, 기능이 늘면 한 곳만 고치면 되기 때문이다.
FEATURE_CARDS: tuple[tuple[str, str, str], ...] = (
    ("모의투자 대회", "실제 호가로 체결. 편입 한도·섹터 한도·주간 회전율 규칙이 적용됩니다.", "🏆"),
    ("연습 거래", "주식 · 코인 · 대체자산을 각각 1억원으로. 계좌가 분리돼 실력을 따로 볼 수 있습니다.", "📊"),
    ("투자 학습", "투자분석 14레슨과 KB · KIS · TradingView · Pine 가이드.", "📚"),
    ("AI 분석", "내 포트폴리오와 대회 맥락을 읽고 분석합니다. 지식 검색(RAG) 포함.", "🤖"),
)


# ─────────────────────────────────────────────────────────────────
# 1. 내 계좌 요약 (F-21 2.1 "내 계좌 요약")
# ─────────────────────────────────────────────────────────────────


@dataclass
class AccountCard:
    """계좌 미니 카드 하나."""

    account: Account
    label: str
    total_asset: int
    profit: int
    profit_pct: Decimal
    position_count: int
    # 정직성 표시 (U-01 5장) — 이 계좌의 평가액이 얼마나 믿을 만한가
    simulated_symbols: list[str] = field(default_factory=list)
    estimated_symbols: list[str] = field(default_factory=list)
    is_stale: bool = False

    @property
    def is_contest(self) -> bool:
        return self.account.mode == AccountMode.CONTEST

    @property
    def has_caveat(self) -> bool:
        """평가액에 단서가 붙는가 — 화면이 배지를 띄울지 결정한다."""
        return bool(self.simulated_symbols or self.estimated_symbols or self.is_stale)


def _price_map(asset_class: str, symbols: list[str]) -> tuple[dict, set, bool]:
    """`(가격 맵, 시뮬레이션 종목, 낡은 값 포함 여부)`.

    ★ **시뮬레이션 가격을 걸러내지 않는다.** 연습 모드는 그 값으로 실제 체결까지
      하고 있으므로(F-16 4.1 — 거부는 대회만), 평가액에서만 빼면 화면의 숫자와
      계좌의 현실이 어긋난다. **쓰되 배지로 알린다**가 이 프로젝트의 방침이다.
    """
    if not symbols:
        return {}, set(), False
    quotes = get_quotes(asset_class, symbols)
    prices = {symbol: quote.price for symbol, quote in quotes.items()}
    simulated = {symbol for symbol, quote in quotes.items() if quote.is_simulated}
    is_stale = any(quote.is_stale for quote in quotes.values())
    return prices, simulated, is_stale


def account_cards(member) -> list[AccountCard]:
    """내 계좌 전부(연습 3종 + 참가 중인 대회 계좌)를 평가한다.

    ★ 쿼리 수를 자산군 수(최대 3)로 묶는다. 계좌마다 시세를 조회하면 계좌 4개 ×
      종목 수만큼 쿼리가 난다(N+1). 자산군별로 종목을 모아 **한 번씩** 읽는다.
    """
    accounts = list(
        Account.objects.filter(member=member)
        .select_related("contest")
        .order_by("mode", "pk")
    )
    if not accounts:
        return []

    # 계좌 → 포지션. 한 번의 쿼리로 전부 읽어 계좌별로 나눈다.
    from trading.models import Position     # noqa: PLC0415 — 지연 임포트(순환 회피)

    positions_by_account: dict[int, list] = {account.pk: [] for account in accounts}
    symbols_by_class: dict[str, set[str]] = {}
    for position in Position.objects.filter(account__in=accounts).order_by("symbol"):
        positions_by_account[position.account_id].append(position)
        symbols_by_class.setdefault(position.asset_class, set()).add(position.symbol)

    # 자산군별로 시세를 한 번씩 읽는다.
    prices_by_class, simulated_by_class, stale_by_class = {}, {}, {}
    for asset_class, symbols in symbols_by_class.items():
        prices, simulated, is_stale = _price_map(asset_class, sorted(symbols))
        prices_by_class[asset_class] = prices
        simulated_by_class[asset_class] = simulated
        stale_by_class[asset_class] = is_stale

    cards = []
    for account in accounts:
        asset_class = account.asset_class
        prices = prices_by_class.get(asset_class, {})
        simulated = simulated_by_class.get(asset_class, set())
        positions = positions_by_account[account.pk]

        # ★ `evaluate_account` 를 재사용한다 — 정산 잡이 쓰는 것과 **같은 산식**이다.
        #   홈에서 따로 계산하면 "홈에서는 +8.2%, 포트폴리오에서는 +8.1%" 가 된다.
        #   `names` 를 비워 넘기면 종목명 없이 숫자만 나온다 (카드에는 이름이 필요 없다).
        valuation = evaluate_account(account, positions, prices, {})

        profit = valuation.total_asset - account.initial_capital
        cards.append(
            AccountCard(
                account=account,
                label=(
                    f"대회: {account.contest.name}"
                    if account.is_contest and account.contest
                    else account.get_mode_display()
                ),
                total_asset=valuation.total_asset,
                profit=profit,
                profit_pct=scoring.ratio_pct(profit, account.initial_capital),
                position_count=len(positions),
                simulated_symbols=sorted(
                    simulated & {position.symbol for position in positions}
                ),
                estimated_symbols=sorted(set(valuation.estimated_symbols)),
                is_stale=stale_by_class.get(asset_class, False),
            )
        )
    return cards


# ─────────────────────────────────────────────────────────────────
# 2. 내 대회 현황 (F-21 3.1 — 가장 중요한 위젯)
# ─────────────────────────────────────────────────────────────────


@dataclass
class MyContestCard:
    """참가 중인 대회 하나의 현황."""

    participation: Participation
    contest: Contest
    d_day: int
    ranking: ContestRanking | None
    snapshot: DailySnapshot | None
    turnover: WeeklyTurnover | None
    turnover_minimum_pct: Decimal
    violations: list[dict]

    @property
    def rank_delta(self) -> int | None:
        """전일 대비 순위 변동. 양수면 올랐다(등수 숫자가 줄었다)."""
        if not self.ranking or not self.ranking.prev_rank or not self.ranking.rank:
            return None
        return self.ranking.prev_rank - self.ranking.rank

    @property
    def turnover_warning(self) -> bool:
        """회전율 경고 — F-21 3.1 이 노란색으로 강조하라고 한 조건.

        ★ **이번 주 잠정치(`is_confirmed=False`)에도 경고를 띄운다.** 확정된 뒤에
          알려주면 이미 늦었다 — 회전율은 주중에 매매를 해야 채울 수 있다.
          다만 화면에는 "예상" 임을 함께 적는다 (WeeklyTurnover 주석 참조).
        """
        return bool(self.turnover and self.turnover.is_violation)

    @property
    def has_limit_violation(self) -> bool:
        """편입·섹터 한도 초과 상태인가 — 빨간색 경고."""
        return bool(self.violations)

    @property
    def violation_lines(self) -> list[str]:
        """위반 JSONB → 화면에 그대로 찍을 문장.

        ★★ **왜 템플릿이 아니라 여기서 만드는가** ─────────────────────────────

        `DailySnapshot.violations` 는 규칙 종류마다 **키가 다르다**
        (`contests.services.evaluate_violations` 참조).

            종목 한도  → symbol · name
            섹터 한도  → sector_code · sector_name
            소형주 한도 → 둘 다 없음

        템플릿에서 `{{ v.detail|default:v.symbol }}` 처럼 짜면, 키가 없는 종류가
        왔을 때 **`VariableDoesNotExist` 로 렌더가 통째로 터진다.** (실제로 겪었다.)
        Django 템플릿은 필터 인자를 조용히 넘기지 않는다.

        모양이 제각각인 데이터를 **한 모양으로 정리하는 것은 서비스 계층의 일**이다.
        템플릿은 문자열을 받아 그리기만 하면 된다.
        """
        from contests.models import ViolationRule      # noqa: PLC0415

        lines = []
        for item in self.violations:
            if not isinstance(item, dict):
                continue
            try:
                label = ViolationRule(item.get("rule")).label
            except ValueError:
                # 규칙 종류가 늘었는데 여기가 못 따라온 경우 — 원문이라도 보여준다.
                label = str(item.get("rule") or "규칙 위반")

            subject = (
                item.get("name")
                or item.get("symbol")
                or item.get("sector_name")
                or item.get("sector_code")
                or ""
            )
            current, limit = item.get("current_pct"), item.get("limit_pct")
            if current is not None and limit is not None:
                detail = f"{subject} {current}% (한도 {limit}%)".strip()
            else:
                detail = subject or item.get("detail") or ""

            lines.append(f"{label} — {detail}" if detail else label)
        return lines


def my_contest_cards(member) -> list[MyContestCard]:
    """참가 중인(승인된) 진행 대회들의 현황.

    여러 대회에 동시 참가 중이면 **종료가 임박한 순서**로 준다 (F-21 3.1).
    """
    today = today_kst()
    participations = list(
        Participation.objects.filter(
            member=member,
            status=ParticipationStatus.APPROVED,
            contest__status__in=[ContestStatus.ONGOING, ContestStatus.SETTLING],
        )
        .select_related("contest")
        .order_by("contest__end_date")
    )
    if not participations:
        return []

    ids = [participation.pk for participation in participations]

    # ★ 참가별 "가장 최근" 행 3종을 각각 한 번의 쿼리로 읽는다.
    #   참가마다 `.filter(...).first()` 를 부르면 참가 수 × 3 번 쿼리가 난다.
    #   전부 읽어 파이썬에서 첫 행만 취한다 — 홈에 뜨는 대회는 많아야 서너 개다.
    latest_ranking: dict[int, ContestRanking] = {}
    for row in ContestRanking.objects.filter(participation_id__in=ids).order_by(
        "participation_id", "-date"
    ):
        latest_ranking.setdefault(row.participation_id, row)

    latest_snapshot: dict[int, DailySnapshot] = {}
    for row in DailySnapshot.objects.filter(participation_id__in=ids).order_by(
        "participation_id", "-date"
    ):
        latest_snapshot.setdefault(row.participation_id, row)

    # 회전율은 **이번 주** 행을 본다. 지난 주 확정치가 아니라 지금 채워야 할 값이다.
    this_week = week_start_kst(today)
    turnovers = {
        row.participation_id: row
        for row in WeeklyTurnover.objects.filter(
            participation_id__in=ids, week_start=this_week
        )
    }

    cards = []
    for participation in participations:
        contest = participation.contest
        snapshot = latest_snapshot.get(participation.pk)
        rule_set = contest.rule_set or {}
        cards.append(
            MyContestCard(
                participation=participation,
                contest=contest,
                d_day=(contest.end_date - today).days,
                ranking=latest_ranking.get(participation.pk),
                snapshot=snapshot,
                turnover=turnovers.get(participation.pk),
                turnover_minimum_pct=Decimal(str(rule_set.get("weekly_turnover_min_pct", 5))),
                # `DailySnapshot.violations` 는 정산 시점에 굳혀 둔 JSONB 다.
                # 여기서 다시 판정하지 않는다 (F-05 7장).
                violations=(snapshot.violations if snapshot else []) or [],
            )
        )
    return cards


# ─────────────────────────────────────────────────────────────────
# 3. 공개 대회 카드 (비로그인·미참가 사용자용)
# ─────────────────────────────────────────────────────────────────


@dataclass
class PublicContestCard:
    contest: Contest
    participant_count: int
    d_day: int
    top_return_pct: Decimal | None
    is_recruiting: bool


def public_contest_cards(*, limit: int = CONTEST_CARD_LIMIT) -> list[PublicContestCard]:
    """진행 중 · 모집 중 대회 (F-21 2.1).

    ★ `visibility=PUBLIC` 만 보여준다. `LINK`(링크 있는 사람만)·`PRIVATE` 대회가
      홈에 뜨면 비공개의 의미가 없다.
    """
    from contests.models import Visibility        # noqa: PLC0415

    today = today_kst()
    contests = list(
        Contest.objects.filter(
            status__in=[ContestStatus.ONGOING, ContestStatus.UPCOMING],
            visibility=Visibility.PUBLIC,
        )
        # ★ 참가자 수를 SQL 의 COUNT 로 센다. 대회마다 `participations.count()` 를
        #   부르면 대회 수만큼 쿼리가 난다(N+1). `filter=` 를 주면
        #   `COUNT(*) FILTER (WHERE status = 'APPROVED')` 로 번역돼 **승인된 참가만** 센다.
        .annotate(
            participant_count=Count(
                "participations",
                filter=Q(participations__status=ParticipationStatus.APPROVED),
            )
        )
        .order_by("start_date")[:limit]
    )
    if not contests:
        return []

    # 대회별 1위 수익률 — 최신 날짜의 1위 행만 읽는다.
    top_by_contest: dict[int, Decimal] = {}
    for row in ContestRanking.objects.filter(
        contest__in=contests, rank=1
    ).order_by("contest_id", "-date"):
        top_by_contest.setdefault(row.contest_id, row.cumulative_return_pct)

    cards = [
        PublicContestCard(
            contest=contest,
            participant_count=contest.participant_count,
            d_day=(contest.end_date - today).days,
            top_return_pct=top_by_contest.get(contest.pk),
            is_recruiting=contest.status == ContestStatus.UPCOMING,
        )
        for contest in contests
    ]
    # 진행 중을 위로.
    cards.sort(key=lambda card: (card.is_recruiting, card.contest.start_date))
    return cards


# ─────────────────────────────────────────────────────────────────
# 4. 시장 현황 (F-21 3.2)
# ─────────────────────────────────────────────────────────────────


@dataclass
class MarketOverview:
    indices: list[IndexCache]
    gainers: list[QuoteCache]
    losers: list[QuoteCache]
    cryptos: list[CryptoRank]
    # 화면이 "언제 값인지" 를 말할 수 있게 한다 (U-01 5장).
    quote_fetched_at: object = None
    has_simulated: bool = False

    @property
    def is_empty(self) -> bool:
        """캐시가 통째로 비었는가.

        ★ **빈 것을 빈 채로 알린다.** v1.0 은 KRX 뉴스 수집이 실패하면 조용히 빈
          배열을 반환해 화면이 아무 말 없이 비었다(01-HTMX 규약 7.2 의 교훈).
          여기서는 "아직 시세를 받지 못했습니다" 를 화면에 띄우는 근거가 된다.
        """
        return not (self.indices or self.gainers or self.losers or self.cryptos)


def market_overview() -> MarketOverview:
    """지수 · 주식 상승/하락 TOP · 코인 TOP.

    ★ **v1.0 의 등락률 절대값 정렬을 버린다** (F-21 3.2). 상승과 하락이 섞여 나와
      "지금 뭐가 오르나" 를 알 수 없었다. 상승 TOP 5 / 하락 TOP 5 로 나눈다.
    """
    indices = list(IndexCache.objects.filter(index_code__in=INDEX_CODES).order_by("index_code"))

    # 주식 현재가 캐시에서 상승·하락 상위. **거래량이 0 인 종목은 뺀다** —
    # 거래가 없는데 등락률만 큰 종목(관리·정리매매)이 올라오면 오해를 부른다.
    stock_quotes = QuoteCache.objects.filter(
        asset_class=AssetClass.STOCK, price__gt=0, volume__gt=0
    )
    gainers = list(stock_quotes.order_by("-change_pct")[:MOVER_LIMIT])
    losers = list(stock_quotes.order_by("change_pct")[:MOVER_LIMIT])

    cryptos = list(CryptoRank.objects.order_by("rank")[:CRYPTO_LIMIT])

    movers = gainers + losers
    return MarketOverview(
        indices=indices,
        gainers=gainers,
        losers=losers,
        cryptos=cryptos,
        # 가장 오래된 값을 대표로 삼는다 — "최소한 이만큼은 최신" 이라고 말할 수 있다.
        quote_fetched_at=min((quote.fetched_at for quote in movers), default=None),
        has_simulated=any(quote.is_simulated for quote in movers),
    )


# ─────────────────────────────────────────────────────────────────
# 5. 학습 진행률 (F-21 3.3)
# ─────────────────────────────────────────────────────────────────


@dataclass
class LearningSummary:
    earned: int
    total: int
    last_lesson: object = None

    @property
    def percent(self) -> int:
        return round(self.earned * 100 / self.total) if self.total else 0


def learning_summary(member) -> LearningSummary:
    """`32 / 100 포인트 · 이어서 학습하기` 위젯.

    ★ 분모는 `Lesson.max_points` 의 합이다. 상수 100 을 박아 두면 레슨을 추가한
      순간 진행률이 거짓말이 된다.
    """
    from django.db.models import Sum                    # noqa: PLC0415
    from learning.models import LearningProgress, Lesson  # noqa: PLC0415

    total = Lesson.objects.aggregate(total=Sum("max_points"))["total"] or 0
    rows = LearningProgress.objects.filter(member=member).select_related("lesson")
    earned = sum(row.progress_points for row in rows)
    # "이어서 학습하기" — 가장 최근에 손댄 레슨.
    last = max(rows, key=lambda row: row.updated_at, default=None)
    return LearningSummary(
        earned=earned, total=total, last_lesson=last.lesson if last else None
    )


# ─────────────────────────────────────────────────────────────────
# 6. 홈 전체 조립
# ─────────────────────────────────────────────────────────────────


def home_context(user) -> dict:
    """홈 화면 한 벌.

    로그인 여부로 구성이 갈린다 (F-21 2.1):

        비로그인      히어로 + 공개 대회 + 시장 현황 + 기능 소개
        로그인·참가중  내 대회 현황 + 내 계좌 + 시장 현황 + 학습
        로그인·미참가  (내 대회 자리에) 모집 중 대회 + 참가 버튼
    """
    context = {
        "market": market_overview(),
        "today": today_kst(),
    }
    if not (user and user.is_authenticated):
        context["public_contests"] = public_contest_cards()
        context["features"] = FEATURE_CARDS
        return context

    my_contests = my_contest_cards(user)
    context["my_contests"] = my_contests
    context["account_cards"] = account_cards(user)
    context["learning"] = learning_summary(user)
    # 참가 중인 대회가 없으면 모집 중 대회를 대신 보여준다 (F-21 2.1 세 번째 경우).
    if not my_contests:
        context["public_contests"] = public_contest_cards()
    return context
