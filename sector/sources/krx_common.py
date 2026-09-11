"""KRX Open API 어댑터들이 공유하는 것 — 전송 · 값 파싱 · 원천 저장 게이트.

원천이 여럿이다(ETF `etp/etf_bydd_trd` · 유가증권 `sto/stk_bydd_trd` ·
코스닥 `sto/ksq_bydd_trd`). **스키마는 다르지만 값의 문법은 같다** — 같은 결측
토큰, 같은 소수 2자리 비율, 같은 401, 같은 `OutBlock_1`. 그 공통분모만 여기 둔다.

🔴 **스키마를 여기 두지 않는다.** 어떤 필드가 있고 무엇을 뜻하는지는 각 어댑터가
   말한다. 여기 있는 것은 "문자열을 어떻게 읽는가"뿐이다. 필드표를 이 파일로
   끌어오면 ETF 와 주식의 차이(V23 — `MKT_NM`·`SECT_TP_NM` 이 있고 `NAV`·기초지수가
   없다)가 한 표에 뭉개진다.

🔒 **핵심 불변식은 `"0"` 과 `""` 를 가르는 것이다.** 거래가 없던 날(0)과 원천이
   주지 않은 칸("")은 다른 사건이고, 섞으면 유동성 게이트가 조용히 거짓말을 한다
   (→ ADR-SC-0007).
"""

from __future__ import annotations

import re
import subprocess
import time
from collections.abc import Mapping
from datetime import datetime
from decimal import Decimal, InvalidOperation
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable

import requests

from sector.secret_access import MissingSecretError, get_secret

__all__ = [
    "AUTH_HEADER",
    "BASE_URL",
    "DEFAULT_RETRIES",
    "DEFAULT_TIMEOUT_S",
    "KrxAuthError",
    "KrxError",
    "KrxSchemaError",
    "KrxTransportError",
    "RESULT_BLOCK",
    "SECRET_NAME",
    "assert_raw_output_allowed",
    "fetch_json",
    "parse_bp",
    "parse_decimal",
    "parse_int",
    "raw_dir",
    "raw_output_path",
    "repo_root",
    "resolve_auth_key",
    "result_rows",
    "validate_bas_dd",
]

# ── 원천 좌표 ────────────────────────────────────────────────────────────────
BASE_URL = "https://data-dbg.krx.co.kr/svc/apis"
RESULT_BLOCK = "OutBlock_1"
AUTH_HEADER = "AUTH_KEY"  # ★ `Authorization` 이 아니다. KRX 고유 헤더다
SECRET_NAME = "KRX_API_KEY"

# 키당 일 10,000회(V4). 재시도를 넉넉히 잡으면 이 예산을 태운다 — 2회로 묶는다.
DEFAULT_RETRIES = 2
DEFAULT_TIMEOUT_S = 30.0
RETRY_BACKOFF_S = 1.5

BAS_DD_RE = re.compile(r"^[0-9]{8}$")
# ★ 영문자를 허용하는 근거: ETF 1168행 중 304행, 주식 2766행 중 81행이 영문자를
#   포함한다(V22·V23 실측). `\d{6}` 으로 좁히면 그것들이 조용히 사라진다. 좁히지 마라.
ISU_CD_RE = re.compile(r"^[0-9A-Z]{6}$")

# 결측을 뜻하는 토큰. 🔒 `"0"` 은 여기 **절대** 넣지 않는다.
MISSING_TOKENS = frozenset({"", "-", "—", "N/A", "n/a", "null", "NULL", "None"})


# ── 예외 ─────────────────────────────────────────────────────────────────────

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


# ── 값 파싱 ──────────────────────────────────────────────────────────────────

def clean(raw: Any) -> str | None:
    """공백을 벗기고 결측 토큰을 `None` 으로 바꾼다. 🔒 `"0"` 은 살아남는다."""
    if raw is None:
        return None
    text = str(raw).strip()
    if text in MISSING_TOKENS:
        return None
    return text


def where(field: str, isu_cd: str | None) -> str:
    """오류 메시지용 좌표. 수천 행 중 어느 행의 어느 필드인지 한눈에 보여야 한다."""
    return f"{field}(ISU_CD={isu_cd or '?'})"


def parse_int(raw: Any, *, field: str, isu_cd: str | None) -> int | None:
    """정수(원·주·좌). 관찰상 천단위 구분자가 없으나 들어와도 견디게 벗겨 둔다."""
    text = clean(raw)
    if text is None:
        return None
    try:
        return int(text.replace(",", "").replace("_", ""))
    except ValueError as exc:
        raise KrxSchemaError(
            f"{where(field, isu_cd)} 를 정수로 읽을 수 없다: {text!r}. "
            f"원천 포맷이 바뀐 것으로 본다 — None 으로 삼키지 않는다."
        ) from exc


def parse_decimal(raw: Any, *, field: str, isu_cd: str | None) -> Decimal | None:
    """가격성 소수. 문자열에서 바로 `Decimal` 로 — float 를 경유하지 않는다."""
    text = clean(raw)
    if text is None:
        return None
    try:
        return Decimal(text.replace(",", ""))
    except InvalidOperation as exc:
        raise KrxSchemaError(
            f"{where(field, isu_cd)} 를 Decimal 로 읽을 수 없다: {text!r}."
        ) from exc


def parse_bp(raw: Any, *, field: str, isu_cd: str | None) -> int | None:
    """퍼센트 문자열을 **bp 정수**로. `"1.23"` → `123`.

    🔒 나머지가 생기면 던진다. 관찰 시점(ETF 1168행 · 주식 2766행 전수 소수 2자리)에는
       전부 통과하지만, 원천이 소수 3자리로 바뀌면 **반올림으로 조용히 값을 바꾸는
       대신** 멈춘다.
    """
    value = parse_decimal(raw, field=field, isu_cd=isu_cd)
    if value is None:
        return None
    scaled = value * 100
    if scaled != scaled.to_integral_value():
        raise KrxSchemaError(
            f"{where(field, isu_cd)} 가 bp 정수로 떨어지지 않는다: {raw!r}. "
            f"원천의 소수 자릿수가 늘어난 것으로 본다 — 반올림하지 않는다."
        )
    return int(scaled)


def parse_identity(row: Mapping[str, Any], *, expected_bas_dd: str | None) -> tuple[str, str, str]:
    """모든 원천이 공유하는 세 칸 — 종목코드 · 종목명 · 영업일.

    🔒 요청한 날짜와 응답의 `BAS_DD` 가 다르면 던진다 — 원천이 다른 날 데이터를
       돌려주면 룩어헤드거나 stale 이고, 둘 다 조용히 섞이면 뒤에서 찾을 수 없다.
    """
    isu_cd = clean(row.get("ISU_CD"))
    if isu_cd is None or not ISU_CD_RE.match(isu_cd):
        raise KrxSchemaError(f"ISU_CD 가 없거나 6자리 영숫자가 아니다: {row.get('ISU_CD')!r}")
    isu_nm = clean(row.get("ISU_NM"))
    if isu_nm is None:
        raise KrxSchemaError(f"ISU_NM 이 없다 (ISU_CD={isu_cd})")
    bas_dd = clean(row.get("BAS_DD"))
    if bas_dd is None or not BAS_DD_RE.match(bas_dd):
        raise KrxSchemaError(f"BAS_DD 가 없거나 YYYYMMDD 가 아니다 (ISU_CD={isu_cd}): {bas_dd!r}")
    if expected_bas_dd is not None and bas_dd != expected_bas_dd:
        raise KrxSchemaError(
            f"요청한 영업일({expected_bas_dd})과 응답의 BAS_DD({bas_dd})가 다르다 "
            f"(ISU_CD={isu_cd})."
        )
    return bas_dd, isu_cd, isu_nm


def result_rows(payload: Any, *, api_label: str) -> list[Mapping[str, Any]]:
    """응답 본문에서 `OutBlock_1` 리스트를 꺼낸다.

    휴일·미래일자는 **빈 리스트**다. 예외가 아니다 — "그날은 장이 없었다"는 사실이고,
    그것을 오류로 만들면 호출부가 달력을 다시 구현하게 된다.
    """
    if not isinstance(payload, Mapping):
        raise KrxSchemaError(f"{api_label} 응답 최상위가 객체가 아니다: {type(payload).__name__}")
    if RESULT_BLOCK not in payload:
        # 오류 응답은 `respMsg`·`respCode` 를 준다. 그대로 실어 보낸다.
        msg = payload.get("respMsg")
        code = payload.get("respCode")
        detail = f" (respCode={code}, respMsg={msg!r})" if msg or code else ""
        raise KrxSchemaError(f"{api_label} 응답에 '{RESULT_BLOCK}' 가 없다{detail}")
    block = payload[RESULT_BLOCK]
    if not isinstance(block, list):
        raise KrxSchemaError(
            f"{api_label} 의 '{RESULT_BLOCK}' 가 리스트가 아니다: {type(block).__name__}"
        )
    for row in block:
        if not isinstance(row, Mapping):
            raise KrxSchemaError(f"{api_label} 의 행이 객체가 아니다: {type(row).__name__}")
    return list(block)


# ── 영업일 · 인증 ────────────────────────────────────────────────────────────

def validate_bas_dd(bas_dd: str) -> str:
    """`"YYYYMMDD"` 형식 + 달력상 실재하는 날짜인지 본다.

    형식만 보면 `20260231` 을 통과시키고, 원천은 그것을 빈 리스트로 답한다 —
    "휴일이라 0건"과 구별할 수 없게 된다.
    """
    text = str(bas_dd).strip()
    if not BAS_DD_RE.match(text):
        raise ValueError(f"bas_dd 는 'YYYYMMDD' 8자리여야 한다: {bas_dd!r}")
    try:
        datetime.strptime(text, "%Y%m%d")
    except ValueError as exc:
        raise ValueError(f"달력에 없는 날짜다: {text}") from exc
    return text


def resolve_auth_key(auth_key: str | None) -> str:
    if auth_key is not None:
        return auth_key
    try:
        key = get_secret(SECRET_NAME)
    except MissingSecretError as exc:
        # 시크릿 관문의 메시지가 이미 "어디에 넣어라"를 말한다. 원천 문맥만 덧붙인다.
        raise KrxAuthError(f"KRX 인증키가 없다. {exc}") from exc
    assert key is not None  # required=True 라 None 이 나올 수 없다
    return key


def auth_error_message(api_label: str) -> str:
    """401 메시지. 🔒 **원인 둘을 다 적는다** — 원천이 구별해 주지 않기 때문이다."""
    return (
        f"KRX Open API 가 401 을 돌려줬다. 원인이 둘인데 응답은 둘을 구별해 주지 "
        f"않는다 — ① 인증키가 틀렸거나 만료됐다(유효 1년) ② 키는 맞으나 "
        f"'{api_label}' 개별 API 이용신청이 아직 승인되지 않았다. "
        f"개별 승인은 인증키와 별도다."
    )


# ── 전송 ─────────────────────────────────────────────────────────────────────

def fetch_json(
    path: str,
    bas_dd: str,
    *,
    api_label: str,
    auth_key: str | None = None,
    timeout: float = DEFAULT_TIMEOUT_S,
    retries: int = DEFAULT_RETRIES,
    session: Any | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> Any:
    """하루치 응답을 JSON 으로 받아 온다. 파싱은 호출부(어댑터)가 한다.

    재시도는 **전송 오류와 5xx 에만** 적용한다. 401·4xx 는 재시도해도 같은 답이고,
    키당 일 10,000회 예산을 태울 뿐이다.
    """
    day = validate_bas_dd(bas_dd)
    key = resolve_auth_key(auth_key)
    url = f"{BASE_URL}/{path}"
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
                raise KrxAuthError(auth_error_message(api_label))
            if status >= 500:
                last_error = KrxTransportError(f"원천이 {status} 를 돌려줬다")
            elif status != 200:
                raise KrxTransportError(f"예상치 못한 상태코드 {status}")
            else:
                try:
                    return response.json()
                except ValueError as exc:
                    raise KrxSchemaError(f"{api_label} 응답 본문이 JSON 이 아니다") from exc

        if attempt < attempts - 1:
            sleep(RETRY_BACKOFF_S * (attempt + 1))

    raise KrxTransportError(
        f"{attempts}회 시도 후에도 {api_label} {day} 데이터를 받지 못했다: {last_error}"
    ) from last_error


# ── 원천 저장 게이트 ─────────────────────────────────────────────────────────

def repo_root() -> Path:
    """저장소 루트. `sector/sources/krx_common.py` 에서 두 단계 위다."""
    return Path(__file__).resolve().parents[2]


def raw_dir() -> Path:
    """`data/raw/`. 🔒 KRX 원천은 여기까지만 산다 (약관 제11조② → ADR-SC-0006)."""
    return repo_root() / "data" / "raw"


def raw_output_path(prefix: str, bas_dd: str, *, compressed: bool = False) -> Path:
    """원천 저장 경로. **`data/raw/` 외에는 만들지 않는다.**

    1년치는 파일이 700개를 넘고 비압축이면 수백 MB 다. gzip 은 같은 JSON 을
    10분의 1 아래로 줄이고, 읽는 쪽(`sector_master.load_universe`)이 두 확장자를
    모두 안다.
    """
    suffix = ".json.gz" if compressed else ".json"
    return raw_dir() / f"{prefix}_{bas_dd}{suffix}"


@lru_cache(maxsize=64)
def _git_ignores(directory: str) -> bool | None:
    """git 이 이 디렉터리를 무시하는가. `None` 은 "git 이 판단하지 못했다".

    ★ 디렉터리 단위로 묻고 캐시한다 — 1년치를 저장하면 파일이 700개를 넘는데,
      파일마다 subprocess 를 띄우면 그것만으로 배치가 느려진다. `.gitignore` 는
      한 배치 안에서 바뀌지 않는다.
    """
    try:
        proc = subprocess.run(
            ["git", "check-ignore", "-q", directory],
            cwd=repo_root(),
            capture_output=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None  # git 이 없는 환경
    if proc.returncode == 0:
        return True
    if proc.returncode == 1:
        return False
    return None  # 2 이상 = git 이 판단 못 함(저장소 밖 등)


def assert_raw_output_allowed(path: Path) -> None:
    """🔴 KRX 원천이 Public 저장소로 새는 경로를 **코드로** 막는다.

    주석으로만 적어 둔 규칙은 언젠가 깨진다. 검사를 둘로 건다 —

    ① 경로가 정말 `<repo>/data/raw/` 안인가
    ② **git 이 실제로 그 경로를 무시하는가** — `.gitignore` 가 편집돼 `/data/`
       규칙이 사라지면 ① 은 통과하지만 ② 가 걸린다. 규칙이 살아 있는지를
       사람이 기억하는 대신 git 에게 물어본다.

    근거: 약관 제11조②(제3자 제공 금지) · 제11조④(이용승인 철회) → ADR-SC-0006.
    """
    target = raw_dir().resolve()
    resolved = path.resolve()
    if resolved.parent != target and target not in resolved.parents:
        raise RuntimeError(
            f"KRX 원천은 data/raw/ 밖에 쓰지 않는다. 요청된 경로: {resolved}"
        )
    if _git_ignores(str(resolved.parent)) is False:
        raise RuntimeError(
            f".gitignore 가 {resolved} 를 무시하지 않는다. 지금 쓰면 KRX 원천이 "
            f"Public 저장소에 커밋될 수 있다 (약관 제11조②). .gitignore 의 "
            f"'/data/' 규칙을 먼저 확인한다."
        )
