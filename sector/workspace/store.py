"""이벤트 원장을 **어디에 둘 것인가** — 로컬 · HF · Supabase 세 구현.

## 왜 셋인가

- `LocalStore` — 개발·테스트. 네트워크도 토큰도 필요 없다. 테스트가 원격을 부르면
  그것은 테스트가 아니라 통합 점검이고, 오프라인에서 깨진다.
- `HubStore` — HF private dataset. **지금 팀이 쓰는 경로다.**
- `SupabaseStore` — 팀 원장의 다음 집(ADR-SC-0010 ④ · ADR-SC-0011). 동시 쓰기·
  append-only 강제·passcode 게이트가 **DB 안에서** 돌아간다.

셋이 **같은 계약**을 지키므로 화면 코드는 어느 쪽인지 모른다.

## 🔴 계약이 둘 늘었다 — passcode 가 원장 밖으로 나갔기 때문이다

2026-09-12 까지 `team.created` 의 payload 에 passcode 해시가 실려 있었고, 화면이
`fold` 결과에서 해시를 꺼내 **앱에서** 검증했다. Supabase 는 원장 읽기가 공개라
(ADR-SC-0011 ⑥) 그대로 옮기면 해시가 공개된다. 그래서 —

| 계약 | 무엇 |
|---|---|
| `create_team(event, passcode_hash=)` | 조 생성. 🔒 이벤트와 해시가 **갈라지지 않는다** |
| `passcode_params(team_id)` | `scrypt$n$r$p$salt$` — 🔴 **digest 는 주지 않는다** |
| `verify(team_id, credential)` | 그 조에 쓸 수 있는가. 🔒 해시를 밖으로 내지 않고 답한다 |
| `append(events, credential=)` | 더한다. 자격증명을 확인할 수 있으면 확인한다 |
| `read_all()` | `Ledger` — 시간순 이벤트 + **읽지 못한 줄** (아래) |

🔒 **검증을 화면이 아니라 저장소가 한다.** 화면이 해시를 손에 들면, 원장 읽기가
   공개인 순간 그것이 곧 노출 경로가 된다. 저장소는 `True`/`False` 만 돌려준다.

🔴 **`append` 로는 조를 만들 수 없다**(`_reject_team_created`). Supabase 가 구조적으로
   그렇다 — `workspace_append` 는 그 조의 passcode 해시가 **이미 있어야** 통과하므로
   새 조의 `team.created` 는 어떤 passcode 로도 들어가지 않는다. 세 구현이 같은 답을
   내도록 로컬·HF 도 같이 막는다. 안 막으면 **passcode 없는 조**가 생기고, 그 조는
   아무도 참가할 수 없으면서 목록에는 보인다 — 조용히 망가진 상태다.

## 🔴 한 줄 때문에 원장 전체가 멈추지 않는다 (2026-09-14 · ADR-SC-0011 ⑬)

`read_all` 은 **내용이 틀린 줄**(JSON 이 아니다 · id 가 내용과 어긋난다 · 칸이 틀렸다)을
건너뛰고 `Ledger.rejected` 에 담는다. RPC 는 id 를 다시 계산하지 않으므로 passcode 를 가진
사람은 그런 줄을 쓸 수 있고, 원장은 append-only 라 지울 수 없다 — 던지면 **모든 조**가 멈춘다.

🔒 **닿지 못한 것은 여전히 던진다**(파일 읽기 · 네트워크 · 권한 · 목록 · 상한). 일부만 읽은
   원장을 온전한 것처럼 보여주는 것은 한 줄을 건너뛰는 것과 다르다 — 무엇이 빠졌는지 모른다.

## 🔒 멱등 — 같은 이벤트를 두 번 써도 하나다

이벤트 id 가 내용에서 나오므로(→ `events`) 같은 이벤트는 같은 자리에 쓰인다.
그래서 409 재시도가 안전하고, "확정" 버튼을 두 번 눌러도 원장이 더러워지지 않는다.
🔒 세 구현 모두 **실제로 쓴 건수**를 돌려준다 — 0건 쓰고 N건이라 답하면 화면이
   "기록됐다" 고 거짓말한다(2026-09-12 실서버에서 실제로 그랬다).

## 🔴 왜 스냅샷을 같이 두지 않는가

"현재 상태" 파일을 이벤트 옆에 두면 빨라지지만, 둘이 어긋나는 순간 **어느 쪽이
사실인지 알 수 없다.** 상태는 언제나 이벤트를 접어서 만든다(→ `fold`). 느려지면
캐시는 **앱 계층**에서 건다 — 캐시는 틀려도 지우면 되지만 저장된 스냅샷은
틀린 채로 남는다.
"""

from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any, Iterable, Protocol, Sequence

from sector.datastore import hub
from sector.secret_access import get_secret
from sector.sources.krx_common import assert_ignored_output, repo_root
from sector.workspace import auth
from sector.workspace.events import (
    Event,
    EventError,
    Ledger,
    RejectedRow,
    parse_event,
    require_team_id,
    sort_key,
)

__all__ = [
    "StoreError",
    "PasscodeRejected",
    "REJECTED_MESSAGE",
    "EventStore",
    "LocalStore",
    "HubStore",
    "SupabaseStore",
    "SUPABASE_URL_SECRET",
    "SUPABASE_ANON_KEY_SECRET",
    "default_local_root",
    "secret_path_in_repo",
]


class StoreError(RuntimeError):
    """원장을 읽거나 쓸 수 없다."""


class PasscodeRejected(StoreError):
    """그 조에 쓸 자격이 없다.

    🔒 **"조가 없다" 와 "passcode 가 틀렸다" 를 구별하지 않는다.** 구별해 주면
       그것이 곧 정보다 — `auth.verify_passcode` 와 같은 규율이다.
    """


#: 🔴 **이 문장은 마이그레이션 `20260912095328_workspace_ledger.sql` 의 것과 같아야
#:    한다.** `workspace_append` 가 passcode 를 거부할 때 PostgREST 가 이 문장을
#:    그대로 실어 보내고, `SupabaseStore._decode` 가 그것을 보고 `PasscodeRejected`
#:    로 올린다. 갈라지면 **거부를 통신 오류로 오인**해 화면이 엉뚱한 말을 한다.
#:    `workspace_test.test_거부_문장이_마이그레이션과_같다` 가 둘을 묶어 둔다.
REJECTED_MESSAGE = "쓰지 못했다. 조와 passcode 를 확인한다"


class EventStore(Protocol):
    """원장의 계약. 🔒 **읽기와 더하기뿐이다** — 고치기·지우기가 없다."""

    def create_team(self, event: Event, *, passcode_hash: str) -> int: ...
    def passcode_params(self, team_id: str) -> str | None: ...
    def verify(self, team_id: str, credential: str) -> bool: ...
    def append(self, events: Sequence[Event], *, credential: str | None = None) -> int: ...
    def read_all(self) -> Ledger: ...


def _sorted(events: Iterable[Event]) -> list[Event]:
    """시간순. 같은 시각은 `events.sort_key` 가 정한 인과 순서로 가른다.

    🔒 tie-break 를 명시하는 이유는 `scoring._ranks` 와 같다 — 정렬이 흔들리면
       같은 원장에서 다른 상태가 나오고, 그것은 재현 불가다.
    """
    return sorted(events, key=sort_key)


def _json_of(raw: bytes) -> Any:
    """저장된 바이트 → JSON. 🔒 풀리지 않는 것은 **내용이 틀린 줄**이다 — `EventError` 로 올린다.

    `RecursionError` 도 여기다. DB 는 payload 를 16KB 로 묶어 중첩이 8000단을 못 넘고 그만큼은
    풀리지만(2026-09-14 실측), 파일에는 상한이 없어 2만 단이면 `json.loads` 가 멈춘다.
    """
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as exc:
        raise EventError(f"JSON 으로 읽을 수 없다: {exc}") from exc


def _ledger(rows: Iterable[tuple[str, Any]]) -> Ledger:
    """(위치, 원문) 들 → `Ledger`. 원문은 파일 바이트이거나 이미 풀린 JSON 이다.

    🔒 **내용이 틀린 줄만** 건너뛴다(`EventError`). `rows` 를 돌다 나는 오류(파일을 못 읽는다 ·
       내려받지 못한다)는 `try` 밖의 `for` 가 받으므로 그대로 올라간다 (머리주석).
    """
    events: list[Event] = []
    rejected: list[RejectedRow] = []
    for where, raw in rows:
        data: Any = None
        try:
            data = _json_of(raw) if isinstance(raw, bytes) else raw
            events.append(parse_event(data))
        except EventError as exc:
            rejected.append(RejectedRow.of(where, data, exc))
    return Ledger(events=tuple(_sorted(events)), rejected=tuple(rejected))


def _reject_team_created(events: Sequence[Event]) -> None:
    """🔴 조 생성은 `create_team` 만 한다 (머리주석)."""
    bad = sorted({e.team_id for e in events if e.kind == "team.created"})
    if bad:
        raise StoreError(
            f"`append` 로는 조를 만들 수 없다: {', '.join(bad)}. "
            f"`create_team` 을 쓴다 — 이벤트와 passcode 해시가 함께 들어가야 한다"
        )


def _team_of(events: Sequence[Event]) -> str:
    """한 번의 `append` 는 **한 조**다.

    🔒 자격증명은 그 조의 것이다. 섞이면 남의 조 이벤트가 내 passcode 로 들어간다 —
       `workspace_append` 가 DB 에서 막는 것과 같은 검사를 여기서도 한다.
    """
    teams = sorted({e.team_id for e in events})
    if len(teams) != 1:
        raise StoreError(f"한 번에 한 조만 쓴다. 섞였다: {', '.join(teams)}")
    return teams[0]


def _require_created(event: Event) -> None:
    if event.kind != "team.created":
        raise StoreError(f"조 생성은 `team.created` 만 받는다 (받은 것: {event.kind})")


def _require_hash(passcode_hash: str) -> str:
    """🔒 평문이 이 계층을 지나갈 수 없다 — 형식을 본다."""
    if not isinstance(passcode_hash, str) or not passcode_hash.startswith("scrypt$"):
        raise StoreError("passcode 는 해시로만 받는다 — `auth.hash_passcode` 를 쓴다")
    return passcode_hash


def _event_bytes(event: Event) -> bytes:
    return (json.dumps(event.to_json(), ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def _secret_bytes(team_id: str, passcode_hash: str) -> bytes:
    return (
        json.dumps({"team_id": team_id, "passcode_hash": passcode_hash},
                   ensure_ascii=False, indent=2) + "\n"
    ).encode("utf-8")


def _hash_from_bytes(raw: bytes, *, where: str) -> str:
    try:
        value = json.loads(raw.decode("utf-8")).get("passcode_hash")
    except (UnicodeDecodeError, json.JSONDecodeError, AttributeError) as exc:
        raise StoreError(f"{where} 를 읽을 수 없다: {exc}") from exc
    return _require_hash(str(value or ""))


def secret_path_in_repo(team_id: str) -> str:
    """passcode 해시가 사는 자리. `hub.WORKSPACE_ALLOWED_PREFIXES` 와 맞물린다.

    🔒 원장(`events/`)과 **갈라 둔다** — Supabase 의 `workspace_team_secret` 과
       구조를 맞추기 위해서다. 나중에 원장만 옮겨도 모양이 달라지지 않는다.
    🔒 id 를 여기서 확인한다 — 이 문자열이 곧 파일 경로다(`require_team_id`).
    """
    return f"secrets/{require_team_id(team_id)}.json"


# ── 로컬 ────────────────────────────────────────────────────────────────────

def default_local_root() -> Path:
    """`data/workspace/`. `.gitignore` 의 `/data/` 가 이미 막는다."""
    return repo_root() / "data" / "workspace"


class LocalStore:
    """파일 하나 = 이벤트 하나. 개발·테스트용.

    🔴 **여기서 passcode 검증은 예의다.** 파일을 쓸 수 있는 사람은 해시 파일도
       고칠 수 있으므로 실제 경계는 파일시스템이다. 그래도 검증하는 이유는
       **화면이 세 저장소에서 같게 굴어야** 하기 때문이다 — 로컬에서만 통과하는
       코드는 배포에서 처음 깨진다.
    """

    def __init__(self, root: Path | str | None = None) -> None:
        self.root = Path(root) if root is not None else default_local_root()
        self.events_dir = self.root / "events"
        self.secrets_dir = self.root / "secrets"

    # 쓰기 ─────────────────────────────────────────────────────────────────

    def create_team(self, event: Event, *, passcode_hash: str) -> int:
        """🔒 해시를 **먼저** 쓰고 이벤트를 쓴다. 이벤트가 실패하면 해시를 지운다.

        로컬에는 트랜잭션이 없으니 순서로 민다 — 원장에 조가 남고 해시가 없으면
        **아무도 참가할 수 없는 조**가 목록에 뜨고, 같은 id 로 다시 만들 수도 없다.
        해시만 남는 실패는 원장을 건드리지 않으므로 되돌리기가 싸다.
        """
        _require_created(event)
        _require_hash(passcode_hash)
        secret = self._secret_file(event.team_id)
        if secret.exists():
            raise StoreError(f"조 '{event.team_id}' 는 이미 있다. 다른 id 로 만든다")

        self.secrets_dir.mkdir(parents=True, exist_ok=True)
        # 🔒 만든 **뒤에** 묻는다 — git 은 없는 디렉터리의 무시 여부를 답하지 못한다.
        assert_ignored_output(self.secrets_dir / "probe.json", what="passcode 해시")
        secret.write_bytes(_secret_bytes(event.team_id, passcode_hash))
        try:
            written = self._write_events([event])
        except Exception:
            secret.unlink(missing_ok=True)      # 🔒 해시만 남기지 않는다
            raise
        return written

    def append(self, events: Sequence[Event], *, credential: str | None = None) -> int:
        if not events:
            return 0
        _reject_team_created(events)
        if credential is not None and not self.verify(_team_of(events), credential):
            raise PasscodeRejected(REJECTED_MESSAGE)
        return self._write_events(events)

    def _write_events(self, events: Sequence[Event]) -> int:
        self.events_dir.mkdir(parents=True, exist_ok=True)
        # 🔒 만든 **뒤에** 묻는다 — git 은 없는 디렉터리의 무시 여부를 답하지 못한다.
        assert_ignored_output(self.events_dir / "probe.json", what="조 이름·토론 글")
        written = 0
        for event in events:
            path = self.root / event.path_in_repo
            body = _event_bytes(event)
            if path.exists() and path.read_bytes() == body:
                continue            # 멱등 — 같은 내용이면 건드리지 않는다
            path.write_bytes(body)
            written += 1
        return written

    # passcode ────────────────────────────────────────────────────────────

    def _secret_file(self, team_id: str) -> Path:
        return self.root / secret_path_in_repo(team_id)

    def _stored_hash(self, team_id: str) -> str | None:
        path = self._secret_file(team_id)
        if not path.is_file():
            return None
        return _hash_from_bytes(path.read_bytes(), where=str(path))

    def passcode_params(self, team_id: str) -> str | None:
        stored = self._stored_hash(team_id)
        return None if stored is None else auth.params_of(stored)

    def verify(self, team_id: str, credential: str) -> bool:
        stored = self._stored_hash(team_id)
        return stored is not None and auth.credential_matches(credential, stored)

    # 읽기 ─────────────────────────────────────────────────────────────────

    def read_all(self) -> Ledger:
        if not self.events_dir.is_dir():
            return Ledger()
        return _ledger(self._rows())

    def _rows(self) -> Iterable[tuple[str, bytes]]:
        for path in sorted(self.events_dir.glob("*.json")):
            try:
                raw = path.read_bytes()
            except OSError as exc:
                # 🔴 못 읽은 것은 내용이 틀린 것이 아니다 — 무엇이 빠졌는지 모르므로 던진다
                raise StoreError(f"{path} 를 읽을 수 없다: {exc}") from exc
            yield f"events/{path.name}", raw


# ── Hugging Face ────────────────────────────────────────────────────────────

class HubStore:
    """HF private dataset. 🔒 저장소는 `hub.WORKSPACE_REPO_ID` 하나다.

    ★ `api` 를 주입받는다 — 테스트가 가짜 API 로 계약을 확인할 수 있고, 토큰을
      읽는 길이 `secret_access` 하나로 유지된다(V19).

    🔴 `LocalStore` 와 같은 한계를 갖는다 — 토큰이 있으면 `secrets/` 도 고칠 수
       있으므로 실제 경계는 토큰이다. 검증은 세 저장소가 같게 굴기 위한 것이다.
    """

    def __init__(self, api: Any, *, repo_id: str = hub.WORKSPACE_REPO_ID) -> None:
        self.api = api
        self.repo_id = repo_id

    # 쓰기 ─────────────────────────────────────────────────────────────────

    def create_team(self, event: Event, *, passcode_hash: str) -> int:
        """🔴 **단일 커밋.** 이벤트와 해시가 함께 들어가거나 둘 다 안 들어간다.

        `workspace_create_team` RPC 가 한 트랜잭션으로 하는 것과 같은 뜻이다 —
        둘이 갈라지면 "passcode 없는 조" 나 "조 없는 passcode" 가 남는다.
        """
        _require_created(event)
        _require_hash(passcode_hash)
        from huggingface_hub import CommitOperationAdd

        secret = secret_path_in_repo(event.team_id)
        if secret in set(self._list_files()):
            raise StoreError(f"조 '{event.team_id}' 는 이미 있다. 다른 id 로 만든다")

        ops = [
            CommitOperationAdd(path_in_repo=secret,
                               path_or_fileobj=_secret_bytes(event.team_id, passcode_hash)),
            CommitOperationAdd(path_in_repo=event.path_in_repo,
                               path_or_fileobj=_event_bytes(event)),
        ]
        hub.commit(self.api, ops, message=f"workspace: team.created ({event.team_id})",
                   repo_id=self.repo_id)
        return 1

    def append(self, events: Sequence[Event], *, credential: str | None = None) -> int:
        """🔴 **단일 커밋.** 여러 건이 한 번에 들어가거나 한 건도 안 들어간다.

        `hub.commit` 이 경로 검사 → private 확인 → 커밋 순서를 지킨다.

        ★ **이미 있는 이벤트는 빼고 올린다.** 이벤트 id 가 내용에서 나오므로
          같은 이벤트는 같은 경로다 — 다시 올려도 바이트가 같다. 그런데 그대로
          커밋하면 `huggingface_hub` 가 *"No files have been modified"* 로 건너뛰면서
          우리는 "N건 올렸다" 고 답하게 된다. **0건 쓰고 N건이라 답하는 것**은
          `LocalStore` 와 계약이 어긋나고, 화면이 "기록됐다" 고 거짓말하게 만든다.
          (2026-09-12 실서버에서 실제로 그렇게 나왔다.)
        """
        if not events:
            return 0
        _reject_team_created(events)
        if credential is not None and not self.verify(_team_of(events), credential):
            raise PasscodeRejected(REJECTED_MESSAGE)
        from huggingface_hub import CommitOperationAdd

        existing = set(self._list_files())
        fresh = [e for e in events if e.path_in_repo not in existing]
        if not fresh:
            return 0          # 🔒 빈 커밋을 만들지 않는다

        ops = [
            CommitOperationAdd(path_in_repo=event.path_in_repo,
                               path_or_fileobj=_event_bytes(event))
            for event in fresh
        ]
        kinds = ", ".join(sorted({e.kind for e in fresh}))
        hub.commit(self.api, ops, message=f"workspace: {kinds} ({len(fresh)}건)",
                   repo_id=self.repo_id)
        return len(fresh)

    # passcode ────────────────────────────────────────────────────────────

    def _stored_hash(self, team_id: str) -> str | None:
        path = secret_path_in_repo(team_id)
        raw = hub.download_bytes(self.api, path, repo_id=self.repo_id)
        if raw is None:
            return None
        return _hash_from_bytes(raw, where=f"{self.repo_id}:{path}")

    def passcode_params(self, team_id: str) -> str | None:
        stored = self._stored_hash(team_id)
        return None if stored is None else auth.params_of(stored)

    def verify(self, team_id: str, credential: str) -> bool:
        stored = self._stored_hash(team_id)
        return stored is not None and auth.credential_matches(credential, stored)

    # 읽기 ─────────────────────────────────────────────────────────────────

    def _list_files(self) -> list[str]:
        try:
            return list(self.api.list_repo_files(repo_id=self.repo_id, repo_type=hub.REPO_TYPE))
        except Exception as exc:                 # noqa: BLE001 — 되던진다
            raise StoreError(
                f"{self.repo_id} 의 파일 목록을 받지 못했다: {exc}\n"
                f"  → 저장소가 있는지, 토큰에 읽기 권한이 있는지 확인한다"
            ) from exc

    def read_all(self) -> Ledger:
        return _ledger(self._rows(self._list_files()))

    def _rows(self, names: Sequence[str]) -> Iterable[tuple[str, bytes]]:
        for name in sorted(n for n in names if n.startswith("events/") and n.endswith(".json")):
            raw = hub.download_bytes(self.api, name, repo_id=self.repo_id)
            if raw is not None:                      # 목록과 실제가 어긋나면 건너뛴다
                yield name, raw


# ── Supabase ────────────────────────────────────────────────────────────────
#
# ## 🔒 `requests` + PostgREST — 의존 증가 0
#
# `supabase-py` 도 `psycopg` 도 넣지 않는다. 루트 `requirements.txt` 는 Streamlit
# Cloud 가 설치하는 목록이고 메모리 한도를 직접 깎는다(V20). **PostgREST 는 그냥
# REST API 다** — 이미 있는 `requests` 로 부른다. `python-dotenv` 를 넣지 않고
# `.env` 를 직접 파싱한 것과 같은 판단이다(`secret_access` 머리주석).
#
# ## 🔴 쓰기는 RPC 하나뿐이다
#
# 앱은 anon 키로 붙고 저장소는 Public 이므로 **anon 키는 공개된다고 가정한다**.
# 테이블 INSERT 가 열려 있으면 키를 가진 누구나 남의 조 이름으로 쓸 수 있다 —
# passcode 가 지키기로 한 바로 그것이 뚫린다. 그래서 쓰기 권한이 **키가 아니라
# passcode 에** 걸리도록 `SECURITY DEFINER` 함수 안에서 검증한다 (ADR-SC-0011 ⑤).

SUPABASE_URL_SECRET = "SUPABASE_URL"
SUPABASE_ANON_KEY_SECRET = "SUPABASE_ANON_KEY"

#: PostgREST 한 페이지. 🔒 **조용히 자르지 않는다** — `read_all` 이 페이지를 끝까지
#:    돌고, 상한에 닿으면 던진다. 잘린 원장은 "가끔 조가 사라진다" 로 나타난다.
_PAGE = 1000
_MAX_EVENTS = 50_000

#: 🔴 앱에 들어오면 안 되는 키. `service_role`(JWT) 과 `sb_secret_`(새 형식)은
#:    RLS 를 통째로 우회하고 53개 테이블 전부에 닿는다 — HF 토큰을 `WRITE`/`READ`
#:    로 가른 것과 같은 사고를 여기서 미리 막는다(V29).
_SECRET_KEY_PREFIX = "sb_secret_"
_ALLOWED_ROLES = ("anon",)


def _row_where(row: Any) -> str:
    """원장 행의 위치 — 읽지 못한 줄을 알릴 때 쓴다."""
    event_id = row.get("event_id") if isinstance(row, dict) else None
    return f"event_id {event_id!r}"


def _jwt_role(key: str) -> str | None:
    """JWT 라면 `role` 클레임. 아니거나 읽을 수 없으면 `None`.

    🔒 **판별할 수 없으면 통과시킨다.** 우리가 막으려는 것은 *아는 사고*
       (service_role 키를 앱 칸에 붙여넣기)이고, 모르는 새 형식을 막아 앱이 아예
       못 뜨게 만드는 것은 그보다 나쁘다. 서명은 검증하지 않는다 — 서버가 한다.
    """
    parts = key.split(".")
    if len(parts) != 3:
        return None
    try:
        raw = base64.urlsafe_b64decode(parts[1] + "=" * (-len(parts[1]) % 4))
        role = json.loads(raw.decode("utf-8")).get("role")
    except Exception:      # noqa: BLE001 — 못 읽으면 모르는 것으로 둔다 (머리주석)
        return None
    return str(role) if role else None


def _require_url(url: str | None) -> str:
    value = url if url is not None else get_secret(SUPABASE_URL_SECRET, required=False)
    if not value:
        raise StoreError(
            f"Supabase URL '{SUPABASE_URL_SECRET}' 이 없다.\n"
            f"  → 루트 `.env`(또는 Streamlit Secrets)에 "
            f"`{SUPABASE_URL_SECRET}=https://<project-ref>.supabase.co` 를 넣는다"
        )
    value = value.strip().rstrip("/")
    # 🔒 http 면 키가 평문으로 나간다. 되돌릴 수 없는 종류의 실수다.
    if not value.startswith("https://"):
        raise StoreError(f"Supabase URL 은 https 여야 한다: {value!r}")
    return value


def _require_anon_key(key: str | None) -> str:
    value = key if key is not None else get_secret(SUPABASE_ANON_KEY_SECRET, required=False)
    if not value:
        raise StoreError(
            f"Supabase anon 키 '{SUPABASE_ANON_KEY_SECRET}' 이 없다.\n"
            f"  → Supabase → Settings → API 의 **anon / publishable** 키를 넣는다.\n"
            f"     🔴 `service_role`·`secret` 키를 넣지 않는다 — RLS 를 통째로 우회한다"
        )
    value = value.strip()
    if value.startswith(_SECRET_KEY_PREFIX):
        raise StoreError(
            f"🔴 `{_SECRET_KEY_PREFIX}…` 는 **secret 키**다. 앱에 두지 않는다 — "
            f"RLS 를 우회하고 v2.0 유산 53개 테이블 전부에 닿는다.\n"
            f"  → anon / publishable 키로 바꾼다 (ADR-SC-0011 ⑤)"
        )
    role = _jwt_role(value)
    if role is not None and role not in _ALLOWED_ROLES:
        raise StoreError(
            f"🔴 `role={role}` 키다. 앱에는 `anon` 만 둔다 — 그 밖의 역할은 RLS 를 "
            f"우회한다.\n  → Supabase → Settings → API 의 anon 키로 바꾼다"
        )
    return value


class SupabaseStore:
    """PostgREST 로 부르는 팀 원장 (ADR-SC-0011).

    ★ `transport` 를 주입받는다 — `requests` 와 같은 모양(`get`·`post`)이면 된다.
      테스트가 네트워크를 부르지 않고 계약을 확인한다(`HubStore` 가 `api` 를 받는
      것과 같은 이유).
    """

    def __init__(
        self,
        *,
        url: str | None = None,
        key: str | None = None,
        transport: Any = None,
        timeout: float = 10.0,
    ) -> None:
        self.url = _require_url(url)
        self._key = _require_anon_key(key)
        self._transport = transport
        self.timeout = timeout

    # HTTP ────────────────────────────────────────────────────────────────

    def _http(self) -> Any:
        if self._transport is None:
            import requests

            self._transport = requests.Session()
        return self._transport

    def _headers(self) -> dict[str, str]:
        return {
            "apikey": self._key,
            "Authorization": f"Bearer {self._key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def _decode(self, response: Any, *, what: str) -> Any:
        """응답 하나. 🔴 passcode 거부는 `PasscodeRejected` 로 **올려 구별한다.**

        나머지 실패는 삼키지 않고 그대로 올린다 — 네트워크·권한 문제를 "passcode 가
        틀렸다" 로 바꿔 보여주면 아무도 원인을 못 찾는다.
        """
        status = int(getattr(response, "status_code", 0) or 0)
        text = getattr(response, "text", "") or ""
        body: Any = None
        if text:
            try:
                body = response.json()
            except Exception:      # noqa: BLE001 — JSON 이 아니면 원문을 쓴다
                body = None
        if 200 <= status < 300:
            return body
        message = body.get("message") if isinstance(body, dict) else None
        if message == REJECTED_MESSAGE:
            raise PasscodeRejected(REJECTED_MESSAGE)
        detail = message or text[:300] or "(본문 없음)"
        raise StoreError(f"{what} 가 실패했다 (HTTP {status}): {detail}")

    def _rpc(self, name: str, payload: dict[str, Any]) -> Any:
        response = self._http().post(
            f"{self.url}/rest/v1/rpc/{name}",
            json=payload, headers=self._headers(), timeout=self.timeout,
        )
        return self._decode(response, what=f"RPC `{name}`")

    def _select_events(self, *, offset: int = 0, **filters: str) -> list[dict[str, Any]]:
        params = {
            "select": "event_id,kind,team_id,actor,at,payload",
            # 🔒 정렬 키에 `event_id`(PK)를 더한다 — `at` 만으로는 유일하지 않아
            #    페이지 경계에서 행이 겹치거나 빠진다.
            "order": "at.asc,event_id.asc",
            "limit": str(_PAGE),
            "offset": str(offset),
            **filters,
        }
        response = self._http().get(
            f"{self.url}/rest/v1/workspace_event",
            params=params, headers=self._headers(), timeout=self.timeout,
        )
        rows = self._decode(response, what="원장 조회")
        if not isinstance(rows, list):
            raise StoreError(f"원장 조회가 목록이 아니다: {type(rows).__name__}")
        return rows

    # 쓰기 ─────────────────────────────────────────────────────────────────

    def create_team(self, event: Event, *, passcode_hash: str) -> int:
        """🔒 이벤트와 해시가 **한 트랜잭션**에 들어간다 (`workspace_create_team`)."""
        _require_created(event)
        _require_hash(passcode_hash)
        written = self._rpc("workspace_create_team",
                            {"p_event": event.to_json(), "p_passcode_hash": passcode_hash})
        return int(written or 0)

    def append(self, events: Sequence[Event], *, credential: str | None = None) -> int:
        """🔴 자격증명이 **없으면 쓰지 않는다.** 로컬·HF 와 다른 유일한 지점이다.

        `workspace_append` 는 passcode 없이는 통과하지 않는다. 그러니 여기서 미리
        멈추고 *무엇을 해야 하는지* 말한다 — 서버에 보내 봐야 같은 답이 온다.

        🔒 **마스터(개발자) 경로는 DB 에 없고, 앞으로도 더하지 않는다**
           (2026-09-12 · ADR-SC-0011 ⑫ 로 닫혔다). 남의 조를 보관·복구하려면 그 조의
           passcode 가 필요하고, 마스터는 그것을 갖고 있지 않다 — 그래서 **화면에서
           마스터를 걷어냈다.** 복구는 그 조의 passcode 로 한다(`teams` 머리주석).
           🔴 여기에 우회 경로를 뚫지 마라. 해시 하나가 **모든 조**에 대한 쓰기
           권한이 되는 순간 ⑤ 가 무너진다.
        """
        if not events:
            return 0
        _reject_team_created(events)
        team_id = _team_of(events)
        if not credential:
            raise StoreError(
                f"passcode 없이 원장에 쓸 수 없다 (조 '{team_id}').\n"
                f"  → 화면에서 그 조에 참가한 뒤 쓴다. 쓰기 권한은 키가 아니라 "
                f"passcode 에 걸려 있다 (ADR-SC-0011 ⑤)"
            )
        written = self._rpc("workspace_append", {
            "p_team_id": team_id,
            "p_encoded": credential,
            "p_events": [event.to_json() for event in events],
        })
        return int(written or 0)

    def heartbeat(self, bas_dd: str, note: str | None = None) -> None:
        """게시 하트비트 — **7일 pause 를 이것으로 푼다** (ADR-SC-0010 ④).

        🔒 GitHub Actions keep-alive 를 쓰지 않는다(절대 제약 6). 살아 있게 하는
           일이 **이미 매일 하는 일**(`batch.publish`) 안에 있어야 잊히지 않는다.
        🔴 원장이 아니라 운영 로그다 — 같은 날 두 번 게시하면 덮어쓴다.
        """
        self._rpc("workspace_heartbeat", {"p_bas_dd": bas_dd, "p_note": note})

    # passcode ────────────────────────────────────────────────────────────

    def passcode_params(self, team_id: str) -> str | None:
        """`scrypt$n$r$p$salt$`. 없는 조는 `None`.

        🔴 digest 가 실려 오면 **던진다.** RPC 가 바뀌어 해시 전체를 주기 시작했다는
           뜻이고, 그것은 ④(해시는 어떤 경로로도 나가지 않는다)가 깨진 상태다.
           조용히 받아 쓰면 아무도 모른다.
        """
        value = self._rpc("workspace_passcode_params", {"p_team_id": team_id})
        if not value:
            return None
        params = str(value)
        if params.split("$")[-1] != "":
            raise StoreError(
                "`workspace_passcode_params` 가 digest 를 돌려줬다. 🔴 앱은 digest 를 "
                "받지 않는다 (ADR-SC-0011 ④) — 마이그레이션을 확인한다"
            )
        return params

    def verify(self, team_id: str, credential: str) -> bool:
        """그 조에 쓸 수 있는가. 🔒 **이미 있는 이벤트를 다시 보내서** 확인한다.

        🔴 읽기 전용 검증 RPC 가 없다. `workspace_append` 는 빈 배열을 passcode 검사
           **앞에서** 0건으로 돌려보내므로 검증에 쓸 수 없다. 그래서 그 조의
           `team.created` 를 그대로 다시 보낸다 —

        1. passcode 는 검사된다 (삽입 전에 검사한다)
        2. `on conflict (event_id) do nothing` 이라 **0건 쓰인다**

        즉 원장의 멱등성(이벤트 id 가 내용에서 나온다)을 **우회가 아니라 그대로**
        쓴다. 되돌아오는 값이 0 인 것이 정상이고, 그래서 반환값을 보지 않는다.
        """
        # 🔒 id 가 조회 필터에 그대로 들어간다 — 문 앞에서 확인한다
        rows = self._select_events(
            team_id=f"eq.{require_team_id(team_id)}", kind="eq.team.created")
        # 🔒 되읽은 행을 `parse_event` 로 통과시킨다 — DB 가 읽는 칸만 정확히 되보낸다.
        # 🔴 **읽지 못한 행은 건너뛴다**(ADR-SC-0011 ⑬). 첫 행만 보던 예전 코드는 조원이
        #    `at` 이 더 이른 가짜 `team.created` 를 한 줄 넣으면 그 조의 참가를 전부 막았다.
        #    probe 는 `on conflict` 에 걸리기만 하면 되므로 **읽히는 아무 행**이나 된다.
        #    읽히는 행이 없으면 `fold` 에도 그 조가 없다 — 참가할 수 없다는 답이 맞다.
        readable = _ledger((_row_where(row), row) for row in rows).events
        if not readable:
            return False
        probe = readable[0]
        try:
            self._rpc("workspace_append", {
                "p_team_id": team_id,
                "p_encoded": credential,
                "p_events": [probe.to_json()],
            })
        except PasscodeRejected:
            return False
        return True

    # 읽기 ─────────────────────────────────────────────────────────────────

    def read_all(self) -> Ledger:
        rows: list[dict[str, Any]] = []
        offset = 0
        while True:
            page = self._select_events(offset=offset)
            rows.extend(page)
            if len(page) < _PAGE:
                break
            offset += len(page)
            if offset >= _MAX_EVENTS:
                # 🔴 조용히 자르지 않는다. 잘린 원장은 "가끔 조가 사라진다" 다.
                raise StoreError(
                    f"원장이 {_MAX_EVENTS}건을 넘는다. 조용히 자르지 않는다 — "
                    f"읽는 방식을 먼저 고친다(조별 조회·보관 정리)"
                )
        # 🔒 줄마다 읽는다 — 한 줄이 틀렸다고 원장 전체를 버리지 않는다 (머리주석)
        return _ledger((_row_where(row), row) for row in rows)
