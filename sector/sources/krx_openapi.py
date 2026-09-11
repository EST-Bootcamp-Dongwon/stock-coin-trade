"""KRX Open API — ETF 일별매매정보(`etp/etf_bydd_trd`) 어댑터.

이 모듈이 하는 일은 둘이다 — ① 하루치 ETF 일별매매정보를 받아 ② 값 객체로 파싱한다.
집계·스코어링은 KRX 의 필드명도, 값이 문자열이라는 사실도 알지 못한다.

전송·값 파싱·원천 저장 게이트처럼 **원천이 달라도 같은 것**은
[`krx_common`](krx_common.py) 에 있다. 여기 남은 것은 **ETF 스키마**다 —
어떤 필드가 있고 무엇을 뜻하는지. 주식 원천은 스키마가 달라
[`krx_stock`](krx_stock.py) 이 따로 서술한다(V23).

## 왜 이 원천인가 (확정 사실 V4)

`etp/etf_bydd_trd` 하나가 자금흐름 축에 필요한 셋을 **한 번에** 준다 —
`NAV` · `LIST_SHRS`(상장좌수) · `INVSTASST_NETASST_TOTAMT`(순자산총액).
2010-01-04 부터 · 키당 일 10,000회 · 영업일 익일 08:00 갱신 ·
🔴 **인증키와 별도로 개별 API 이용신청 승인이 필요하다.**

🔒 자금흐름 축은 `LIST_SHRS` 변화율만 본다. 순자산총액을 쓰지 않는다 —
   순자산총액 = 좌수 × NAV 라 NAV 변화를 모멘텀 축과 **이중 계산**하게 된다
   (→ `세션-시작-프롬프트.md` 3장). 어댑터는 셋 다 실어 보내고, 무엇을 쓸지는
   집계층(`sector/aggregate.py`)이 정한다.

## 원천을 직접 관찰해 확정한 것 — 추측이 아니다 (2026-09-11 · `basDd=20260910` 1168행)

| 관찰 | 그래서 코드가 이렇게 됐다 |
|---|---|
| `ISU_CD` 1168행 중 **304행에 영문자가 있다**(`9999A9` 꼴) | 🔴 `\\d{6}` 으로 검증하면 ETF 304개가 조용히 사라진다. `[0-9A-Z]{6}` 으로 본다 |
| 모든 비율·NAV 가 **소수 2자리로 고정** | `%×100 = bp` 가 나머지 없이 정수로 떨어진다(1168행 전수 확인). 비율은 **bp 정수**로 싣는다 |
| 금액·수량 필드에 소수점이 **한 건도 없다** | 금액·수량은 `int`(원·주·좌) |
| `ACC_TRDVOL` · `ACC_TRDVAL` 이 **실제로 `"0"` 인 행이 11건** | 거래가 없던 날은 진짜 0 이다. **결측이 아니다** |
| `OBJ_STKPRC_IDX`·`CMPPREVDD_IDX`·`FLUC_RT_IDX` 가 **빈 문자열인 행이 1건** (기초지수가 공표되지 않는 ETF) | 🔴 그 행은 **버리지도 0 으로 채우지도 않는다.** 해당 **필드만** `None` 이고 NAV·좌수는 정상값으로 남는다 |
| 휴일 요청 → HTTP 200 + `{"OutBlock_1": []}` | 예외가 아니다. 빈 리스트를 돌려주고 판단은 호출부에 맡긴다 |
| 잘못된 키·키 누락 → **HTTP 401** + `{"respMsg": "Unauthorized Key", "respCode": "401"}` | `KrxAuthError` 로 번역한다 |

🔴 **`"0"` 과 `""` 는 절대 같은 값이 되어선 안 된다.** 이 어댑터의 핵심 불변식이다.
   빈 문자열을 0 으로 읽으면 거래대금 0 원인 ETF 가 생겨 유동성 게이트가 오작동하고,
   0 을 결측으로 읽으면 거래가 없던 날이 데이터 누락으로 보고된다
   (→ ADR-SC-0007 "값을 지어내지 않는다").

## 값이 이상하면 던진다 — 조용히 넘기지 않는다

파싱할 수 없는 값은 `None` 으로 바꾸지 않고 `KrxSchemaError` 를 던진다.
결측(`""`)과 **포맷 변경**(예: KRX 가 `"1.2조"` 로 보내기 시작)은 다른 사건이고,
후자를 `None` 으로 삼키면 자금흐름 축이 조용히 비어 버린다. 배치가 멈추는 편이 싸다.

🔴 **이 모듈로 받은 데이터는 KRX 원천이다.** 저장소·HF 에 올리지 않는다.
   `--probe` 출력도 `data/raw/`(gitignore) 까지다 — 약관 제11조② (→ ADR-SC-0006).
   그 규칙을 주석이 아니라 **코드로** 막아 뒀다(`_assert_raw_output_allowed`).
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping
from dataclasses import dataclass, fields
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable

import requests

from sector.sources.krx_common import (
    AUTH_HEADER,
    BASE_URL,
    DEFAULT_RETRIES,
    DEFAULT_TIMEOUT_S,
    RESULT_BLOCK,
    KrxAuthError,
    KrxError,
    KrxSchemaError,
    KrxTransportError,
    assert_raw_output_allowed,
    auth_error_message,
    clean,
    fetch_json,
    parse_bp,
    parse_decimal,
    parse_identity,
    parse_int,
    raw_output_path as _common_raw_output_path,
    repo_root,
    resolve_auth_key,
    result_rows,
    validate_bas_dd,
)

__all__ = [
    "BASE_URL",
    "EtfDailyQuote",
    "KrxAuthError",
    "KrxError",
    "KrxSchemaError",
    "KrxTransportError",
    "PATH_ETF_BYDD_TRD",
    "RAW_PREFIX",
    "fetch_etf_daily",
    "parse_payload",
    "raw_output_path",
    "summarize",
]

PATH_ETF_BYDD_TRD = "etp/etf_bydd_trd"
API_LABEL = "ETF 일별매매정보"
#: `data/raw/` 파일 이름 접두. `sector_master._RAW_PATTERNS` 와 짝을 이룬다.
RAW_PREFIX = "etf_bydd_trd"

# ★ 예전 이름을 유지한다 — 이 모듈의 내부 헬퍼를 부르던 곳(테스트 포함)이 공통
#   모듈로 옮겨진 사실을 알 필요가 없다.
_repo_root = repo_root
_validate_bas_dd = validate_bas_dd
_resolve_auth_key = resolve_auth_key
_assert_raw_output_allowed = assert_raw_output_allowed


# ── 값 객체 ──────────────────────────────────────────────────────────────────

@dataclass(frozen=True, slots=True)
class EtfDailyQuote:
    """ETF 한 종목의 하루치 매매정보.

    `None` 은 **"원천이 주지 않았다"** 는 뜻이다. 0 이나 전일값으로 채우지 않는다.
    금액·수량은 정수(원·주·좌), 비율은 **bp 정수**, 가격성 소수는 `Decimal` 이다.
    🔒 `float` 를 쓰지 않는다 — 누적 반올림이 샌다 (→ `AGENTS.md` 4장).
    """

    bas_dd: str                     # 영업일 `"YYYYMMDD"` — 사전순 = 날짜순
    isu_cd: str                     # 종목코드 6자리 (영문자 포함 가능)
    isu_nm: str                     # 종목명
    close_prc: int | None           # 종가 (원)
    chg_prc: int | None             # 전일대비 (원, 음수 가능)
    fluc_rt_bp: int | None          # 등락률 (bp = %×100) — ★ 기준가 조정이 반영된 값
    nav: Decimal | None             # 순자산가치 (원, 소수 2자리)
    open_prc: int | None            # 시가 (원)
    high_prc: int | None            # 고가 (원)
    low_prc: int | None             # 저가 (원)
    acc_trdvol: int | None          # 누적거래량 (주) — 0 이 실재한다
    acc_trdval: int | None          # 누적거래대금 (원) — 🔒 유동성 게이트 전용. 점수 금지(V15)
    mktcap: int | None              # 시가총액 (원)
    net_asset_total: int | None     # 순자산총액 (원) — 🔒 자금흐름 축에 쓰지 않는다
    list_shrs: int | None           # 상장좌수 (좌) — ★ 자금흐름 축의 유일한 입력
    idx_nm: str | None              # 기초지수명
    idx_close: Decimal | None       # 기초지수 종가
    idx_chg: Decimal | None         # 기초지수 전일대비
    idx_fluc_rt_bp: int | None      # 기초지수 등락률 (bp)


# ── 파싱 ─────────────────────────────────────────────────────────────────────

# 필드명 → (dataclass 속성, 파서). 🔒 이 표가 ETF 원천 스키마의 유일한 기술이다.
_FIELD_SPECS: tuple[tuple[str, str, Callable[..., Any]], ...] = (
    ("TDD_CLSPRC", "close_prc", parse_int),
    ("CMPPREVDD_PRC", "chg_prc", parse_int),
    ("FLUC_RT", "fluc_rt_bp", parse_bp),
    ("NAV", "nav", parse_decimal),
    ("TDD_OPNPRC", "open_prc", parse_int),
    ("TDD_HGPRC", "high_prc", parse_int),
    ("TDD_LWPRC", "low_prc", parse_int),
    ("ACC_TRDVOL", "acc_trdvol", parse_int),
    ("ACC_TRDVAL", "acc_trdval", parse_int),
    ("MKTCAP", "mktcap", parse_int),
    ("INVSTASST_NETASST_TOTAMT", "net_asset_total", parse_int),
    ("LIST_SHRS", "list_shrs", parse_int),
    ("OBJ_STKPRC_IDX", "idx_close", parse_decimal),
    ("CMPPREVDD_IDX", "idx_chg", parse_decimal),
    ("FLUC_RT_IDX", "idx_fluc_rt_bp", parse_bp),
)

#: 원천이 주는 필드 이름 전체. `--probe` 가 스키마 변화를 감지하는 기준이다.
KNOWN_FIELDS = frozenset(
    {"BAS_DD", "ISU_CD", "ISU_NM", "IDX_IND_NM"} | {name for name, _, _ in _FIELD_SPECS}
)


def _parse_row(row: Mapping[str, Any], *, expected_bas_dd: str | None) -> EtfDailyQuote:
    # ① 신원 먼저 — 종목코드·종목명·영업일이 없으면 이 행은 무엇에 대한 값인지 알 수 없다.
    bas_dd, isu_cd, isu_nm = parse_identity(row, expected_bas_dd=expected_bas_dd)
    values: dict[str, Any] = {
        "bas_dd": bas_dd,
        "isu_cd": isu_cd,
        "isu_nm": isu_nm,
        "idx_nm": clean(row.get("IDX_IND_NM")),
    }
    for source_name, attr, parser in _FIELD_SPECS:
        values[attr] = parser(row.get(source_name), field=source_name, isu_cd=isu_cd)
    return EtfDailyQuote(**values)


def parse_payload(payload: Any, *, expected_bas_dd: str | None = None) -> list[EtfDailyQuote]:
    """응답 본문(이미 JSON 으로 읽은 것)을 값 객체 리스트로 바꾼다.

    휴일·미래일자는 **빈 리스트**다. 예외가 아니다 — "그날은 장이 없었다"는
    사실이고, 그것을 오류로 만들면 호출부가 달력을 다시 구현하게 된다.
    """
    rows = result_rows(payload, api_label=API_LABEL)
    return [_parse_row(row, expected_bas_dd=expected_bas_dd) for row in rows]


# ── 취득 ─────────────────────────────────────────────────────────────────────

def fetch_etf_daily(
    bas_dd: str,
    *,
    auth_key: str | None = None,
    timeout: float = DEFAULT_TIMEOUT_S,
    retries: int = DEFAULT_RETRIES,
    session: Any | None = None,
    sleep: Callable[[float], None] | None = None,
) -> list[EtfDailyQuote]:
    """하루치 ETF 일별매매정보를 받아 파싱한다.

    영업일 익일 08:00(KST) 에 갱신되므로, 그 전에 당일을 물으면 0건이 정상이다.
    """
    day = validate_bas_dd(bas_dd)
    kwargs: dict[str, Any] = {}
    if sleep is not None:
        kwargs["sleep"] = sleep
    payload = fetch_json(
        PATH_ETF_BYDD_TRD,
        day,
        api_label=API_LABEL,
        auth_key=auth_key,
        timeout=timeout,
        retries=retries,
        session=session,
        **kwargs,
    )
    return parse_payload(payload, expected_bas_dd=day)


# ── `--probe` ────────────────────────────────────────────────────────────────

def raw_output_path(bas_dd: str, *, compressed: bool = False) -> Path:
    """`--probe` 응답을 저장할 경로. **`data/raw/` 외에는 만들지 않는다.**"""
    return _common_raw_output_path(RAW_PREFIX, bas_dd, compressed=compressed)


def summarize(quotes: list[EtfDailyQuote], raw_rows: list[Mapping[str, Any]]) -> str:
    """🔒 **값을 찍지 않는다.** 행 수·결측 수·스키마 변화만 요약한다.

    `--probe` 출력은 터미널·로그·세션 기록에 남는다. 시세를 그대로 흘리면
    `data/raw/` 로 막아 둔 것이 표준출력으로 새어 나간다 (약관 제11조②).
    개발자가 실제로 알아야 하는 것은 값이 아니라 **모양**이다.
    """
    lines = [f"행 수: {len(quotes)}"]
    if not quotes:
        lines.append("  → 0건. 휴일·미래일자·갱신 전(영업일 익일 08:00 KST)이면 정상이다.")

    observed = {key for row in raw_rows for key in row}
    if extra := sorted(observed - KNOWN_FIELDS):
        lines.append(f"🔴 원천에 새 필드가 생겼다: {extra}")
    if gone := sorted(KNOWN_FIELDS - observed):
        if raw_rows:
            lines.append(f"🔴 알던 필드가 사라졌다: {gone}")

    if quotes:
        lines.append("결측(None) 필드 — 0 으로 채우지 않은 칸의 수:")
        for spec in fields(EtfDailyQuote):
            missing = sum(1 for q in quotes if getattr(q, spec.name) is None)
            if missing:
                lines.append(f"  {spec.name:20s} {missing}건")
        zero_vol = sum(1 for q in quotes if q.acc_trdvol == 0)
        lines.append(f"거래량이 실제로 0 인 행: {zero_vol}건 (결측과 구별된다)")
    return "\n".join(lines)


def _run_probe(bas_dd: str, *, save: bool) -> int:
    day = validate_bas_dd(bas_dd)
    out_path = raw_output_path(day)
    if save:
        # 🔒 네트워크를 부르기 **전에** 쓰기 대상을 검사한다. 받아 놓고 쓸 곳이
        #    없다고 알면 그 데이터를 어디에 둘지 즉흥적으로 정하게 된다.
        assert_raw_output_allowed(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)

    key = resolve_auth_key(None)
    url = f"{BASE_URL}/{PATH_ETF_BYDD_TRD}"
    response = requests.get(
        url, params={"basDd": day}, headers={AUTH_HEADER: key}, timeout=DEFAULT_TIMEOUT_S
    )
    if response.status_code == 401:
        print(f"❌ 401 Unauthorized — {auth_error_message(API_LABEL)}", file=sys.stderr)
        return 2
    if response.status_code != 200:
        print(f"❌ HTTP {response.status_code}", file=sys.stderr)
        return 2

    payload = response.json()
    raw_rows = [r for r in payload.get(RESULT_BLOCK, []) if isinstance(r, Mapping)]
    quotes = parse_payload(payload, expected_bas_dd=day)

    print(f"✅ {day} · HTTP 200")
    print(summarize(quotes, raw_rows))
    if save:
        out_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        print(f"원천 저장: {out_path.relative_to(repo_root())}  (gitignore 확인됨)")
    else:
        print("원천을 저장하지 않았다 (--no-save).")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m sector.sources.krx_openapi",
        description="KRX Open API ETF 일별매매정보 어댑터 — 단독 점검용",
    )
    parser.add_argument("--probe", action="store_true", help="원천을 한 번 불러 모양을 확인한다")
    parser.add_argument("--date", required=True, metavar="YYYYMMDD", help="영업일")
    parser.add_argument(
        "--no-save",
        action="store_true",
        help="원천을 data/raw/ 에 저장하지 않는다 (승인 여부만 확인할 때)",
    )
    args = parser.parse_args(argv)
    if not args.probe:
        parser.error("--probe 를 지정한다. 이 모듈은 라이브러리이고 CLI 는 점검용이다.")

    try:
        return _run_probe(args.date, save=not args.no_save)
    except (KrxError, RuntimeError, ValueError) as exc:
        print(f"❌ {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
