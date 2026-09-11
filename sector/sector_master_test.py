"""섹터 마스터 검증기 테스트.

🔒 **픽스처는 전부 합성이다.** 실제 KRX 데이터를 커밋하지 않는다 (제약 10 ·
   약관 제11조② → ADR-SC-0006). 코드는 `[0-9A-Z]{6}` 형식만 맞춘 가짜다.

★ 예외가 하나 있다 — `test_real_config_*` 는 **커밋된 `sectors.yaml` 자체**를
  읽는다. 그것은 KRX 데이터가 아니라 사람이 쓴 설정이고, "정본이 언제나 규칙을
  통과한다"는 것이 M4 의 실제 완료 조건이다.

★ CLI(`batch/validate_config.py`) 테스트가 왜 `sector/` 에 있는가 —
  🔒 루트 `pytest.ini` 가 `testpaths = sector` 다. 그 설정은 두 러너(v3.0 · 동결
  v2.0)를 가르는 장치라 바꾸지 않는다 (→ `AGENTS.md` 5장). 그래서 CLI 의 종료코드
  계약만 여기서 확인한다.
"""

from __future__ import annotations

import json

import pytest
import yaml

from batch import validate_config as cli
from sector.sector_master import (
    GICS_SECTORS,
    LIQUIDITY_GATE_KRW,
    SectorConfigError,
    Universe,
    has_errors,
    load,
    load_universe,
    validate,
)

# ── 합성 픽스처 ──────────────────────────────────────────────────────────────

_NOTE = "합성 섹터다. 근거 게이트를 통과할 만큼 길게 적은 한 문장이다."
_EMPTY_REASON = "합성 설정에서는 이 대분류를 쓰지 않는다는 것을 밝히는 한 문장이다."


def _gics_block(used: set[str]) -> list[dict]:
    """GICS 11 을 전부 선언한다. 쓰이지 않는 대분류에는 `empty_reason` 을 넣는다."""
    block = []
    for i, name in enumerate(GICS_SECTORS):
        entry: dict[str, str] = {"id": name, "name_ko": f"대분류{i}"}
        if name not in used:
            entry["empty_reason"] = _EMPTY_REASON
        block.append(entry)
    return block


def _sector(sector_id: str = "alpha", **over) -> dict:
    base = {
        "id": sector_id,
        "name_ko": f"섹터-{sector_id}",
        "gics": "Industrials",
        "etfs": [{"code": "0001A0", "name": "합성ETF 알파"}],
        "members": [
            {"code": "000001", "name": "합성종목1"},
            {"code": "000002", "name": "합성종목2"},
            {"code": "000003", "name": "합성종목3"},
        ],
        "note": _NOTE,
    }
    base.update(over)
    return base


def _doc(sectors: list[dict] | None = None, **over) -> dict:
    sectors = sectors if sectors is not None else [_sector()]
    doc = {
        "version": "2026-09-11",
        "source_notice": "한국거래소 통계정보",
        "gics_sectors": _gics_block({s["gics"] for s in sectors}),
        "sectors": sectors,
    }
    doc.update(over)
    return doc


def _write(tmp_path, doc: dict):
    path = tmp_path / "sectors.yaml"
    path.write_text(yaml.safe_dump(doc, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return path


def _universe(**over) -> Universe:
    base = {
        "bas_dds": ("20260910",),
        "etf_names": {"0001A0": "합성ETF 알파", "0002B0": "합성ETF 베타"},
        "stock_names": {f"00000{i}": f"합성종목{i}" for i in (1, 2, 3, 4)},
        "etf_gate_pass": frozenset({"0001A0", "0002B0"}),
    }
    base.update(over)
    return Universe(**base)


def _codes(findings, rule: str) -> list[str]:
    return [f.where for f in findings if f.rule == rule]


def _levels(findings, rule: str) -> set[str]:
    return {f.level for f in findings if f.rule == rule}


# ── 커밋된 정본 — 이 둘이 M4 의 실제 완료 조건이다 ──────────────────────────

def test_real_config_loads():
    master = load()
    assert master.version
    assert master.source_notice == "한국거래소 통계정보"   # 약관 제10조③
    assert len(master.gics_sectors) == len(GICS_SECTORS)
    assert len(master.sectors) >= 21
    assert len(master.config_sha256) == 64


def test_real_config_has_no_structural_errors():
    """🔒 정본은 **원천 없이도** 구조 규칙을 전부 통과해야 한다.

    실재 확인(코드가 원천에 있는가)은 `data/raw/` 스냅샷이 필요해 CI 가 아니라
    사람이 돌린다. 그러나 id·note·GICS 커버리지·코드 형식은 언제나 참이어야 한다.
    """
    findings = validate(load(), universe=None)
    errors = [f.render() for f in findings if f.level == "error"]
    assert errors == [], "정본에 구조 오류가 있다:\n" + "\n".join(errors)


def test_real_config_every_sector_has_note():
    """근거 없는 묶음을 막는 게이트가 실제로 채워져 있는가."""
    for sector in load().sectors:
        assert len("".join(sector.note.split())) >= 20, sector.id


# ── 코드 형식 — V22 회귀 ────────────────────────────────────────────────────

def test_code_with_letters_is_accepted(tmp_path):
    """🔴 V22 — 신규 상장 코드에는 영문자가 있다. `\\d{6}` 으로 좁히면 ETF 304개가 사라진다."""
    doc = _doc([_sector(etfs=[{"code": "0091P0", "name": "합성ETF"}],
                        members=[{"code": "18064K", "name": "합성우선주"},
                                 {"code": "000002", "name": "합성종목2"},
                                 {"code": "000003", "name": "합성종목3"}])])
    findings = validate(load(_write(tmp_path, doc)), _universe(
        etf_names={"0091P0": "합성ETF"},
        stock_names={"18064K": "합성우선주", "000002": "합성종목2", "000003": "합성종목3"},
        etf_gate_pass=frozenset({"0091P0"}),
    ))
    assert _codes(findings, "code-format") == []
    assert not has_errors(findings)


@pytest.mark.parametrize("bad", ["00593", "0059300", "00593a", "005-93", "00 930"])
def test_malformed_code_is_error(tmp_path, bad):
    doc = _doc([_sector(etfs=[{"code": bad, "name": "합성ETF"}])])
    findings = validate(load(_write(tmp_path, doc)), _universe())
    assert _levels(findings, "code-format") == {"error"}


# ── 모양 오류는 던진다 (규칙 위반과 구별한다) ───────────────────────────────

def test_unknown_top_level_key_is_rejected(tmp_path):
    path = _write(tmp_path, _doc(sourec_notice="오타"))
    with pytest.raises(SectorConfigError, match="모르는 키"):
        load(path)


def test_unknown_sector_key_is_rejected(tmp_path):
    """🔒 `liquidity_warn` 같은 오타를 통과시키면 경고가 조용히 사라진다."""
    path = _write(tmp_path, _doc([_sector(liquidity_warn=True)]))
    with pytest.raises(SectorConfigError, match="모르는 키"):
        load(path)


def test_non_bool_liquidity_warning_is_rejected(tmp_path):
    path = _write(tmp_path, _doc([_sector(liquidity_warning="true")]))
    with pytest.raises(SectorConfigError, match="true/false"):
        load(path)


def test_missing_file_is_rejected(tmp_path):
    with pytest.raises(SectorConfigError, match="읽을 수 없다"):
        load(tmp_path / "없는파일.yaml")


def test_broken_yaml_is_rejected(tmp_path):
    path = tmp_path / "sectors.yaml"
    path.write_text("sectors: [unclosed\n", encoding="utf-8")
    with pytest.raises(SectorConfigError, match="YAML"):
        load(path)


# ── note 게이트 ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("note", ["", "   \n  ", "-", "국내 최대 테마"])
def test_thin_note_is_error(tmp_path, note):
    doc = _doc([_sector(note=note)])
    findings = validate(load(_write(tmp_path, doc)), _universe())
    assert _levels(findings, "note") == {"error"}


def test_note_absent_key_is_error(tmp_path):
    sector = _sector()
    del sector["note"]
    findings = validate(load(_write(tmp_path, _doc([sector]))), _universe())
    assert _levels(findings, "note") == {"error"}


# ── id · 이름 ───────────────────────────────────────────────────────────────

def test_duplicate_id_is_error(tmp_path):
    doc = _doc([_sector("alpha"), _sector("alpha", name_ko="다른이름")])
    findings = validate(load(_write(tmp_path, doc)), _universe())
    assert _levels(findings, "id-unique") == {"error"}


def test_duplicate_name_ko_is_error(tmp_path):
    doc = _doc([_sector("alpha", name_ko="같은이름"), _sector("beta", name_ko="같은이름")])
    findings = validate(load(_write(tmp_path, doc)), _universe())
    assert _levels(findings, "name-unique") == {"error"}


@pytest.mark.parametrize("bad_id", ["Alpha", "1alpha", "al-pha", "al pha", "알파"])
def test_bad_id_format_is_error(tmp_path, bad_id):
    doc = _doc([_sector(bad_id)])
    findings = validate(load(_write(tmp_path, doc)), _universe())
    assert _levels(findings, "id-format") == {"error"}


# ── 실재 확인 ───────────────────────────────────────────────────────────────

def test_unknown_member_code_is_error(tmp_path):
    doc = _doc([_sector(members=[{"code": "999999", "name": "상장폐지된것"},
                                 {"code": "000002", "name": "합성종목2"},
                                 {"code": "000003", "name": "합성종목3"}])])
    findings = validate(load(_write(tmp_path, doc)), _universe())
    assert _levels(findings, "exists") == {"error"}


def test_unknown_etf_code_is_error(tmp_path):
    doc = _doc([_sector(etfs=[{"code": "999990", "name": "없는ETF"}])])
    findings = validate(load(_write(tmp_path, doc)), _universe())
    assert _levels(findings, "exists") == {"error"}


def test_name_drift_is_warn_not_error(tmp_path):
    """🔒 사명 변경으로 배치를 멈추지 않는다. 코드가 맞으면 값은 정확히 계산된다.

    멈추게 하면 그 다음부터 사람이 검증기를 끄고, 그때 진짜 오류도 함께 사라진다.
    """
    doc = _doc([_sector(members=[{"code": "000001", "name": "옛사명"},
                                 {"code": "000002", "name": "합성종목2"},
                                 {"code": "000003", "name": "합성종목3"}])])
    findings = validate(load(_write(tmp_path, doc)), _universe())
    assert _levels(findings, "name-drift") == {"warn"}
    assert not has_errors(findings)


def test_missing_universe_is_reported_not_silent(tmp_path):
    """🔒 "검증 통과"가 "코드가 실재한다"는 뜻이 되어야 한다."""
    findings = validate(load(_write(tmp_path, _doc())), universe=None)
    assert _levels(findings, "universe-skip") == {"warn"}


def test_etf_only_universe_reports_member_skip(tmp_path):
    findings = validate(load(_write(tmp_path, _doc())), _universe(stock_names={}))
    assert "(구성종목)" in _codes(findings, "universe-skip")


# ── 두 렌즈 ─────────────────────────────────────────────────────────────────

def test_sector_without_etf_is_error(tmp_path):
    doc = _doc([_sector(etfs=[])])
    findings = validate(load(_write(tmp_path, doc)), _universe())
    assert _levels(findings, "lens") == {"error"}


def test_too_few_members_is_error(tmp_path):
    doc = _doc([_sector(members=[{"code": "000001", "name": "합성종목1"}])])
    findings = validate(load(_write(tmp_path, doc)), _universe())
    assert _levels(findings, "lens") == {"error"}


def test_too_many_members_is_warn(tmp_path):
    members = [{"code": f"{i:06d}", "name": f"합성종목{i}"} for i in range(1, 15)]
    doc = _doc([_sector(members=members)])
    findings = validate(load(_write(tmp_path, doc)),
                        _universe(stock_names={m["code"]: m["name"] for m in members}))
    assert _levels(findings, "lens") == {"warn"}
    assert not has_errors(findings)


def test_code_in_both_lenses_is_error(tmp_path):
    doc = _doc([_sector(etfs=[{"code": "000001", "name": "합성종목1"}])])
    findings = validate(load(_write(tmp_path, doc)), _universe(
        etf_names={"000001": "합성종목1"}, etf_gate_pass=frozenset({"000001"})))
    assert _levels(findings, "code-dup") == {"error"}


def test_duplicate_code_within_list_is_error(tmp_path):
    doc = _doc([_sector(members=[{"code": "000001", "name": "합성종목1"},
                                 {"code": "000001", "name": "합성종목1"},
                                 {"code": "000002", "name": "합성종목2"},
                                 {"code": "000003", "name": "합성종목3"}])])
    findings = validate(load(_write(tmp_path, doc)), _universe())
    assert _levels(findings, "code-dup") == {"error"}


def test_cross_sector_overlap_is_info(tmp_path):
    """🔒 오류가 아니다 — 한화오션(조선·방산)처럼 실제로 겹친다."""
    doc = _doc([_sector("alpha"), _sector("beta", gics="Materials")])
    findings = validate(load(_write(tmp_path, doc)), _universe())
    assert _levels(findings, "overlap") == {"info"}
    assert not has_errors(findings)


# ── 유동성 게이트 ───────────────────────────────────────────────────────────

def test_all_thin_etfs_require_warning(tmp_path):
    doc = _doc([_sector()])
    findings = validate(load(_write(tmp_path, doc)), _universe(etf_gate_pass=frozenset()))
    assert _levels(findings, "liquidity") == {"error"}


def test_declared_warning_satisfies_thin_etfs(tmp_path):
    doc = _doc([_sector(liquidity_warning=True)])
    findings = validate(load(_write(tmp_path, doc)), _universe(etf_gate_pass=frozenset()))
    assert _codes(findings, "liquidity") == []
    assert not has_errors(findings)


def test_warning_on_liquid_sector_is_warn(tmp_path):
    """선언과 원천이 어긋난다 — 고치라고 말하되 배치는 멈추지 않는다."""
    doc = _doc([_sector(liquidity_warning=True)])
    findings = validate(load(_write(tmp_path, doc)), _universe())
    assert _levels(findings, "liquidity") == {"warn"}
    assert not has_errors(findings)


def test_one_thin_leg_is_info(tmp_path):
    doc = _doc([_sector(etfs=[{"code": "0001A0", "name": "합성ETF 알파"},
                              {"code": "0002B0", "name": "합성ETF 베타"}])])
    findings = validate(load(_write(tmp_path, doc)),
                        _universe(etf_gate_pass=frozenset({"0001A0"})))
    assert _levels(findings, "liquidity") == {"info"}
    assert not has_errors(findings)


# ── GICS 커버리지 ───────────────────────────────────────────────────────────

def test_unknown_gics_is_error(tmp_path):
    doc = _doc([_sector(gics="Crypto")])
    doc["gics_sectors"] = _gics_block(set()) + [
        {"id": "Crypto", "name_ko": "가상자산", "empty_reason": _EMPTY_REASON}]
    findings = validate(load(_write(tmp_path, doc)), _universe())
    assert _levels(findings, "gics-coverage") >= {"error"}


def test_missing_gics_declaration_is_error(tmp_path):
    doc = _doc()
    doc["gics_sectors"] = [g for g in doc["gics_sectors"] if g["id"] != "Real Estate"]
    findings = validate(load(_write(tmp_path, doc)), _universe())
    assert any("Real Estate" in f.message for f in findings if f.level == "error")


def test_empty_gics_without_reason_is_error(tmp_path):
    doc = _doc()
    for entry in doc["gics_sectors"]:
        entry.pop("empty_reason", None)
    findings = validate(load(_write(tmp_path, doc)), _universe())
    assert _levels(findings, "gics-coverage") == {"error"}


def test_empty_reason_on_used_gics_is_error(tmp_path):
    """비어 있다고 적혔는데 섹터가 달려 있다 — 둘 중 하나가 거짓이다."""
    doc = _doc()
    for entry in doc["gics_sectors"]:
        if entry["id"] == "Industrials":
            entry["empty_reason"] = _EMPTY_REASON
    findings = validate(load(_write(tmp_path, doc)), _universe())
    assert _levels(findings, "gics-coverage") == {"error"}


def test_thin_empty_reason_is_error(tmp_path):
    doc = _doc()
    for entry in doc["gics_sectors"]:
        if entry["id"] == "Real Estate":
            entry["empty_reason"] = "없음"
    findings = validate(load(_write(tmp_path, doc)), _universe())
    assert _levels(findings, "note") == {"error"}


# ── 메타 ────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("bad", ["2026-9-11", "20260911", "v1", "2026-09-11 수정"])
def test_bad_version_is_error(tmp_path, bad):
    findings = validate(load(_write(tmp_path, _doc(version=bad))), _universe())
    assert _levels(findings, "meta") == {"error"}


def test_altered_source_notice_is_error(tmp_path):
    """약관 제10조③ — 문구를 바꾸면 표시 의무를 어긴다."""
    findings = validate(load(_write(tmp_path, _doc(source_notice="KRX"))), _universe())
    assert _levels(findings, "meta") == {"error"}


def test_config_sha256_follows_bytes(tmp_path):
    path = _write(tmp_path, _doc())
    before = load(path).config_sha256
    path.write_text(path.read_text(encoding="utf-8") + "# 주석 한 줄\n", encoding="utf-8")
    after = load(path).config_sha256
    # 🔒 주석이 바뀌어도 해시가 바뀌는 것이 맞다 — 근거가 바뀌었다는 뜻이다
    assert before != after
    assert len(after) == 64


# ── 유니버스 적재 ───────────────────────────────────────────────────────────

def _raw(tmp_path, name: str, rows: list[dict]):
    (tmp_path / name).write_text(json.dumps({"OutBlock_1": rows}, ensure_ascii=False),
                                 encoding="utf-8")


def test_universe_reads_etf_and_stock_snapshots(tmp_path):
    _raw(tmp_path, "etf_bydd_trd_20260910.json",
         [{"BAS_DD": "20260910", "ISU_CD": "0001A0", "ISU_NM": "합성ETF",
           "ACC_TRDVAL": str(LIQUIDITY_GATE_KRW * 2)}])
    _raw(tmp_path, "stk_bydd_trd_20260910.json",
         [{"BAS_DD": "20260910", "ISU_CD": "000001", "ISU_NM": "합성종목"}])
    _raw(tmp_path, "ksq_bydd_trd_20260910.json",
         [{"BAS_DD": "20260910", "ISU_CD": "000002", "ISU_NM": "합성코스닥"}])
    universe = load_universe(tmp_path)
    assert universe.days == 1
    assert universe.etf_names == {"0001A0": "합성ETF"}
    assert universe.stock_names == {"000001": "합성종목", "000002": "합성코스닥"}
    assert universe.etf_gate_pass == frozenset({"0001A0"})


def test_universe_keeps_zero_and_missing_apart(tmp_path):
    """🔴 V22 의 핵심 불변식 — `"0"`(거래 없음)과 `""`(결측)은 다른 사건이다.

    거래가 없던 날은 평균을 **끌어내려야** 하고, 원천이 주지 않은 날은 평균에서
    **빠져야** 한다. 둘을 같게 만들면 유동성 게이트가 거짓으로 걸린다.
    """
    # 🔒 첫날은 둘 다 게이트에 **딱 걸치는** 값이다. 그래서 둘째 날의 처리만이
    #    결과를 가른다 — 0 을 세면 평균이 절반이 되고, 결측을 빼면 그대로다.
    on_gate = str(LIQUIDITY_GATE_KRW)
    _raw(tmp_path, "etf_bydd_trd_20260909.json", [
        {"BAS_DD": "20260909", "ISU_CD": "0001A0", "ISU_NM": "거래없던ETF", "ACC_TRDVAL": on_gate},
        {"BAS_DD": "20260909", "ISU_CD": "0002B0", "ISU_NM": "결측ETF", "ACC_TRDVAL": on_gate},
    ])
    _raw(tmp_path, "etf_bydd_trd_20260910.json", [
        {"BAS_DD": "20260910", "ISU_CD": "0001A0", "ISU_NM": "거래없던ETF", "ACC_TRDVAL": "0"},
        {"BAS_DD": "20260910", "ISU_CD": "0002B0", "ISU_NM": "결측ETF", "ACC_TRDVAL": ""},
    ])
    universe = load_universe(tmp_path)
    assert universe.days == 2
    # 0 은 세어져 평균이 절반으로 떨어진다 → 게이트 미달
    assert "0001A0" not in universe.etf_gate_pass
    # 결측은 빠져 하루치 평균이 그대로 남는다 → 게이트 통과
    assert "0002B0" in universe.etf_gate_pass


def test_universe_takes_newest_name(tmp_path):
    _raw(tmp_path, "stk_bydd_trd_20260909.json",
         [{"BAS_DD": "20260909", "ISU_CD": "000001", "ISU_NM": "옛사명"}])
    _raw(tmp_path, "stk_bydd_trd_20260910.json",
         [{"BAS_DD": "20260910", "ISU_CD": "000001", "ISU_NM": "새사명"}])
    assert load_universe(tmp_path).stock_names["000001"] == "새사명"


def test_universe_is_empty_when_no_snapshot(tmp_path):
    universe = load_universe(tmp_path / "없는폴더")
    assert not universe
    assert universe.days == 0


def test_broken_snapshot_is_rejected(tmp_path):
    (tmp_path / "etf_bydd_trd_20260910.json").write_text("{}", encoding="utf-8")
    with pytest.raises(SectorConfigError, match="OutBlock_1"):
        load_universe(tmp_path)


# ── CLI 종료코드 계약 ───────────────────────────────────────────────────────

def test_cli_refuses_without_universe(tmp_path, capsys):
    """🔒 형식만 맞는 설정을 '통과'로 보고하지 않는다 — 2 는 1 과 다른 사건이다."""
    path = _write(tmp_path, _doc())
    code = cli.main(["--config", str(path), "--raw-dir", str(tmp_path / "없는폴더")])
    assert code == 2
    assert "실재 확인" in capsys.readouterr().err


def test_cli_allows_explicit_no_universe(tmp_path):
    path = _write(tmp_path, _doc())
    assert cli.main(["--config", str(path), "--raw-dir", str(tmp_path / "없음"),
                     "--allow-no-universe"]) == 0


def test_cli_returns_1_on_error(tmp_path):
    path = _write(tmp_path, _doc([_sector(note="짧다")]))
    _raw(tmp_path, "etf_bydd_trd_20260910.json",
         [{"BAS_DD": "20260910", "ISU_CD": "0001A0", "ISU_NM": "합성ETF 알파",
           "ACC_TRDVAL": str(LIQUIDITY_GATE_KRW * 2)}])
    assert cli.main(["--config", str(path), "--raw-dir", str(tmp_path)]) == 1


def test_cli_returns_2_on_malformed_config(tmp_path):
    path = _write(tmp_path, _doc(sourec_notice="오타"))
    assert cli.main(["--config", str(path), "--raw-dir", str(tmp_path)]) == 2


def test_cli_passes_real_config_with_real_snapshot():
    """정본 + `data/raw/` 스냅샷. 🔒 스냅샷이 없는 환경에서는 건너뛴다."""
    if not load_universe():
        pytest.skip("data/raw 에 스냅샷이 없다 (gitignore 라 CI·새 클론에는 없다)")
    assert cli.main([]) == 0
