"""이벤트 원장을 **어디에 둘 것인가** — 로컬과 HF 두 구현.

## 왜 둘인가

- `LocalStore` — 개발·테스트. 네트워크도 토큰도 필요 없다. 테스트가 HF 를 부르면
  그것은 테스트가 아니라 통합 점검이고, 오프라인에서 깨진다.
- `HubStore` — 팀 공유. 앱이 쓰는 유일한 경로다.

둘이 **같은 계약**을 지키므로 화면 코드는 어느 쪽인지 모른다.

## 🔒 멱등 — 같은 이벤트를 두 번 써도 하나다

이벤트 id 가 내용에서 나오므로(→ `events`) 같은 이벤트는 같은 경로에 쓰인다.
그래서 409 재시도가 안전하고, "확정" 버튼을 두 번 눌러도 원장이 더러워지지 않는다.

## 🔴 왜 스냅샷을 같이 두지 않는가

"현재 상태" 파일을 이벤트 옆에 두면 빨라지지만, 둘이 어긋나는 순간 **어느 쪽이
사실인지 알 수 없다.** 상태는 언제나 이벤트를 접어서 만든다(→ `fold`). 느려지면
캐시는 **앱 계층**에서 건다 — 캐시는 틀려도 지우면 되지만 저장된 스냅샷은
틀린 채로 남는다.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable, Protocol, Sequence

from sector.datastore import hub
from sector.sources.krx_common import assert_ignored_output, repo_root
from sector.workspace.events import Event, EventError, parse_event, sort_key

__all__ = ["StoreError", "EventStore", "LocalStore", "HubStore", "default_local_root"]


class StoreError(RuntimeError):
    """원장을 읽거나 쓸 수 없다."""


class EventStore(Protocol):
    """원장의 계약. 🔒 **읽기와 더하기뿐이다** — 고치기·지우기가 없다."""

    def append(self, events: Sequence[Event]) -> int: ...
    def read_all(self) -> list[Event]: ...


def _sorted(events: Iterable[Event]) -> list[Event]:
    """시간순. 같은 시각은 `events.sort_key` 가 정한 인과 순서로 가른다.

    🔒 tie-break 를 명시하는 이유는 `scoring._ranks` 와 같다 — 정렬이 흔들리면
       같은 원장에서 다른 상태가 나오고, 그것은 재현 불가다.
    """
    return sorted(events, key=sort_key)


# ── 로컬 ────────────────────────────────────────────────────────────────────

def default_local_root() -> Path:
    """`data/workspace/`. `.gitignore` 의 `/data/` 가 이미 막는다."""
    return repo_root() / "data" / "workspace"


class LocalStore:
    """파일 하나 = 이벤트 하나. 개발·테스트용."""

    def __init__(self, root: Path | str | None = None) -> None:
        self.root = Path(root) if root is not None else default_local_root()
        self.events_dir = self.root / "events"

    def append(self, events: Sequence[Event]) -> int:
        if not events:
            return 0
        self.events_dir.mkdir(parents=True, exist_ok=True)
        # 🔒 만든 **뒤에** 묻는다 — git 은 없는 디렉터리의 무시 여부를 답하지 못한다.
        assert_ignored_output(self.events_dir / "probe.json", what="조 이름·토론 글")
        written = 0
        for event in events:
            path = self.root / event.path_in_repo
            body = json.dumps(event.to_json(), ensure_ascii=False, indent=2) + "\n"
            if path.exists() and path.read_text(encoding="utf-8") == body:
                continue            # 멱등 — 같은 내용이면 건드리지 않는다
            path.write_text(body, encoding="utf-8")
            written += 1
        return written

    def read_all(self) -> list[Event]:
        if not self.events_dir.is_dir():
            return []
        events = []
        for path in sorted(self.events_dir.glob("*.json")):
            try:
                events.append(parse_event(json.loads(path.read_text(encoding="utf-8"))))
            except (OSError, json.JSONDecodeError, EventError) as exc:
                # 🔴 삼키지 않는다. 원장에 읽을 수 없는 줄이 있다는 것은 사고다.
                raise StoreError(f"{path} 를 이벤트로 읽을 수 없다: {exc}") from exc
        return _sorted(events)


# ── Hugging Face ────────────────────────────────────────────────────────────

class HubStore:
    """HF private dataset. 🔒 저장소는 `hub.WORKSPACE_REPO_ID` 하나다.

    ★ `api` 를 주입받는다 — 테스트가 가짜 API 로 계약을 확인할 수 있고, 토큰을
      읽는 길이 `secret_access` 하나로 유지된다(V19).
    """

    def __init__(self, api: Any, *, repo_id: str = hub.WORKSPACE_REPO_ID) -> None:
        self.api = api
        self.repo_id = repo_id

    def append(self, events: Sequence[Event]) -> int:
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
        from huggingface_hub import CommitOperationAdd

        existing = set(self._list_files())
        fresh = [e for e in events if e.path_in_repo not in existing]
        if not fresh:
            return 0          # 🔒 빈 커밋을 만들지 않는다

        ops = [
            CommitOperationAdd(
                path_in_repo=event.path_in_repo,
                path_or_fileobj=(
                    json.dumps(event.to_json(), ensure_ascii=False, indent=2) + "\n"
                ).encode("utf-8"),
            )
            for event in fresh
        ]
        kinds = ", ".join(sorted({e.kind for e in fresh}))
        hub.commit(self.api, ops, message=f"workspace: {kinds} ({len(fresh)}건)",
                   repo_id=self.repo_id)
        return len(fresh)

    def _list_files(self) -> list[str]:
        try:
            return list(self.api.list_repo_files(repo_id=self.repo_id, repo_type=hub.REPO_TYPE))
        except Exception as exc:                 # noqa: BLE001 — 되던진다
            raise StoreError(
                f"{self.repo_id} 의 파일 목록을 받지 못했다: {exc}\n"
                f"  → 저장소가 있는지, 토큰에 읽기 권한이 있는지 확인한다"
            ) from exc

    def read_all(self) -> list[Event]:
        names = self._list_files()
        events = []
        for name in sorted(n for n in names if n.startswith("events/") and n.endswith(".json")):
            raw = hub.download_bytes(self.api, name, repo_id=self.repo_id)
            if raw is None:
                continue                             # 목록과 실제가 어긋나면 건너뛴다
            try:
                events.append(parse_event(json.loads(raw.decode("utf-8"))))
            except (UnicodeDecodeError, json.JSONDecodeError, EventError) as exc:
                raise StoreError(f"{self.repo_id}:{name} 를 이벤트로 읽을 수 없다: {exc}") from exc
        return _sorted(events)
