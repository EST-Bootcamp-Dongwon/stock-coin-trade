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
from sector.scoring import ScoreRow

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
        "eqw_idx_bp": 10000 + i * 20, "eqw_ret_1d_bp": 20,
        "breadth_up_bp": 5500, "breadth_n": 2700,
        "is_partial": False, "fetched_at": "2026-09-11T06:00:00+00:00",
    } for i, day in enumerate(days)]
    frame = pd.DataFrame(rows)
    return frame.astype({c: "Int64" for c in frame.columns
                         if frame[c].dtype.kind in "iu"})


def derived_score(days=DAYS, sectors=SECTORS) -> pd.DataFrame:
    """채점이 내놓는 모양의 합성 프레임 (M7).

    🔒 실제 채점을 부르지 않는다 — 3영업일로는 어떤 축도 서지 않아 순위·축 검사가
       아무것도 보지 못한다. 여기서 보려는 것은 **게이트가 무엇을 막는가**다.
    """
    rows = []
    for step, day in enumerate(days):
        for i, sid in enumerate(sectors):
            z = (1 - 2 * i) * 10_000 + step * 10
            rows.append({
                "bas_dd": day, "sector_id": sid, "gics": "Industrials",
                "m_raw_bp": 100, "f_raw_bp": 200, "b_raw_bp": 500, "v_raw_bp": -50,
                "m_z_bp": z, "f_z_bp": z, "b_z_bp": z, "v_z_bp": z,
                "n_axes_used": 4, "axes_missing": "", "axes_degraded": "",
                "score_balanced_bp": z, "rank_balanced": i + 1,
                "score_momentum_bp": z, "rank_momentum": i + 1,
                "score_contrarian_bp": z, "rank_contrarian": i + 1,
                "liquidity_ok": True, "etf_n": 2, "is_partial": False,
                "config_version": "test-1", "config_sha256": "c" * 64,
                "fetched_at": "2026-09-11T06:00:00+00:00",
            })
    frame = pd.DataFrame(rows)
    return frame.astype({c: "Int64" for c in frame.columns
                         if frame[c].dtype.kind in "iu"})


@pytest.fixture
def published():
    return (gate.project_sector(derived_sector()),
            gate.project_market(derived_market()),
            gate.project_score(derived_score()))


def run(published, as_of="20260903"):
    return gate.check(sector=published[0], market=published[1],
                      score=published[2], as_of=as_of)


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


def test_점수_허용목록이_채점_필드와_같다():
    """M7 의 같은 장치 — 점수에 열이 생기면 여기서 먼저 깨진다.

    점수는 파생의 파생이라 원천 복원 위험이 낮지만, **열이 늘어나는 곳**이라
    판단을 강제하는 문이 필요하다 (→ `gate.py` 머리주석).
    """
    fields = {f.name for f in dataclasses.fields(ScoreRow)}
    assert set(gate.SCORE_PUBLISHED_COLUMNS) == fields, (
        f"판정되지 않은 채점 열: {sorted(fields - set(gate.SCORE_PUBLISHED_COLUMNS))} · "
        f"채점에 없는 허용 열: {sorted(set(gate.SCORE_PUBLISHED_COLUMNS) - fields)}"
    )


def test_점수_열_순서가_채점_순서와_같다():
    """🔒 순서까지 계약이다 — parquet 바이트가 달라지면 멱등성이 깨진다."""
    assert list(gate.SCORE_PUBLISHED_COLUMNS) == [
        f.name for f in dataclasses.fields(ScoreRow)
    ]


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
    empty_c = gate.project_score(derived_score()).iloc[0:0]
    report = gate.check(sector=empty_s, market=empty_m, score=empty_c, as_of="20260903")
    assert report.checks_run == gate.REQUIRED_CHECKS      # 검사는 다 돌았고
    assert not report.ok                                   # 그런데 통과가 아니다
    assert any("0행" in v for v in report.violations)


# ── 위반을 실제로 잡는가 ────────────────────────────────────────────────────

def test_허용되지_않은_열을_막는다(published):
    sector, market, score = published
    sector = sector.assign(etf_close=10000)
    report = gate.check(sector=sector, market=market, score=score, as_of="20260903")
    assert any("허용되지 않은 열" in v for v in report.violations)


def test_일부러_뺀_열이_섞이면_그_사실을_말한다(published):
    """경고가 막다른 길이 되지 않게 — 왜 뺐는지를 메시지가 들고 있어야 한다."""
    sector, market, score = published
    sector = sector.assign(etf_nav_sum=5_000_000)
    report = gate.check(sector=sector, market=market, score=score, as_of="20260903")
    assert any("일부러 뺀 열" in v and "etf_nav_sum" in v for v in report.violations)


def test_열이_모자라도_막는다(published):
    sector, market, score = published
    report = gate.check(sector=sector.drop(columns=["breadth_n"]), market=market,
                        score=score, as_of="20260903")
    assert any("없다" in v for v in report.violations)


def test_열_순서가_바뀌면_막는다(published):
    """순서도 계약이다 — parquet 바이트가 달라져 멱등성이 깨진다."""
    sector, market, score = published
    swapped = sector[list(reversed(gate.SECTOR_PUBLISHED_COLUMNS))]
    report = gate.check(sector=swapped, market=market, score=score, as_of="20260903")
    assert any("순서" in v for v in report.violations)


def test_중복_키를_막는다(published):
    sector, market, score = published
    report = gate.check(sector=pd.concat([sector, sector.iloc[:1]]), market=market,
                        score=score, as_of="20260903")
    assert any("중복" in v for v in report.violations)


def test_주말이_섞이면_막는다():
    """20260905 는 토요일이다."""
    days = ["20260903", "20260905"]
    report = gate.check(sector=gate.project_sector(derived_sector(days=days)),
                        market=gate.project_market(derived_market(days=days)),
                        score=gate.project_score(derived_score(days=days)),
                        as_of="20260905")
    assert any("주말" in v for v in report.violations)


def test_date_보다_뒤의_날을_막는다(published):
    """🔒 룩어헤드. `--date` 는 '이 날짜까지의 세상' 이라는 뜻이다."""
    report = run(published, as_of="20260902")
    assert any("--date" in v for v in report.violations)


def test_형식이_아닌_날짜를_막는다():
    days = ["20260901", "2026-09-02"]
    report = gate.check(sector=gate.project_sector(derived_sector(days=days)),
                        market=gate.project_market(derived_market(days=days)),
                        score=gate.project_score(derived_score(days=days)),
                        as_of="20260903")
    assert any("형식" in v for v in report.violations)


def test_지수가_0_이하면_막는다(published):
    sector, market, score = published
    sector = sector.copy()
    sector.loc[0, "etf_idx_bp"] = 0
    report = gate.check(sector=sector, market=market, score=score, as_of="20260903")
    assert any("0 이하" in v for v in report.violations)


def test_가격제한폭을_넘으면_막는다(published):
    sector, market, score = published
    sector = sector.copy()
    sector.loc[0, "etf_ret_1d_bp"] = gate.DAILY_MOVE_LIMIT_BP + 1
    report = gate.check(sector=sector, market=market, score=score, as_of="20260903")
    assert any("가격제한폭" in v for v in report.violations)


def test_정확히_상한이면_막지_않는다(published):
    """±30.00% 는 실제로 일어난다. 구조적 상한이지 여유분이 아니다."""
    sector, market, score = published
    sector = sector.copy()
    sector.loc[0, "etf_ret_1d_bp"] = -gate.DAILY_MOVE_LIMIT_BP
    report = gate.check(sector=sector, market=market, score=score, as_of="20260903")
    assert not any("가격제한폭" in v for v in report.violations)


def test_날짜_축이_갈라지면_막는다(published):
    sector, market, score = published
    report = gate.check(sector=sector, market=market.iloc[:-1], score=score,
                        as_of="20260903")
    assert any("market_daily 에만" in v or "sector_daily 에만" in v
               for v in report.violations)


def test_날마다_섹터_수가_다르면_막는다(published):
    sector, market, score = published
    dropped = sector.drop(index=sector.index[sector["bas_dd"] == "20260902"][:1])
    report = gate.check(sector=dropped, market=market, score=score, as_of="20260903")
    assert any("섹터 수가 다르다" in v for v in report.violations)


# ── 점수 검사 (M7) ──────────────────────────────────────────────────────────

def test_z_가_clip_범위를_넘으면_막는다(published):
    """z 는 `clip(±3)` 이다. 넘었다면 시장이 아니라 **표준화가 깨진** 것이다."""
    sector, market, score = published
    broken = score.copy()
    broken.loc[broken.index[0], "m_z_bp"] = gate.SCORE_LIMIT_BP + 1
    report = gate.check(sector=sector, market=market, score=broken, as_of="20260903")
    assert any("clip" in v and "m_z_bp" in v for v in report.violations)


def test_n_axes_used_가_z_결측과_어긋나면_막는다(published):
    """🔴 셋이 어긋나면 화면이 '3축으로 계산했다'면서 4축 점수를 보여준다.

    숫자는 그럴듯하고 **설명만 거짓**이 되는 종류의 오류라, 사람 눈으로는 안 잡힌다.
    """
    sector, market, score = published
    broken = score.copy()
    broken.loc[broken.index[0], "n_axes_used"] = 3      # z 는 넷 다 있는데 3 이라고 한다
    report = gate.check(sector=sector, market=market, score=broken, as_of="20260903")
    assert any("n_axes_used" in v for v in report.violations)


def test_axes_missing_이_실제_결측과_다르면_막는다(published):
    sector, market, score = published
    broken = score.copy()
    broken.loc[broken.index[0], "axes_missing"] = "V"
    report = gate.check(sector=sector, market=market, score=broken, as_of="20260903")
    assert any("axes_missing" in v for v in report.violations)


def test_순위가_점수와_어긋나면_막는다(published):
    sector, market, score = published
    broken = score.copy()
    first = broken.index[0]
    broken.loc[first, "rank_balanced"] = 2 if broken.loc[first, "rank_balanced"] == 1 else 1
    report = gate.check(sector=sector, market=market, score=broken, as_of="20260903")
    assert any("rank_balanced" in v for v in report.violations)


def test_점수가_없는데_순위가_있으면_막는다(published):
    """🔒 점수가 없으면 순위도 없어야 한다 — 맨 뒤에 매기면 화면이 '꼴찌'로 읽는다."""
    sector, market, score = published
    broken = score.copy()
    broken.loc[broken.index[0], "score_balanced_bp"] = pd.NA
    report = gate.check(sector=sector, market=market, score=broken, as_of="20260903")
    assert any("결측이 어긋난" in v for v in report.violations)


def test_집계에_없는_날을_채점했으면_막는다(published):
    """점수는 집계의 **부분집합**이어야 한다 — 없는 날을 채점할 수는 없다."""
    sector, market, score = published
    extra = score.iloc[:2].copy()
    extra["bas_dd"] = "20260904"
    broken = pd.concat([score, extra], ignore_index=True)
    report = gate.check(sector=sector, market=market, score=broken, as_of="20260904")
    assert any("score_daily 에만" in v for v in report.violations)


def test_점수가_0행이면_통과가_아니다(published):
    """🔴 빈 점수를 올리면 앱이 조용히 빈 랭킹을 그린다."""
    sector, market, score = published
    report = gate.check(sector=sector, market=market, score=score.iloc[0:0],
                        as_of="20260903")
    assert report.checks_run == gate.REQUIRED_CHECKS
    assert any("score_daily 이 0행" in v and "build_scores" in v
               for v in report.violations)


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


# ── 재현성 — 게시본만으로 그 점수에 닿는가 (M9 · 2026-09-17) ────────────────

def test_게시되는_z_로_점수를_재현할_수_없으면_막는다(published):
    """🔴 앱은 `*_z_bp` 밖에 못 본다. 슬라이더가 그 열을 가중합해 점수를 다시 내는데,
    게시된 점수가 그 방식으로 나온 값이 아니면 **같은 화면의 표와 근거가 다른 숫자를
    말한다.** 실측에서 한 섹터의 순위 진폭이 표에서 4.4, 근거에서 4.5 로 나왔다.
    """
    sector, market, score = published
    tampered = score.copy()
    tampered.loc[0, "score_balanced_bp"] = int(tampered.loc[0, "score_balanced_bp"]) + 1
    report = gate.check(sector=sector, market=market, score=tampered, as_of=DAYS[-1])
    assert not report.ok
    assert any("재현할 수 없다" in v for v in report.violations), report.violations


def test_순위만_어긋나도_막는다(published):
    """🔒 점수가 맞아도 tie-break 가 갈리면 화면과 근거의 등수가 달라진다."""
    sector, market, score = published
    tampered = score.copy()
    tampered["rank_momentum"] = tampered["rank_momentum"].max() + 1 - tampered["rank_momentum"]
    report = gate.check(sector=sector, market=market, score=tampered, as_of=DAYS[-1])
    assert not report.ok
    assert any("rank" in v or "순위" in v for v in report.violations), report.violations


def test_재현성_검사가_배치와_같은_함수를_쓴다():
    """🔒 검사가 산수를 되풀이하면 배치와 검사가 같이 틀릴 수 있다. 검사하는 것은
    "계산이 맞나" 가 아니라 **"게시본만으로 이 값에 닿을 수 있나"** 다."""
    import inspect

    source = inspect.getsource(gate._check_score_reproducible)
    assert "weighted_score_bp" in source and "rank_scores" in source


def test_실제_채점_결과가_재현성을_통과한다():
    """🔴 합성 프레임은 네 축 z 가 같아 어떤 가중치로도 같은 답이 나온다 — 반올림
    지형을 대표하지 못한다. 그래서 **골든 픽스처로 실제 채점을 돌려** 통과를 본다.
    """
    import dataclasses as _dc

    from sector import scoring
    from sector.scoring_golden_test import FETCHED_AT, _read, golden_config

    sector_frame, market_frame = _read("golden_sector_daily.csv"), _read("golden_market_daily.csv")
    days = sorted(sector_frame["bas_dd"].dropna().unique())
    rows = scoring.score_history(sector_frame, market_frame, as_of=days[-1],
                                 config=golden_config(), fetched_at=FETCHED_AT)
    score = pd.DataFrame([_dc.asdict(r) for r in rows])
    assert gate._check_score_reproducible(None, None, score, days[-1]) == []
