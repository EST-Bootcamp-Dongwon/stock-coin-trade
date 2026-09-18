"""섹터 마스터 — `sector/config/sectors.yaml` 을 읽고 **검증한다**.

이 모듈이 하는 일은 셋이다 — ① 설정을 값 객체로 읽고 ② 원천 스냅샷과 맞춰 보고
③ 어긋난 것을 **등급을 나눠** 보고한다. CLI 는 `batch/validate_config.py` 다.

## 왜 검증기가 따로 필요한가 (확정 사실 V5)

KRX Open API 에 **지수 구성종목·ETF PDF 가 없다.** 섹터 구성은 자동으로 채울
원천이 없어 사람이 손으로 적고, 그래서 **틀릴 수 있다.** 실제로 2026-09-11 에
기억으로 적은 125건 중 6건이 어긋났다 —

| 적은 것 | 원천 | 무엇이었나 |
|---|---|---|
| `한글과컴퓨터` | `한컴` | 사명 변경 |
| `엔씨소프트` | `NC` | 사명 변경 |
| `LIG넥스원` | `LIG디펜스앤에어로스페이스` | 사명 변경 |
| `HDC현대산업개발` | `IPARK현대산업개발` | 사명 변경 |
| `더존비즈온` | *(없음)* | 유니버스에 없다 |
| `HD현대미포` | *(없음)* | 유니버스에 없다 |

검증이 없었다면 여섯 개가 그대로 굳었다. 🔒 **사람이 적고 코드가 검증한다** —
이 모듈의 존재 이유다.

## 등급을 나누는 이유 — 무엇이 배치를 멈춰야 하는가

| 등급 | 뜻 | 배치 |
|---|---|---|
| `error` | 그 섹터를 **계산할 수 없다** (코드가 실재하지 않는다 · note 가 없다) | 🔴 멈춘다 |
| `warn` | 계산은 되지만 사람이 봐야 한다 (사명 변경 · 유동성 선언 불일치) | 계속한다 |
| `info` | 맥락 (의도된 중복 · 얇은 다리 하나) | 계속한다 |

🔒 **사명 변경을 `error` 로 두지 않는다.** 코드가 맞으면 값은 정확히 계산된다.
   회사가 이름을 바꾼 날 배치 전체가 멈추면, 그 다음부터 사람은 검증기를 끈다.

## 🔒 이 모듈은 KRX 값을 담지 않는다

`Universe` 는 이름과 **유동성 게이트 통과 여부(bool)** 만 담는다. 금액을 담지
않으므로 검증 출력이 시세를 흘릴 **경로 자체가 없다** — 규칙을 주석이 아니라
자료구조로 막았다 (약관 제11조② · `--probe` 의 `summarize` 와 같은 원칙).
"""

from __future__ import annotations

import gzip
import hashlib
import json
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

__all__ = [
    "has_errors",
    "read_raw_payload",
    "default_raw_dir",
    "GICS_SECTORS",
    "LIQUIDITY_GATE_KRW",
    "Finding",
    "GicsSector",
    "Instrument",
    "Sector",
    "SectorConfigError",
    "SectorMaster",
    "Universe",
    "default_config_path",
    "load",
    "load_universe",
    "validate",
]

#: GICS 11 대분류. 🔒 여기 없는 이름은 오타로 본다 — 설정이 마음대로 늘리지 못한다.
GICS_SECTORS: tuple[str, ...] = (
    "Energy",
    "Materials",
    "Industrials",
    "Consumer Discretionary",
    "Consumer Staples",
    "Health Care",
    "Financials",
    "Information Technology",
    "Communication Services",
    "Utilities",
    "Real Estate",
)

#: 유동성 게이트 — 일평균 거래대금 1억 원.
#: 🔒 점수 축이 아니다(V15: 고회전은 오히려 미래 수익률이 낮다). **게이트 전용**이다.
LIQUIDITY_GATE_KRW = 100_000_000

#: 약관 제10조③ — 화면에 이 문구를 표시할 의무가 있다. 바꾸면 의무를 어긴다.
REQUIRED_SOURCE_NOTICE = "한국거래소 통계정보"

_ID_RE = re.compile(r"^[a-z][a-z0-9_]*$")
# 🔴 `\d{6}` 이 아니다. 신규 상장은 `0091P0`·`18064K` 처럼 영문자를 포함한다(V22).
#    좁히면 ETF 304개와 우선주가 조용히 사라진다. **좁히지 마라.**
_CODE_RE = re.compile(r"^[0-9A-Z]{6}$")
_VERSION_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")

#: `note` 최소 길이(공백 제외). 한 문장이 안 되는 note 는 근거가 아니다.
#: 🔒 숫자를 낮추지 마라 — `note: "-"` 를 통과시키면 이 게이트가 장식이 된다.
_MIN_NOTE_CHARS = 20

#: 구성종목 수 범위. 3 미만은 "섹터"가 아니고, 12 초과는 "대표"가 아니다.
_MIN_MEMBERS = 3
_MAX_MEMBERS = 12


class SectorConfigError(Exception):
    """설정의 **모양**이 틀렸다 — 값 객체를 만들 수조차 없다.

    규칙 위반(`Finding`)과 구별한다. 모양이 틀리면 검증을 시작할 수 없고,
    규칙 위반은 등급을 나눠 보고할 수 있다.
    """


# ── 값 객체 ──────────────────────────────────────────────────────────────────

@dataclass(frozen=True, slots=True)
class Instrument:
    """ETF 든 종목이든 6자리 코드 + 이름이다. 🔒 어휘를 둘로 두지 않는다."""

    code: str
    name: str


@dataclass(frozen=True, slots=True)
class GicsSector:
    """GICS 대분류 하나. `empty_reason` 은 **테마 섹터가 없을 때만** 있다."""

    id: str
    name_ko: str
    empty_reason: str | None = None


@dataclass(frozen=True, slots=True)
class Sector:
    """한국 테마 섹터 하나 — 화면의 주인공이고 실제 매수 대상이다."""

    id: str
    name_ko: str
    gics: str
    note: str
    etfs: tuple[Instrument, ...] = ()
    members: tuple[Instrument, ...] = ()
    liquidity_warning: bool = False


@dataclass(frozen=True, slots=True)
class SectorMaster:
    version: str
    source_notice: str
    gics_sectors: tuple[GicsSector, ...]
    sectors: tuple[Sector, ...]
    config_sha256: str
    """설정 파일 바이트의 SHA-256. 🔒 점수 행에 박아 재현성을 고정한다(M7)."""


@dataclass(frozen=True, slots=True)
class Universe:
    """원천 스냅샷에서 만든 **실재 확인용** 사전.

    🔒 **금액을 담지 않는다.** 이름과 유동성 게이트 통과 여부만 담는다 —
       검증 출력이 시세를 흘릴 경로를 자료구조에서 없앤다 (약관 제11조②).
    """

    bas_dds: tuple[str, ...]
    etf_names: Mapping[str, str] = field(default_factory=dict)
    stock_names: Mapping[str, str] = field(default_factory=dict)
    etf_gate_pass: frozenset[str] = frozenset()

    @property
    def days(self) -> int:
        return len(self.bas_dds)

    def __bool__(self) -> bool:
        return bool(self.etf_names or self.stock_names)


@dataclass(frozen=True, slots=True)
class Finding:
    """검증 결과 한 줄. `level` 이 `error` 인 것이 하나라도 있으면 배치가 멈춘다."""

    level: str   # "error" | "warn" | "info"
    rule: str    # 어느 규칙인가 — 메시지가 길어도 이걸로 묶어 본다
    where: str   # 어디인가 (`semiconductor.etfs[0]` 꼴)
    message: str

    def render(self) -> str:
        mark = {"error": "🔴", "warn": "⚠️ ", "info": "  "}.get(self.level, "  ")
        return f"{mark} [{self.rule}] {self.where}: {self.message}"


# ── 읽기 ─────────────────────────────────────────────────────────────────────

def default_config_path() -> Path:
    """`sector/config/sectors.yaml`. 이 파일에서 한 단계 아래다."""
    return Path(__file__).resolve().parent / "config" / "sectors.yaml"


def _as_mapping(value: Any, where: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise SectorConfigError(f"{where} 가 매핑이 아니다: {type(value).__name__}")
    return value


def _strict_keys(data: Mapping[str, Any], allowed: Iterable[str], where: str) -> None:
    """🔒 모르는 키를 **거부한다.** 오타를 통과시키면 게이트가 조용히 꺼진다.

    예: `liquidity_warning` 을 `liquidity_warn` 으로 적으면 경고가 사라지고,
    아무도 모른다. 그 사고를 구조적으로 막는 유일한 방법이 이 검사다.
    """
    unknown = sorted(set(data) - set(allowed))
    if unknown:
        raise SectorConfigError(
            f"{where} 에 모르는 키가 있다: {unknown}. 오타이거나 스키마가 바뀐 것이다 — "
            f"허용된 키는 {sorted(allowed)} 다."
        )


def _require_text(data: Mapping[str, Any], key: str, where: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise SectorConfigError(f"{where}.{key} 가 없거나 비어 있다: {value!r}")
    return value.strip()


def _parse_instruments(raw: Any, where: str) -> tuple[Instrument, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        raise SectorConfigError(f"{where} 가 리스트가 아니다: {type(raw).__name__}")
    out: list[Instrument] = []
    for i, item in enumerate(raw):
        spot = f"{where}[{i}]"
        entry = _as_mapping(item, spot)
        _strict_keys(entry, ("code", "name"), spot)
        out.append(
            Instrument(code=_require_text(entry, "code", spot),
                       name=_require_text(entry, "name", spot))
        )
    return tuple(out)


def _parse_sector(raw: Any, where: str) -> Sector:
    data = _as_mapping(raw, where)
    _strict_keys(
        data,
        ("id", "name_ko", "gics", "etfs", "members", "note", "liquidity_warning"),
        where,
    )
    sector_id = _require_text(data, "id", where)
    spot = f"sectors.{sector_id}"
    warning = data.get("liquidity_warning", False)
    if not isinstance(warning, bool):
        raise SectorConfigError(
            f"{spot}.liquidity_warning 은 true/false 여야 한다: {warning!r}"
        )
    # 🔒 `note` 는 여기서 '있는지'만 본다. '충분한지'는 규칙(N1)이 판단한다 —
    #    모양과 규칙을 섞으면 등급을 나눌 수 없다.
    note = data.get("note")
    if note is not None and not isinstance(note, str):
        raise SectorConfigError(f"{spot}.note 가 문자열이 아니다: {type(note).__name__}")
    return Sector(
        id=sector_id,
        name_ko=_require_text(data, "name_ko", spot),
        gics=_require_text(data, "gics", spot),
        note=(note or ""),
        etfs=_parse_instruments(data.get("etfs"), f"{spot}.etfs"),
        members=_parse_instruments(data.get("members"), f"{spot}.members"),
        liquidity_warning=warning,
    )


def _parse_gics(raw: Any, where: str) -> GicsSector:
    data = _as_mapping(raw, where)
    _strict_keys(data, ("id", "name_ko", "empty_reason"), where)
    gics_id = _require_text(data, "id", where)
    reason = data.get("empty_reason")
    if reason is not None and not isinstance(reason, str):
        raise SectorConfigError(f"{where}.empty_reason 이 문자열이 아니다")
    return GicsSector(
        id=gics_id,
        name_ko=_require_text(data, "name_ko", f"gics_sectors.{gics_id}"),
        empty_reason=(reason.strip() if isinstance(reason, str) else None),
    )


def load(path: Path | str | None = None) -> SectorMaster:
    """설정을 읽어 값 객체로 만든다. **모양이 틀리면 던진다.**

    규칙 위반은 던지지 않는다 — `validate()` 가 등급을 나눠 보고한다.
    """
    config_path = Path(path) if path is not None else default_config_path()
    try:
        raw_bytes = config_path.read_bytes()
    except OSError as exc:
        raise SectorConfigError(f"설정을 읽을 수 없다: {config_path} ({exc})") from exc

    try:
        payload = yaml.safe_load(raw_bytes.decode("utf-8"))
    except yaml.YAMLError as exc:
        raise SectorConfigError(f"YAML 파싱 실패: {config_path}\n{exc}") from exc

    data = _as_mapping(payload, "설정 최상위")
    _strict_keys(data, ("version", "source_notice", "gics_sectors", "sectors"), "설정 최상위")

    gics_raw = data.get("gics_sectors")
    if not isinstance(gics_raw, Sequence) or isinstance(gics_raw, (str, bytes)):
        raise SectorConfigError("gics_sectors 가 리스트가 아니다")
    sectors_raw = data.get("sectors")
    if not isinstance(sectors_raw, Sequence) or isinstance(sectors_raw, (str, bytes)):
        raise SectorConfigError("sectors 가 리스트가 아니다")

    return SectorMaster(
        version=_require_text(data, "version", "설정 최상위"),
        source_notice=_require_text(data, "source_notice", "설정 최상위"),
        gics_sectors=tuple(
            _parse_gics(item, f"gics_sectors[{i}]") for i, item in enumerate(gics_raw)
        ),
        sectors=tuple(
            _parse_sector(item, f"sectors[{i}]") for i, item in enumerate(sectors_raw)
        ),
        # 🔒 바이트 그대로 해싱한다. 파싱 결과가 아니라 **파일**이 재현성의 단위다
        #    (주석 한 줄이 바뀌어도 해시가 바뀌는 것이 맞다 — 근거가 바뀌었다).
        config_sha256=hashlib.sha256(raw_bytes).hexdigest(),
    )


# ── 원천 스냅샷 → 유니버스 ───────────────────────────────────────────────────

#: `data/raw/` 안의 파일 이름 규칙. 🔒 이 폴더는 gitignore 다 — KRX 원천이 여기까지만 산다.
#: ★ `*` 가 날짜 뒤의 `.gz` 까지 먹는다 — 1년치 배치(`batch/fetch_daily.py`)는 gzip 으로
#:   저장하고 단발 `--probe` 는 평문으로 저장하는데, 읽는 쪽은 둘을 구별할 이유가 없다.
_RAW_PATTERNS: tuple[tuple[str, str], ...] = (
    ("etf", "etf_bydd_trd_*.json*"),   # ETP > ETF 일별매매정보
    ("stk", "stk_bydd_trd_*.json*"),   # 유가증권 일별매매정보
    ("ksq", "ksq_bydd_trd_*.json*"),   # 코스닥 일별매매정보
)
_RESULT_BLOCK = "OutBlock_1"


def default_raw_dir() -> Path:
    """`data/raw/`. 🔒 KRX 원천은 여기까지만 산다 (약관 제11조② → ADR-SC-0006)."""
    return Path(__file__).resolve().parents[1] / "data" / "raw"


def read_raw_payload(path: Path) -> Any:
    """`data/raw/` 스냅샷 한 개를 읽는다. `.json` 과 `.json.gz` 를 모두 안다."""
    try:
        if path.suffix == ".gz":
            with gzip.open(path, "rt", encoding="utf-8") as fp:
                return json.load(fp)
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise SectorConfigError(f"원천 스냅샷을 읽을 수 없다: {path} ({exc})") from exc


def _iter_rows(path: Path) -> Iterable[Mapping[str, Any]]:
    payload = read_raw_payload(path)
    block = payload.get(_RESULT_BLOCK) if isinstance(payload, Mapping) else None
    if not isinstance(block, list):
        raise SectorConfigError(f"{path.name} 에 '{_RESULT_BLOCK}' 리스트가 없다")
    return [row for row in block if isinstance(row, Mapping)]


def _trdval(row: Mapping[str, Any]) -> int | None:
    """거래대금. 🔒 `""`(결측)과 `"0"`(거래 없음)을 가른다 — 결측을 0 으로 만들지 않는다.

    결측이면 그날은 **평균 계산에서 제외한다.** 0 으로 세면 없는 데이터가
    "거래가 없었다"로 바뀌어 유동성 게이트가 거짓으로 걸린다 (→ ADR-SC-0007).
    """
    text = str(row.get("ACC_TRDVAL", "")).strip().replace(",", "")
    if not text:
        return None
    try:
        return int(text)
    except ValueError:
        return None


def load_universe(raw_dir: Path | str | None = None) -> Universe:
    """`data/raw/` 의 스냅샷으로 실재 확인용 유니버스를 만든다.

    스냅샷이 여러 날 있으면 **거래대금을 날짜별로 평균**해 게이트를 판정하고,
    이름은 **가장 최근 날짜**의 것을 쓴다 (사명 변경을 최신으로 본다).
    파일이 없으면 빈 `Universe` 다 — 예외가 아니다. "확인할 수 없다"는 사실이고,
    그것을 어떻게 다룰지는 호출부(CLI)가 정한다.
    """
    base = Path(raw_dir) if raw_dir is not None else default_raw_dir()
    etf_names: dict[str, str] = {}
    stock_names: dict[str, str] = {}
    # code → [거래대금 합, 관측 일수]. 🔒 결측일은 세지 않는다
    trdval_acc: dict[str, list[int]] = {}
    days: set[str] = set()
    newest: dict[str, str] = {}   # code → 그 이름을 준 bas_dd

    if not base.is_dir():
        return Universe(bas_dds=())

    for kind, pattern in _RAW_PATTERNS:
        for path in sorted(base.glob(pattern)):
            for row in _iter_rows(path):
                code = str(row.get("ISU_CD", "")).strip()
                name = str(row.get("ISU_NM", "")).strip()
                bas_dd = str(row.get("BAS_DD", "")).strip()
                if not code or not name:
                    continue
                days.add(bas_dd)
                target = etf_names if kind == "etf" else stock_names
                # 사전순 = 날짜순이라 `>=` 비교가 그대로 통한다 (`AGENTS.md` 4장)
                if bas_dd >= newest.get(code, ""):
                    newest[code] = bas_dd
                    target[code] = name
                if kind == "etf":
                    value = _trdval(row)
                    if value is not None:
                        acc = trdval_acc.setdefault(code, [0, 0])
                        acc[0] += value
                        acc[1] += 1

    gate_pass = frozenset(
        code for code, (total, count) in trdval_acc.items()
        if count and total / count >= LIQUIDITY_GATE_KRW
    )
    return Universe(
        bas_dds=tuple(sorted(days)),
        etf_names=etf_names,
        stock_names=stock_names,
        etf_gate_pass=gate_pass,
    )


# ── 검증 ─────────────────────────────────────────────────────────────────────

def _check_meta(master: SectorMaster) -> list[Finding]:
    out: list[Finding] = []
    if not _VERSION_RE.match(master.version):
        out.append(Finding(
            "error", "meta", "version",
            f"`YYYY-MM-DD` 형식이어야 한다: {master.version!r}. 이 값은 설정이 언제 "
            f"사람 손을 거쳤는지 말하는 유일한 단서다.",
        ))
    if master.source_notice != REQUIRED_SOURCE_NOTICE:
        out.append(Finding(
            "error", "meta", "source_notice",
            f"{REQUIRED_SOURCE_NOTICE!r} 이어야 한다(현재 {master.source_notice!r}). "
            f"KRX Open API 약관 제10조③ 이 화면 표시를 의무로 둔다 — 문구를 바꾸면 "
            f"의무를 어긴다.",
        ))
    return out


def _check_note(text: str, where: str, *, what: str) -> list[Finding]:
    """🔒 근거 없는 묶음을 막는 게이트. 이 검사가 M4 의 핵심이다."""
    stripped = "".join(text.split())
    if not stripped:
        return [Finding(
            "error", "note", where,
            f"{what} 가 비어 있다. **왜 이렇게 묶었나**를 1줄 이상 적는다 — "
            f"근거 없는 묶음을 막는 게이트다.",
        )]
    if len(stripped) < _MIN_NOTE_CHARS:
        return [Finding(
            "error", "note", where,
            f"{what} 가 너무 짧다({len(stripped)}자 < {_MIN_NOTE_CHARS}자). "
            f"한 문장이 안 되는 note 는 근거가 아니다.",
        )]
    return []


def _check_ids(master: SectorMaster) -> list[Finding]:
    out: list[Finding] = []
    seen_id: dict[str, int] = {}
    seen_name: dict[str, str] = {}
    for sector in master.sectors:
        where = f"sectors.{sector.id}"
        if not _ID_RE.match(sector.id):
            out.append(Finding(
                "error", "id-format", where,
                f"id 는 `{_ID_RE.pattern}` 이어야 한다 — URL 쿼리·파일명·컬럼명으로 "
                f"그대로 쓰이므로 대문자·하이픈·공백이 들어가면 뒤에서 깨진다.",
            ))
        seen_id[sector.id] = seen_id.get(sector.id, 0) + 1
        if sector.name_ko in seen_name:
            out.append(Finding(
                "error", "name-unique", where,
                f"한글 이름 {sector.name_ko!r} 가 {seen_name[sector.name_ko]!r} 와 겹친다. "
                f"화면에서 두 섹터를 구별할 수 없다.",
            ))
        else:
            seen_name[sector.name_ko] = sector.id
    for sector_id, count in seen_id.items():
        if count > 1:
            out.append(Finding(
                "error", "id-unique", f"sectors.{sector_id}",
                f"id 가 {count}번 나온다. 뒤에 나온 것이 앞을 덮어 한 섹터가 사라진다.",
            ))
    # 🔴 **대분류 이름도 유일해야 한다** (2026-09-18 · M9c). 랭킹의 대분류 분포표는
    #    화면 이름으로 묶어 색인을 만든다(`view.gics_distribution`) — 서로 다른 두 id 가
    #    같은 한국어 이름을 가지면 두 대분류가 **한 줄로 합쳐지고** 사용자는 합쳐진 줄
    #    알 수 없다. 이름으로 묶는 것을 그만두면 이번엔 색인이 중복돼 표가 깨진다.
    #    🔒 그래서 근원에서 막는다 — 섹터 이름을 그렇게 막는 것과 같은 이유다
    seen_gics_name: dict[str, str] = {}
    for gics in master.gics_sectors:
        if gics.name_ko in seen_gics_name:
            out.append(Finding(
                "error", "name-unique", f"gics_sectors.{gics.id}",
                f"한글 이름 {gics.name_ko!r} 가 {seen_gics_name[gics.name_ko]!r} 와 겹친다. "
                f"대분류 분포표가 두 대분류를 한 줄로 합친다.",
            ))
        else:
            seen_gics_name[gics.name_ko] = gics.id
    return out


def _check_codes(master: SectorMaster) -> list[Finding]:
    """코드 형식과 **섹터 안** 중복. 섹터 **사이** 중복은 의도된 것이라 따로 본다."""
    out: list[Finding] = []
    for sector in master.sectors:
        for kind, items in (("etfs", sector.etfs), ("members", sector.members)):
            for i, inst in enumerate(items):
                where = f"sectors.{sector.id}.{kind}[{i}]"
                if not _CODE_RE.match(inst.code):
                    out.append(Finding(
                        "error", "code-format", where,
                        f"코드는 `{_CODE_RE.pattern}` 이어야 한다: {inst.code!r}. "
                        f"🔴 6자리에 **영문자가 들어갈 수 있다**(`0091P0`·`18064K`) — "
                        f"`\\d{{6}}` 로 좁히면 ETF 304개가 사라진다(V22).",
                    ))
            codes = [inst.code for inst in items]
            for code in sorted({c for c in codes if codes.count(c) > 1}):
                out.append(Finding(
                    "error", "code-dup", f"sectors.{sector.id}.{kind}",
                    f"{code} 가 같은 목록에 두 번 있다. 폭·자금흐름 계산에서 두 번 세어진다.",
                ))
        both = {i.code for i in sector.etfs} & {i.code for i in sector.members}
        for code in sorted(both):
            out.append(Finding(
                "error", "code-dup", f"sectors.{sector.id}",
                f"{code} 가 ETF 렌즈와 구성종목 렌즈에 함께 있다. 두 렌즈는 "
                f"서로를 검증하는 장치인데, 같은 것을 담으면 검증이 사라진다.",
            ))
    return out


def _check_membership(master: SectorMaster) -> list[Finding]:
    out: list[Finding] = []
    for sector in master.sectors:
        where = f"sectors.{sector.id}"
        if not sector.etfs:
            out.append(Finding(
                "error", "lens", f"{where}.etfs",
                "ETF 가 하나도 없다. 자금흐름 축(상장좌수 변화)의 유일한 입력이 ETF 라 "
                "이 섹터는 점수를 계산할 수 없다.",
            ))
        count = len(sector.members)
        if count < _MIN_MEMBERS:
            out.append(Finding(
                "error", "lens", f"{where}.members",
                f"구성종목이 {count}개다. {_MIN_MEMBERS}개 미만이면 폭(breadth) 축이 "
                f"개별 종목 한 개의 등락과 같아져 축의 뜻을 잃는다.",
            ))
        elif count > _MAX_MEMBERS:
            out.append(Finding(
                "warn", "lens", f"{where}.members",
                f"구성종목이 {count}개다. {_MAX_MEMBERS}개를 넘으면 '대표 종목'이 아니라 "
                f"지수 복제에 가까워진다 — 사람이 고른 근거가 희석된다.",
            ))
    return out


def _check_gics(master: SectorMaster) -> list[Finding]:
    out: list[Finding] = []
    declared = [g.id for g in master.gics_sectors]
    for gics_id in sorted(set(declared)):
        if declared.count(gics_id) > 1:
            out.append(Finding(
                "error", "gics-coverage", f"gics_sectors.{gics_id}",
                f"{declared.count(gics_id)}번 선언됐다.",
            ))
    for unknown in sorted(set(declared) - set(GICS_SECTORS)):
        out.append(Finding(
            "error", "gics-coverage", f"gics_sectors.{unknown}",
            f"GICS 11 대분류에 없는 이름이다. 허용되는 것: {list(GICS_SECTORS)}",
        ))
    for missing in (g for g in GICS_SECTORS if g not in declared):
        out.append(Finding(
            "error", "gics-coverage", "gics_sectors",
            f"{missing!r} 가 선언되지 않았다. 🔒 11개를 **전부** 적는다 — 빠진 대분류는 "
            f"화면에서 '없음'이 아니라 그냥 보이지 않게 되고, 팀원은 빠뜨린 줄 모른다.",
        ))

    used: dict[str, list[str]] = {}
    for sector in master.sectors:
        used.setdefault(sector.gics, []).append(sector.id)
        if sector.gics not in declared:
            out.append(Finding(
                "error", "gics", f"sectors.{sector.id}",
                f"gics {sector.gics!r} 가 `gics_sectors` 에 없다.",
            ))

    for gics in master.gics_sectors:
        members = used.get(gics.id, [])
        where = f"gics_sectors.{gics.id}"
        if members and gics.empty_reason:
            out.append(Finding(
                "error", "gics-coverage", where,
                f"`empty_reason` 이 적혔는데 테마 섹터 {members} 가 달려 있다. "
                f"둘 중 하나가 거짓이다.",
            ))
        elif not members and not gics.empty_reason:
            out.append(Finding(
                "error", "gics-coverage", where,
                "테마 섹터가 없는데 `empty_reason` 이 없다. 왜 비었는지 적지 않으면 "
                "화면이 '없음'을 설명할 수 없고, 팀원은 빠뜨린 것으로 읽는다.",
            ))
        elif not members and gics.empty_reason:
            out.extend(_check_note(gics.empty_reason, where, what="empty_reason"))
    return out


def _check_universe(master: SectorMaster, universe: Universe) -> list[Finding]:
    """🔴 **실재 확인** — 이 검사가 없으면 기억으로 적은 코드가 그대로 굳는다."""
    out: list[Finding] = []
    for sector in master.sectors:
        for kind, items, names, what in (
            ("etfs", sector.etfs, universe.etf_names, "ETF"),
            ("members", sector.members, universe.stock_names, "종목"),
        ):
            if not names:
                continue   # 그 종류의 스냅샷이 없다 — 아래 요약이 건너뛴 사실을 말한다
            for i, inst in enumerate(items):
                where = f"sectors.{sector.id}.{kind}[{i}]"
                actual = names.get(inst.code)
                if actual is None:
                    out.append(Finding(
                        "error", "exists", where,
                        f"{what} 코드 {inst.code}({inst.name}) 가 원천 유니버스에 없다. "
                        f"상장폐지·합병·오타 중 하나다. 🔒 **없는 코드는 적지 않는다** — "
                        f"확인될 때까지 지운다.",
                    ))
                elif actual != inst.name:
                    # 🔒 warn 이다. 코드가 맞으면 값은 정확히 계산된다 — 사명이 바뀐 날
                    #    배치가 멈추면 그 다음부터 사람은 검증기를 끈다.
                    out.append(Finding(
                        "warn", "name-drift", where,
                        f"이름이 원천과 다르다. 설정={inst.name!r} 원천={actual!r}. "
                        f"사명 변경으로 본다 — 원천 이름으로 고친다(화면이 증권사 앱과 "
                        f"같은 이름을 보여야 팀원이 종목을 찾는다).",
                    ))
    return out


def _check_liquidity(master: SectorMaster, universe: Universe) -> list[Finding]:
    """유동성 게이트 — 🔒 점수 축이 아니라 **경고 배지**의 근거다(V15)."""
    out: list[Finding] = []
    if not universe.etf_names:
        return out
    basis = f"{universe.days}영업일 기준"
    for sector in master.sectors:
        known = [e for e in sector.etfs if e.code in universe.etf_names]
        if not known:
            continue
        passing = [e for e in known if e.code in universe.etf_gate_pass]
        where = f"sectors.{sector.id}"
        if not passing and not sector.liquidity_warning:
            out.append(Finding(
                "error", "liquidity", where,
                f"ETF 전부가 유동성 게이트(일평균 {LIQUIDITY_GATE_KRW // 100_000_000}억) "
                f"아래인데 `liquidity_warning` 이 없다({basis}). 경고 없이 내보내면 "
                f"팀원이 살 수 없는 ETF 를 추천받는다 — `liquidity_warning: true` 를 켜고 "
                f"note 에 대안(구성종목 매수)을 적는다.",
            ))
        elif passing and sector.liquidity_warning:
            out.append(Finding(
                "warn", "liquidity", where,
                f"`liquidity_warning: true` 인데 {len(passing)}개 ETF 가 게이트를 "
                f"넘는다({basis}). 스냅샷이 짧으면 하루 특이값일 수 있다 — 20영업일 "
                f"평균으로 재확인하고, 여전히 넘으면 경고를 내린다.",
            ))
        thin = [e.code for e in known if e.code not in universe.etf_gate_pass]
        if thin and passing:
            out.append(Finding(
                "info", "liquidity", where,
                f"게이트 미달 ETF {thin} — 섹터는 다른 ETF 로 유동성이 있다. "
                f"화면에서 이 ETF 에만 배지를 단다({basis}).",
            ))
    return out


def _check_overlap(master: SectorMaster) -> list[Finding]:
    """섹터 **사이** 중복. 🔒 오류가 아니다 — 한화오션(조선·방산)처럼 실제로 겹친다."""
    out: list[Finding] = []
    for kind in ("etfs", "members"):
        owners: dict[str, list[str]] = {}
        for sector in master.sectors:
            for inst in getattr(sector, kind):
                owners.setdefault(inst.code, []).append(sector.id)
        for code, ids in sorted(owners.items()):
            if len(ids) > 1:
                out.append(Finding(
                    "info", "overlap", f"{kind}.{code}",
                    f"{len(ids)}개 섹터가 공유한다: {ids}. 두 섹터가 같이 오를 때 그 이유가 "
                    f"이 겹치는 코드일 수 있다 — 근거 화면에서 확인한다.",
                ))
    return out


_LEVEL_ORDER = {"error": 0, "warn": 1, "info": 2}


def validate(master: SectorMaster, universe: Universe | None = None) -> list[Finding]:
    """설정을 규칙과 원천에 맞춰 본다. **던지지 않고 목록을 돌려준다.**

    `universe` 가 없거나 비어 있으면 실재 확인을 **건너뛴다** — 그리고 건너뛴
    사실을 `warn` 으로 남긴다. 🔒 조용히 통과시키지 않는다: "검증 통과"가
    "코드가 실재한다"는 뜻이 되어야 한다.
    """
    findings: list[Finding] = []
    findings.extend(_check_meta(master))
    findings.extend(_check_ids(master))
    findings.extend(_check_codes(master))
    findings.extend(_check_membership(master))
    findings.extend(_check_gics(master))
    for sector in master.sectors:
        findings.extend(_check_note(sector.note, f"sectors.{sector.id}", what="note"))
    findings.extend(_check_overlap(master))

    if universe is None or not universe:
        findings.append(Finding(
            "warn", "universe-skip", "(전체)",
            "원천 스냅샷이 없어 **코드 실재 확인과 유동성 게이트를 건너뛰었다.** "
            "`python -m sector.sources.krx_openapi --probe --date YYYYMMDD` 로 "
            "`data/raw/` 에 스냅샷을 만든 뒤 다시 돌린다.",
        ))
    else:
        findings.extend(_check_universe(master, universe))
        findings.extend(_check_liquidity(master, universe))
        if not universe.stock_names:
            findings.append(Finding(
                "warn", "universe-skip", "(구성종목)",
                "주식 스냅파일(`stk_bydd_trd_*` · `ksq_bydd_trd_*`)이 없어 **구성종목 "
                "실재 확인을 건너뛰었다.** ETF 만 확인됐다.",
            ))

    findings.sort(key=lambda f: (_LEVEL_ORDER.get(f.level, 9), f.rule, f.where))
    return findings


def has_errors(findings: Iterable[Finding]) -> bool:
    return any(f.level == "error" for f in findings)
