"""엔진 — 질문 하나를 **라우터 → 장부 → 조립 → guard** 로 흘리고 인계 블록을 만든다.

## 인계 블록 (GIC 에서 옮긴 것 · 구조만)

프롬프트는 단계를 넘길 때 블록 하나만 붙여도 이어지게 했다 — 대상 · 기준일 · 확정값 · 고정 ID 대장 ·
구조적으로 없는 것의 목록 · 미해결 빈칸 · 다음 입력. 여기서는 그것이 **후속 질문의 맥락**이다.
"반도체 왜 1위야?" 다음에 "그럼 믿어도 돼?" 라고 물으면 섹터 이름이 없어도 반도체로 답한다.

🔒 **화면에서 고른 섹터가 바뀌면 인계를 버린다.** 사람이 다른 섹터를 골랐는데 옛 맥락으로 답하면
   그것이 곧 엉뚱한 답이다.
🔒 **인계는 사람이 물은 답에서만 만든다**(화면 쪽 `evidence.py`). 질문하지 않은 기본 답으로 인계를 덮으면
   처음 묻는 질문의 빈칸이 "유지" 로 나왔다(리뷰 O8).

## 🔒 섹터를 정하는 순서

질문에 적힌 섹터 → (화면의 섹터가 그대로면) 인계받은 섹터 → 화면에서 고른 섹터.
화면의 섹터와 다르면 **어디서 온 섹터인지** 머리에 적는다(2026-09-14 사용자 결정).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from dashboard.agent import compose as composer
from dashboard.agent import guard, ids
from dashboard.agent import intent as router
from dashboard.agent.inventory import Gap, collect, with_past
from dashboard.agent.lexicon import Lexicon
from dashboard.agent.redteam import risk_ledger
from dashboard.explain import josa

__all__ = ["Handoff", "Answer", "answer", "brief_for", "NOT_IN_TABLE", "NO_SECTOR"]

NOT_IN_TABLE = "그 섹터는 기준일 점수 표에 없다 — 짐작해서 답하지 않는다."
NO_SECTOR = "어느 섹터를 묻는지 모른다 — 섹터 이름을 적거나 위에서 섹터를 고른다."


@dataclass(frozen=True, slots=True)
class Handoff:
    sector_id: str
    context_sector: str | None
    intent: str
    as_of: str
    config_version: str
    window_days: int | None
    since_confirm: bool
    confirmed: tuple[str, ...]
    ledger: tuple[tuple[str, str, str], ...]
    structural: tuple[str, ...]
    unresolved: tuple[tuple[str, str, str], ...]   # (Gap ID, 항목, 결측 유형)
    next_input: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Answer:
    route: router.Route
    sector_id: str | None = None
    notice: str | None = None
    brief: composer.Brief | None = None
    violations: tuple[str, ...] = ()
    handoff: Handoff | None = None
    message: str | None = None
    choices: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return self.brief is not None and not self.violations


def _gap_states(brief: composer.Brief, route: router.Route, previous: Handoff | None) -> composer.Brief:
    """🔒 신규 · 유지 · 닫힘. **닫힘은 같은 섹터 · 같은 질문 · 같은 기준일 · 같은 창**일 때만 말한다.

    다른 질문(또는 다른 창)에서 안 나온 빈칸은 채워진 것이 아니라 묻지 않은 것이다(리뷰 O8).
    """
    if previous is None or previous.sector_id != brief.sector_id:
        return brief
    before = {gap_id: (item, kind) for gap_id, item, kind in previous.unresolved}
    gaps = [gap.with_state("유지" if gap.id in before else "신규") for gap in brief.gaps]
    same_question = (previous.intent == brief.intent and previous.as_of == brief.as_of
                     and previous.window_days == route.window_days
                     and previous.since_confirm == route.since_confirm)
    if same_question:
        present = {gap.id for gap in brief.gaps}
        for gap_id, (item, kind) in before.items():
            if gap_id not in present:
                gaps.append(Gap(id=gap_id, item=item, kind=kind,
                                why="이번 답에서는 이 빈칸이 생기지 않았다", needed="없다",
                                workaround="없다", affects="없다", state="닫힘"))
    return brief.with_gaps(tuple(gaps))


def _handoff(brief: composer.Brief, route: router.Route, context_sector: str | None) -> Handoff:
    cited: list[str] = []
    for _, sentence in brief.labelled():
        cited.extend(sentence.cites)
    for attack in brief.attacks:
        cited.extend(attack.cites)
    confirmed = tuple(
        f"{item.id} = {item.shown()} · {item.source} · 신뢰도 {item.confidence}"
        for key in dict.fromkeys(cited) if (item := brief.evidence.get(key)) is not None)
    weakened = {a.target for a in brief.attacks if a.status == "취약"}
    targets = dict.fromkeys([card.target for card in brief.cards] + [a.target for a in brief.attacks])
    ledger = tuple((t, ids.CATALOG[t], "약화" if t in weakened else "유효") for t in targets)
    ledger += risk_ledger(brief.attacks)
    return Handoff(
        sector_id=brief.sector_id, context_sector=context_sector, intent=brief.intent,
        as_of=brief.as_of, config_version=str(brief.evidence["EV-CONFIG"].value),
        window_days=route.window_days, since_confirm=route.since_confirm,
        confirmed=confirmed, ledger=ledger,
        structural=tuple(g.id for g in brief.gaps if g.kind == "구조적 비공시"),
        unresolved=tuple((g.id, g.item, g.kind) for g in brief.gaps
                         if g.state != "닫힘" and g.kind != "구조적 비공시"),
        next_input=brief.followups)


def _build(route: router.Route, intent: str, *, sector_id: str, context_sector: str | None,
           origin: str, frame: Any, names: Any, master: Any, profile: str, days: int,
           workspace: Any, team_id: str | None, previous: Handoff | None) -> Answer:
    inventory = collect(frame, sector_id, profile=profile, days=days, names=names, master=master,
                        workspace=workspace, team_id=team_id)
    if inventory is None:
        return Answer(route=route, sector_id=sector_id, message=NOT_IN_TABLE, choices=router.INTENTS)
    extra: tuple[Gap, ...] = ()
    if intent == "changed":
        inventory, extra = with_past(
            inventory, frame, window_days=route.window_days or router.DEFAULT_WINDOW,
            since_confirm=route.since_confirm, workspace=workspace, team_id=team_id)
    brief = composer.compose(inventory, intent, since_confirm=route.since_confirm,
                             window_days=route.window_days, window_source=route.window_source,
                             extra_gaps=extra)
    brief = _gap_states(brief, route, previous)
    violations = guard.verify(brief, frame=frame, master=master, workspace=workspace, team_id=team_id)
    notice = None
    if context_sector and sector_id != context_sector:
        chosen = names.sector_label(context_sector)
        where = "질문에 적힌" if origin == "question" else "앞 질문에서 이어진"
        notice = f"위에서 고른 **{chosen}**{josa(chosen, '이가')} 아니라 {where} **{inventory.label}** 에 대해 답한다."
    return Answer(route=route, sector_id=sector_id, notice=notice, brief=brief,
                  violations=violations, handoff=_handoff(brief, route, context_sector))


def answer(question: object, *, frame: Any, context_sector: str | None, names: Any,
           master: Any = None, profile: str = "balanced", days: int = 20, workspace: Any = None,
           team_id: str | None = None, previous: Handoff | None = None,
           lexicon: Lexicon | None = None) -> Answer:
    """자유 질문 하나. 🔒 순수 함수다 — 벽시계 · 네트워크 · 원장 쓰기가 없다."""
    lexicon = lexicon if lexicon is not None else Lexicon.of(master)
    route = router.route(question, lexicon)
    if route.kind == "refuse":
        return Answer(route=route, message=router.REFUSAL_TEXT[route.reason], choices=router.INTENTS)
    if route.kind == "clarify":
        message = router.CLARIFY_TEXT[route.reason]
        if route.reason == "gics" and master is not None:
            members = [names.sector_label(s.id) for s in master.sectors if s.gics == route.gics]
            group = next((g for g in master.gics_sectors if g.id == route.gics), None)
            if members:
                message += " 그 안의 섹터 — " + " · ".join(members)
            elif group is not None and group.empty_reason:
                message += " 이 대분류에는 섹터가 없다 — " + " ".join(group.empty_reason.split())
        if route.reason == "sector_unclear" and route.candidates:
            message += " 혹시 — " + " · ".join(names.sector_label(s) for s in route.candidates)
        choices = route.candidates if route.reason == "ambiguous" else router.INTENTS
        return Answer(route=route, sector_id=route.sector_id, message=message, choices=choices)

    carried = (previous.sector_id
               if previous is not None and previous.context_sector == context_sector else None)
    if route.sector_id:
        sector_id, origin = route.sector_id, "question"
    elif carried:
        sector_id, origin = carried, "handoff"
    else:
        sector_id, origin = context_sector, "context"
    if sector_id is None:
        return Answer(route=route, message=NO_SECTOR, choices=router.INTENTS)
    return _build(route, route.intent or "why_rank", sector_id=sector_id,
                  context_sector=context_sector, origin=origin, frame=frame, names=names,
                  master=master, profile=profile, days=days, workspace=workspace, team_id=team_id,
                  previous=previous)


def brief_for(intent: str, *, frame: Any, sector_id: str, names: Any, master: Any = None,
              profile: str = "balanced", days: int = 20, workspace: Any = None,
              team_id: str | None = None, window_days: int = router.DEFAULT_WINDOW,
              since_confirm: bool = False, window_source: str = "영업일") -> Answer:
    """질문 없이 — 기본 화면과 모달이 쓴다."""
    route = router.Route(kind="answer", reason="기본", intent=intent,
                         window_days=window_days if intent == "changed" else None,
                         since_confirm=since_confirm,
                         window_source=("확정" if since_confirm else window_source) if intent == "changed" else "")
    return _build(route, intent, sector_id=sector_id, context_sector=sector_id, origin="context",
                  frame=frame, names=names, master=master, profile=profile, days=days,
                  workspace=workspace, team_id=team_id, previous=None)
