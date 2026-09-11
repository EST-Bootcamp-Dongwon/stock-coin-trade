"""`sector/datastore/gate.py` 테스트 — **무엇이 나가도 되는가**를 지키는 부분.

여기서 보는 것은 산술이 아니라 **경계**다. 경계가 조용히 새면 약관 제11조②
위반이 되고, 그 대가는 이용승인 철회(④)라 되돌릴 수 없다.

🔒 합성 데이터만 쓴다 (제약 10 · AGENTS.md 5장).
"""

from __future__ import annotations

import dataclasses

import pandas as pd
import pytest

from sector.aggregate import MarketDailyRow, SectorDailyRow
from sector.datastore import gate

# ── 합성 프레임 ─────────────────────────────────────────────────────────────

DAYS = ["20260901", "20260902", "20260903"]
SECTORS = ["alpha", "beta"]


def derived_sector(days=DAYS, sectors=SECTORS, shares=None) -> pd.DataFrame:
    """집계가 내놓는 모양(투영 **전**)의 합성 프레임."""
    rows = []
    for day in days:
        for i, sid in enumerate(sectors):
            step = days.index(day)
            rows.append({
                "bas_dd": day, "sector_id": sid, "gics": "Industrials",
                "etf_n": 2, "etf_idx_bp": 10000 + step * 100,
                "etf_ret_1d_bp": 100, "etf_ret_20d_bp": 500,
                "etf_shares_sum": (shares or [1000, 1200, 1500])[step] * (i + 1),
                "etf_nav_sum": 5_000_000, "etf_value_sum": 200_000_000,
                "etf_premium_bp": 12,
                "member_n": 5, "member_idx_bp": 10000 + step * 50,
                "member_ret_1d_bp": 50, "member_ret_20d_bp": 300,
                "breadth_up_bp": 6000, "breadth_n": 5,
                "source_ids": ("etf", "stk"), "is_partial": False,
                "fetched_at": "2026-09-11T06:00:00+00:00",
            })
    frame = pd.DataFrame(rows)
    return frame.astype({c: "Int64" for c in frame.columns
                         if frame[c].dtype.kind in "iu"})


def derived_market(days=DAYS) -> pd.DataFrame:
    rows = [{
        "bas_dd": day, "stock_n": 2700,
        "mkt_idx_bp": 10000 + i * 30, "mkt_ret_1d_bp": 30, "mkt_ret_20d_bp": 200,
        "eqw_idx_bp": 10000 + i * 20,
        "breadth_up_bp": 5500, "breadth_n": 2700,
        "is_partial": False, "fetched_at": "2026-09-11T06:00:00+00:00",
    } for i, day in enumerate(days)]
    frame = pd.DataFrame(rows)
    return frame.astype({c: "Int64" for c in frame.columns
                         if frame[c].dtype.kind in "iu"})


@pytest.fixture
def published():
    return (gate.project_sector(derived_sector()), gate.project_market(derived_market()))


def run(published, as_of="20260903"):
    return gate.check(sector=published[0], market=published[1], as_of=as_of)


# ── 허용목록이 집계와 어긋나지 않는가 ───────────────────────────────────────

def test_섹터_허용목록이_집계_필드를_빠짐없이_판정한다():
    """🔴 이 테스트가 M6 의 핵심 장치다.

    집계에 열이 하나 생기면 그 열은 **내보낼 것**이거나 **일부러 뺀 것**이어야
    한다. 둘 중 어디에도 없으면 여기서 깨진다 — 사람이 "이 값을 제3자에게
    줘도 되는가"를 한 번 판단하게 만드는 것이 목적이다.
    """
    fields = {f.name for f in dataclasses.fields(SectorDailyRow)}
    judged = (set(gate.SECTOR_PUBLISHED_COLUMNS) - gate.PROJECTED_COLUMNS) | set(
        gate.DELIBERATELY_WITHHELD)
    assert judged == fields, (
        f"판정되지 않은 집계 열: {sorted(fields - judged)} · "
        f"집계에 없는 허용 열: {sorted(judged - fields)}"
    )


def test_시장_허용목록이_집계_필드와_같다():
    fields = {f.name for f in dataclasses.fields(MarketDailyRow)}
    assert set(gate.MARKET_PUBLISHED_COLUMNS) == fields


def test_절대가격으로_읽히는_열이_허용목록에_없다():
    """ADR-SC-0006 ② — 절대가격을 올리지 않는다. 이름으로도 한 겹 막는다."""
    banned = ("clsprc", "_close", "close_", "price", "_prc", "opnprc", "nav")
    for column in gate.SECTOR_PUBLISHED_COLUMNS + gate.MARKET_PUBLISHED_COLUMNS:
        assert not any(b in column.lower() for b in banned), column


def test_일부러_뺀_열에_이유가_적혀_있다():
    for column, why in gate.DELIBERATELY_WITHHELD.items():
        assert len(why) >= 10, f"{column} 을 왜 뺐는지 적혀 있지 않다"


# ── 검사기가 실제로 도는가 ──────────────────────────────────────────────────

def test_검사를_하나도_빠뜨리지_않는다(published):
    assert run(published).checks_run == gate.REQUIRED_CHECKS


def test_정상_프레임은_위반이_없다(published):
    report = run(published)
    assert report.violations == () and report.ok


def test_빈_프레임은_통과가_아니다():
    empty_s = gate.project_sector(derived_sector()).iloc[0:0]
    empty_m = gate.project_market(derived_market()).iloc[0:0]
    report = gate.check(sector=empty_s, market=empty_m, as_of="20260903")
    assert report.checks_run == gate.REQUIRED_CHECKS      # 검사는 다 돌았고
    assert not report.ok                                   # 그런데 통과가 아니다
    assert any("0행" in v for v in report.violations)


# ── 위반을 실제로 잡는가 ────────────────────────────────────────────────────

def test_허용되지_않은_열을_막는다(published):
    sector, market = published
    sector = sector.assign(etf_close=10000)
    report = gate.check(sector=sector, market=market, as_of="20260903")
    assert any("허용되지 않은 열" in v for v in report.violations)


def test_일부러_뺀_열이_섞이면_그_사실을_말한다(published):
    """경고가 막다른 길이 되지 않게 — 왜 뺐는지를 메시지가 들고 있어야 한다."""
    sector, market = published
    sector = sector.assign(etf_nav_sum=5_000_000)
    report = gate.check(sector=sector, market=market, as_of="20260903")
    assert any("일부러 뺀 열" in v and "etf_nav_sum" in v for v in report.violations)


def test_열이_모자라도_막는다(published):
    sector, market = published
    report = gate.check(sector=sector.drop(columns=["breadth_n"]), market=market,
                        as_of="20260903")
    assert any("없다" in v for v in report.violations)


def test_열_순서가_바뀌면_막는다(published):
    """순서도 계약이다 — parquet 바이트가 달라져 멱등성이 깨진다."""
    sector, market = published
    swapped = sector[list(reversed(gate.SECTOR_PUBLISHED_COLUMNS))]
    report = gate.check(sector=swapped, market=market, as_of="20260903")
    assert any("순서" in v for v in report.violations)


def test_중복_키를_막는다(published):
    sector, market = published
    report = gate.check(sector=pd.concat([sector, sector.iloc[:1]]), market=market,
                        as_of="20260903")
    assert any("중복" in v for v in report.violations)


def test_주말이_섞이면_막는다():
    """20260905 는 토요일이다."""
    days = ["20260903", "20260905"]
    report = gate.check(sector=gate.project_sector(derived_sector(days=days)),
                        market=gate.project_market(derived_market(days=days)),
                        as_of="20260905")
    assert any("주말" in v for v in report.violations)


def test_date_보다_뒤의_날을_막는다(published):
    """🔒 룩어헤드. `--date` 는 '이 날짜까지의 세상' 이라는 뜻이다."""
    report = run(published, as_of="20260902")
    assert any("--date" in v for v in report.violations)


def test_형식이_아닌_날짜를_막는다():
    sector = gate.project_sector(derived_sector(days=["20260901", "2026-09-02"]))
    market = gate.project_market(derived_market(days=["20260901", "2026-09-02"]))
    report = gate.check(sector=sector, market=market, as_of="20260903")
    assert any("형식" in v for v in report.violations)


def test_지수가_0_이하면_막는다(published):
    sector, market = published
    sector = sector.copy()
    sector.loc[0, "etf_idx_bp"] = 0
    report = gate.check(sector=sector, market=market, as_of="20260903")
    assert any("0 이하" in v for v in report.violations)


def test_가격제한폭을_넘으면_막는다(published):
    sector, market = published
    sector = sector.copy()
    sector.loc[0, "etf_ret_1d_bp"] = gate.DAILY_MOVE_LIMIT_BP + 1
    report = gate.check(sector=sector, market=market, as_of="20260903")
    assert any("가격제한폭" in v for v in report.violations)


def test_정확히_상한이면_막지_않는다(published):
    """±30.00% 는 실제로 일어난다. 구조적 상한이지 여유분이 아니다."""
    sector, market = published
    sector = sector.copy()
    sector.loc[0, "etf_ret_1d_bp"] = -gate.DAILY_MOVE_LIMIT_BP
    report = gate.check(sector=sector, market=market, as_of="20260903")
    assert not any("가격제한폭" in v for v in report.violations)


def test_날짜_축이_갈라지면_막는다(published):
    sector, market = published
    report = gate.check(sector=sector, market=market.iloc[:-1], as_of="20260903")
    assert any("market_daily 에만" in v or "sector_daily 에만" in v
               for v in report.violations)


def test_날마다_섹터_수가_다르면_막는다(published):
    sector, market = published
    dropped = sector.drop(index=sector.index[sector["bas_dd"] == "20260902"][:1])
    report = gate.check(sector=dropped, market=market, as_of="20260903")
    assert any("섹터 수가 다르다" in v for v in report.violations)


# ── 좌수 지수화 (ADR-SC-0006 ②) ─────────────────────────────────────────────

def test_좌수_지수가_비율을_보존한다():
    sector = gate.project_sector(derived_sector(shares=[1000, 1200, 1500]))
    alpha = sector[sector.sector_id == "alpha"].sort_values("bas_dd")
    assert list(alpha.etf_shares_idx_bp) == [10000, 12000, 15000]


def test_좌수_원본_수준이_게시본에_없다():
    """🔴 ETF 1개 섹터에서 `etf_shares_sum` 은 그 ETF 의 `LIST_SHRS` 그 값이다."""
    sector = gate.project_sector(derived_sector(shares=[123_456_789, 123_456_789, 1]))
    assert "etf_shares_sum" not in sector.columns
    assert not (sector.select_dtypes("Int64") == 123_456_789).any().any()


def test_좌수_지수가_float로_새지_않는다():
    sector = gate.project_sector(derived_sector(shares=[3, 7, 11]))
    assert str(sector["etf_shares_idx_bp"].dtype) == "Int64"
    assert not any("float" in str(d) for d in sector.dtypes)


def test_좌수_지수의_기준이_뒤_날짜에_의존하지_않는다():
    """🔒 멱등성이 여기서 나온다 — 새 날이 들어와도 지난달 샤드가 안 바뀐다."""
    short = gate.project_sector(derived_sector(days=DAYS[:2]))
    long = gate.project_sector(derived_sector(days=DAYS))
    merged = long[long.bas_dd.isin(DAYS[:2])].reset_index(drop=True)
    pd.testing.assert_frame_equal(short.reset_index(drop=True), merged)


def test_좌수가_없으면_지어내지_않는다():
    frame = derived_sector()
    frame["etf_shares_sum"] = pd.array([None] * len(frame), dtype="Int64")
    sector = gate.project_sector(frame)
    assert sector["etf_shares_idx_bp"].isna().all()


def test_입력_순서가_달라도_같은_출력이다():
    """정렬을 투영이 못박는다 — 안 그러면 같은 데이터가 다른 바이트가 된다."""
    frame = derived_sector()
    a = gate.project_sector(frame)
    b = gate.project_sector(frame.sample(frac=1.0, random_state=7))
    pd.testing.assert_frame_equal(a, b)
