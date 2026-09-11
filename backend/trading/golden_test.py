"""체결 엔진 **골든 테스트** — `quant-core` 이식 대비 회귀 고정 (ADR-SC-0002).

★★ **이 파일이 왜 `tests.py` 와 따로 있는가** ────────────────────────────────

`trading/tests.py` 는 *의미*를 검증한다 — "여러 호가를 소진하면 가중평균이 된다".
이 파일은 *값*을 고정한다 — "이 입력에는 정확히 이 조각들이 나온다".

둘은 목적이 다르다. 체결 로직을 `quant-core` 로 옮길 때 필요한 것은
**이식본이 원본과 한 글자도 다르지 않다**는 증거이고, 그건 의미 서술이 아니라
값 대조로만 얻어진다. 이식 후 `quant-core` 쪽에서 같은 입력을 넣어
`__snapshots__/golden_test.ambr` 와 맞춰 보면 된다.

★★ **DB 를 쓰지 않는다** ──────────────────────────────────────────────────

여기 담긴 함수는 전부 순수 함수다 — ORM 도, 설정 조회도 없다. 그래서 픽스처를
`Orderbook`/`Quote` dataclass 로 직접 만든다. `quant-core` 는 Django 를 모르므로,
**DB 에 닿는 순간 그 테스트는 이식할 수 없다.**

    ORM 이 필요한 `apply_fills` 는 여기 없다. 그건 아직 순수 함수가 아니고,
    경계를 어디로 미는지가 `docs/how-to/quant-core-이식-경계표.md` 다.

★★ **`Decimal` 직렬화를 고정한다** ────────────────────────────────────────

스냅샷이 환경마다 흔들리면 없느니만 못하다. `float` 로 바꾸면 74300.0 과
74300.00000001 을 구분하지 못하고, `repr` 은 파이썬 버전에 묶인다.
**`str(Decimal)` 로 고정한다** — 유효숫자와 지수 표기가 그대로 보존된다.

    실행:  cd backend && .venv/bin/pytest trading/golden_test.py
    갱신:  ... --snapshot-update      ← 값이 바뀐 이유를 설명할 수 있을 때만
"""

from decimal import Decimal

import pytest

from core.constants import AssetClass, OrderSide
from market.quotes import Level, Orderbook, PriceUnavailable, Quote, price_limits, tick_size
from trading import services
from trading.models import OrderType

SYMBOL = "005930"
FETCHED_AT = "2026-08-17T09:30:00+09:00"   # 고정값. timezone.now() 를 쓰면 스냅샷이 흔들린다


# ─────────────────────────────────────────────────────────────────
# 직렬화 — 스냅샷에 들어가는 모양을 여기 한 곳에서만 정한다
# ─────────────────────────────────────────────────────────────────


def _j(value):
    """스냅샷용 JSON 안전 변환. **`Decimal` 은 `str` 로 고정한다.**"""
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, dict):
        return {key: _j(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_j(item) for item in value]
    return value


def dump_fill(fill) -> dict:
    return {
        "qty": _j(fill.qty),
        "price": _j(fill.price),
        "price_level": fill.price_level,
        "is_assumed_depth": fill.is_assumed_depth,
        "amount": fill.amount,          # int — 원 단위 절사가 이미 끝난 값
    }


def dump_result(result) -> dict:
    """`FillResult` 를 통째로 편다. **파생 속성까지 넣는다** — 조각만 맞고
    가중평균이 틀리는 이식 실수를 잡으려면 계산된 값도 고정해야 한다."""
    return {
        "fills": [dump_fill(fill) for fill in result.fills],
        "remaining_qty": _j(result.remaining_qty),
        "filled_qty": _j(result.filled_qty),
        "gross_amount": result.gross_amount,
        "avg_price": _j(result.avg_price),
        "has_assumed_depth": result.has_assumed_depth,
    }


# ─────────────────────────────────────────────────────────────────
# 픽스처 — DB 없이 손으로 만든 호가창
# ─────────────────────────────────────────────────────────────────


def make_book(asks=None, bids=None, symbol=SYMBOL) -> Orderbook:
    """`[(가격, 잔량), …]` 두 벌로 10단계 호가창을 만든다.

    빈 단계는 `(0, 0)` 으로 채운다 — `opposite`/`own` 이 가격 0 을 걸러내므로
    "매도호가가 통째로 빈 상한가 종목"을 이렇게 표현한다.
    """
    asks = asks if asks is not None else [(74_300, 60), (74_400, 40), (74_500, 100)]
    bids = bids if bids is not None else [(74_200, 50), (74_100, 80), (74_000, 120)]
    levels = []
    for index in range(max(len(asks), len(bids), 1)):
        ask = asks[index] if index < len(asks) else (0, 0)
        bid = bids[index] if index < len(bids) else (0, 0)
        levels.append(
            Level(
                ask_price=Decimal(ask[0]), ask_qty=Decimal(ask[1]),
                bid_price=Decimal(bid[0]), bid_qty=Decimal(bid[1]),
            )
        )
    return Orderbook(symbol=symbol, levels=levels, fetched_at=FETCHED_AT, is_stale=False)


def make_quote(price=74_300, prev_close=74_000) -> Quote:
    return Quote(
        symbol=SYMBOL, price=Decimal(price), prev_close=Decimal(prev_close),
        is_simulated=False, fetched_at=FETCHED_AT, is_stale=False,
    )


# ─────────────────────────────────────────────────────────────────
# 1. 거래비용 — `calc_cost` (F-03 6장)
# ─────────────────────────────────────────────────────────────────


def test_calc_cost_골든(snapshot):
    """수수료·매도세의 **절사 방향**과 소수 bp 처리를 고정한다.

    ★ `cost_rates(account)` 는 여기 없다 — 요율을 `Contest`·`AppSetting` 에서
      읽어 DB 가 필요하고, **이식 대상도 아니다**(ADR-SC-0004).
      순수한 것은 "요율을 받아 금액을 내는" `calc_cost` 뿐이다.
    """
    cases = {}
    for label, gross, side, fee_bp, tax_bp in [
        ("매수_기본_15bp",        1_000_000, OrderSide.BUY,  Decimal("15"),  Decimal("20")),
        ("매도_세금이_붙는다",     1_000_000, OrderSide.SELL, Decimal("15"),  Decimal("20")),
        ("소수bp_1.5bp",          1_000_000, OrderSide.SELL, Decimal("1.5"), Decimal("20")),
        ("절사_1원_미만은_버린다",     3_333, OrderSide.SELL, Decimal("1.5"), Decimal("1.5")),
        ("요율0_비용을_지어내지_않는다", 1_000_000, OrderSide.SELL, Decimal(0), Decimal(0)),
        ("대회요율_5bp_20bp",     7_430_000, OrderSide.SELL, Decimal("5"),   Decimal("20")),
    ]:
        fee, tax = services.calc_cost(gross, side, fee_bp, tax_bp)
        cases[label] = {"gross": gross, "fee": fee, "tax": tax}
    assert cases == snapshot


# ─────────────────────────────────────────────────────────────────
# 2. 지정가 결정 — `resolve_limit_price` (F-03 4장)
# ─────────────────────────────────────────────────────────────────


def test_resolve_limit_price_4종_골든(snapshot):
    """가격 유형 4종 × 매수/매도. **`None` 은 "제한 없음"이지 실패가 아니다.**"""
    book = make_book()
    quote = make_quote()
    cases = {}
    for label, order_type, side, level, limit in [
        ("상대호가_매수_1",  OrderType.RELATIVE, OrderSide.BUY,  1, None),
        ("상대호가_매수_3",  OrderType.RELATIVE, OrderSide.BUY,  3, None),
        ("상대호가_매도_1",  OrderType.RELATIVE, OrderSide.SELL, 1, None),
        ("자기호가_매수_1",  OrderType.OWN,      OrderSide.BUY,  1, None),
        ("자기호가_매수_2",  OrderType.OWN,      OrderSide.BUY,  2, None),
        ("자기호가_매도_1",  OrderType.OWN,      OrderSide.SELL, 1, None),
        ("지정가_그대로",    OrderType.LIMIT,    OrderSide.BUY,  None, Decimal(74_250)),
        ("시장가_제한없음",  OrderType.MARKET,   OrderSide.BUY,  None, None),
        ("STOP_제한없음",    OrderType.STOP,     OrderSide.SELL, None, None),
    ]:
        price = services.resolve_limit_price(
            order_type=order_type, side=side, price_level=level,
            limit_price=limit, book=book, quote=quote,
        )
        cases[label] = _j(price)
    assert cases == snapshot


def test_resolve_limit_price_상하한가_특례_골든(snapshot):
    """★ **상·하한가 특례** (F-03 4.1) — 한쪽 호가가 통째로 빌 때만 발동한다.

    상한가에 갇히면 파는 사람이 없어 매도호가가 빈다. 그때 자기호가 매도는
    갈 곳이 없는데, 규칙은 **상한가에 체결된다**고 본다. 하한가는 대칭이다.
    """
    quote = make_quote(prev_close=74_000)       # 상한 96,200 · 하한 51,800
    cases = {
        "참고_상하한가": _j(price_limits(Decimal(74_000))),
    }

    # 상한가 — 매도호가가 통째로 빈다
    upper_book = make_book(asks=[], bids=[(96_200, 500)])
    cases["상한가_자기호가_매도"] = _j(
        services.resolve_limit_price(
            order_type=OrderType.OWN, side=OrderSide.SELL, price_level=1,
            limit_price=None, book=upper_book, quote=quote,
        )
    )

    # 하한가 — 매수호가가 통째로 빈다
    lower_book = make_book(asks=[(51_800, 500)], bids=[])
    cases["하한가_자기호가_매수"] = _j(
        services.resolve_limit_price(
            order_type=OrderType.OWN, side=OrderSide.BUY, price_level=1,
            limit_price=None, book=lower_book, quote=quote,
        )
    )

    # ★ 특례는 OWN 에만 걸린다. 상대호가는 "받아 줄 사람이 없다"는 뜻이라
    #   거부하지 않고 미체결로 남긴다 — 예외 종류까지 고정한다.
    try:
        services.resolve_limit_price(
            order_type=OrderType.RELATIVE, side=OrderSide.SELL, price_level=1,
            limit_price=None, book=make_book(asks=[(74_300, 10)], bids=[]), quote=quote,
        )
        cases["상대호가가_비면"] = "예외가 나지 않았다"
    except PriceUnavailable as exc:
        cases["상대호가가_비면"] = {
            "예외": type(exc).__name__, "recoverable": exc.recoverable,
        }
    assert cases == snapshot


# ─────────────────────────────────────────────────────────────────
# 3. 호가 소진 체결 — `match_orderbook` (F-03 5.1)
# ─────────────────────────────────────────────────────────────────


def test_match_orderbook_10호가_소진_골든(snapshot):
    """실제 호가 1~10단계만으로 끝나는 경우들."""
    book = make_book()
    cases = {}
    for label, side, limit, qty in [
        ("최우선호가_안이면_한_조각",  OrderSide.BUY,  Decimal(74_500), Decimal(50)),
        ("여러_호가_가중평균",         OrderSide.BUY,  Decimal(74_500), Decimal(120)),
        ("지정가를_넘으면_멈춘다",     OrderSide.BUY,  Decimal(74_300), Decimal(200)),
        ("매도는_비싼_매수호가부터",   OrderSide.SELL, Decimal(74_000), Decimal(150)),
        ("시장가는_전부_먹는다",       OrderSide.BUY,  None,            Decimal(200)),
    ]:
        result = services.match_orderbook(
            book=book, side=side, limit_price=limit, qty=qty, quote=None,
        )
        cases[label] = dump_result(result)
    assert cases == snapshot


def test_match_orderbook_호가순서_무관_골든(snapshot):
    """★ 캐시 JSON 배열 순서가 뒤집혀 들어와도 **결과가 같아야 한다.**

    `Orderbook.opposite()` 가 매번 정렬하기 때문인데, 이식하면서 그 정렬을
    빠뜨리기 쉽다. 두 결과가 같은 스냅샷을 가리키도록 묶어 둔다.
    """
    normal = make_book(asks=[(74_300, 60), (74_400, 40), (74_500, 100)])
    shuffled = make_book(asks=[(74_500, 100), (74_300, 60), (74_400, 40)])
    a = dump_result(services.match_orderbook(
        book=normal, side=OrderSide.BUY, limit_price=Decimal(74_500), qty=Decimal(120)))
    b = dump_result(services.match_orderbook(
        book=shuffled, side=OrderSide.BUY, limit_price=Decimal(74_500), qty=Decimal(120)))
    assert a == b                    # 먼저 서로 같음을 못박고
    assert a == snapshot             # 그다음 값 자체를 고정한다


def test_match_orderbook_잔량0_단계는_건너뛴다_골든(snapshot):
    """가격은 있는데 잔량이 0 인 단계 — `continue` 지 `break` 가 아니다."""
    book = make_book(asks=[(74_300, 0), (74_400, 40), (74_500, 100)])
    result = services.match_orderbook(
        book=book, side=OrderSide.BUY, limit_price=Decimal(74_500), qty=Decimal(60))
    assert dump_result(result) == snapshot


# ─────────────────────────────────────────────────────────────────
# 4. 11호가 이후 **가정 체결** — `_fill_assumed_depth` (F-03 5.1 4단계)
# ─────────────────────────────────────────────────────────────────
#
# ★★ 이 규칙은 문서로도 남겼다 → `docs/how-to/quant-core-이식-경계표.md` §3.
#    "평균 잔량이 1틱 간격으로 무한히 이어진다"는 **실제 시장보다 유리한 가정**이고,
#    이식본이 이 가정을 그대로 재현하지 않으면 백테스트 성과가 조용히 달라진다.


def test_가정체결_골든(snapshot):
    """호가를 다 먹고도 남으면 11호가부터를 가정해 잇는다."""
    book = make_book(asks=[(74_300, 60), (74_400, 40), (74_500, 30)])   # 평균 잔량 43 → 43주
    quote = make_quote(prev_close=74_000)
    cases = {}

    result = services.match_orderbook(
        book=book, side=OrderSide.BUY, limit_price=None, qty=Decimal(300), quote=quote)
    cases["매수_가정체결"] = dump_result(result)

    # ★ 상한가가 벽이 된다 — 시장가는 지정가로 멈추지 않으므로 이것이 유일한 제동이다.
    tight = make_book(asks=[(96_000, 10), (96_100, 10)])
    cases["상한가에서_멈춘다"] = dump_result(services.match_orderbook(
        book=tight, side=OrderSide.BUY, limit_price=None, qty=Decimal(10_000), quote=quote))

    # ★ 호가창이 아예 비면 가정도 하지 않는다 — 근거가 없으면 지어내지 않는다.
    cases["호가창이_비면"] = dump_result(services.match_orderbook(
        book=make_book(asks=[], bids=[(74_000, 10)]), side=OrderSide.BUY,
        limit_price=None, qty=Decimal(100), quote=quote))

    assert cases == snapshot


def test_가정체결_수량단위_골든(snapshot):
    """★ 평균 잔량은 **자산군 단위로 절사한다** (변경노트 E-38).

    60·40·30 의 평균은 43.33 이다. 그대로 쓰면 *주식이 43.33주 체결되는 조각*이
    생긴다 — 실측에서 실제로 나왔던 값이라 이식본에서도 반드시 재현돼야 한다.
    """
    book = make_book(asks=[(74_300, 60), (74_400, 40), (74_500, 30)])
    quote = make_quote(prev_close=74_000)
    cases = {}
    for label, asset_class in [("주식은_정수주", AssetClass.STOCK),
                               ("코인은_소수허용", AssetClass.CRYPTO)]:
        result = services.match_orderbook(
            book=book, side=OrderSide.BUY, limit_price=None, qty=Decimal(200),
            quote=quote, asset_class=asset_class,
        )
        cases[label] = dump_result(result)
    assert cases == snapshot


# ─────────────────────────────────────────────────────────────────
# 5. 단일가 전량 체결 — `match_at_price` (F-03 5.2)
# ─────────────────────────────────────────────────────────────────


def test_match_at_price_골든(snapshot):
    """연습 모드와 추종 체결이 쓰는 경로. 호가창을 보지 않는다."""
    cases = {
        "전량_한_조각": dump_result(
            services.match_at_price(price=Decimal(74_300), qty=Decimal(10))),
        "수량0은_빈_결과": dump_result(
            services.match_at_price(price=Decimal(74_300), qty=Decimal(0))),
        "가격0은_빈_결과": dump_result(
            services.match_at_price(price=Decimal(0), qty=Decimal(10))),
        "코인_소수수량": dump_result(
            services.match_at_price(price=Decimal("95000000.5"), qty=Decimal("0.00123456"))),
    }
    assert cases == snapshot


# ─────────────────────────────────────────────────────────────────
# 6. 호가단위·상하한가 경계 — `tick_size` / `price_limits`
# ─────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("price", [1_000, 1_999, 2_000, 4_999, 5_000, 19_999, 20_000,
                                   49_999, 50_000, 199_999, 200_000, 499_999, 500_000])
def test_tick_size_경계_골든(price, snapshot):
    """★ **경계값의 전날/당일에 해당하는 것**이 가격대 경계다.
    각 구간의 마지막 값과 다음 구간 첫 값을 나란히 고정한다."""
    assert {"price": price, "tick": _j(tick_size(Decimal(price)))} == snapshot


def test_price_limits_골든(snapshot):
    """±30% 후 **호가단위로 절사**. 절사 방향이 뒤집히면 특례 판정이 흔들린다."""
    cases = {
        str(prev): _j(price_limits(Decimal(prev)))
        for prev in [74_000, 1_000, 5_000, 199_999, 0]
    }
    assert cases == snapshot
