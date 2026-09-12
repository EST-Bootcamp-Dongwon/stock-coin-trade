"""Hugging Face Hub — 파생값이 이 기계를 떠나는 **유일한 문**.

## 🔴 이 파일이 지키는 것 세 가지

**① private 인지 올리기 직전에 서버에 다시 묻는다** (ADR-SC-0006 ⑥)

`create_repo(exist_ok=True)` 는 대상이 **public 이어도 그냥 성공한다.** 즉
"만들 때 private 으로 만들었다"는 기억은 증거가 아니다. 누가 웹에서 공개로
바꿔도 코드는 모른다. 그래서 `assert_private` 이 **커밋 직전에** 서버에 묻고,
아니면 **한 파일도 올리지 않고** 멈춘다. 약관 제11조② 위반의 대가는
이용승인 철회(④)라 "일단 올리고 나중에 내린다"가 성립하지 않는다.

**② 단일 커밋으로 올린다 — 원자적이다**

`upload_file` 을 파일마다 부르면 중간에 끊길 때 **절반만 갱신된** 상태가 남는다.
그 상태는 에러를 내지 않는다 — 앱은 새 `latest/` 와 옛 `MANIFEST.json` 을 보고
아무 불평 없이 틀린 화면을 그린다. **조용한 불일치가 시끄러운 실패보다 나쁘다.**
`create_commit` 은 작업 전체를 한 커밋으로 묶어 그 상태를 만들지 않는다.

**③ 경로 허용목록이 문 앞에 있다**

`commit()` 이 모든 작업의 경로를 검사한다. 검사를 호출부에 두면 새 호출부가
생길 때마다 잊힌다. **문에 두면 지나갈 수 없다.** `etf_bydd_trd_*` 같은 원천
파일 이름은 이름만으로 거부한다 (ADR-SC-0006 ①).

## 토큰 — 이름이 역할을 말한다

| 이름 | 누가 | 어디에 |
|---|---|---|
| `HF_TOKEN_WRITE` | 로컬 배치(`batch/publish.py`) | 루트 `.env` **만** |
| `HF_TOKEN_READ` | Streamlit 앱 | Streamlit Secrets |

🔒 옛 이름 `HUGGINGFACE_ACCESS_TOKEN` 을 버린 이유 — "ACCESS" 가 read 인지
   write 인지 이름으로 알 수 없었고, 실제로 **org 전체 쓰기 권한 토큰이 앱용
   칸에 들어가 있었다**(2026-09-11 확인). 그대로 배포했다면 쓰기 토큰이
   Streamlit Secrets 로 넘어간다. 이름이 역할과 어긋나면 언젠가 그렇게 샌다.

시크릿은 `sector.secret_access.get_secret` 하나로만 읽는다 (확정 사실 V19 —
`st.secrets` 는 접근만으로 던진다).
"""

from __future__ import annotations

import json
from typing import Any

from sector.secret_access import get_secret

__all__ = [
    "HubError",
    "PublishBlocked",
    "REPO_ID",
    "TOKEN_ENV_WRITE",
    "TOKEN_ENV_READ",
    "assert_publishable_path",
    "WORKSPACE_REPO_ID",
    "assert_private",
    "commit",
    "dataset_api",
    "download_bytes",
    "ensure_private_repo",
    "read_json",
    "read_token",
    "repo_exists",
    "write_token",
]

#: 파생값 저장소. 🔒 **private 이어야 한다** — 팀원 7명도 약관상 제3자다.
REPO_ID = "stock-coin-trade/sector-scores"

#: 팀 노트(M13). 여기서는 쓰지 않고, 토큰이 갈린다는 사실만 남긴다.
NOTES_REPO_ID = "stock-coin-trade/team-notes"

#: 조별 협업 상태(조 · 참가 · 섹터 확정 · 코멘트)가 사는 곳 — M8.
#: 🔒 **점수 저장소와 갈라 둔 것이 의도다.** 이쪽은 앱이 **쓰기**를 하고 저쪽은 읽기만
#:    한다. 한 저장소에 두면 앱에 준 토큰 하나가 점수까지 덮어쓸 수 있다 (→ V29).
WORKSPACE_REPO_ID = "stock-coin-trade/team-workspace"

TOKEN_ENV_WRITE = "HF_TOKEN_WRITE"
TOKEN_ENV_READ = "HF_TOKEN_READ"

REPO_TYPE = "dataset"

#: 나가도 되는 경로. 🔒 **접두사와 정확한 이름 둘 다 명시한다** — 와일드카드를
#: 하나 넓히면 그 순간 무엇이 나가는지 아무도 말할 수 없게 된다.
ALLOWED_PATH_PREFIXES: tuple[str, ...] = (
    "sector_daily/",
    "market_daily/",
    "score_daily/",   # M7 이 쓴다
    "latest/",
)
ALLOWED_EXACT_PATHS: tuple[str, ...] = ("MANIFEST.json", "README.md")

#: 워크스페이스 저장소의 허용 경로. 이벤트 1건 = 파일 1개라 접두 하나면 충분하다.
#: 🔒 여기에 `latest/` 같은 **집계 스냅샷을 두지 않는다** — 상태는 이벤트를 접어서
#:    만든다. 스냅샷을 같이 두면 둘이 어긋났을 때 어느 쪽이 사실인지 알 수 없다.
WORKSPACE_ALLOWED_PREFIXES: tuple[str, ...] = ("events/",)
WORKSPACE_ALLOWED_EXACT: tuple[str, ...] = ("README.md",)

#: `repo_id` → (허용 정확 경로, 허용 접두).
#: 🔒 **저장소를 늘리는 것은 여기 한 줄을 더하는 명시적 결정이다.** 등록되지 않은
#:    저장소로는 한 파일도 올라가지 않는다 — 기본값으로 통과시키지 않는다.
_PATH_POLICY: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    REPO_ID: (ALLOWED_EXACT_PATHS, ALLOWED_PATH_PREFIXES),
    WORKSPACE_REPO_ID: (WORKSPACE_ALLOWED_EXACT, WORKSPACE_ALLOWED_PREFIXES),
}

#: 원천 파일 이름 조각. 경로가 어디에 있든 이름만으로 거부한다 (ADR-SC-0006 ①).
#: `sector/sources/*.RAW_PREFIX` 와 같은 값이지만 **일부러 여기 다시 적는다** —
#: 어댑터가 이름을 바꿔도 이 문은 옛 이름을 계속 막아야 한다.
FORBIDDEN_NAME_FRAGMENTS: tuple[str, ...] = (
    "etf_bydd_trd", "stk_bydd_trd", "ksq_bydd_trd", "bydd_trd",
    "data/raw", "OutBlock",
)


class HubError(RuntimeError):
    """HF 와의 통신·설정 문제. `hint` 에 **운영자가 무엇을 해야 하는지** 적는다."""

    def __init__(self, message: str, *, hint: str = "") -> None:
        super().__init__(message if not hint else f"{message}\n  → {hint}")
        self.hint = hint


class PublishBlocked(HubError):
    """게이트가 막았다. 🔴 **한 파일도 올라가지 않은 상태**로 끝난다."""


# ── 토큰 ────────────────────────────────────────────────────────────────────

def write_token() -> str:
    """배치용 쓰기 토큰. 없으면 무엇을 해야 하는지까지 말하고 던진다."""
    return _token(TOKEN_ENV_WRITE, "쓰기", "로컬 배치에서만 쓴다. 루트 `.env` 에 넣는다")


def read_token() -> str:
    """앱용 읽기 토큰. Streamlit Secrets 에 들어가는 것은 이것 하나뿐이다."""
    return _token(
        TOKEN_ENV_READ, "읽기",
        "`sector-scores` **read 전용** fine-grained 토큰을 발급해 "
        "Streamlit Secrets(또는 루트 `.env`)에 넣는다",
    )


def _token(name: str, role: str, how: str) -> str:
    value = get_secret(name, required=False)
    if not value:
        raise HubError(
            f"HF {role} 토큰 '{name}' 이 없다.",
            hint=(
                f"{how}.\n"
                f"     발급: https://huggingface.co/settings/tokens → Fine-grained\n"
                f"     🔒 읽기 토큰과 쓰기 토큰을 같은 값으로 두지 않는다 — "
                f"앱에 쓰기 권한이 넘어간다"
            ),
        )
    return value


# ── API ─────────────────────────────────────────────────────────────────────

def dataset_api(token: str) -> Any:
    """`HfApi` 하나. import 를 함수 안에 두어 `huggingface_hub` 없이도 이 모듈이
    import 되게 한다 — 테스트와 `--help` 가 무거운 의존성을 끌지 않는다."""
    from huggingface_hub import HfApi

    return HfApi(token=token)


def repo_exists(api: Any, repo_id: str = REPO_ID) -> bool:
    from huggingface_hub.errors import RepositoryNotFoundError

    try:
        api.repo_info(repo_id=repo_id, repo_type=REPO_TYPE)
    except RepositoryNotFoundError:
        return False
    return True


def ensure_private_repo(api: Any, repo_id: str = REPO_ID) -> bool:
    """없으면 **private 으로** 만든다. 이미 있으면 건드리지 않는다.

    🔴 여기서 만들었다는 사실이 private 을 보장하지 않는다. 보장은
       `assert_private` 이 **커밋 직전에** 서버에 물어서 한다.
    """
    if repo_exists(api, repo_id):
        return False
    api.create_repo(repo_id=repo_id, repo_type=REPO_TYPE, private=True, exist_ok=True)
    return True


def assert_private(api: Any, repo_id: str = REPO_ID) -> None:
    """🔴 **올리기 직전에 서버에 다시 묻는다** (ADR-SC-0006 ⑥).

    `create_repo(exist_ok=True)` 가 public 대상에도 성공하기 때문이다. 그리고
    사람이 웹에서 공개로 바꾸는 일은 코드에 통보되지 않는다.
    """
    from huggingface_hub.errors import RepositoryNotFoundError

    try:
        info = api.repo_info(repo_id=repo_id, repo_type=REPO_TYPE)
    except RepositoryNotFoundError as exc:
        raise PublishBlocked(
            f"{repo_id} 를 찾을 수 없다 (또는 토큰에 권한이 없다).",
            hint="토큰이 org `stock-coin-trade` 에 접근 권한을 가졌는지 확인한다",
        ) from exc
    if info.private is not True:
        raise PublishBlocked(
            f"🔴 {repo_id} 가 private 이 아니다. **한 파일도 올리지 않는다.**",
            hint=(
                "HF 웹 → Settings → Change visibility → Private 로 되돌린 뒤 다시 "
                "실행한다. KRX 약관 제11조② 위반은 이용승인 철회(④) 사유다"
            ),
        )


# ── 경로 게이트 ─────────────────────────────────────────────────────────────

def assert_publishable_path(path_in_repo: str, *, repo_id: str = REPO_ID) -> None:
    """🔒 **문 앞의 검사.** `commit()` 이 모든 작업에 대해 부른다.

    ★ 허용목록이 **저장소마다 다르다.** 점수 저장소에 `events/` 를 올리거나 반대로
      워크스페이스에 `score_daily/` 를 올리는 것은 둘 다 사고다 — 경로가 맞아도
      *저장소가 틀리면* 막는다. 원천 이름 검사(아래)만은 저장소를 가리지 않는다.
    """
    path = str(path_in_repo)
    if not path or path != path.strip():
        raise PublishBlocked(f"경로가 비었거나 공백이 붙어 있다: {path!r}")
    if path.startswith("/") or ".." in path.split("/") or "\\" in path:
        raise PublishBlocked(f"경로가 저장소 밖을 가리킨다: {path!r}")
    lowered = path.lower()
    for fragment in FORBIDDEN_NAME_FRAGMENTS:
        if fragment.lower() in lowered:
            raise PublishBlocked(
                f"🔴 원천 파일로 보이는 경로다: {path!r}",
                hint=(
                    "KRX 원천은 로컬 `data/raw/` 를 떠나지 않는다 (약관 제11조② · "
                    "ADR-SC-0006 ①). 올릴 것은 집계·파생값뿐이다"
                ),
            )
    policy = _PATH_POLICY.get(repo_id)
    if policy is None:
        raise PublishBlocked(
            f"경로 정책이 등록되지 않은 저장소다: {repo_id!r}",
            hint=(
                "`sector/datastore/hub.py` 의 `_PATH_POLICY` 에 허용목록을 **명시로** "
                "추가한다. 등록 없이 통과시키면 '어디에 무엇이 나가는가'를 아무도 "
                "결정하지 않은 채 파일이 올라간다"
            ),
        )
    exact, prefixes = policy
    if path in exact:
        return
    if any(path.startswith(prefix) for prefix in prefixes):
        return
    raise PublishBlocked(
        f"{repo_id} 의 허용목록에 없는 경로다: {path!r}",
        hint=(
            f"허용: {', '.join(exact)} · {', '.join(prefixes)}\n"
            "     늘리려면 `sector/datastore/hub.py` 에 **명시로** 추가한다"
        ),
    )


# ── 읽기 ────────────────────────────────────────────────────────────────────

def download_bytes(api: Any, path_in_repo: str, *, repo_id: str = REPO_ID) -> bytes | None:
    """원격 파일 하나를 바이트로. **없으면 `None`** — 예외로 만들지 않는다.

    첫 게시에는 `MANIFEST.json` 이 없는 것이 정상이고, 그것은 오류가 아니다.
    🔒 그 외의 실패(권한·네트워크)는 삼키지 않고 그대로 올라간다.
    """
    from pathlib import Path

    from huggingface_hub.errors import EntryNotFoundError, RepositoryNotFoundError

    try:
        local = api.hf_hub_download(repo_id=repo_id, repo_type=REPO_TYPE, filename=path_in_repo)
    except (EntryNotFoundError, RepositoryNotFoundError):
        return None
    return Path(local).read_bytes()


def read_json(api: Any, path_in_repo: str, *, repo_id: str = REPO_ID) -> Any | None:
    """원격 JSON 하나. 없으면 `None`.

    🔴 내용이 JSON 이 아니면 **던진다.** 깨진 MANIFEST 를 "없음"으로 취급하면
       다음 게시가 모든 샤드를 새로 올리며 그 사실을 아무도 모른다.
    """
    raw = download_bytes(api, path_in_repo, repo_id=repo_id)
    if raw is None:
        return None
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HubError(
            f"{repo_id}:{path_in_repo} 를 JSON 으로 읽을 수 없다 ({exc}).",
            hint="HF 웹에서 파일을 확인한다. 깨졌다면 지우고 다시 게시한다",
        ) from exc


# ── 쓰기 ────────────────────────────────────────────────────────────────────

def commit(
    api: Any,
    operations: list[Any],
    *,
    message: str,
    repo_id: str = REPO_ID,
    description: str = "",
) -> str:
    """🔴 **단일 커밋.** 모든 경로를 검사하고, private 을 다시 확인한 뒤 한 번에 올린다.

    순서가 의도다 —
    ① 경로 검사(네트워크 없이 실패할 수 있는 것을 먼저)
    ② `assert_private`(**커밋 직전**에 서버에 묻는다)
    ③ `create_commit`(원자적)
    """
    if not operations:
        raise ValueError("올릴 작업이 없다. 호출부가 0건을 걸러야 한다")
    for operation in operations:
        assert_publishable_path(getattr(operation, "path_in_repo", ""), repo_id=repo_id)

    assert_private(api, repo_id)

    info = api.create_commit(
        repo_id=repo_id,
        repo_type=REPO_TYPE,
        operations=operations,
        commit_message=message,
        commit_description=description,
    )
    return str(getattr(info, "oid", "") or getattr(info, "commit_url", "") or "")
