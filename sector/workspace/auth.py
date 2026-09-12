"""조 passcode — **해시만 저장한다.**

## 🔴 위협 모델을 먼저 적는다

앱은 **public** 이다(ADR-SC-0010). 그러니 passcode 가 지키는 것이 무엇인지 분명히
해 둔다 —

| passcode 가 지키는 것 | 지키지 **않는** 것 |
|---|---|
| 남의 조에 **쓰는** 것 — 섹터 확정·코멘트 | 섹터 점수 화면. 그건 어차피 공개다 |
| 조 이름을 사칭하는 것 | KRX 파생값. 화면 표출은 약관 허용 범위다 |
| — | 🔴 **원장 읽기.** 조·확정 사유·코멘트를 누구나 읽는다 (ADR-SC-0011 ⑥) |

즉 이것은 **기밀 보호가 아니라 쓰기 권한 구분**이다. 그렇게 적어 두지 않으면
나중에 누군가 "passcode 가 있으니 민감한 것을 넣어도 된다" 고 생각한다.

🔴 **원장 읽기가 공개인 것은 HF private 대비 실질 변화다**(2026-09-12 · Supabase).
   `workspace_event` 의 SELECT 가 `anon` 에 열려 있다 — 두 렌더러가 모두 anon 키로
   붙으므로 "anon 이 할 수 있는 것 = 앱이 할 수 있는 것" 이고, 읽기를 RPC 뒤에 숨겨도
   실질 경계가 생기지 않는다. 그리고 조별 확정 현황을 팀 전체가 보는 것이 이 도구의
   목적이다. 따라서 —

- **코멘트·확정 사유에 비밀을 적지 않는다.** 화면도 그렇게 말해야 한다
- 실제로 지키는 것은 하나다 — **passcode 해시는 어떤 경로로도 나가지 않는다.**
  해시는 `workspace_team_secret`(정책 0개 · GRANT 0)에 있고, 앱이 받는 것은
  `scrypt$n$r$p$salt$` 까지다(→ `recompute_passcode`)

## 🔒 왜 `hashlib.scrypt` 인가

- **표준 라이브러리다.** bcrypt·argon2 는 새 의존이고, 루트 `requirements.txt` 는
  Streamlit Cloud 가 설치하는 목록이라(V20) 한 줄이 메모리 2.7GB 한도를 깎는다.
- **메모리 하드**라 GPU 로 병렬화하기 어렵다. `n=2**14` 이면 한 번에 16MB·수십 ms —
  사람에게는 안 느껴지고 대입 공격에는 실질적인 마찰이 된다.
- 🔴 **앱에 시도 횟수 제한을 걸 수단이 없다.** Streamlit 은 요청 사이에 서버 상태를
  두지 않고, 이벤트 원장에 실패를 적으면 그것대로 원장이 더러워진다. 그래서
  방어를 **passcode 길이**와 해시 비용으로 민다 (→ `MIN_LENGTH`).
  ★ **원장이 Supabase 로 가면 그 수단이 생긴다**(2026-09-12) — 검증이 `workspace_append`
    RPC 안에서 일어나므로 DB 가 횟수를 셀 수 있다. 길이 방어를 푸는 것은 아니고,
    "지금은 없다" 가 "이제 있다" 로 바뀐 자리를 적어 둔다.

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
    "params_of",
    "recompute_passcode",
    "credential_matches",
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

    ★ `salt` 를 주입할 수 있게 둔 것은 두 가지 때문이다 —

    1. **테스트** — 이 저장소의 규율이 "언제 돌려도 같은 답"이라(AGENTS.md 5장)
       무작위가 섞이면 스냅샷이 흔들린다.
    2. **저장된 salt 로 재계산** — 검증이 DB 안으로 갔으므로(ADR-SC-0011 ⑤) 앱은
       *이미 저장된* salt 로 같은 해시를 다시 만들어 보내야 한다.

    🔒 **새 조를 만들 때는 절대 주지 않는다.** 기본값이 `secrets.token_bytes` 인
       이유다 — 두 조가 같은 salt 를 쓰면 같은 passcode 인지가 드러난다.
       재계산 경로는 `recompute_passcode` 하나로 좁혀 뒀다.
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


# ── 검증이 DB 안으로 갔다 — 앱은 재계산만 한다 ──────────────────────────────
#
# ## 🔒 왜 salt 를 받아도 약해지지 않는가
#
# `workspace_passcode_params` RPC 는 `scrypt$n$r$p$salt$` 까지만 준다 — **digest 는
# 주지 않는다.** salt 는 비밀이 아니라 레인보우 테이블을 막는 장치고, salt 만으로는
# 오프라인 대입이 성립하지 않는다: 맞는지 확인하려면 매번 이 DB 를 불러야 하므로
# 대입이 **온라인으로 묶이고**, 그러면 횟수를 셀 수 있다.
#
# 🔴 HF 방식(해시를 원장에 통째로 담아 토큰 있는 사람이 다 보는 것)보다 **강하다.**


def params_of(stored: str) -> str:
    """`scrypt$n$r$p$salt$digest` → `scrypt$n$r$p$salt$` (**digest 를 뗀다**).

    🔒 SQL `workspace_passcode_params` 와 **같은 것**을 한다 —
       `left(h, length(h) - length(split_part(h, '$', 6)))`. 저장소 구현이 셋인데
       (`LocalStore`·`HubStore`·`SupabaseStore`) 앞 둘은 해시를 손에 들고 있으므로
       **떼는 자리를 여기 한 곳으로 모은다.** 두 곳에서 자르면 한 곳이 digest 를
       흘린다.

    🔒 꼬리 `$` 를 남긴다. `recompute_passcode` 가 digest 를 **이어 붙이기만** 하면
       저장된 문자열과 바이트가 같아지기 때문이다 — 다시 조립하면 표기가 어긋난다.
    """
    if not isinstance(stored, str):
        raise PasscodeError("해시가 문자열이 아니다")
    parts = stored.split("$")
    if len(parts) != 6 or parts[0] != _PREFIX:
        raise PasscodeError("해시 형식이 아니다 — `hash_passcode` 가 만든 값이어야 한다")
    return stored[: len(stored) - len(parts[5])]


def recompute_passcode(passcode: str, params: str) -> str:
    """저장된 파라미터·salt 로 **같은 인코딩을 다시 만든다.**

    돌려주는 것이 곧 `workspace_append` 에 보낼 자격증명(`p_encoded`)이다.
    평문은 어떤 형태로도 DB 에 도달하지 않는다.

    🔴 `params` 에 digest 가 실려 있으면 **거부한다.** 호출부가 저장된 해시를 통째로
       들고 있다는 뜻이고, 그것은 ④(해시는 원장 밖 · 어떤 경로로도 안 나간다)가
       깨진 상태다. 규칙을 사람의 기억이 아니라 코드가 지킨다.

    🔒 `DK_LEN` 은 인코딩에 **없다.** n·r·p 는 문자열에 적혀 있어 나중에 비용을
       올려도 옛 해시가 검증되지만(→ `verify_passcode`), dklen 을 바꾸면 옛 조가
       들어오지 못한다. 바꿀 일이 생기면 인코딩에 칸을 하나 더해야 한다.
    """
    if not isinstance(passcode, str):
        raise PasscodeError("passcode 가 문자열이 아니다")
    if not isinstance(params, str):
        raise PasscodeError("passcode 파라미터가 문자열이 아니다")
    parts = params.split("$")
    if len(parts) != 6 or parts[0] != _PREFIX:
        raise PasscodeError(
            f"passcode 파라미터 형식이 아니다: {len(parts)}칸. "
            f"`{_PREFIX}$n$r$p$salt$` 여야 한다"
        )
    if parts[5]:
        raise PasscodeError(
            "파라미터에 digest 가 실려 있다. 🔴 앱은 digest 를 받지 않는다 "
            "(ADR-SC-0011 ④) — `params_of` 로 떼고 넘긴다"
        )
    try:
        n, r, p = int(parts[1]), int(parts[2]), int(parts[3])
        salt = _unb64(parts[4])
    except (ValueError, TypeError) as exc:
        raise PasscodeError(f"passcode 파라미터를 읽을 수 없다: {exc}") from exc
    if not salt:
        raise PasscodeError("salt 가 비어 있다")
    try:
        digest = hashlib.scrypt(
            passcode.encode("utf-8"), salt=salt, n=n, r=r, p=p, dklen=DK_LEN
        )
    except (ValueError, MemoryError) as exc:
        raise PasscodeError(f"passcode 를 다시 계산할 수 없다: {exc}") from exc
    # 🔒 앞부분을 **그대로** 이어 쓴다. 다시 조립하면 표기가 어긋날 수 있다.
    return params + _b64(digest)


def credential_matches(credential: str, stored: str) -> bool:
    """자격증명이 저장된 해시와 같은가. 🔒 **상수시간 비교.**

    SQL `workspace_ct_eq` 와 같은 것을 한다 — 저장된 해시를 손에 든 구현
    (`LocalStore`·`HubStore`)이 DB 와 **같은 방식으로** 판정해야 세 구현이 같은
    답을 낸다.

    🔒 `==` 이 아닌 이유는 `verify_passcode` 와 같다. 그리고 틀린 이유를 말하지
       않는다 — 형식이 깨졌어도 그냥 `False` 다.
    """
    if not isinstance(credential, str) or not isinstance(stored, str):
        return False
    if not credential or not stored:
        return False
    return hmac.compare_digest(credential.encode("utf-8"), stored.encode("utf-8"))


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
