"""`batch/fetch_daily.py` 테스트 — 네트워크를 부르지 않는다.

전송은 `fetch` 인자로 주입한다. 🔒 실제 KRX 를 부르는 테스트는 두지 않는다 —
언제 돌려도 같은 답이어야 하고(`AGENTS.md` 5장), 키 예산을 테스트가 태우면 안 된다.
"""

from __future__ import annotations

import gzip
import json
from datetime import date

import pytest

from batch import fetch_daily
from sector.sources.krx_common import KrxAuthError, KrxTransportError


@pytest.fixture
def raw(tmp_path, monkeypatch):
    """`data/raw/` 를 tmp 로 돌린다. 🔒 테스트가 진짜 원천 폴더를 건드리지 않는다."""
    target = tmp_path / "data" / "raw"
    target.mkdir(parents=True)
    monkeypatch.setattr(fetch_daily, "raw_output_path",
                        lambda prefix, bas_dd, compressed=False:
                        target / f"{prefix}_{bas_dd}.json{'.gz' if compressed else ''}")
    monkeypatch.setattr(fetch_daily, "assert_raw_output_allowed", lambda path: None)
    return target


ETF_ONLY = (fetch_daily.Endpoint("etf", "etp/etf_bydd_trd", "etf_bydd_trd", "ETF"),)


def payload(n: int) -> dict:
    return {"OutBlock_1": [{"ISU_CD": f"{i:06d}"} for i in range(n)]}


def run(days, raw, *, rows=3, today=date(2026, 9, 11), **kw):
    calls: list[str] = []

    def fake(path, bas_dd, *, api_label, **_):
        calls.append(bas_dd)
        return payload(rows)

    tally = fetch_daily.fetch_range(
        days, targets=ETF_ONLY, today=today, log=lambda _: None,
        fetch=kw.pop("fetch", fake), sleep=lambda _: None, **kw,
    )
    return tally, calls


# ── 영업일 ───────────────────────────────────────────────────────────────────

def test_주말은_요청하지_않는다():
    """달력이 확실히 아는 것을 원천에 묻지 않는다 — 요청 예산을 그렇게 아낀다."""
    # 2026-09-11(금) ~ 2026-09-14(월)
    days = fetch_daily.business_days(date(2026, 9, 11), date(2026, 9, 14))
    assert days == ["20260911", "20260914"]


def test_공휴일_표를_코드에_박지_않는다():
    """공휴일은 주말과 달리 요청한다 — 표가 틀리면 조용히 하루가 빈다."""
    # 2026-01-01 은 신정(목)이지만 목록에 남고, 원천이 0건으로 답하게 둔다
    assert "20260101" in fetch_daily.business_days(date(2025, 12, 29), date(2026, 1, 2))


# ── 🔴 빈 응답을 휴일로 굳히지 않는다 ────────────────────────────────────────

def test_최근_빈_응답은_저장하지_않는다(raw):
    """🔴 영업일 익일 08:00 갱신 전이면 0건이 정상이다. 저장하면 영구 결측이 된다."""
    tally, _ = run(["20260910"], raw, rows=0, today=date(2026, 9, 11))
    assert tally["unsettled"] == 1 and tally["saved"] == 0
    assert list(raw.iterdir()) == []          # 파일을 남기지 않는다


def test_오래된_빈_응답은_휴일로_확정한다(raw):
    """그래야 다음 실행이 공휴일을 다시 묻지 않는다 — 예산이 산다."""
    tally, _ = run(["20260101"], raw, rows=0, today=date(2026, 9, 11))
    assert tally["holiday"] == 1 and tally["saved"] == 1
    saved = raw / "etf_bydd_trd_20260101.json.gz"
    with gzip.open(saved, "rt", encoding="utf-8") as fp:
        assert json.load(fp)["OutBlock_1"] == []


# ── 멱등 ─────────────────────────────────────────────────────────────────────

def test_이미_받은_날은_다시_묻지_않는다(raw):
    """끊긴 배치를 그냥 다시 돌리면 이어진다."""
    _, first = run(["20260101", "20260102"], raw)
    _, second = run(["20260101", "20260102"], raw)
    assert first == ["20260101", "20260102"]
    assert second == []                        # 한 번도 부르지 않았다


def test_평문_파일도_받은_것으로_센다(raw):
    """M3 `--probe` 가 남긴 `.json` 을 gzip 으로 다시 받지 않는다."""
    (raw / "etf_bydd_trd_20260101.json").write_text("{}", encoding="utf-8")
    _, calls = run(["20260101"], raw)
    assert calls == []


def test_force면_다시_받는다(raw):
    run(["20260101"], raw)
    _, calls = run(["20260101"], raw, force=True)
    assert calls == ["20260101"]


def test_반쪽_파일을_남기지_않는다(raw):
    """원자적으로 쓴다 — 중간에 끊긴 파일이 남으면 다음 실행이 "받았다"고 믿는다."""
    run(["20260101"], raw)
    assert [p.name for p in raw.iterdir()] == ["etf_bydd_trd_20260101.json.gz"]


# ── 실패 ─────────────────────────────────────────────────────────────────────

def test_401은_전체를_멈춘다(raw):
    """재시도해도 같은 답이고, 승인 문제라면 남은 요청이 전부 헛돈다."""
    def boom(*a, **k):
        raise KrxAuthError("승인되지 않았다")
    with pytest.raises(KrxAuthError):
        run(["20260101", "20260102"], raw, fetch=boom)


def test_전송실패는_그날만_건너뛴다(raw):
    """하루 실패로 1년치를 버리지 않는다. 다시 돌리면 실패분만 재시도한다."""
    def flaky(path, bas_dd, *, api_label, **_):
        if bas_dd == "20260101":
            raise KrxTransportError("끊겼다")
        return payload(3)
    tally, _ = run(["20260101", "20260102"], raw, fetch=flaky)
    assert tally["failed"] == 1 and tally["saved"] == 1
