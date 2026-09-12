"""조 passcode — **해시만 저장한다.**

## 🔴 위협 모델을 먼저 적는다

앱은 **public** 이다(ADR-SC-0010). 그러니 passcode 가 지키는 것이 무엇인지 분명히
해 둔다 —

| passcode 가 지키는 것 | 지키지 **않는** 것 |
|---|---|
| 남의 조에 **쓰는** 것 — 섹터 확정·코멘트 | 섹터 점수 화면. 그건 어차피 공개다 |
| 조 이름을 사칭하는 것 | KRX 파생값. 화면 표출은 약관 허용 범위다 |

즉 이것은 **기밀 보호가 아니라 쓰기 권한 구분**이다. 그렇게 적어 두지 않으면
나중에 누군가 "passcode 가 있으니 민감한 것을 넣어도 된다" 고 생각한다.

## 🔒 왜 `hashlib.scrypt` 인가

- **표준 라이브러리다.** bcrypt·argon2 는 새 의존이고, 루트 `requirements.txt` 는
  Streamlit Cloud 가 설치하는 목록이라(V20) 한 줄이 메모리 2.7GB 한도를 깎는다.
- **메모리 하드**라 GPU 로 병렬화하기 어렵다. `n=2**14` 이면 한 번에 16MB·수십 ms —
  사람에게는 안 느껴지고 대입 공격에는 실질적인 마찰이 된다.
- 🔴 **앱에 시도 횟수 제한을 걸 수단이 없다.** Streamlit 은 요청 사이에 서버 상태를
  두지 않고, 이벤트 원장에 실패를 적으면 그것대로 원장이 더러워진다. 그래서
  방어를 **passcode 길이**와 해시 비용으로 민다 (→ `MIN_LENGTH`).

## 🔒 평문은 어디에도 남기지 않는다

이 모듈 밖으로 평문이 나가지 않는다 — 이벤트에도, 로그에도, 예외 메시지에도.
`events.team_created` 가 `scrypt$` 로 시작하지 않는 값을 **거부**해서 그 규칙을
사람의 기억이 아니라 코드로 지킨다.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets

__all__ = [
    "PasscodeError",
    "MIN_LENGTH",
    "SCRYPT_N",
    "hash_passcode",
    "verify_passcode",
    "suggest_passcode",
]


class PasscodeError(ValueError):
    """passcode 가 규칙에 맞지 않는다. 🔒 메시지에 평문을 넣지 않는다."""


#: 🔴 public 앱이라 시도 횟수를 못 막는다(머리주석). 길이로 민다.
MIN_LENGTH = 8
MAX_LENGTH = 128

SCRYPT_N = 1 << 14   # 16384 — 약 16MB · 수십 ms
SCRYPT_R = 8
SCRYPT_P = 1
DK_LEN = 32
SALT_LEN = 16

_PREFIX = "scrypt"


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _check(passcode: str) -> bytes:
    """길이만 본다. 🔒 **내용을 예외 메시지에 넣지 않는다.**"""
    if not isinstance(passcode, str):
        raise PasscodeError("passcode 가 문자열이 아니다")
    if len(passcode) < MIN_LENGTH:
        raise PasscodeError(
            f"passcode 가 너무 짧다 ({len(passcode)}자). {MIN_LENGTH}자 이상이어야 한다.\n"
            f"  → 앱이 공개라 시도 횟수를 막을 수 없다. 기억하기 쉬운 단어 서넛을 "
            f"이어 붙이는 편이 짧고 복잡한 것보다 낫다"
        )
    if len(passcode) > MAX_LENGTH:
        raise PasscodeError(f"passcode 가 너무 길다 ({len(passcode)}자 > {MAX_LENGTH}자)")
    return passcode.encode("utf-8")


def hash_passcode(passcode: str, *, salt: bytes | None = None) -> str:
    """`scrypt$n$r$p$salt$hash`.

    ★ `salt` 를 주입할 수 있게 둔 것은 **테스트 때문이다** — 이 저장소의 규율이
      "언제 돌려도 같은 답"이라(AGENTS.md 5장) 무작위가 섞이면 스냅샷이 흔들린다.
      🔒 운영 경로에서는 절대 주지 않는다. 기본값이 `secrets.token_bytes` 인 이유다.
    """
    raw = _check(passcode)
    if salt is None:
        salt = secrets.token_bytes(SALT_LEN)
    elif len(salt) < 8:
        raise PasscodeError("salt 가 너무 짧다 (8바이트 이상)")
    digest = hashlib.scrypt(raw, salt=salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P, dklen=DK_LEN)
    return f"{_PREFIX}${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}${_b64(salt)}${_b64(digest)}"


def verify_passcode(passcode: str, stored: str) -> bool:
    """맞으면 `True`. 🔒 **틀린 이유를 말하지 않는다** — 전부 `False` 다.

    "형식이 깨졌다" 와 "틀렸다" 를 구별해 주면 그것이 곧 정보다. 예외를 던지지
    않는 것도 같은 이유다 — 던지면 화면이 죽고, 죽는 방식이 답을 알려준다.
    """
    if not isinstance(passcode, str) or not isinstance(stored, str):
        return False
    parts = stored.split("$")
    if len(parts) != 6 or parts[0] != _PREFIX:
        return False
    try:
        n, r, p = int(parts[1]), int(parts[2]), int(parts[3])
        salt, expected = _unb64(parts[4]), _unb64(parts[5])
        # 저장된 파라미터를 그대로 쓴다 — 나중에 비용을 올려도 옛 해시가 계속 검증된다.
        actual = hashlib.scrypt(
            passcode.encode("utf-8"), salt=salt, n=n, r=r, p=p, dklen=len(expected)
        )
    except (ValueError, TypeError, MemoryError):
        return False
    # 🔒 `==` 이 아니라 상수시간 비교. 바이트 단위 조기 반환이 타이밍을 흘린다.
    return hmac.compare_digest(actual, expected)


def suggest_passcode(*, words: int = 4) -> str:
    """기억할 수 있으면서 긴 passcode 를 제안한다.

    🔴 사람에게 "알아서 정하라" 고 하면 `1234` 를 쓴다. 앱이 기본값을 **보여주고**
       고치게 하는 편이 실제로 안전하다. 목록은 짧지만 조합이 크다 —
       48^4 ≈ 530만. 공개 앱의 대입 속도(해시 수십 ms)에서는 실질적이다.
    """
    pool = (
        "산 바다 강 들 숲 밤 낮 별 달 해 비 눈 바람 구름 안개 서리 "
        "돌 나무 풀 꽃 잎 뿌리 열매 씨앗 길 다리 문 집 마을 언덕 골짜기 벌판 "
        "봄 여름 가을 겨울 아침 저녁 새벽 한낮 북 남 동 서 위 아래 안 밖"
    ).split()
    if words < 2:
        raise PasscodeError("단어가 둘 이상이어야 한다")

    # 🔴 한 글자 단어만 뽑히면 `산-강-들-숲` = 7자라 **자기 최소 길이를 못 넘긴다.**
    #    무작위라 간헐적으로만 드러나는 종류의 버그다. 그래서 길이를 맞출 때까지
    #    단어를 **더한다** — 줄이지 않으므로 조합 수가 약속보다 작아지지 않는다.
    chosen = [secrets.choice(pool) for _ in range(words)]
    while len("-".join(chosen)) < MIN_LENGTH:
        chosen.append(secrets.choice(pool))
    return "-".join(chosen)


# ── 마스터(개발자) 계정 ─────────────────────────────────────────────────────

MASTER_SECRET = "ADMIN_PASSCODE_HASH"


def master_hash() -> str | None:
    """마스터 passcode 의 **해시**. 없으면 `None` — 마스터 기능이 잠긴 것이다.

    🔴 **해시를 시크릿에 넣는다. 평문이 아니다.** 평문을 `.env` 에 두면 그 파일을
       여는 모든 경로가 곧 마스터 권한이 된다. 해시만 두면 파일을 봐도 쓸 수 없다.

    🔒 시크릿이 없을 때 `None` 을 돌려주는 것이 중요하다 — "설정 안 했으니 모두
       마스터" 가 되면 정반대가 된다. 호출부는 `None` 을 **잠김**으로 읽는다.
    """
    from sector.secret_access import get_secret

    value = get_secret(MASTER_SECRET, required=False)
    return value if value and value.startswith(f"{_PREFIX}$") else None


def is_master(passcode: str) -> bool:
    """마스터인가. 🔒 시크릿이 없으면 **언제나 거짓**이다."""
    stored = master_hash()
    return bool(stored) and verify_passcode(passcode, stored)
