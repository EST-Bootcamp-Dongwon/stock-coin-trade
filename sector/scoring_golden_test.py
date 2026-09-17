"""`sector/scoring.py` **골든 테스트** — 점수의 값과 성질을 함께 고정한다.

★★ **파일 이름이 `scoring_golden_test.py` 인 것은 우연이 아니다** ──────────

pytest 의 `*_test.py` 는 줍고 Django 의 테스트 탐색기(`test*.py`)는 안 줍는다.
`test_scoring.py` 로 바꾸면 동결된 v2.0 러너까지 이 파일을 주워 `snapshot` 픽스처가
없다며 깨진다 (→ `AGENTS.md` 5장 · `pytest.ini` 머리주석).

★★ **무엇을 고정하는가** ─────────────────────────────────────────────────

두 종류다. 섞이지 않게 아래에서도 절로 나눠 뒀다.

1. **값** — 축별 raw / z / 점수 / 순위를 스냅샷에 적어 둔다. 축의 정의를 고치면
   여기가 먼저 깨지고, `git diff` 가 "무엇이 몇 bp 움직였는지" 를 보여준다.
2. **성질** — 스냅샷으로는 잡히지 않는 것들. 룩어헤드가 없는가, ETF 없는 섹터의
   가중치가 다시 정규화되는가, 동점이 어떻게 갈리는가. 🔴 **성질 쪽이 더 중요하다** —
   스냅샷은 "값이 바뀌었다"만 말하고, 성질은 "무엇이 틀렸다"를 말한다.

★★ **픽스처는 완전 합성이다** ────────────────────────────────────────────

`testdata/golden_*.csv` 에 KRX 값이 한 칸도 없다(제약 10). 대신 축이 비는 상황을
일부러 심어 뒀다 — 관측이 빠진 섹터, ETF 가 없는 섹터, ETF 수가 바뀌는 섹터,
점수가 같은 두 섹터, 그리고 중앙값절대편차가 0 이 되는 날.

    실행:  .venv/bin/python -m pytest sector/scoring_golden_test.py
    갱신:  ... --snapshot-update      ← 값이 바뀐 이유를 설명할 수 있을 때만
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from sector import scoring
from sector.sector_master import GicsSector, Instrument, Sector, SectorMaster

TESTDATA = Path(__file__).parent / "testdata"

#: 픽스처가 담은 섹터. 각각이 어떤 상황인지는 머리주석 참조.
SECTOR_IDS = ("alpha", "bravo", "charlie", "delta", "echo", "foxtrot")

#: 🔒 벽시계를 쓰지 않는다. `fetched_at` 이 스냅샷에 들어가므로 고정값이어야 한다.
FETCHED_AT = "2026-09-11T06:00:00+00:00"

#: 스냅샷에 담을 날짜 — 축이 **서고 지는 경계**를 고른다.
#: 20: F 축 첫날 · 60: M 축 첫날 · 80: delta 의 ETF 편입 · 119: V 축 첫날 ·
#: 130: 중앙값절대편차가 0 이 되는 날 · 149: 마지막 날
MILESTONE_INDEXES = (0, 19, 20, 59, 60, 79, 80, 118, 119, 129, 130, 149)

_INT_COLUMNS = (
    "etf_n", "etf_idx_bp", "etf_ret_1d_bp", "etf_shares_sum",
    "etf_value_sum", "breadth_up_bp", "eqw_idx_bp", "eqw_ret_1d_bp",
)


def _read(name: str):
    """CSV → 집계가 내놓는 것과 같은 자료형의 프레임.

    🔒 빈 칸은 `pd.NA` 다. `0` 으로 읽히면 "값이 없다"가 "값이 0 이다"로 바뀌고,
       이 파일이 잡으려는 오류가 바로 그것이다 (→ ADR-SC-0007).
    """
    import pandas as pd

    frame = pd.read_csv(TESTDATA / name, dtype="string", keep_default_na=False)
    frame = frame.replace("", pd.NA)
    for column in _INT_COLUMNS:
        if column in frame.columns:
            frame[column] = frame[column].astype("Int64")
    if "is_partial" in frame.columns:
        frame["is_partial"] = frame["is_partial"].map(
            {"true": True, "false": False}
        ).astype("boolean")
    return frame


@pytest.fixture(scope="module")
def frames():
    return _read("golden_sector_daily.csv"), _read("golden_market_daily.csv")


def golden_config() -> SectorMaster:
    """합성 섹터 마스터. 🔒 실제 `sectors.yaml` 을 읽지 않는다 —
    설정이 바뀔 때마다 점수 스냅샷이 흔들리면 그건 골든이 아니다.

    🔒 픽스처가 아니라 **평범한 함수**다 — 다른 테스트 파일(`gate_test`)이 골든
       픽스처로 실제 채점을 돌려 보는데, pytest 내부 속성(`__wrapped__`)에 기대면
       pytest 를 올릴 때 조용히 깨진다.
    """
    return SectorMaster(
        version="golden-2026-09-11",
        source_notice="합성 데이터 (시험용)",
        gics_sectors=(GicsSector(id="Industrials", name_ko="산업재"),),
        sectors=tuple(
            Sector(
                id=sid,
                name_ko=sid,
                gics="Industrials",
                note="합성 픽스처",
                etfs=() if sid == "echo" else (Instrument(code="000000", name="합성"),),
            )
            for sid in SECTOR_IDS
        ),
        config_sha256="0" * 64,
    )


@pytest.fixture(scope="module")
def config() -> SectorMaster:
    return golden_config()


@pytest.fixture(scope="module")
def days(frames) -> list[str]:
    return sorted(frames[0]["bas_dd"].dropna().unique())


def _score(frames, config, as_of: str):
    return scoring.score(frames[0], frames[1], as_of=as_of,
                         config=config, fetched_at=FETCHED_AT)


def _dump(rows) -> list[dict]:
    """스냅샷에 들어가는 모양. 전부 정수·문자열·불이라 `Decimal` 이 남지 않는다."""
    return [dataclasses.asdict(row) for row in
            sorted(rows, key=lambda r: (r.bas_dd, r.sector_id))]


# ═══ 1. 값 — 스냅샷 ═════════════════════════════════════════════════════════

def test_마지막날_전체_점수를_고정한다(frames, config, days, snapshot):
    """6섹터 × 모든 열. 🔴 축의 정의를 고치면 여기가 **먼저** 깨진다."""
    snapshot.assert_match(_dump(_score(frames, config, days[-1])))


def test_축이_서고_지는_경계마다_점수를_고정한다(frames, config, days, snapshot):
    """창이 차는 날·ETF 가 편입되는 날처럼 **값이 바뀌어야 하는 날**을 골라 둔다."""
    out = {}
    for index in MILESTONE_INDEXES:
        day = days[index]
        out[f"t{index:03d}_{day}"] = _dump(_score(frames, config, day))
    snapshot.assert_match(out)


def test_축별_원시값과_z_를_따로_고정한다(frames, config, days, snapshot):
    """점수보다 **앞단**을 따로 잡아 둔다 — 점수가 같아도 축이 달라졌을 수 있다."""
    out = {}
    for index in (59, 119, 149):
        day = days[index]
        out[day] = {
            row.sector_id: {
                "raw": [row.m_raw_bp, row.f_raw_bp, row.b_raw_bp, row.v_raw_bp],
                "z": [row.m_z_bp, row.f_z_bp, row.b_z_bp, row.v_z_bp],
                "missing": row.axes_missing,
                "degraded": row.axes_degraded,
            }
            for row in sorted(_score(frames, config, day), key=lambda r: r.sector_id)
        }
    snapshot.assert_match(out)


# ═══ 2. 성질 — 스냅샷으로는 안 잡히는 것 ════════════════════════════════════

@pytest.mark.parametrize("index", [70, 100, 119, 140])
def test_asof_monotone(frames, config, days, index):
    """🔴 **이 한 줄이 룩어헤드를 구조적으로 잡는다** (계획서 D-2 ⑤).

    `data[:T+30]` 으로 계산한 `score(T)` 와 `data[:T]` 로 계산한 값이 **정확히**
    같아야 한다. 다르면 어딘가에서 미래를 봤다는 뜻이고, 그 오류는 백테스트를
    통째로 거짓말로 만든다 — 그러면서 아무 예외도 던지지 않는다.
    """
    sector, market = frames
    target = days[index]
    later = days[min(index + 30, len(days) - 1)]
    assert later > target, "30일 뒤가 픽스처 밖이면 이 시험은 아무것도 검사하지 않는다"

    def cut(frame, until):
        return frame[frame["bas_dd"] <= until]

    with_future = scoring.score(cut(sector, later), cut(market, later),
                                as_of=target, config=config, fetched_at=FETCHED_AT)
    without = scoring.score(cut(sector, target), cut(market, target),
                            as_of=target, config=config, fetched_at=FETCHED_AT)
    assert _dump(with_future) == _dump(without)


def test_score_history_의_과거_행이_그날_계산한_값과_같다(frames, config, days):
    """시계열로 한 번에 낸 값과 날짜마다 잘라 낸 값이 같아야 한다.

    다르면 `score_daily` 의 과거 행이 "오늘 시점에서 다시 본 과거"가 되고,
    화면의 추세선이 매일 조용히 다시 그려진다.
    """
    history = scoring.score_history(frames[0], frames[1], as_of=days[-1],
                                    config=config, fetched_at=FETCHED_AT)
    by_day: dict[str, list] = {}
    for row in history:
        by_day.setdefault(row.bas_dd, []).append(row)
    for index in (60, 120, len(days) - 1):
        day = days[index]
        assert _dump(by_day[day]) == _dump(_score(frames, config, day))


def test_as_of_는_기본값이_없다():
    """🔒 기본값이 있으면 빠뜨린 호출이 **조용히** 전체 기간을 본다 (계획서 D-2 ①)."""
    import inspect

    for name in ("score", "score_history"):
        signature = inspect.signature(getattr(scoring, name))
        parameter = signature.parameters["as_of"]
        assert parameter.kind is inspect.Parameter.KEYWORD_ONLY, name
        assert parameter.default is inspect.Parameter.empty, name


def test_ETF_가_없는_섹터는_F_를_0_으로_채우지_않는다(frames, config, days):
    """`echo` 는 ETF 가 없다 → M·F·V 가 전부 비고 **B 축만** 남는다.

    🔴 0 으로 채우면 "돈이 안 들어온 섹터"로 읽혀 중간 순위를 받는다. 그건 사실이
       아니라 결측이다 (→ ADR-SC-0007).
    """
    row = next(r for r in _score(frames, config, days[-1]) if r.sector_id == "echo")
    assert (row.m_raw_bp, row.f_raw_bp, row.v_raw_bp) == (None, None, None)
    assert (row.m_z_bp, row.f_z_bp, row.v_z_bp) == (None, None, None)
    assert row.b_z_bp is not None
    assert row.axes_missing == "MFV" and row.n_axes_used == 1
    # 🔒 3축이 빠졌어도 점수는 나온다 — 남은 축으로 가중치를 다시 정규화한다
    assert row.score_balanced_bp is not None


def test_결측축은_가중치를_다시_정규화한다(frames, config, days):
    """1축만 선 섹터의 점수 = 그 축의 z. 가중치를 다시 나눴다는 증거다."""
    row = next(r for r in _score(frames, config, days[-1]) if r.sector_id == "echo")
    assert row.n_axes_used == 1
    assert row.score_balanced_bp == row.b_z_bp
    # 프리셋마다 B 가중치가 달라도 1축이면 결과가 같아야 한다 — w/w = 1 이므로
    assert row.score_momentum_bp == row.b_z_bp == row.score_contrarian_bp


def test_동점은_sector_id_로_갈린다(frames, config, days):
    """`foxtrot` 은 `alpha` 를 그대로 베낀 섹터다 — 점수가 같아야 하고,
    순위는 **사전순**으로 갈려야 한다. 동점 처리가 입력 순서에 좌우되면
    같은 자료로 순위표가 매번 달라진다."""
    rows = {r.sector_id: r for r in _score(frames, config, days[-1])}
    alpha, foxtrot = rows["alpha"], rows["foxtrot"]
    assert alpha.score_balanced_bp == foxtrot.score_balanced_bp
    assert alpha.rank_balanced is not None
    assert foxtrot.rank_balanced == alpha.rank_balanced + 1, "alpha 가 앞서야 한다"


def test_창이_온전하지_않으면_축을_계산하지_않는다(frames, config, days):
    """`charlie` 는 60~62 일에 관측이 없다. 그 뒤 20·60영업일 창에 그 구멍이
    들어 있는 동안 M 축이 비어야 한다.

    🔴 전일값으로 메우면 그 구간의 수익률이 **0 으로 채워진 20일**이 된다.
       숫자는 나오고 뜻만 거짓이 되는 종류의 오류다.
    """
    # 구멍(t=62)이 60일 창 안에 있는 날 — 축이 비어 있어야 한다
    hurt = next(r for r in _score(frames, config, days[100]) if r.sector_id == "charlie")
    assert hurt.m_raw_bp is None and "M" in hurt.axes_missing

    # 구멍이 창 밖으로 빠진 날 — 다시 계산된다 (영영 죽는 것이 아니다)
    healed = next(r for r in _score(frames, config, days[140]) if r.sector_id == "charlie")
    assert healed.m_raw_bp is not None and "M" not in healed.axes_missing


def test_ETF_수가_바뀌면_그_구간_F_축이_빈다(frames, config, days):
    """`delta` 는 80일째에 ETF 가 하나 더 붙는다 — 좌수 합이 **구성 변화만으로**
    점프한다. 그걸 "돈이 들어왔다"고 읽으면 거짓이다 (계획서 D-2 ④)."""
    during = next(r for r in _score(frames, config, days[85]) if r.sector_id == "delta")
    assert during.f_raw_bp is None and "F" in during.axes_missing

    after = next(r for r in _score(frames, config, days[110]) if r.sector_id == "delta")
    assert after.f_raw_bp is not None, "구성이 안정되면 다시 계산돼야 한다"


def test_중앙값절대편차가_0_이면_평균절대편차로_내려간다(frames, config, days):
    """t=130 에 네 섹터의 폭이 같아 MAD 가 0 이 된다.

    🔴 그때 축을 통째로 버리면 **나머지 섹터의 차이를 잃는다**(실데이터에서
       266일 중 45일이 이 상황이었고, 중앙값과 다른 섹터가 늘 2개 이상이었다).
       척도만 한 단 내리고, 내렸다는 사실을 `axes_degraded` 가 기록한다.
    """
    rows = _score(frames, config, days[130])
    assert all("B" in r.axes_degraded for r in rows)
    # 🔒 그러고도 z 는 나온다 — 버리지 않았다는 뜻이다
    assert all(r.b_z_bp is not None for r in rows)
    # 🔒 같은 폭을 가진 넷은 z 가 같고, 그 값은 중앙값이므로 0 이다
    same = [r for r in rows if r.sector_id in ("alpha", "bravo", "charlie", "foxtrot")]
    assert {r.b_z_bp for r in same} == {0}

    # 평범한 날에는 내려가지 않는다 — 폴백이 상시 켜져 있으면 장치가 아니다
    ordinary = _score(frames, config, days[129])
    assert all("B" not in r.axes_degraded for r in ordinary)


def test_공통상수를_더해도_z_가_정확히_같다():
    """🔴 V28 의 답이 서는 자리 — **횡단면 표준화는 공통 상수를 소거한다.**

    M 축의 `r20_mkt` 는 모든 섹터에 같은 값으로 들어간다. 그러므로 시장 기준선을
    무엇으로 고르든 z 는 움직이지 않는다 — 중앙값도 편차도 함께 이동하기 때문이다.

    여기서는 **정확히** 같아야 한다. 아래 통합 시험과 달리 bp 양자화를 거치지
    않아서다. 두 시험이 따로 있는 이유가 그것이다.
    """
    raw = {"a": -1200, "b": -30, "c": 0, "d": 45, "e": 900, "f": None}
    base, base_kind = scoring._standardize(raw)
    for offset in (-50_000, -7, 1, 12_345):
        shifted = {k: (None if v is None else v + offset) for k, v in raw.items()}
        moved, kind = scoring._standardize(shifted)
        assert kind == base_kind
        assert moved == base, f"공통 상수 {offset} 가 z 를 움직였다"


def test_시장_기준선을_바꿔도_순위가_거의_유지된다(frames, config, days):
    """위 성질의 귀결 — 기준선은 **점수가 아니라 설명** 때문에 고른다.

    다만 이 층에서는 정확히 같지 않다. 원시값을 **먼저 bp 로 양자화한 뒤** z 를
    계산하기 때문이다(그래야 게시된 raw 로 앱이 z 를 다시 구할 수 있다). 기준선이
    바뀌면 같은 날 다른 섹터의 반올림이 1bp 씩 달라지고, 그것이 중앙값·MAD 를
    조금 옮긴다.

    🔴 그 잔차의 크기는 **섹터 수에 달렸다.** 합성 픽스처는 6섹터뿐이라 MAD 가
       거칠어 z 가 최대 0.12σ 까지 흔들린다. 실제 21섹터에서는 5985행을 전부 재서
       raw 4354bp 가 움직이는 동안 z 98bp · 점수 40bp · 순위가 달라진 행 18/5985
       였다(2026-09-11 실측). 그래서 여기서는 **순위 안정성**으로 본다.

    그러니 동일가중을 고른 근거는 "점수가 더 낫다"가 아니라 축 분해 표에
    **"시장 대비"라고 적을 수 있다**는 것이다.
    """
    sector, market = frames
    shifted = market.copy()
    shifted["eqw_idx_bp"] = (market["eqw_idx_bp"] * 2 + 3000).astype("Int64")

    base = scoring.score_history(sector, market, as_of=days[-1],
                                 config=config, fetched_at=FETCHED_AT)
    swapped = scoring.score_history(sector, shifted, as_of=days[-1],
                                    config=config, fetched_at=FETCHED_AT)
    assert [ (r.bas_dd, r.sector_id) for r in base ] == [ (r.bas_dd, r.sector_id) for r in swapped ]

    raw_moved = max(
        abs((a.m_raw_bp or 0) - (b.m_raw_bp or 0)) for a, b in zip(base, swapped)
    )
    rank_moved = sum(1 for a, b in zip(base, swapped) if a.rank_balanced != b.rank_balanced)
    assert raw_moved > 0, "기준선이 실제로 움직이지 않았다면 이 시험은 무의미하다"
    assert rank_moved * 100 <= len(base), (
        f"순위가 달라진 행 {rank_moved}/{len(base)} — 1% 를 넘으면 기준선이 점수를 "
        f"실제로 바꾸고 있다는 뜻이다"
    )


def test_점수는_clip_범위를_넘지_않는다(frames, config, days):
    """z 는 `clip(±3)` 이고 점수는 그 **가중평균**이라 원소 범위를 벗어날 수 없다.

    넘었다면 표준화가 깨진 것이다 — `gate._check_score_bounds` 가 게시 직전에
    같은 것을 본다.
    """
    limit = int(scoring.CLIP_Z * 10_000)
    rows = scoring.score_history(frames[0], frames[1], as_of=days[-1],
                                 config=config, fetched_at=FETCHED_AT)
    for row in rows:
        for value in (row.m_z_bp, row.f_z_bp, row.b_z_bp, row.v_z_bp,
                      row.score_balanced_bp, row.score_momentum_bp,
                      row.score_contrarian_bp):
            assert value is None or abs(value) <= limit, (row.bas_dd, row.sector_id)


def test_순위는_점수와_어긋나지_않는다(frames, config, days):
    """점수가 없으면 순위도 없고, 있으면 1..N 으로 이어진다."""
    for index in (0, 60, 130, len(days) - 1):
        rows = _score(frames, config, days[index])
        for preset in scoring.PRESETS:
            score_of = {r.sector_id: getattr(r, f"score_{preset}_bp") for r in rows}
            rank_of = {r.sector_id: getattr(r, f"rank_{preset}") for r in rows}
            graded = [s for s in score_of if score_of[s] is not None]
            assert sorted(rank_of[s] for s in graded) == list(range(1, len(graded) + 1))
            assert all(rank_of[s] is None for s in score_of if s not in graded)
            ordered = sorted(graded, key=lambda s: (-score_of[s], s))
            assert [rank_of[s] for s in ordered] == list(range(1, len(graded) + 1))


def test_날짜축이_갈라지면_조용히_교집합을_쓰지_않는다(frames, config, days):
    """🔴 M 축이 **다른 기간끼리** 빼는 것을 막는 장치. 던지지 않고 넘어가면
    그 뺄셈은 틀렸다고 말해 주지 않는다."""
    sector, market = frames
    short = market[market["bas_dd"] < days[-1]]
    with pytest.raises(scoring.ScoringError, match="날짜 축"):
        scoring.score(sector, short, as_of=days[-1], config=config, fetched_at=FETCHED_AT)


def test_섹터가_중복되면_던진다(frames, config, days):
    import pandas as pd

    sector, market = frames
    doubled = pd.concat([sector, sector[sector["bas_dd"] == days[-1]]])
    with pytest.raises(scoring.ScoringError, match="중복"):
        scoring.score(doubled, market, as_of=days[-1], config=config, fetched_at=FETCHED_AT)


def test_프리셋_가중치의_합이_100_이다():
    """계획서 D-4 의 세 프리셋. 🔒 합이 100 이 아니면 화면의 '35/30/20/15' 가 거짓이 된다."""
    for name, weights in scoring.PRESETS.items():
        assert set(weights) == set(scoring.AXES), name
        assert sum(weights.values()) == 100, name


def test_유동성은_판정불가와_미달을_구별한다(frames, config, days):
    """🔒 "아직 모른다"(창이 안 참)와 "미달이다"는 다른 말이다.

    같게 그리면 신규 상장 ETF 가 이유 없이 경고를 받는다.
    """
    early = {r.sector_id: r for r in _score(frames, config, days[5])}
    assert all(r.liquidity_ok is None for r in early.values()), "창이 차기 전이다"

    late = {r.sector_id: r for r in _score(frames, config, days[-1])}
    assert late["bravo"].liquidity_ok is False, "거래대금이 1억 미만인 섹터다"
    assert late["alpha"].liquidity_ok is True
    assert late["echo"].liquidity_ok is None, "ETF 가 없으면 판정할 재료가 없다"


# ── 재현성 — 게시본만으로 같은 점수에 닿는가 (2026-09-17) ────────────────────
# 🔴 앱은 `*_z_bp` 밖에 못 본다. 슬라이더가 그 열을 가중합해 점수를 다시 내는데,
#    게시된 점수가 그 방식으로 나온 값이 아니면 **같은 화면의 표와 근거가 다른
#    숫자를 말한다.** 실측에서 최근 20영업일 창의 첫날이 갈려 한 섹터의 순위
#    진폭이 표에서 4.4, 근거에서 4.5 로 나왔다. 아래 둘이 그것을 구조적으로 막는다.

def test_게시된_z_로_프리셋_점수를_정확히_재현한다(frames, config, days):
    """🔒 `±1bp 안` 이 아니라 **정확히 같아야** 한다. 1bp 가 순위를 뒤집는 날이 있다."""
    for day in (days[-1], days[len(days) // 2], days[MILESTONE_INDEXES[5]]):
        rows = _score(frames, config, day)
        for row in rows:
            z_bp = {"M": row.m_z_bp, "F": row.f_z_bp, "B": row.b_z_bp, "V": row.v_z_bp}
            for name, weights in scoring.PRESETS.items():
                again = scoring.weighted_score_bp(z_bp, weights)
                stored = getattr(row, f"score_{name}_bp")
                assert again == stored, f"{day} {row.sector_id} {name}: {again} != {stored}"


def test_게시된_z_로_프리셋_순위를_정확히_재현한다(frames, config, days):
    """점수가 같아도 tie-break 가 갈리면 순위가 달라진다 — 그것까지 고정한다."""
    for day in (days[-1], days[len(days) // 2]):
        rows = _score(frames, config, day)
        for name, weights in scoring.PRESETS.items():
            again = scoring.rank_scores({
                r.sector_id: scoring.weighted_score_bp(
                    {"M": r.m_z_bp, "F": r.f_z_bp, "B": r.b_z_bp, "V": r.v_z_bp}, weights)
                for r in rows})
            stored = {r.sector_id: getattr(r, f"rank_{name}") for r in rows}
            assert again == stored, f"{day} {name}"


def test_가중치를_배로_올려도_점수가_같다(frames, config, days):
    """`Σw·z/Σw` 는 가중치 스칼라배에 불변이다 — 35/30/20/15 == 70/60/40/30.

    🔒 슬라이더는 합이 100 이 아닌 값을 낸다. 이 성질이 없으면 "합을 100 으로 맞춰
       주세요" 라는 요구가 화면에 생기고, 그건 재정규화를 두 번 하는 것이다.
    🔒 **미리 합 100 으로 정규화하면 깨진다** — 정수 나눗셈이 끼기 때문이다.
    """
    rows = _score(frames, config, days[-1])
    for row in rows:
        z_bp = {"M": row.m_z_bp, "F": row.f_z_bp, "B": row.b_z_bp, "V": row.v_z_bp}
        for name, weights in scoring.PRESETS.items():
            doubled = {a: w * 2 for a, w in weights.items()}
            assert scoring.weighted_score_bp(z_bp, doubled) == \
                   scoring.weighted_score_bp(z_bp, weights), f"{row.sector_id} {name}"


def test_살아있는_축의_가중치가_전부_0_이면_점수가_없다():
    """🔒 0 점이 아니라 **없음**이다 (ADR-SC-0007). 화면이 그 섹터를 지워야 한다."""
    assert scoring.weighted_score_bp({"M": 1000, "F": None}, {"M": 0, "F": 50}) is None
    assert scoring.weighted_score_bp({"M": 1000}, {"M": 0}) is None
    assert scoring.weighted_score_bp({"M": None}, {"M": 35}) is None
    # 살아 있는 축이 하나라도 가중치를 받으면 점수가 있다
    assert scoring.weighted_score_bp({"M": 1000, "F": None}, {"M": 35, "F": 30}) == 1000
