"""`sector/aggregate.py` 골든·성질 테스트.

🔒 **실제 KRX 데이터를 픽스처로 쓰지 않는다** (제약 10 · ADR-SC-0006).
   전부 합성이고, 값은 손으로 검산할 수 있을 만큼 단순하게 골랐다.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from sector.aggregate import BASE_IDX_BP, aggregate
from sector.sector_master import Instrument, Sector, SectorMaster

FETCHED_AT = "2026-09-11T00:00:00Z"


# ── 합성 값 객체 ─────────────────────────────────────────────────────────────
# 어댑터의 dataclass 는 필드가 많다. 테스트가 읽히려면 **관심 있는 것만** 적어야
# 하므로 나머지를 채우는 생성기를 둔다.

def etf(bas_dd, code, *, fluc_rt_bp=0, net_asset=1_000_000, shares=1_000,
        trdval=500_000_000, close=None, nav=None, name="합성 ETF"):
    from sector.sources.krx_openapi import EtfDailyQuote
    return EtfDailyQuote(
        bas_dd=bas_dd, isu_cd=code, isu_nm=name,
        close_prc=close, chg_prc=None, fluc_rt_bp=fluc_rt_bp,
        nav=Decimal(nav) if nav is not None else None,
        open_prc=None, high_prc=None, low_prc=None,
        acc_trdvol=None, acc_trdval=trdval, mktcap=None,
        net_asset_total=net_asset, list_shrs=shares,
        idx_nm=None, idx_close=None, idx_chg=None, idx_fluc_rt_bp=None,
    )


def stock(bas_dd, code, *, fluc_rt_bp=0, mktcap=1_000_000, close=10_000,
          mkt="KOSPI", name="합성 종목"):
    from sector.sources.krx_stock import StockDailyQuote
    return StockDailyQuote(
        bas_dd=bas_dd, isu_cd=code, isu_nm=name, mkt_nm=mkt, sect_tp_nm=None,
        close_prc=close, chg_prc=None, fluc_rt_bp=fluc_rt_bp,
        open_prc=None, high_prc=None, low_prc=None,
        acc_trdvol=None, acc_trdval=None, mktcap=mktcap, list_shrs=None,
    )


def master(*sectors: Sector) -> SectorMaster:
    return SectorMaster(
        version="2026-09-11", source_notice="한국거래소 통계정보",
        gics_sectors=(), sectors=sectors, config_sha256="0" * 64,
    )


ONE_ETF = master(Sector(
    id="semi", name_ko="반도체", gics="Information Technology",
    note="합성 테스트용 섹터다. 근거는 테스트 코드가 말한다.",
    etfs=(Instrument("091160", "합성 반도체 ETF"),),
    members=(Instrument("005930", "합성 대형주"),),
))


def days(n, start=1):
    """`20260101` 부터 n 일. 달력 경계를 넘지 않게 1월만 쓴다."""
    return [f"202601{d:02d}" for d in range(start, start + n)]


def run(frames, cfg=ONE_ETF, *, as_of=None):
    as_of = as_of if as_of is not None else frames[-1][0]
    return aggregate(frames, cfg, as_of=as_of, fetched_at=FETCHED_AT)


# ── 체인연결 ─────────────────────────────────────────────────────────────────

def test_지수가_수익률을_정확히_곱한다():
    """+10%, +10% 면 121.00 이다. 🔒 float 반올림이 끼면 12099 가 나온다."""
    frames = [
        (d, [etf(d, "091160", fluc_rt_bp=1000)], [])
        for d in days(3)
    ]
    rows = run(frames).sectors
    # 첫날은 전일 순자산이 없어 가중할 수 없다 → 전진하지 않는다
    assert rows[0].etf_idx_bp is None
    assert rows[1].etf_idx_bp == 11_000    # 100 × 1.10
    assert rows[2].etf_idx_bp == 12_100    # 100 × 1.10 × 1.10  ← 정확히 121.00


def test_기준일은_10000bp다():
    frames = [(d, [etf(d, "091160", fluc_rt_bp=0)], []) for d in days(2)]
    assert run(frames).sectors[1].etf_idx_bp == BASE_IDX_BP


def test_액면분할이_지수를_흔들지_않는다():
    """🔴 이 테스트가 `FLUC_RT` 를 쓰는 이유 전체다.

    종가가 20000 → 10000 으로 반토막나도(2:1 분할) 거래소가 준 등락률은 0% 다.
    종가 비율로 계산했다면 -50% 가 찍혔을 자리에 0 이 와야 한다.
    """
    d1, d2, d3 = days(3)
    frames = [
        (d1, [], [stock(d1, "005930", fluc_rt_bp=0, close=20_000)]),
        (d2, [], [stock(d2, "005930", fluc_rt_bp=0, close=10_000)]),  # 분할일
        (d3, [], [stock(d3, "005930", fluc_rt_bp=100, close=10_100)]),
    ]
    rows = run(frames).sectors
    assert rows[1].member_ret_1d_bp == 0        # -5000 이 아니다
    assert rows[2].member_idx_bp == 10_100      # 분할과 무관하게 +1%


# ── 🔒 룩어헤드 ──────────────────────────────────────────────────────────────

def test_asof_monotone():
    """🔒 계획서 D-2 ⑤ — `data[:T+30]` 의 `row(T)` 와 `data[:T]` 의 값이 같아야 한다.

    같지 않다면 미래가 과거로 새고 있다는 뜻이고, 백테스트가 전부 거짓이 된다.
    """
    all_days = days(28)
    frames = [
        (
            d,
            [etf(d, "091160", fluc_rt_bp=(i * 37) % 500 - 250, net_asset=1_000_000 + i)],
            [stock(d, "005930", fluc_rt_bp=(i * 53) % 600 - 300)],
        )
        for i, d in enumerate(all_days)
    ]
    cut = 22
    short = run(frames[:cut]).sectors
    long_ = aggregate(frames, ONE_ETF, as_of=all_days[cut - 1], fetched_at=FETCHED_AT).sectors
    assert len(short) == len(long_) == cut
    assert short == long_          # dataclass 전 필드 비교


def test_as_of_이후는_버린다():
    all_days = days(5)
    frames = [(d, [etf(d, "091160")], []) for d in all_days]
    result = aggregate(frames, ONE_ETF, as_of=all_days[2], fetched_at=FETCHED_AT)
    assert result.bas_dds == tuple(all_days[:3])


def test_날짜가_역순이면_던진다():
    """정렬해 주지 않는다 — 호출부가 무엇을 주는지 알아야 한다."""
    d1, d2 = days(2)
    frames = [(d2, [etf(d2, "091160")], []), (d1, [etf(d1, "091160")], [])]
    with pytest.raises(ValueError, match="오름차순"):
        aggregate(frames, ONE_ETF, as_of=d2, fetched_at=FETCHED_AT)


def test_가중치는_전일_순자산이다():
    """🔒 당일 순자산으로 가중하면 그날 오른 ETF 에 큰 가중치가 붙어 부풀려진다.

    A 는 전일 순자산 900, B 는 100. 오늘 A 가 +10%, B 가 0% 이고 **오늘** 순자산은
    반대로 뒤집혀 있다. 전일 가중이면 +9%, 당일 가중이면 +1% 가 나온다.
    """
    cfg = master(Sector(
        id="semi", name_ko="반도체", gics="Information Technology",
        note="가중치 시점을 확인하는 합성 섹터다. 전일 가중이어야 한다.",
        etfs=(Instrument("091160", "A"), Instrument("091170", "B")),
    ))
    d1, d2 = days(2)
    frames = [
        (d1, [etf(d1, "091160", net_asset=900), etf(d1, "091170", net_asset=100)], []),
        (d2, [etf(d2, "091160", fluc_rt_bp=1000, net_asset=100),
              etf(d2, "091170", fluc_rt_bp=0, net_asset=900)], []),
    ]
    assert run(frames, cfg).sectors[1].etf_ret_1d_bp == 900   # 9.00% — 전일 가중


# ── 🔴 채우지 않는다 ─────────────────────────────────────────────────────────

def test_결측일은_전진하지_않는다():
    """등락률이 없는 날은 지수가 멈추고 그날 값이 `None` 이다. 0 이 아니다."""
    d1, d2, d3 = days(3)
    frames = [
        (d1, [etf(d1, "091160", fluc_rt_bp=0)], []),
        (d2, [etf(d2, "091160", fluc_rt_bp=1000)], []),
        (d3, [etf(d3, "091160", fluc_rt_bp=None)], []),
    ]
    rows = run(frames).sectors
    assert rows[1].etf_idx_bp == 11_000
    assert rows[2].etf_ret_1d_bp is None
    assert rows[2].etf_idx_bp == 11_000      # 멈춘다 — 0 으로도 전일값으로도 안 채운다
    assert rows[2].is_partial is True


def test_설정된_ETF가_원천에_없으면_partial이다():
    d1, d2 = days(2)
    frames = [(d1, [etf(d1, "091160")], []), (d2, [], [])]
    rows = run(frames).sectors
    assert rows[1].etf_n == 0
    assert rows[1].is_partial is True
    assert rows[1].etf_shares_sum is None    # 0 이 아니다


def test_20일_수익률은_창이_찰_때까지_None이다():
    """🔒 모자란 창을 0 으로 채우면 신규 섹터가 "수익률 0%"로 1위 근처에 선다."""
    all_days = days(25)
    frames = [(d, [etf(d, "091160", fluc_rt_bp=100)], []) for d in all_days]
    rows = run(frames).sectors
    assert rows[20].etf_ret_20d_bp is None   # 지수가 20번 전진하지 못했다
    assert rows[-1].etf_ret_20d_bp is not None


# ── 폭(breadth) ──────────────────────────────────────────────────────────────

def test_SMA20이_안_선_종목은_폭에서_제외된다():
    """🔒 `False` 로 세면 신규 상장이 많은 섹터의 폭이 가짜로 낮아진다."""
    all_days = days(25)
    frames = [(d, [], [stock(d, "005930", fluc_rt_bp=100)]) for d in all_days]
    rows = run(frames).sectors
    assert rows[5].breadth_n == 0 and rows[5].breadth_up_bp is None
    assert rows[-1].breadth_n == 1
    assert rows[-1].breadth_up_bp == 10_000     # 계속 올랐으니 100%


def test_시장_기준선이_함께_나온다():
    """모멘텀·폭 축이 빼는 기준선이다. 없으면 M7 이 시작될 수 없다."""
    all_days = days(25)
    frames = [
        (d, [], [stock(d, "005930", fluc_rt_bp=100, mktcap=900),
                 stock(d, "035720", fluc_rt_bp=0, mktcap=100, mkt="KOSDAQ")])
        for d in all_days
    ]
    market = run(frames).market
    assert market[1].mkt_ret_1d_bp == 90        # 시총가중 0.9×1% + 0.1×0%
    assert market[1].stock_n == 2
    assert market[-1].mkt_ret_20d_bp is not None
    assert market[-1].breadth_n == 2


def test_source_ids가_실제로_들어온_원천만_적는다():
    d1 = days(1)[0]
    rows = run([(d1, [etf(d1, "091160")], [stock(d1, "005930", mkt="KOSDAQ")])]).sectors
    assert rows[0].source_ids == ("etf", "ksq")


# ── 원천 복원 방지 ───────────────────────────────────────────────────────────

def test_섹터에_ETF가_하나여도_절대가격이_나가지_않는다():
    """🔴 약관 제11조② — 파생값만 내보낸다. 종가·NAV 가 행에 그대로 실리면 안 된다."""
    d1, d2 = days(2)
    frames = [
        (d1, [etf(d1, "091160", close=12_345, nav=12_300, net_asset=777_777)], []),
        (d2, [etf(d2, "091160", close=12_345, nav=12_300, net_asset=777_777)], []),
    ]
    row = run(frames).sectors[1]
    leaked = {row.etf_idx_bp, row.etf_ret_1d_bp, row.etf_premium_bp}
    assert 12_345 not in leaked
    assert row.etf_idx_bp == BASE_IDX_BP       # 지수는 기준일 100.00 에서 출발한다


def test_괴리율은_첫날부터_나온다():
    """괴리율은 수익률이 아니라 그날의 상태값이라 전일 가중이 필요 없다.

    전일 가중을 고집하면 신규 상장 ETF 의 괴리율이 이유 없이 하루 빈다.
    종가 10100 · NAV 10000 → +1.00% = 100bp.
    """
    d1 = days(1)[0]
    row = run([(d1, [etf(d1, "091160", close=10_100, nav=10_000)], [])]).sectors[0]
    assert row.etf_premium_bp == 100
    assert row.etf_ret_1d_bp is None      # 수익률은 여전히 전일이 있어야 한다
