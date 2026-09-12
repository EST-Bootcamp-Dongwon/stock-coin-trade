"""조별 협업 상태의 **원장** — append-only 이벤트.

## 🔴 왜 상태가 아니라 이벤트인가

조 목록·확정 섹터를 파일 하나에 담아 덮어쓰면 두 사람이 같은 순간에 쓸 때 한쪽이
말없이 사라진다. HF 는 커밋 기반이라 그 충돌이 **409 로 드러나지도 않는다** —
나중에 쓴 쪽이 이긴다. 그래서 **이벤트 1건 = 파일 1개**로 나눈다. 파일이 다르면
내용 충돌이 없고 커밋 경합만 남으며, 그것은 재시도로 끝난다.

부수 효과가 더 크다 — **누가 언제 무엇을 했는지가 남는다.** 섹터 확정을 되돌려도
"한 번 정했다가 바꿨다"는 사실이 지워지지 않는다. 대회 보고서가 요구하는
"포폴 변경 사유"가 바로 이 기록이다 (→ 계획서 부록 A).

## 🔒 시각은 입력이지 환경이 아니다

`at` 에 **기본값이 없다.** 이 저장소의 다른 계층(`aggregate(as_of=)` ·
`score(fetched_at=)`)과 같은 규율이다. 도메인 안에서 `datetime.now()` 를 부르면
테스트가 돌릴 때마다 다른 답을 내고, 이벤트 id 가 내용이 아니라 **실행 시각**에
좌우된다 — 그러면 같은 이벤트를 두 번 써도 파일이 둘이 된다.

## 🔒 이벤트 id 는 내용에서 나온다

`{at}-{kind}-{내용해시12}`. 세 가지가 따라온다 —
1. **사전순 = 시간순** (`bas_dd` 가 문자열인 것과 같은 이유)
2. **멱등** — 같은 이벤트를 두 번 써도 같은 파일이라 중복이 생기지 않는다.
   409 재시도가 안전해지는 것이 여기서 나온다
3. **변조 탐지** — 파일 내용을 고치면 id 와 어긋난다
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any, Mapping

__all__ = [
    "EventError",
    "Event",
    "KINDS",
    "LEGACY_SECRET_KEY",
    "canonical_json",
    "carries_legacy_secret",
    "make_event",
    "team_created",
    "member_joined",
    "sector_confirmed",
    "sector_unconfirmed",
    "comment_posted",
    "team_archived",
    "team_restored",
    "parse_event",
    "require_team_id",
    "sort_key",
]


class EventError(ValueError):
    """이벤트가 만들어질 수 없거나 읽을 수 없다."""


#: 이벤트 종류. 🔒 늘리는 것은 명시적 결정이다 — `fold` 가 모르는 종류를 만나면
#:    조용히 무시하지 않고 던진다. 조용한 무시는 "왜 화면에 안 나오지"로 돌아온다.
KINDS: tuple[str, ...] = (
    "team.created",
    "member.joined",
    "sector.confirmed",
    "sector.unconfirmed",
    "comment.posted",
    # 🔒 "삭제" 가 아니라 **보관**이다. 원장은 append-only 라 지우지 않는다 —
    #    지우면 그 조가 무엇을 정했었는지도 함께 사라지고, 3개월 뒤 되짚을 수 없다.
    "team.archived",
    "team.restored",
)

#: 조 id·섹터 id 와 같은 식별자 규칙. `sectors.yaml` 의 `id` 규칙과 맞춘다.
_ID_RE = re.compile(r"^[a-z][a-z0-9_]{1,39}$")
#: UTC ISO — `fetched_at` 과 같은 모양. 사전순 정렬이 시간순이 되는 것이 요점이다.
_AT_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?\+00:00$")

_MAX_TEXT = 4000

#: 🔴 **passcode 는 payload 에 들어가지 않는다** (ADR-SC-0011 ④ · 2026-09-12).
#:
#: 옛 구조는 `team.created` 의 payload 에 해시를 담았다. HF private dataset 위에서는
#: 토큰 없이 읽을 수 없었기에 성립했지만, 원장이 Supabase 로 가면 **읽기가 공개**라
#: 그대로 옮기면 해시가 공개된다. 해시는 원장 **밖**(`EventStore.create_team`)에 쓰고
#: 이 계층은 그 자리를 아예 막는다.
#:
#: 🔒 DB 도 같은 규칙을 갖는다 — `workspace_event` 의
#:    `check (not (payload ? 'passcode_hash'))`. **두 곳이 같은 것을 막는다.**
#:    이쪽이 더 넓다(`passcode` 를 품은 모든 키) — 넓은 쪽이 먼저 거부하므로
#:    "앱이 만든 이벤트를 DB 가 거부한다" 는 일이 생기지 않는다. 반대로 좁으면 생긴다.
_SECRET_KEY_MARK = "passcode"

#: 옛 원장(2026-09-12 이전 HF)이 담던 키.
#: 🔒 **읽기는 막지 않는다.** 막으면 옛 원장 한 줄 때문에 원장 전체가 예외가 되고
#:    화면이 통째로 죽는다 (`fold` 머리주석의 "한 줄 때문에 팀 화면이 죽으면 안 된다").
#:    대신 `fold` 가 어긋난 것으로 올려 사람에게 보인다.
LEGACY_SECRET_KEY = "passcode_hash"


def canonical_json(value: Any) -> str:
    """해시·저장에 쓰는 **한 가지** 직렬화.

    🔒 모양이 둘이면 같은 내용이 다른 해시를 낸다. `sort_keys` 로 키 순서를 없애고,
       `separators` 로 공백을 없애고, `ensure_ascii=False` 로 한글을 그대로 둔다
       (`\\uXXXX` 로 부풀면 파일이 읽히지 않는다).
    """
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _require_id(value: Any, what: str) -> str:
    text = str(value).strip() if value is not None else ""
    if not _ID_RE.match(text):
        raise EventError(
            f"{what} 가 식별자 규칙에 맞지 않다: {value!r}. "
            f"소문자로 시작하고 소문자·숫자·밑줄 2~40자여야 한다"
        )
    return text


def _require_text(value: Any, what: str, *, max_len: int = _MAX_TEXT) -> str:
    """빈 문자열을 **받지 않는다.**

    🔴 사유를 비운 채 확정할 수 있으면 "왜 이 섹터인가" 가 남지 않는다. 그 한 칸이
       이 도구가 존재하는 이유다 (`sectors.yaml` 의 `note` 게이트와 같은 정신).
    """
    text = str(value).strip() if value is not None else ""
    if not text:
        raise EventError(f"{what} 가 비어 있다. 지어낼 수 없으므로 받지 않는다")
    if len(text) > max_len:
        raise EventError(f"{what} 가 너무 길다 ({len(text)}자 > {max_len}자)")
    return text


def require_team_id(value: Any) -> str:
    """조 id 를 규칙에 비춰 확인한다. 🔒 **저장소 계층이 쓴다.**

    🔴 id 가 경로(`secrets/{team_id}.json`)와 조회 필터(`team_id=eq.{team_id}`)에
       그대로 들어간다. 규칙을 통과하지 않은 값이 거기 닿으면 경로 탈출과 필터
       주입이 된다 — 규칙이 이미 있으니 **문 앞에서** 한 번 더 묻는다.
    """
    return _require_id(value, "team_id")


def _reject_secret_keys(payload: Mapping[str, Any]) -> None:
    """🔴 payload 에 passcode 가 섞이는 길을 **문에서** 막는다 (`_SECRET_KEY_MARK`)."""
    bad = sorted(str(key) for key in payload if _SECRET_KEY_MARK in str(key).lower())
    if bad:
        raise EventError(
            f"passcode 를 이벤트에 담을 수 없다: {', '.join(bad)}. "
            f"해시는 원장 밖에 둔다 — `EventStore.create_team` 이 쓴다 (ADR-SC-0011 ④)"
        )


def carries_legacy_secret(event: "Event") -> bool:
    """옛 원장 형식인가 — payload 에 passcode 해시가 아직 들어 있는가.

    🔒 판정을 여기 한 곳에 둔다. `fold` 가 이것으로 어긋난 것을 올린다.
    """
    return LEGACY_SECRET_KEY in event.payload


def _require_at(value: Any) -> str:
    text = str(value).strip() if value is not None else ""
    if not _AT_RE.match(text):
        raise EventError(
            f"`at` 이 UTC ISO 가 아니다: {value!r}. "
            f"예: 2026-09-12T05:33:34+00:00 (KST 로 사고하고 UTC 로 저장한다)"
        )
    return text


@dataclass(frozen=True, slots=True)
class Event:
    """원장 한 줄. 🔒 frozen — 한 번 만들어진 이벤트는 고쳐지지 않는다."""

    event_id: str
    kind: str
    team_id: str
    actor: str
    at: str
    payload: Mapping[str, Any] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "kind": self.kind,
            "team_id": self.team_id,
            "actor": self.actor,
            "at": self.at,
            "payload": dict(self.payload),
        }

    @property
    def path_in_repo(self) -> str:
        """저장 경로. `hub.WORKSPACE_ALLOWED_PREFIXES` 와 맞물린다."""
        return f"events/{self.event_id}.json"


def _event_id(kind: str, team_id: str, actor: str, at: str, payload: Mapping[str, Any]) -> str:
    body = canonical_json(
        {"kind": kind, "team_id": team_id, "actor": actor, "at": at, "payload": dict(payload)}
    )
    digest = hashlib.sha256(body.encode("utf-8")).hexdigest()[:12]
    # `:` 와 `.` 는 파일명에서 다루기 번거롭다. 사전순은 그대로 보존된다.
    # 🔒 순서가 중요하다 — `:` 를 먼저 지우면 `+00:00` 이 `+0000` 이 되어
    #    `Z` 치환이 빗나간다. 시간대 표기를 먼저 접는다.
    stamp = at.replace("+00:00", "Z").replace(":", "").replace(".", "-")
    return f"{stamp}-{kind.replace('.', '-')}-{digest}"


def make_event(
    kind: str,
    *,
    team_id: str,
    actor: str,
    at: str,
    payload: Mapping[str, Any] | None = None,
) -> Event:
    """이벤트 하나. 🔒 `at` 에 기본값이 없다 (머리주석).

    🔴 payload 에 passcode 를 담을 수 없다 (`_SECRET_KEY_MARK`). **새 이벤트는
       전부 이 문을 지난다** — 종류별 생성자도 여기로 들어온다.
    """
    body = dict(payload or {})
    _reject_secret_keys(body)
    return _make(kind, team_id=team_id, actor=actor, at=at, payload=body)


def _make(
    kind: str,
    *,
    team_id: str,
    actor: str,
    at: str,
    payload: Mapping[str, Any],
) -> Event:
    """검증하고 id 를 계산한다. 🔒 **passcode 게이트가 없다.**

    🔴 `parse_event` 가 이쪽을 쓴다 — 옛 원장에는 payload 에 해시가 남아 있고,
       읽는 길에 게이트를 두면 그 한 줄이 원장 전체를 예외로 만든다
       (→ `LEGACY_SECRET_KEY`). 새 이벤트를 만들 때는 **반드시 `make_event`** 다.
    """
    if kind not in KINDS:
        raise EventError(f"모르는 이벤트 종류다: {kind!r}. 아는 것 — {', '.join(KINDS)}")
    team = _require_id(team_id, "team_id")
    who = _require_text(actor, "actor", max_len=40)
    when = _require_at(at)
    body = dict(payload)
    return Event(
        event_id=_event_id(kind, team, who, when, body),
        kind=kind, team_id=team, actor=who, at=when, payload=body,
    )


# ── 종류별 생성자 — 여기서 payload 를 검증한다 ────────────────────────────────
# 🔒 `make_event` 를 직접 부르지 않고 이쪽을 쓴다. payload 는 사전이라 오타가
#    조용히 지나가는데, 아래 함수들이 그 자리를 막는다.

def team_created(*, team_id: str, name: str, actor: str, at: str) -> Event:
    """조를 만든다.

    🔴 **passcode 를 받지 않는다** (ADR-SC-0011 ④ · 2026-09-12). 해시는 원장이
       아니라 `EventStore.create_team` 이 원장 **밖**에 쓴다 — 원장 읽기가 공개이기
       때문이다(⑥).

    분리는 설계적으로도 낫다. passcode 는 **사건이 아니라 현재 상태**다. 이벤트에
    박아 두었기 때문에 옛 구조에는 **passcode 를 바꿀 방법이 없었다**
    (`team.created` 에만 있으니까).
    """
    return make_event(
        "team.created", team_id=team_id, actor=actor, at=at,
        payload={"name": _require_text(name, "조 이름", max_len=60)},
    )


def member_joined(*, team_id: str, member: str, actor: str, at: str) -> Event:
    return make_event(
        "member.joined", team_id=team_id, actor=actor, at=at,
        payload={"member": _require_text(member, "참가자 이름", max_len=40)},
    )


def sector_confirmed(*, team_id: str, sector_id: str, reason: str, actor: str, at: str) -> Event:
    """핵심 섹터를 확정한다. 🔴 **사유가 필수다** (`_require_text` 참조)."""
    return make_event(
        "sector.confirmed", team_id=team_id, actor=actor, at=at,
        payload={"sector_id": _require_id(sector_id, "sector_id"),
                 "reason": _require_text(reason, "확정 사유")},
    )


def sector_unconfirmed(*, team_id: str, reason: str, actor: str, at: str) -> Event:
    """확정을 되돌린다. 🔒 **지우는 것이 아니라 되돌리는 이벤트를 더한다.**

    원장은 append-only 다. 되돌린 사실 자체가 기록으로 남아야 "왜 바꿨나" 를
    나중에 말할 수 있다.
    """
    return make_event(
        "sector.unconfirmed", team_id=team_id, actor=actor, at=at,
        payload={"reason": _require_text(reason, "취소 사유")},
    )


def comment_posted(
    *, team_id: str, body: str, actor: str, at: str, sector_id: str | None = None
) -> Event:
    payload: dict[str, Any] = {"body": _require_text(body, "코멘트 본문")}
    if sector_id is not None:
        payload["sector_id"] = _require_id(sector_id, "sector_id")
    return make_event("comment.posted", team_id=team_id, actor=actor, at=at, payload=payload)


def team_archived(*, team_id: str, reason: str, actor: str, at: str) -> Event:
    """조를 **보관**한다. 목록에서 감추되 기록은 남긴다.

    🔴 누가 보관할 수 있는지는 **여기서 정하지 않는다.** 이벤트는 "무슨 일이
       있었나" 이지 "누가 해도 되나" 가 아니다.

    🔒 그 답은 두 층에 있다 — **저장소**가 passcode 로 가르고(`SupabaseStore` ·
       ADR-SC-0011 ⑤), 화면은 *실수 방지*로 만든 사람에게만 버튼을 보여준다
       (`teams._can_archive`). ⚠️ 로컬·HF 에는 앞의 층이 없다. 거기서는 실제
       경계가 파일·토큰이고, 그 사실을 숨기지 않는다 — `auth` 머리주석의 위협
       모델과 같은 이야기다.
    """
    return make_event(
        "team.archived", team_id=team_id, actor=actor, at=at,
        payload={"reason": _require_text(reason, "보관 사유")},
    )


def team_restored(*, team_id: str, reason: str, actor: str, at: str) -> Event:
    """보관을 되돌린다."""
    return make_event(
        "team.restored", team_id=team_id, actor=actor, at=at,
        payload={"reason": _require_text(reason, "복구 사유")},
    )


#: 같은 시각일 때 먼저 접혀야 하는 것. 🔴 **인과 관계다.**
#:
#: 시각은 초 단위로 자른다(마이크로초까지 넣으면 같은 동작이 매번 다른 id 를 낳아
#: 멱등이 깨진다). 그래서 한 초 안에 두 이벤트가 들어올 수 있고, 그때 tie-break 가
#: `event_id` 사전순이면 **`team-archived` 가 `team-created` 보다 앞선다**(a < c).
#: 실제로 그렇게 접혀서 "없는 조에 대한 보관 이벤트" 라는 이상이 떴다 —
#: 조를 만들자마자 보관하면 재현된다.
#:
#: 🔒 조 생성은 그 조에 대한 **모든 이벤트의 전제**다. 같은 초 안에서만 순서를
#:    바로잡고, 서로 다른 시각의 순서는 건드리지 않는다.
_KIND_ORDER: Mapping[str, int] = {"team.created": 0}


def sort_key(event: "Event") -> tuple[str, int, str]:
    """원장 정렬 키. 🔒 `store` 와 `fold` 가 **같은 것**을 써야 한다.

    둘이 달라지면 저장소가 준 순서와 접는 쪽이 기대하는 순서가 어긋나고,
    그 어긋남은 "가끔 조가 사라진다" 로 나타난다.
    """
    return (event.at, _KIND_ORDER.get(event.kind, 1), event.event_id)


# ── 읽기 ────────────────────────────────────────────────────────────────────

def parse_event(data: Any) -> Event:
    """저장된 JSON 하나를 `Event` 로. 🔴 id 가 내용과 어긋나면 **던진다.**

    조용히 받아들이면 손으로 고친 파일이 원장에 섞이고, 그 뒤로는 원장이
    원장이 아니게 된다.

    🔒 **passcode 게이트를 통과시키지 않는다**(`_make`). 옛 원장을 읽어야 하고,
       읽지 못하면 화면이 통째로 죽는다 — 어긋난 것은 `fold` 가 말한다.
    """
    if not isinstance(data, Mapping):
        raise EventError(f"이벤트가 사전이 아니다: {type(data).__name__}")
    missing = [k for k in ("event_id", "kind", "team_id", "actor", "at") if k not in data]
    if missing:
        raise EventError(f"이벤트에 칸이 없다: {', '.join(missing)}")
    payload = data.get("payload") or {}
    if not isinstance(payload, Mapping):
        raise EventError("payload 가 사전이 아니다")

    event = _make(
        str(data["kind"]), team_id=str(data["team_id"]), actor=str(data["actor"]),
        at=str(data["at"]), payload=dict(payload),
    )
    if event.event_id != str(data["event_id"]):
        raise EventError(
            f"이벤트 id 가 내용과 어긋난다: 파일은 {data['event_id']!r} 인데 "
            f"내용에서는 {event.event_id!r} 가 나온다. 파일이 손으로 고쳐졌을 수 있다"
        )
    return event
