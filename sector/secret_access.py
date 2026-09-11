"""시크릿 접근 — 이 저장소에서 시크릿을 읽는 **유일한 관문**.

🔴 왜 관문이 하나여야 하는가 (확정 사실 V19 · streamlit 1.63.0 실측)

`st.secrets` 는 시크릿 파일이 없을 때 **접근하는 것만으로** 예외를 던진다.
네 가지 접근 형태를 전부 실측했고 넷 다 `StreamlitSecretNotFoundError` 였다::

    st.secrets["K"]        -> StreamlitSecretNotFoundError
    "K" in st.secrets      -> StreamlitSecretNotFoundError
    st.secrets.get("K")    -> StreamlitSecretNotFoundError   # ← `.get()` 도 던진다
    bool(st.secrets)       -> StreamlitSecretNotFoundError

즉 **`.get()` 은 안전한 접근자가 아니다.** 존재 확인조차 안전하지 않다.
Streamlit 은 스크립트 최상단부터 다시 실행하므로, 시크릿을 주입하지 않은 채
배포하면 예외가 첫 접근에서 터져 **화면이 통째로 빈다** — 팀원 7명은 개발자가
아니라 빈 화면에서 원인을 짚을 수단이 없다.

그래서 시크릿 접근을 이 함수 하나로 모으고 **예외를 여기서 끝낸다.** 호출부는
`None` 이냐 값이냐만 본다. 관문이 둘이 되면 둘 중 하나는 반드시 이 규칙을 잊는다.

MRO 는 `StreamlitSecretNotFoundError → … → Error → FileNotFoundError → OSError`
라 `except FileNotFoundError` 로도 잡힌다. 그런데도 아래에서 `Exception` 을 잡는
이유는 이 함수의 계약이 *"시크릿이 없을 때 절대 던지지 않는다"* 이기 때문이다 —
streamlit 이 마이너 버전에서 예외 계층을 바꿔도 빈 화면이 되살아나선 안 된다.
예외를 넓게 잡는 자리는 **이 저장소에서 여기 한 곳뿐이어야 한다.**

## 탐색 순서 — 앞의 것이 이긴다

1. ``st.secrets``  — Streamlit Community Cloud 에서는 **이것만 존재한다**
2. ``os.environ``  — 실제 환경변수. 셸에서 준 것이 파일을 이겨야 한다
3. 루트 ``.env``   — 로컬 개발·배치의 실질 경로

`.env` 를 직접 읽는 이유는 `python-dotenv` 를 **넣지 않기 위해서**다. 루트
`requirements.txt` 는 Streamlit Cloud 가 설치하는 목록이고(확정 사실 V20),
Cloud 에는 `.env` 가 아예 없다 — 거기서 쓰지 않을 의존성을 배포 목록에 올리면
메모리 2.7GB 한도를 미리 깎는다. 파서가 필요한 문법은 `KEY=VALUE` 뿐이다.

🔒 루트 `.env` 와 `backend/.env` 는 **다른 파일이다.** 여기서 읽는 것은 루트다.
   Django(동결)는 `backend/.env` 만 읽는다 — 키를 반대쪽에 넣으면 조용히 무시된다.
"""

from __future__ import annotations

import os
from pathlib import Path

__all__ = ["MissingSecretError", "get_secret", "reset_cache"]


class MissingSecretError(RuntimeError):
    """필수 시크릿이 세 경로 어디에도 없다.

    ★ 이 예외는 **던져야 한다.** 시크릿이 없는데 빈 문자열이나 `None` 으로 원천을
      부르면 401 이 돌아오고, 그 401 은 "키가 틀렸나 / 승인이 안 됐나 / 키를 아예
      안 줬나"를 구별하지 못한다. 없는 것은 없다고 여기서 말하는 편이 싸다.
    """


# ── 루트 `.env` ─────────────────────────────────────────────────────────────

def _repo_root() -> Path:
    """저장소 루트. `sector/secret_access.py` 의 한 단계 위다."""
    return Path(__file__).resolve().parent.parent


_dotenv_cache: dict[str, str] | None = None


def _parse_dotenv(text: str) -> dict[str, str]:
    """`KEY=VALUE` 만 읽는다.

    지원하는 것 — 주석(`#`) · 빈 줄 · `export ` 접두 · 값을 감싼 따옴표 ·
    값 안의 `=`(첫 `=` 에서만 자른다).
    지원하지 않는 것 — 여러 줄 값 · `${VAR}` 치환 · 명령 치환.
    🔒 **기능을 늘리지 않는다.** 늘리면 `.env` 가 코드가 되고, 그때부터
       "왜 이 값이 이렇게 들어왔나"를 디버깅해야 한다.
    """
    values: dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        key, sep, value = line.partition("=")
        if not sep:
            continue
        key = key.strip()
        if not key:
            continue
        value = value.strip()
        # 값을 감싼 따옴표만 벗긴다. 안쪽은 건드리지 않는다.
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        values[key] = value
    return values


def _dotenv_values() -> dict[str, str]:
    """루트 `.env` 를 한 번만 읽어 캐시한다. 파일이 없으면 빈 사전이다."""
    global _dotenv_cache
    if _dotenv_cache is None:
        path = _repo_root() / ".env"
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            # 없는 것도 정상이다 — Streamlit Cloud 에는 `.env` 가 없다.
            _dotenv_cache = {}
        else:
            _dotenv_cache = _parse_dotenv(text)
    return _dotenv_cache


def reset_cache() -> None:
    """`.env` 캐시를 비운다. **테스트용이다.**"""
    global _dotenv_cache
    _dotenv_cache = None


# ── `st.secrets` ────────────────────────────────────────────────────────────

def _from_streamlit(name: str) -> str | None:
    """`st.secrets[name]` 을 **절대 던지지 않고** 읽는다.

    streamlit 이 설치돼 있지 않은 경우(순수 배치 환경)도 정상 경로다.
    """
    try:
        import streamlit as st

        return str(st.secrets[name])
    except Exception:
        # 🔒 넓게 잡는 것이 의도다 — 머리주석 참조. 여기서 삼킨 예외는
        #    "시크릿이 없다"와 구별할 필요가 없다. 아래 두 경로가 이어서 찾는다.
        return None


# ── 공개 API ────────────────────────────────────────────────────────────────

def get_secret(name: str, *, required: bool = True, default: str | None = None) -> str | None:
    """시크릿 하나를 읽는다. 없으면 `required` 에 따라 던지거나 `default` 를 준다.

    ★ 빈 문자열·공백만 있는 값은 **없는 것으로 본다.** `.env` 에 `KRX_API_KEY=`
      만 적어 두고 채운 줄 알던 사고를 막는다 (루트 `.env` 37행이 실제로 그런
      모양의 주석 줄과 붙어 있다).
    """
    for source in (_from_streamlit, lambda key: os.environ.get(key),
                   lambda key: _dotenv_values().get(key)):
        value = source(name)
        if value is not None and value.strip():
            return value.strip()

    if required:
        raise MissingSecretError(
            f"시크릿 '{name}' 이 없다. 찾은 곳 — st.secrets · 환경변수 · 루트 .env.\n"
            f"  · 로컬이라면 루트 .env 에 '{name}=...' 을 넣는다 "
            f"(backend/.env 가 아니다).\n"
            f"  · Streamlit Cloud 라면 App settings → Secrets 에 넣는다."
        )
    return default
