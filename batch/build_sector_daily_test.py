"""`batch/build_sector_daily.py` 테스트 — 원천 파일을 날짜 프레임으로 묶는 부분.

집계 산술 자체는 `sector/aggregate_test.py` 가 본다. 여기서 보는 것은 **파일을
어떻게 읽고 무엇을 버리는가** 다 — 그 판단이 조용히 틀리면 20일 창이 20일이 아니게 된다.

🔒 합성 원천만 쓴다 (제약 10).
"""

from __future__ import annotations

import gzip
import json
from pathlib import Path

import pytest

from batch.build_sector_daily import build_frames, scan_raw, summarize
from sector.aggregate import aggregate
from sector.sector_master import Instrument, Sector, SectorMaster


def etf_row(bas_dd, code="091160"):
    return {
        "BAS_DD": bas_dd, "ISU_CD": code, "ISU_NM": "합성 ETF",
        "TDD_CLSPRC": "10000", "CMPPREVDD_PRC": "0", "FLUC_RT": "1.00",
        "NAV": "10000.00", "TDD_OPNPRC": "10000", "TDD_HGPRC": "10000",
        "TDD_LWPRC": "10000", "ACC_TRDVOL": "100", "ACC_TRDVAL": "1000000",
        "MKTCAP": "1000000", "INVSTASST_NETASST_TOTAMT": "1000000",
        "LIST_SHRS": "100", "IDX_IND_NM": "합성지수",
        "OBJ_STKPRC_IDX": "100.00", "CMPPREVDD_IDX": "0.00", "FLUC_RT_IDX": "0.00",
    }


def stk_row(bas_dd, code="005930", mkt="KOSPI"):
    return {
        "BAS_DD": bas_dd, "ISU_CD": code, "ISU_NM": "합성 종목",
        "MKT_NM": mkt, "SECT_TP_NM": "",
        "TDD_CLSPRC": "70000", "CMPPREVDD_PRC": "0", "FLUC_RT": "0.50",
        "TDD_OPNPRC": "70000", "TDD_HGPRC": "70000", "TDD_LWPRC": "70000",
        "ACC_TRDVOL": "100", "ACC_TRDVAL": "7000000",
        "MKTCAP": "7000000", "LIST_SHRS": "100",
    }


def write(path: Path, rows: list[dict]) -> None:
    payload = {"OutBlock_1": rows}
    if path.suffix == ".gz":
        with gzip.open(path, "wt", encoding="utf-8") as fp:
            json.dump(payload, fp, ensure_ascii=False)
    else:
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


@pytest.fixture
def raw(tmp_path):
    d = tmp_path / "raw"
    d.mkdir()
    return d


MASTER = SectorMaster(
    version="2026-09-11", source_notice="한국거래소 통계정보", gics_sectors=(),
    sectors=(Sector(
        id="semi", name_ko="반도체", gics="Information Technology",
        note="합성 테스트용 섹터다. 근거는 테스트 코드가 말한다.",
        etfs=(Instrument("091160", "합성 ETF"),),
        members=(Instrument("005930", "합성 종목"),),
    ),),
    config_sha256="0" * 64,
)


# ── 파일 훑기 ────────────────────────────────────────────────────────────────

def test_gzip과_평문을_모두_읽는다(raw):
    write(raw / "etf_bydd_trd_20260105.json", [etf_row("20260105")])
    write(raw / "stk_bydd_trd_20260105.json.gz", [stk_row("20260105")])
    found = scan_raw(raw)
    assert set(found["20260105"]) == {"etf", "stk"}


def test_같은_날짜면_gzip을_쓴다(raw):
    """`--probe` 가 남긴 평문은 M3 점검의 잔재다. 배치가 나중에 받은 것을 믿는다."""
    write(raw / "etf_bydd_trd_20260105.json", [etf_row("20260105")])
    write(raw / "etf_bydd_trd_20260105.json.gz", [etf_row("20260105")])
    assert scan_raw(raw)["20260105"]["etf"].suffix == ".gz"


def test_관계없는_파일은_무시한다(raw):
    (raw / "probe_holiday.json").write_text("{}", encoding="utf-8")
    (raw / "메모.txt").write_text("x", encoding="utf-8")
    write(raw / "etf_bydd_trd_20260105.json", [etf_row("20260105")])
    assert list(scan_raw(raw)) == ["20260105"]


def test_원천_폴더가_없으면_빈_결과다(tmp_path):
    assert scan_raw(tmp_path / "없다") == {}


# ── 🔴 무엇을 버리는가 ───────────────────────────────────────────────────────

def test_휴일은_프레임에서_뺀다(raw):
    """🔴 지수가 전진하지 않는 날을 20일 창에 세면 "20영업일"이 길어진다."""
    write(raw / "etf_bydd_trd_20260105.json", [etf_row("20260105")])
    write(raw / "etf_bydd_trd_20260106.json", [])          # 휴일로 확정된 날
    write(raw / "etf_bydd_trd_20260107.json", [etf_row("20260107")])
    frames = build_frames(scan_raw(raw), as_of="20260107")
    assert [f[0] for f in frames] == ["20260105", "20260107"]


def test_오름차순으로_준다(raw):
    """🔒 집계 엔진이 순서를 확인하고 던진다 — 여기서 정렬해야 한다."""
    for day in ("20260107", "20260105", "20260106"):
        write(raw / f"etf_bydd_trd_{day}.json", [etf_row(day)])
    assert [f[0] for f in build_frames(scan_raw(raw), as_of="20260107")] == [
        "20260105", "20260106", "20260107"
    ]


def test_as_of_이후_파일은_읽지도_않는다(raw):
    write(raw / "etf_bydd_trd_20260105.json", [etf_row("20260105")])
    write(raw / "etf_bydd_trd_20260109.json", [etf_row("20260109")])
    assert [f[0] for f in build_frames(scan_raw(raw), as_of="20260105")] == ["20260105"]


def test_두_시장이_한_프레임으로_합쳐진다(raw):
    write(raw / "stk_bydd_trd_20260105.json", [stk_row("20260105")])
    write(raw / "ksq_bydd_trd_20260105.json",
          [stk_row("20260105", code="035720", mkt="KOSDAQ")])
    frames = build_frames(scan_raw(raw), as_of="20260105")
    assert len(frames[0][2]) == 2


def test_날짜가_어긋난_파일은_던진다(raw):
    """파일 이름의 날짜와 내용의 `BAS_DD` 가 다르면 stale 이거나 룩어헤드다."""
    write(raw / "etf_bydd_trd_20260105.json", [etf_row("20260106")])
    with pytest.raises(Exception, match="BAS_DD"):
        build_frames(scan_raw(raw), as_of="20260105")


# ── 요약 ─────────────────────────────────────────────────────────────────────

def test_요약은_시세값을_찍지_않는다(raw):
    """🔒 약관 제11조② — 요약이 값을 흘리면 `data/raw/` 로 막은 것이 표준출력으로 샌다."""
    for day in ("20260105", "20260106"):
        write(raw / f"etf_bydd_trd_{day}.json", [etf_row(day)])
        write(raw / f"stk_bydd_trd_{day}.json", [stk_row(day)])
    result = aggregate(
        build_frames(scan_raw(raw), as_of="20260106"), MASTER,
        as_of="20260106", fetched_at="2026-09-11T00:00:00Z",
    )
    text = summarize(result)
    for leak in ("10000", "70000", "7000000", "1000000"):
        assert leak not in text
    assert "영업일 2일" in text


# ── 🔴 저장 자료형 ───────────────────────────────────────────────────────────

def test_결측이_있어도_정수열이_float로_새지_않는다(raw, tmp_path):
    """🔴 규약이 금지한 float 가 저장 계층에 들어오는 가장 흔한 경로다.

    `None` 이 섞인 정수 열을 그냥 넣으면 pandas 가 `float64` 로 올리고,
    스냅샷이 `10000` 대신 `10000.0` 이 되어 골든 테스트가 환경마다 흔들린다.
    """
    pd = pytest.importorskip("pandas")
    pytest.importorskip("pyarrow")
    from batch.build_sector_daily import _write_parquet, _SECTOR_COLUMNS

    # 이틀만 준다 — 20일 창이 차지 않아 수익률·폭 열이 전부 `None` 이 된다
    for day in ("20260105", "20260106"):
        write(raw / f"etf_bydd_trd_{day}.json", [etf_row(day)])
        write(raw / f"stk_bydd_trd_{day}.json", [stk_row(day)])
    result = aggregate(
        build_frames(scan_raw(raw), as_of="20260106"), MASTER,
        as_of="20260106", fetched_at="2026-09-11T00:00:00Z",
    )
    assert any(r.etf_ret_20d_bp is None for r in result.sectors)   # 전제 확인

    out = tmp_path / "sector_daily.parquet"
    _write_parquet(result.sectors, out, columns=_SECTOR_COLUMNS)
    frame = pd.read_parquet(out)
    assert [c for c, t in frame.dtypes.items() if "float" in str(t)] == []
    assert str(frame["etf_ret_20d_bp"].dtype) == "Int64"


def test_자료형을_손으로_적지_않고_주석에서_끌어낸다():
    """필드가 늘 때 표 고치기를 잊으면 그 열만 조용히 float 로 돌아간다."""
    from batch.build_sector_daily import pandas_dtypes
    from sector.aggregate import SectorDailyRow

    dtypes = pandas_dtypes(SectorDailyRow)
    assert dtypes["etf_idx_bp"] == "Int64"       # int | None
    assert dtypes["etf_n"] == "Int64"            # int
    assert dtypes["is_partial"] == "boolean"
    assert dtypes["bas_dd"] == "string"
    assert "source_ids" not in dtypes            # tuple[str, ...] 은 손대지 않는다


# ── 🔴 휴일 판정은 달력이 아니라 데이터로 ────────────────────────────────────

def holiday_etf_row(bas_dd, code="091160"):
    """시장 휴일의 `etp/etf_bydd_trd` 행 — 원천 실관찰(2026-09-11) 재현.

    🔴 0행이 아니다. 1000행 넘게 오는데 가격 칸이 **전부 빈 문자열**이고
       `LIST_SHRS` 만 전일과 같은 값으로 들어 있다.
    """
    row = etf_row(bas_dd, code)
    for field in ("TDD_CLSPRC", "CMPPREVDD_PRC", "FLUC_RT", "NAV", "TDD_OPNPRC",
                  "TDD_HGPRC", "TDD_LWPRC", "ACC_TRDVOL", "ACC_TRDVAL", "MKTCAP",
                  "INVSTASST_NETASST_TOTAMT", "OBJ_STKPRC_IDX", "CMPPREVDD_IDX",
                  "FLUC_RT_IDX"):
        row[field] = ""
    return row                      # LIST_SHRS 는 값이 남는다


def test_휴일에_ETF가_행을_줘도_영업일로_세지_않는다(raw):
    """🔴 이걸 놓치면 자금흐름 축의 "20영업일 전"이 20영업일이 아니게 된다.

    좌수(`LIST_SHRS`)는 휴일에도 값이 차 있어, 행을 남기면 축 입력이 조용히 찬다.
    """
    write(raw / "etf_bydd_trd_20260105.json", [etf_row("20260105")])
    write(raw / "etf_bydd_trd_20260106.json", [holiday_etf_row("20260106")])  # 휴일
    write(raw / "stk_bydd_trd_20260106.json", [])                             # 주식은 0행
    write(raw / "etf_bydd_trd_20260107.json", [etf_row("20260107")])
    frames = build_frames(scan_raw(raw), as_of="20260107")
    assert [f[0] for f in frames] == ["20260105", "20260107"]


def test_장이_열렸는데_일부만_빠진_날은_남긴다(raw):
    """휴일과 결측은 다른 사건이다 — 후자는 `is_partial` 이 말해야 한다."""
    write(raw / "etf_bydd_trd_20260105.json",
          [etf_row("20260105"), holiday_etf_row("20260105", code="091170")])
    frames = build_frames(scan_raw(raw), as_of="20260105")
    assert [f[0] for f in frames] == ["20260105"]     # 한 종목이라도 거래됐다
