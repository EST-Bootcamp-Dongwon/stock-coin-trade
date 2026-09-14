"""이벤트 → 현재 상태. **순수 함수다.**

## 🔒 어긋난 이벤트를 조용히 버리지 않는다

없는 조에 대한 확정, 같은 id 로 두 번 만들어진 조 — 원장에 이런 것이 섞일 수 있다
(동시 생성·손으로 고친 파일). 조용히 건너뛰면 **화면에 안 나오는 이유를 아무도
모른다.** 그래서 `Workspace.anomalies` 에 사람이 읽을 문장으로 담아 올린다.
던지지 않는 이유는 한 줄 때문에 팀 전체의 화면이 죽으면 안 되기 때문이다
(→ ADR-SC-0007 의 "없으면 없다고 말한다" 를 화면 단위로 적용한 것).

**읽지 못한 줄**도 같다(2026-09-14 · ADR-SC-0011 ⑬). 저장소가 건너뛰어 `Ledger.rejected` 로
넘기고, 여기서 `anomalies` 의 맨 앞에 올린다.

## 🔒 마지막이 이긴다 — 다만 기록은 남는다

확정을 두 번 하면 나중 것이 현재 상태다. 그러나 이전 확정도 원장에 그대로 있고
`history_of` 가 그것을 돌려준다. 대회 보고서의 "포폴 변경 사유" 가 여기서 나온다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping

from sector.workspace.events import (
    Event,
    Ledger,
    RejectedRow,
    carries_legacy_secret,
    is_identifier,
    sort_key,
)
from sector.workspace.links import MAX_LINKS, LinkError, normalize_link

__all__ = ["Team", "Comment", "Workspace", "fold"]


@dataclass(frozen=True, slots=True)
class Team:
    """조 하나의 현재 모습.

    🔴 **`passcode_hash` 가 없다** (2026-09-12 · ADR-SC-0011 ④). 해시는 원장 밖에
       있고 검증은 저장소가 한다(`EventStore.verify`) — 화면이 해시를 손에 들면
       원장 읽기가 공개인 순간 그것이 곧 노출 경로가 된다.
    """

    id: str
    name: str
    created_by: str
    created_at: str
    members: tuple[str, ...] = ()
    #: 핵심 섹터. 🔒 **확정 전에는 `None` 이다** — 빈 문자열이나 "미정" 이 아니다.
    core_sector: str | None = None
    core_reason: str | None = None
    confirmed_at: str | None = None
    confirmed_by: str | None = None
    #: 🔒 **보관**이지 삭제가 아니다. 목록에서 감추되 기록은 남는다.
    archived: bool = False
    archived_reason: str | None = None
    archived_by: str | None = None
    archived_at: str | None = None

    @property
    def is_confirmed(self) -> bool:
        return self.core_sector is not None


@dataclass(frozen=True, slots=True)
class Comment:
    event_id: str
    team_id: str
    author: str
    body: str
    at: str
    sector_id: str | None = None
    #: 🔒 **다시 굳혀 봐서 같았던 링크만** 담긴다 (`_links_of`). 화면은 이것을 그대로 그린다.
    links: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Workspace:
    teams: Mapping[str, Team]
    comments: tuple[Comment, ...] = ()
    history: tuple[Event, ...] = ()
    #: 🔴 접는 동안 어긋난 것들. 비어 있지 않으면 화면이 그대로 보여준다.
    anomalies: tuple[str, ...] = ()

    def team(self, team_id: str) -> Team | None:
        return self.teams.get(team_id)

    @property
    def active_teams(self) -> dict[str, Team]:
        """보관되지 않은 조만. 🔒 평소 화면은 **이것**을 쓴다."""
        return {tid: team for tid, team in self.teams.items() if not team.archived}

    @property
    def archived_teams(self) -> dict[str, Team]:
        """보관된 조. 마스터 화면에서만 본다."""
        return {tid: team for tid, team in self.teams.items() if team.archived}

    def history_of(self, team_id: str) -> tuple[Event, ...]:
        """그 조의 원장 전체 — 시간순. "왜 바꿨나" 를 말하려면 이것이 필요하다."""
        return tuple(e for e in self.history if e.team_id == team_id)

    def comments_of(self, team_id: str, *, sector_id: str | None = None) -> tuple[Comment, ...]:
        return tuple(
            c for c in self.comments
            if c.team_id == team_id and (sector_id is None or c.sector_id == sector_id)
        )


def fold(events: Ledger | Iterable[Event]) -> Workspace:
    """🔒 **입력 순서에 의존하지 않는다.** 스스로 정렬한다.

    🔒 `Ledger`(`store.read_all()` 의 답)를 그대로 받는다 — 읽지 못한 줄이 **호출부에서
       빠질 틈이 없게** 하려는 것이다. 이벤트 목록만 넘기는 호출부(테스트 · 직접 만든 원장)도 된다.

    `aggregate` 는 오름차순을 요구하고 정렬해 주지 않는다 — 거기서는 순서가
    데이터의 성질(체인연결)이기 때문이다. 원장은 다르다. **순서는 파일들에서
    유도되는 것**이지 호출부가 책임질 것이 아니다. 여러 사람이 각자 파일을 쓰고
    목록 순서는 저장소가 정한다.

    정렬 키는 `events.sort_key` 하나뿐이고 `store` 도 같은 것을 쓴다.
    """
    # 🔒 `store` 가 이미 정렬해 주지만, 원장을 직접 넘기는 호출부도 있다.
    #    **같은 키**로 한 번 더 맞춘다 — 두 곳이 다른 순서를 쓰면 "가끔 조가
    #    사라진다" 로 나타난다 (→ `events.sort_key` 머리주석).
    rejected = events.rejected if isinstance(events, Ledger) else ()
    ordered = sorted(events.events if isinstance(events, Ledger) else events, key=sort_key)
    teams: dict[str, Team] = {}
    comments: list[Comment] = []
    anomalies: list[str] = [_rejected_line(row) for row in rejected]

    for event in ordered:
        kind = event.kind
        payload = event.payload

        # 🔴 옛 원장 형식 — payload 에 passcode 해시가 남아 있다 (2026-09-12 이전).
        #    읽기는 막지 않되(→ `events.LEGACY_SECRET_KEY`) **조용히 넘기지도 않는다.**
        #    그 조는 `passcode_params` 가 없으므로 **참가할 수 없다** — 화면이 그
        #    이유를 말할 수 있어야 한다.
        if carries_legacy_secret(event):
            anomalies.append(
                f"조 '{event.team_id}' 의 {kind} 이벤트가 옛 형식이다 — passcode "
                f"해시가 원장 안에 있다({event.at}). 이 조에는 참가할 수 없다. "
                f"조를 다시 만든다 (ADR-SC-0011 ④)"
            )

        if kind == "team.created":
            if event.team_id in teams:
                existing = teams[event.team_id]
                anomalies.append(
                    f"조 '{event.team_id}' 가 두 번 만들어졌다 — "
                    f"{existing.created_at} ({existing.created_by}) 것을 쓰고 "
                    f"{event.at} ({event.actor}) 것은 쓰지 않는다. "
                    f"다른 이름으로 다시 만든다"
                )
                continue
            teams[event.team_id] = Team(
                id=event.team_id,
                name=str(payload.get("name", "")),
                created_by=event.actor,
                created_at=event.at,
                members=(event.actor,),      # 만든 사람은 당연히 참가자다
            )
            continue

        team = teams.get(event.team_id)
        if team is None:
            anomalies.append(
                f"없는 조 '{event.team_id}' 에 대한 {kind} 이벤트가 있다 "
                f"({event.at} · {event.actor}). 조 생성 기록이 지워졌을 수 있다"
            )
            continue

        if kind == "member.joined":
            member = str(payload.get("member", ""))
            if member and member not in team.members:
                teams[team.id] = _replace(team, members=team.members + (member,))
        elif kind == "sector.confirmed":
            # 🔴 RPC 는 payload 를 검사하지 않는다 — 규칙에 맞지 않는 섹터 id 는 반영하지 않고 알린다.
            #    그대로 받으면 그 글자가 화면 곳곳(모달 제목 · 목록)으로 흘러간다
            if not is_identifier(payload.get("sector_id")):
                anomalies.append(
                    f"조 '{team.id}' 의 확정({event.at} · {event.actor})은 섹터 id 가 "
                    f"규칙에 맞지 않아 반영하지 않았다")
                continue
            teams[team.id] = _replace(
                team,
                core_sector=str(payload["sector_id"]),
                core_reason=str(payload.get("reason", "")) or None,
                confirmed_at=event.at,
                confirmed_by=event.actor,
            )
        elif kind == "sector.unconfirmed":
            teams[team.id] = _replace(
                team, core_sector=None, core_reason=None,
                confirmed_at=None, confirmed_by=None,
            )
        elif kind == "team.archived":
            teams[team.id] = _replace(
                team, archived=True, archived_reason=str(payload.get("reason", "")) or None,
                archived_by=event.actor, archived_at=event.at)
        elif kind == "team.restored":
            teams[team.id] = _replace(
                team, archived=False, archived_reason=None,
                archived_by=None, archived_at=None)
        elif kind == "comment.posted":
            sector = payload.get("sector_id")
            if sector is not None and not is_identifier(sector):
                anomalies.append(
                    f"조 '{team.id}' 의 코멘트({event.at} · {event.actor})는 섹터 id 가 규칙에 "
                    f"맞지 않아 어느 섹터에도 붙이지 않았다")
                sector = None
            links, problems = _links_of(payload.get("links"))
            anomalies.extend(
                f"조 '{team.id}' 의 코멘트({event.at} · {event.actor})에서 {problem}"
                for problem in problems)
            comments.append(Comment(
                event_id=event.event_id, team_id=team.id, author=event.actor,
                body=str(payload.get("body", "")), at=event.at,
                sector_id=str(sector) if sector else None,
                links=links,
            ))
        else:  # pragma: no cover — `make_event` 가 이미 막는다
            anomalies.append(f"모르는 이벤트 종류다: {kind} ({event.event_id})")

    return Workspace(
        teams=teams, comments=tuple(comments),
        history=tuple(ordered), anomalies=tuple(anomalies),
    )


def _rejected_line(row: RejectedRow) -> str:
    """🔴 읽지 못한 줄 — 조용히 버리지 않는다. 길이는 `RejectedRow.of` 가 이미 묶었다."""
    whose = f"조 '{row.team_id}' 의 " if row.team_id else ""
    return (f"{whose}원장 한 줄을 읽지 못해 반영하지 않았다({row.where}) — {row.reason}. "
            f"앱을 거치지 않고 쓴 줄로 보인다")


def _links_of(raw: object) -> tuple[tuple[str, ...], list[str]]:
    """원장의 링크를 **다시 굳혀 보고 같을 때만** 그린다. (그릴 것, 어긋난 것)

    🔴 쓰기 게이트(`events.comment_posted`)만 믿지 않는다. passcode 를 가진 사람은 RPC 로
       앱을 거치지 않고 아무 payload 나 쓸 수 있다 — ADR-SC-0011 ⑤ 는 *누가* 쓰는지를
       막지 *무엇을* 쓰는지는 막지 않는다. 걸러낸 것은 조용히 버리지 않고 알린다.
    """
    if raw is None:
        return (), []
    if not isinstance(raw, list):
        return (), ["링크 칸이 목록이 아니라 링크를 그리지 않았다"]
    shown: list[str] = []
    problems: list[str] = []
    for item in raw:
        try:
            normalized = normalize_link(item)
        except LinkError as exc:
            problems.append(f"링크 하나를 그리지 않았다 — {exc}")
            continue
        if normalized != item:
            problems.append("저장 모양과 다른 링크 하나를 그리지 않았다 — 앱을 거치지 않고 쓴 것으로 보인다")
            continue
        if item not in shown:
            shown.append(item)
    if len(shown) > MAX_LINKS:
        problems.append(f"링크가 {len(shown)}개라 앞의 {MAX_LINKS}개만 그렸다")
        shown = shown[:MAX_LINKS]
    return tuple(shown), problems


def _replace(team: Team, **changes: object) -> Team:
    """frozen dataclass 를 바꾼 사본으로. `dataclasses.replace` 가 `slots=True` 와
    맞물릴 때 파이썬 버전마다 다르게 굴어 직접 만든다."""
    data = {
        "id": team.id, "name": team.name,
        "created_by": team.created_by, "created_at": team.created_at,
        "members": team.members, "core_sector": team.core_sector,
        "core_reason": team.core_reason, "confirmed_at": team.confirmed_at,
        "confirmed_by": team.confirmed_by, "archived": team.archived,
        "archived_reason": team.archived_reason, "archived_by": team.archived_by,
        "archived_at": team.archived_at,
    }
    data.update(changes)   # type: ignore[arg-type]
    return Team(**data)    # type: ignore[arg-type]
