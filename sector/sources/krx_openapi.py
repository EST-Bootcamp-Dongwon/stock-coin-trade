"""KRX Open API — ETF 일별매매정보(`etp/etf_bydd_trd`) 어댑터.

이 모듈이 하는 일은 둘이다 — ① 하루치 ETF 일별매매정보를 받아 ② 값 객체로 파싱한다.
집계·스코어링은 KRX 의 필드명도, 값이 문자열이라는 사실도 알지 못한다.

## 왜 이 원천인가 (확정 사실 V4)

`etp/etf_bydd_trd` 하나가 자금흐름 축에 필요한 셋을 **한 번에** 준다 —
`NAV` · `LIST_SHRS`(상장좌수) · `INVSTASST_NETASST_TOTAMT`(순자산총액).
2010-01-04 부터 · 키당 일 10,000회 · 영업일 익일 08:00 갱신 ·
🔴 **인증키와 별도로 개별 API 이용신청 승인이 필요하다.**

🔒 자금흐름 축은 `LIST_SHRS` 변화율만 본다. 순자산총액을 쓰지 않는다 —
   순자산총액 = 좌수 × NAV 라 NAV 변화를 모멘텀 축과 **이중 계산**하게 된다
   (→ `세션-시작-프롬프트.md` 3장). 어댑터는 셋 다 실어 보내고, 무엇을 쓸지는
   집계층(M5)이 정한다.

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
import re
import subprocess
import sys
import time
from collections.abc import Mapping
from dataclasses import dataclass, fields
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Callable

import requests

from sector.secret_access import MissingSecretError, get_secret

__all__ = [
    "BASE_URL",
    "EtfDailyQuote",
    "KrxAuthError",
    "KrxError",
    "KrxSchemaError",
    "KrxTransportError",
    "fetch_etf_daily",
    "parse_payload",
]

# ── 원천 좌표 ────────────────────────────────────────────────────────────────
BASE_URL = "https://data-dbg.krx.co.kr/svc/apis"
PATH_ETF_BYDD_TRD = "etp/etf_bydd_trd"
RESULT_BLOCK = "OutBlock_1"
AUTH_HEADER = "AUTH_KEY"  # ★ `Authorization` 이 아니다. KRX 고유 헤더다
SECRET_NAME = "KRX_API_KEY"

# 키당 일 10,000회(V4). 재시도를 넉넉히 잡으면 이 예산을 태운다 — 2회로 묶는다.
DEFAULT_RETRIES = 2
DEFAULT_TIMEOUT_S = 30.0
_RETRY_BACKOFF_S = 1.5

_BAS_DD_RE = re.compile(r"^[0-9]{8}$")
# ★ 영문자를 허용하는 근거는 머리주석 관찰표에 있다. 좁히지 마라.
_ISU_CD_RE = re.compile(r"^[0-9A-Z]{6}$")

# 결측을 뜻하는 토큰. 🔒 `"0"` 은 여기 **절대** 넣지 않는다.
_MISSING_TOKENS = frozenset({"", "-", "—", "N/A", "n/a", "null", "NULL", "None"})


class KrxError(Exception):
    """KRX Open API 관련 오류의 뿌리."""


class KrxAuthError(KrxError):
    """HTTP 401 — 키가 틀렸거나, 키는 맞으나 **개별 API 승인이 없다.**

    ★ 원천이 둘을 구별해 주지 않는다(둘 다 `Unauthorized Key`). 그래서 메시지에
      두 가능성을 **다 적는다** — 하나만 적으면 승인 대기 중인 사람이 키를 다시
      발급받으며 시간을 버린다.
    """


class KrxTransportError(KrxError):
    """네트워크·타임아웃·5xx — 재시도해도 안 된 경우."""


class KrxSchemaError(KrxError):
    """응답 구조나 값 포맷이 예상과 다르다. **조용히 넘기지 않는다.**"""


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
    fluc_rt_bp: int | None          # 등락률 (bp = %×100)
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

def _clean(raw: Any) -> str | None:
    """공백을 벗기고 결측 토큰을 `None` 으로 바꾼다. 🔒 `"0"` 은 살아남는다."""
    if raw is None:
        return None
    text = str(raw).strip()
    if text in _MISSING_TOKENS:
        return None
    return text


def _where(field: str, isu_cd: str | None) -> str:
    """오류 메시지용 좌표. 1168행 중 어느 행의 어느 필드인지 한눈에 보여야 한다."""
    return f"{field}(ISU_CD={isu_cd or '?'})"


def _parse_int(raw: Any, *, field: str, isu_cd: str | None) -> int | None:
    """정수(원·주·좌). 관찰상 천단위 구분자가 없으나 들어와도 견디게 벗겨 둔다."""
    text = _clean(raw)
    if text is None:
        return None
    try:
        return int(text.replace(",", "").replace("_", ""))
    except ValueError as exc:
        raise KrxSchemaError(
            f"{_where(field, isu_cd)} 를 정수로 읽을 수 없다: {text!r}. "
            f"원천 포맷이 바뀐 것으로 본다 — None 으로 삼키지 않는다."
        ) from exc


def _parse_decimal(raw: Any, *, field: str, isu_cd: str | None) -> Decimal | None:
    """가격성 소수. 문자열에서 바로 `Decimal` 로 — float 를 경유하지 않는다."""
    text = _clean(raw)
    if text is None:
        return None
    try:
        return Decimal(text.replace(",", ""))
    except InvalidOperation as exc:
        raise KrxSchemaError(
            f"{_where(field, isu_cd)} 를 Decimal 로 읽을 수 없다: {text!r}."
        ) from exc


def _parse_bp(raw: Any, *, field: str, isu_cd: str | None) -> int | None:
    """퍼센트 문자열을 **bp 정수**로. `"1.23"` → `123`.

    🔒 나머지가 생기면 던진다. 관찰 시점(소수 2자리 고정)에는 전수 통과하지만,
       원천이 소수 3자리로 바뀌면 **반올림으로 조용히 값을 바꾸는 대신** 멈춘다.
    """
    value = _parse_decimal(raw, field=field, isu_cd=isu_cd)
    if value is None:
        return None
    scaled = value * 100
    if scaled != scaled.to_integral_value():
        raise KrxSchemaError(
            f"{_where(field, isu_cd)} 가 bp 정수로 떨어지지 않는다: {raw!r}. "
            f"원천의 소수 자릿수가 늘어난 것으로 본다 — 반올림하지 않는다."
        )
    return int(scaled)


# 필드명 → (dataclass 속성, 파서). 🔒 이 표가 원천 스키마의 유일한 기술이다.
_FIELD_SPECS: tuple[tuple[str, str, Callable[..., Any]], ...] = (
    ("TDD_CLSPRC", "close_prc", _parse_int),
    ("CMPPREVDD_PRC", "chg_prc", _parse_int),
    ("FLUC_RT", "fluc_rt_bp", _parse_bp),
    ("NAV", "nav", _parse_decimal),
    ("TDD_OPNPRC", "open_prc", _parse_int),
    ("TDD_HGPRC", "high_prc", _parse_int),
    ("TDD_LWPRC", "low_prc", _parse_int),
    ("ACC_TRDVOL", "acc_trdvol", _parse_int),
    ("ACC_TRDVAL", "acc_trdval", _parse_int),
    ("MKTCAP", "mktcap", _parse_int),
    ("INVSTASST_NETASST_TOTAMT", "net_asset_total", _parse_int),
    ("LIST_SHRS", "list_shrs", _parse_int),
    ("OBJ_STKPRC_IDX", "idx_close", _parse_decimal),
    ("CMPPREVDD_IDX", "idx_chg", _parse_decimal),
    ("FLUC_RT_IDX", "idx_fluc_rt_bp", _parse_bp),
)

#: 원천이 주는 필드 이름 전체. `--probe` 가 스키마 변화를 감지하는 기준이다.
KNOWN_FIELDS = frozenset(
    {"BAS_DD", "ISU_CD", "ISU_NM", "IDX_IND_NM"} | {name for name, _, _ in _FIELD_SPECS}
)


def _parse_row(row: Any, *, expected_bas_dd: str | None) -> EtfDailyQuote:
    if not isinstance(row, Mapping):
        raise KrxSchemaError(f"행이 객체가 아니다: {type(row).__name__}")

    # ① 신원 먼저. 종목코드·종목명이 없으면 이 행은 무엇에 대한 값인지 알 수 없다.
    isu_cd = _clean(row.get("ISU_CD"))
    if isu_cd is None or not _ISU_CD_RE.match(isu_cd):
        raise KrxSchemaError(f"ISU_CD 가 없거나 6자리 영숫자가 아니다: {row.get('ISU_CD')!r}")
    isu_nm = _clean(row.get("ISU_NM"))
    if isu_nm is None:
        raise KrxSchemaError(f"ISU_NM 이 없다 (ISU_CD={isu_cd})")

    # ② 영업일. 🔒 요청한 날짜와 다르면 던진다 — 원천이 다른 날 데이터를 돌려주면
    #    룩어헤드거나 stale 이고, 둘 다 조용히 섞이면 뒤에서 찾을 수 없다.
    bas_dd = _clean(row.get("BAS_DD"))
    if bas_dd is None or not _BAS_DD_RE.match(bas_dd):
        raise KrxSchemaError(f"BAS_DD 가 없거나 YYYYMMDD 가 아니다 (ISU_CD={isu_cd}): {bas_dd!r}")
    if expected_bas_dd is not None and bas_dd != expected_bas_dd:
        raise KrxSchemaError(
            f"요청한 영업일({expected_bas_dd})과 응답의 BAS_DD({bas_dd})가 다르다 "
            f"(ISU_CD={isu_cd})."
        )

    values: dict[str, Any] = {
        "bas_dd": bas_dd,
        "isu_cd": isu_cd,
        "isu_nm": isu_nm,
        "idx_nm": _clean(row.get("IDX_IND_NM")),
    }
    for source_name, attr, parser in _FIELD_SPECS:
        values[attr] = parser(row.get(source_name), field=source_name, isu_cd=isu_cd)
    return EtfDailyQuote(**values)


def parse_payload(payload: Any, *, expected_bas_dd: str | None = None) -> list[EtfDailyQuote]:
    """응답 본문(이미 JSON 으로 읽은 것)을 값 객체 리스트로 바꾼다.

    휴일·미래일자는 **빈 리스트**다. 예외가 아니다 — "그날은 장이 없었다"는
    사실이고, 그것을 오류로 만들면 호출부가 달력을 다시 구현하게 된다.
    """
    if not isinstance(payload, Mapping):
        raise KrxSchemaError(f"응답 최상위가 객체가 아니다: {type(payload).__name__}")

    if RESULT_BLOCK not in payload:
        # 오류 응답은 `respMsg`·`respCode` 를 준다. 그대로 실어 보낸다.
        msg = payload.get("respMsg")
        code = payload.get("respCode")
        detail = f" (respCode={code}, respMsg={msg!r})" if msg or code else ""
        raise KrxSchemaError(f"응답에 '{RESULT_BLOCK}' 가 없다{detail}")

    block = payload[RESULT_BLOCK]
    if not isinstance(block, list):
        raise KrxSchemaError(f"'{RESULT_BLOCK}' 가 리스트가 아니다: {type(block).__name__}")

    return [_parse_row(row, expected_bas_dd=expected_bas_dd) for row in block]


# ── 취득 ─────────────────────────────────────────────────────────────────────

def _validate_bas_dd(bas_dd: str) -> str:
    """`"YYYYMMDD"` 형식 + 달력상 실재하는 날짜인지 본다.

    형식만 보면 `20260231` 을 통과시키고, 원천은 그것을 빈 리스트로 답한다 —
    "휴일이라 0건"과 구별할 수 없게 된다.
    """
    text = str(bas_dd).strip()
    if not _BAS_DD_RE.match(text):
        raise ValueError(f"bas_dd 는 'YYYYMMDD' 8자리여야 한다: {bas_dd!r}")
    try:
        datetime.strptime(text, "%Y%m%d")
    except ValueError as exc:
        raise ValueError(f"달력에 없는 날짜다: {text}") from exc
    return text


def _resolve_auth_key(auth_key: str | None) -> str:
    if auth_key is not None:
        return auth_key
    try:
        key = get_secret(SECRET_NAME)
    except MissingSecretError as exc:
        # 시크릿 관문의 메시지가 이미 "어디에 넣어라"를 말한다. 원천 문맥만 덧붙인다.
        raise KrxAuthError(f"KRX 인증키가 없다. {exc}") from exc
    assert key is not None  # required=True 라 None 이 나올 수 없다
    return key


def fetch_etf_daily(
    bas_dd: str,
    *,
    auth_key: str | None = None,
    timeout: float = DEFAULT_TIMEOUT_S,
    retries: int = DEFAULT_RETRIES,
    session: Any | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> list[EtfDailyQuote]:
    """하루치 ETF 일별매매정보를 받아 파싱한다.

    영업일 익일 08:00(KST) 에 갱신되므로, 그 전에 당일을 물으면 0건이 정상이다.

    재시도는 **전송 오류와 5xx 에만** 적용한다. 401·4xx 는 재시도해도 같은 답이고,
    키당 일 10,000회 예산을 태울 뿐이다.
    """
    day = _validate_bas_dd(bas_dd)
    key = _resolve_auth_key(auth_key)
    url = f"{BASE_URL}/{PATH_ETF_BYDD_TRD}"
    http = session if session is not None else requests
    attempts = max(1, retries + 1)
    last_error: Exception | None = None

    for attempt in range(attempts):
        try:
            response = http.get(
                url,
                params={"basDd": day},
                headers={AUTH_HEADER: key},
                timeout=timeout,
            )
        except Exception as exc:  # requests.RequestException 및 그 하위 전부
            last_error = exc
        else:
            status = response.status_code
            if status == 401:
                raise KrxAuthError(
                    "KRX Open API 가 401 을 돌려줬다. 원인이 둘인데 응답은 둘을 "
                    "구별해 주지 않는다 — ① 인증키가 틀렸거나 만료됐다(유효 1년) "
                    "② 키는 맞으나 'ETF 일별매매정보' 개별 API 이용신청이 아직 "
                    "승인되지 않았다. 개별 승인은 인증키와 별도다."
                )
            if status >= 500:
                last_error = KrxTransportError(f"원천이 {status} 를 돌려줬다")
            elif status != 200:
                raise KrxTransportError(f"예상치 못한 상태코드 {status}")
            else:
                try:
                    payload = response.json()
                except ValueError as exc:
                    raise KrxSchemaError("응답 본문이 JSON 이 아니다") from exc
                return parse_payload(payload, expected_bas_dd=day)

        if attempt < attempts - 1:
            sleep(_RETRY_BACKOFF_S * (attempt + 1))

    raise KrxTransportError(
        f"{attempts}회 시도 후에도 {day} 데이터를 받지 못했다: {last_error}"
    ) from last_error


# ── `--probe` ────────────────────────────────────────────────────────────────

def _repo_root() -> Path:
    """저장소 루트. `sector/sources/krx_openapi.py` 에서 두 단계 위다."""
    return Path(__file__).resolve().parents[2]


def raw_output_path(bas_dd: str) -> Path:
    """`--probe` 응답을 저장할 경로. **`data/raw/` 외에는 만들지 않는다.**"""
    return _repo_root() / "data" / "raw" / f"etf_bydd_trd_{bas_dd}.json"


def _assert_raw_output_allowed(path: Path) -> None:
    """🔴 KRX 원천이 Public 저장소로 새는 경로를 **코드로** 막는다.

    주석으로만 적어 둔 규칙은 언젠가 깨진다. 검사를 둘로 건다 —

    ① 경로가 정말 `<repo>/data/raw/` 안인가
    ② **git 이 실제로 그 경로를 무시하는가** — `.gitignore` 가 편집돼 `/data/`
       규칙이 사라지면 ① 은 통과하지만 ② 가 걸린다. 규칙이 살아 있는지를
       사람이 기억하는 대신 git 에게 물어본다.

    근거: 약관 제11조②(제3자 제공 금지) · 제11조④(이용승인 철회) → ADR-SC-0006.
    """
    root = _repo_root()
    raw_dir = (root / "data" / "raw").resolve()
    resolved = path.resolve()
    if resolved.parent != raw_dir and raw_dir not in resolved.parents:
        raise RuntimeError(
            f"KRX 원천은 data/raw/ 밖에 쓰지 않는다. 요청된 경로: {resolved}"
        )

    try:
        proc = subprocess.run(
            ["git", "check-ignore", "-q", str(resolved)],
            cwd=root,
            capture_output=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return  # git 이 없는 환경. ① 만으로 넘어간다
    if proc.returncode == 1:
        raise RuntimeError(
            f".gitignore 가 {resolved} 를 무시하지 않는다. 지금 쓰면 KRX 원천이 "
            f"Public 저장소에 커밋될 수 있다 (약관 제11조②). .gitignore 의 "
            f"'/data/' 규칙을 먼저 확인한다."
        )
    # returncode 0 = 무시됨(정상) · 2 이상 = git 이 판단 못 함(저장소 밖 등) → ① 만 믿는다


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
    day = _validate_bas_dd(bas_dd)
    out_path = raw_output_path(day)
    if save:
        # 🔒 네트워크를 부르기 **전에** 쓰기 대상을 검사한다. 받아 놓고 쓸 곳이
        #    없다고 알면 그 데이터를 어디에 둘지 즉흥적으로 정하게 된다.
        _assert_raw_output_allowed(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)

    key = _resolve_auth_key(None)
    url = f"{BASE_URL}/{PATH_ETF_BYDD_TRD}"
    response = requests.get(
        url, params={"basDd": day}, headers={AUTH_HEADER: key}, timeout=DEFAULT_TIMEOUT_S
    )
    if response.status_code == 401:
        print(
            "❌ 401 Unauthorized — ① 인증키가 틀렸거나 만료됐다(유효 1년) "
            "② 'ETF 일별매매정보' 개별 API 이용신청이 승인되지 않았다.\n"
            "   개별 승인은 인증키 발급과 별도이고 시간이 걸린다.",
            file=sys.stderr,
        )
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
        print(f"원천 저장: {out_path.relative_to(_repo_root())}  (gitignore 확인됨)")
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
