"""KRX Open API — 주식 일별매매정보(`sto/stk_bydd_trd` · `sto/ksq_bydd_trd`) 어댑터.

**구성종목 렌즈**의 원천이다. ETF 렌즈(`krx_openapi.py`)와 짝을 이루며,
🔒 **두 렌즈는 합치지 않는다** — 갈라지면 갈라진 채로 보여준다(`AGENTS.md` 6장).

## 🔴 ETF 와 스키마가 다르다 — 어댑터를 하나로 합치지 않은 이유 (V23)

| | ETF `etp/etf_bydd_trd` | 주식 `sto/*_bydd_trd` |
|---|---|---|
| 시장 구분 | 없다 | **`MKT_NM`** (`KOSPI` · `KOSDAQ`) |
| 소속부 | 없다 | **`SECT_TP_NM`** (코스닥 8종 · 코스피는 전부 빈 문자열) |
| 순자산가치 | `NAV` | **없다** |
| 기초지수 4필드 | 있다 | **없다** |
| 필드 수 | 20 | **15** |

값 객체를 하나로 합치면 주식 행에 `nav=None` 이 생기고, 그 `None` 이 "원천이 주지
않았다"인지 "이 자산군에는 그런 개념이 없다"인지 구별되지 않는다. 🔒 **없는 것과
비어 있는 것을 같게 만들지 않는다** — 이 저장소가 반복해서 지키는 규칙이다
(→ ADR-SC-0007).

## 원천을 직접 관찰해 확정한 것 (2026-09-11 · `basDd=20260910` · 943 + 1823행)

| 관찰 | 그래서 코드가 이렇게 됐다 |
|---|---|
| `ISU_CD` 2766행 중 **81행에 영문자**(`18064K` 꼴 우선주) | 🔴 `\\d{6}` 금지가 ETF 만의 문제가 아니다. `[0-9A-Z]{6}` |
| **`SECT_TP_NM` 이 KOSPI 943행 전부 빈 문자열** | 코스피에 소속부 개념이 없다. `None` 으로 두되 **"결측"이 아니라 "해당 없음"** 이다 — 그래서 이 필드로 거르지 않는다 |
| 거래가 없던 종목은 시가·고가·저가·거래량·거래대금이 **`"0"`** (KOSPI 28행 · KOSDAQ 86행) | ETF 는 같은 상황에서 `""` 를 주는데 **주식은 `"0"` 을 준다.** 원천이 자산군마다 다르게 말한다 — 그래서 어댑터도 자산군마다 있다 |
| 종가는 거래가 없어도 **0 이 아니다**(기세) | 종가 0 은 정상 범위를 벗어난 값이다. 집계층이 그렇게 판단할 수 있게 그대로 싣는다 |
| `FLUC_RT` **소수 2자리 고정** · 2766행 전수 bp 정수 | 비율은 bp 정수 |
| 금액·수량에 소수점 **0건** · `CMPPREVDD_PRC` 에 음수 존재 | 금액은 `int`(부호 있음) |
| `MKT_NM` 이 파일마다 단일값(`KOSPI` / `KOSDAQ`) | 🔒 요청한 시장과 다르면 **던진다** — 엔드포인트를 잘못 부른 것이다 |

## ★ `FLUC_RT` 를 쓰고 종가 비율을 직접 계산하지 않는다

KRX 종가는 **무수정 주가**다. 액면분할·병합·유상증자 권리락이 있으면
`close_t / close_{t-1} - 1` 이 -50% 같은 가짜 수익률을 낸다. 반면 `FLUC_RT` 는
**거래소가 조정한 기준가 대비** 등락률이다.

🔒 그래서 수익률 시계열의 재료는 **언제나 `fluc_rt_bp`** 다. 종가는 표시·유동성
   판단용이고, 수익률의 원천이 아니다. 이 결정이 `sector/aggregate.py` 의
   체인연결(chain-link) 지수를 수정주가 시계열과 동등하게 만든다.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any, Callable

import requests

from sector.sources.krx_common import (
    AUTH_HEADER,
    BASE_URL,
    DEFAULT_RETRIES,
    DEFAULT_TIMEOUT_S,
    RESULT_BLOCK,
    KrxError,
    KrxSchemaError,
    assert_raw_output_allowed,
    auth_error_message,
    clean,
    fetch_json,
    parse_bp,
    parse_identity,
    parse_int,
    raw_output_path as _common_raw_output_path,
    repo_root,
    resolve_auth_key,
    result_rows,
    validate_bas_dd,
)

__all__ = [
    "MARKETS",
    "Market",
    "StockDailyQuote",
    "fetch_stock_daily",
    "parse_payload",
    "raw_output_path",
    "summarize",
]


@dataclass(frozen=True, slots=True)
class Market:
    """시장 하나 — 엔드포인트 · `MKT_NM` 기대값 · `data/raw/` 파일 접두를 묶는다.

    ★ 셋을 한 곳에 묶는 이유: 따로 두면 `ksq` 경로를 부르고 `stk` 이름으로 저장하는
      실수가 조용히 성공한다. 묶어 두면 그런 조합을 만들 수 없다.
    """

    key: str          # "stk" | "ksq" — CLI·파일 이름에 쓰는 짧은 이름
    path: str         # API 경로
    mkt_nm: str       # 응답의 `MKT_NM` 기대값
    label: str        # 401 메시지에 쓰는 한국어 이름 (개별 승인 대상 이름)

    @property
    def raw_prefix(self) -> str:
        """`data/raw/` 파일 이름 접두. `sector_master._RAW_PATTERNS` 와 짝을 이룬다."""
        return f"{self.key}_bydd_trd"


#: 🔒 국내 주식 시장 둘. 코넥스(`knx`)는 유동성이 없어 섹터 판단에 쓰지 않는다.
MARKETS: tuple[Market, ...] = (
    Market("stk", "sto/stk_bydd_trd", "KOSPI", "유가증권 일별매매정보"),
    Market("ksq", "sto/ksq_bydd_trd", "KOSDAQ", "코스닥 일별매매정보"),
)
_BY_KEY: dict[str, Market] = {m.key: m for m in MARKETS}


def market(key: str) -> Market:
    try:
        return _BY_KEY[key]
    except KeyError:
        raise ValueError(f"모르는 시장이다: {key!r}. 가능한 값: {sorted(_BY_KEY)}") from None


# ── 값 객체 ──────────────────────────────────────────────────────────────────

@dataclass(frozen=True, slots=True)
class StockDailyQuote:
    """주식 한 종목의 하루치 매매정보.

    `None` 은 **"원천이 주지 않았다"** 는 뜻이다. 0 이나 전일값으로 채우지 않는다.
    🔒 `float` 를 쓰지 않는다 — 금액은 정수(원), 비율은 bp 정수 (`AGENTS.md` 4장).
    """

    bas_dd: str                 # 영업일 `"YYYYMMDD"` — 사전순 = 날짜순
    isu_cd: str                 # 종목코드 6자리 (영문자 포함 가능 — 우선주)
    isu_nm: str                 # 종목명
    mkt_nm: str                 # `KOSPI` · `KOSDAQ`
    sect_tp_nm: str | None      # 소속부 — 코스피는 언제나 None("해당 없음")
    close_prc: int | None       # 종가 (원) — 거래가 없어도 기세가 붙어 0 이 아니다
    chg_prc: int | None         # 전일대비 (원, 음수 가능)
    fluc_rt_bp: int | None      # 등락률 (bp) — ★ 수익률 시계열의 유일한 재료
    open_prc: int | None        # 시가 (원) — 거래 없으면 0
    high_prc: int | None        # 고가 (원)
    low_prc: int | None         # 저가 (원)
    acc_trdvol: int | None      # 누적거래량 (주) — 0 이 실재한다
    acc_trdval: int | None      # 누적거래대금 (원) — 🔒 유동성 판단 전용. 점수 금지(V15)
    mktcap: int | None          # 시가총액 (원)
    list_shrs: int | None       # 상장주식수 (주)


# ── 파싱 ─────────────────────────────────────────────────────────────────────

# 🔒 이 표가 주식 원천 스키마의 유일한 기술이다. ETF 표와 **합치지 않는다**.
_FIELD_SPECS: tuple[tuple[str, str, Callable[..., Any]], ...] = (
    ("TDD_CLSPRC", "close_prc", parse_int),
    ("CMPPREVDD_PRC", "chg_prc", parse_int),
    ("FLUC_RT", "fluc_rt_bp", parse_bp),
    ("TDD_OPNPRC", "open_prc", parse_int),
    ("TDD_HGPRC", "high_prc", parse_int),
    ("TDD_LWPRC", "low_prc", parse_int),
    ("ACC_TRDVOL", "acc_trdvol", parse_int),
    ("ACC_TRDVAL", "acc_trdval", parse_int),
    ("MKTCAP", "mktcap", parse_int),
    ("LIST_SHRS", "list_shrs", parse_int),
)

#: 원천이 주는 필드 이름 전체. `--probe` 가 스키마 변화를 감지하는 기준이다.
KNOWN_FIELDS = frozenset(
    {"BAS_DD", "ISU_CD", "ISU_NM", "MKT_NM", "SECT_TP_NM"}
    | {name for name, _, _ in _FIELD_SPECS}
)


def _parse_row(
    row: Mapping[str, Any], *, expected_bas_dd: str | None, expected_mkt: Market
) -> StockDailyQuote:
    bas_dd, isu_cd, isu_nm = parse_identity(row, expected_bas_dd=expected_bas_dd)

    # 🔒 시장이 다르면 던진다. 엔드포인트를 잘못 불렀거나 원천이 바뀐 것이고,
    #    둘 다 조용히 섞이면 코스닥 종목이 코스피 파일에 들어앉는다.
    mkt_nm = clean(row.get("MKT_NM"))
    if mkt_nm is None:
        raise KrxSchemaError(f"MKT_NM 이 없다 (ISU_CD={isu_cd})")
    if mkt_nm != expected_mkt.mkt_nm:
        raise KrxSchemaError(
            f"{expected_mkt.path} 가 {expected_mkt.mkt_nm} 이 아닌 {mkt_nm!r} 를 "
            f"돌려줬다 (ISU_CD={isu_cd})."
        )

    values: dict[str, Any] = {
        "bas_dd": bas_dd,
        "isu_cd": isu_cd,
        "isu_nm": isu_nm,
        "mkt_nm": mkt_nm,
        # ★ 코스피는 전 행이 빈 문자열이다. 결측이 아니라 **해당 없음**이므로
        #   이 값으로 종목을 거르지 않는다.
        "sect_tp_nm": clean(row.get("SECT_TP_NM")),
    }
    for source_name, attr, parser in _FIELD_SPECS:
        values[attr] = parser(row.get(source_name), field=source_name, isu_cd=isu_cd)
    return StockDailyQuote(**values)


def parse_payload(
    payload: Any, *, mkt: Market, expected_bas_dd: str | None = None
) -> list[StockDailyQuote]:
    """응답 본문(이미 JSON 으로 읽은 것)을 값 객체 리스트로 바꾼다.

    휴일·미래일자는 **빈 리스트**다. 예외가 아니다.
    """
    rows = result_rows(payload, api_label=mkt.label)
    return [
        _parse_row(row, expected_bas_dd=expected_bas_dd, expected_mkt=mkt) for row in rows
    ]


# ── 취득 ─────────────────────────────────────────────────────────────────────

def fetch_stock_daily(
    bas_dd: str,
    mkt: Market | str,
    *,
    auth_key: str | None = None,
    timeout: float = DEFAULT_TIMEOUT_S,
    retries: int = DEFAULT_RETRIES,
    session: Any | None = None,
    sleep: Callable[[float], None] | None = None,
) -> list[StockDailyQuote]:
    """하루치 주식 일별매매정보를 받아 파싱한다.

    영업일 익일 08:00(KST) 에 갱신되므로, 그 전에 당일을 물으면 0건이 정상이다.
    """
    target = market(mkt) if isinstance(mkt, str) else mkt
    day = validate_bas_dd(bas_dd)
    kwargs: dict[str, Any] = {}
    if sleep is not None:
        kwargs["sleep"] = sleep
    payload = fetch_json(
        target.path,
        day,
        api_label=target.label,
        auth_key=auth_key,
        timeout=timeout,
        retries=retries,
        session=session,
        **kwargs,
    )
    return parse_payload(payload, mkt=target, expected_bas_dd=day)


# ── `--probe` ────────────────────────────────────────────────────────────────

def raw_output_path(mkt: Market | str, bas_dd: str, *, compressed: bool = False) -> Path:
    """원천 저장 경로. **`data/raw/` 외에는 만들지 않는다.**"""
    target = market(mkt) if isinstance(mkt, str) else mkt
    return _common_raw_output_path(target.raw_prefix, bas_dd, compressed=compressed)


def summarize(quotes: list[StockDailyQuote], raw_rows: list[Mapping[str, Any]]) -> str:
    """🔒 **값을 찍지 않는다.** 행 수·결측 수·스키마 변화만 요약한다 (약관 제11조②)."""
    lines = [f"행 수: {len(quotes)}"]
    if not quotes:
        lines.append("  → 0건. 휴일·미래일자·갱신 전(영업일 익일 08:00 KST)이면 정상이다.")

    observed = {key for row in raw_rows for key in row}
    if extra := sorted(observed - KNOWN_FIELDS):
        lines.append(f"🔴 원천에 새 필드가 생겼다: {extra}")
    if raw_rows and (gone := sorted(KNOWN_FIELDS - observed)):
        lines.append(f"🔴 알던 필드가 사라졌다: {gone}")

    if quotes:
        lines.append("결측(None) 필드 — 0 으로 채우지 않은 칸의 수:")
        for spec in fields(StockDailyQuote):
            missing = sum(1 for q in quotes if getattr(q, spec.name) is None)
            if missing:
                note = " ← 코스피는 소속부가 없다(해당 없음)" if spec.name == "sect_tp_nm" else ""
                lines.append(f"  {spec.name:20s} {missing}건{note}")
        zero_vol = sum(1 for q in quotes if q.acc_trdvol == 0)
        lines.append(f"거래량이 실제로 0 인 행: {zero_vol}건 (결측과 구별된다)")
        alpha = sum(1 for q in quotes if not q.isu_cd.isdigit())
        lines.append(f"종목코드에 영문자가 있는 행: {alpha}건 (우선주 — 버리지 않는다)")
    return "\n".join(lines)


def _run_probe(mkt: Market, bas_dd: str, *, save: bool) -> int:
    day = validate_bas_dd(bas_dd)
    out_path = raw_output_path(mkt, day)
    if save:
        # 🔒 네트워크를 부르기 **전에** 쓰기 대상을 검사한다.
        assert_raw_output_allowed(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)

    key = resolve_auth_key(None)
    response = requests.get(
        f"{BASE_URL}/{mkt.path}",
        params={"basDd": day},
        headers={AUTH_HEADER: key},
        timeout=DEFAULT_TIMEOUT_S,
    )
    if response.status_code == 401:
        print(f"❌ 401 Unauthorized — {auth_error_message(mkt.label)}", file=sys.stderr)
        return 2
    if response.status_code != 200:
        print(f"❌ HTTP {response.status_code}", file=sys.stderr)
        return 2

    payload = response.json()
    raw_rows = [r for r in payload.get(RESULT_BLOCK, []) if isinstance(r, Mapping)]
    quotes = parse_payload(payload, mkt=mkt, expected_bas_dd=day)

    print(f"✅ {mkt.key} ({mkt.mkt_nm}) · {day} · HTTP 200")
    print(summarize(quotes, raw_rows))
    if save:
        out_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        print(f"원천 저장: {out_path.relative_to(repo_root())}  (gitignore 확인됨)")
    else:
        print("원천을 저장하지 않았다 (--no-save).")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m sector.sources.krx_stock",
        description="KRX Open API 주식 일별매매정보 어댑터 — 단독 점검용",
    )
    parser.add_argument("--probe", action="store_true", help="원천을 한 번 불러 모양을 확인한다")
    parser.add_argument("--date", required=True, metavar="YYYYMMDD", help="영업일")
    parser.add_argument(
        "--market", default="stk", choices=sorted(_BY_KEY), help="시장 (기본: stk)"
    )
    parser.add_argument("--no-save", action="store_true", help="원천을 저장하지 않는다")
    args = parser.parse_args(argv)
    if not args.probe:
        parser.error("--probe 를 지정한다. 이 모듈은 라이브러리이고 CLI 는 점검용이다.")

    try:
        return _run_probe(market(args.market), args.date, save=not args.no_save)
    except (KrxError, RuntimeError, ValueError) as exc:
        print(f"❌ {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
