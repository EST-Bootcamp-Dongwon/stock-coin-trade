"""조립 — 장부를 **결론이 먼저 오는 문단과 해석 카드**로. 여전히 템플릿이다.

## GIC 에서 옮긴 것 (구조만 · ADR-SC-0013)

- **세 원칙** — ① 결론을 첫 문장에 둔다(`Brief.headline`) ② 주장마다 수치와 기간을 붙인다
  ③ 관찰에는 해석을 짝지운다(카드의 `meaning` 칸)
- **해석 카드 여섯 칸** — 칸 이름은 `SLOT_NAMES`, 여섯 칸을 모두 채운다. 🔒 **원인 가설 칸은 "말할 수 없다"
  로 채운다** — 원인을 말할 재료(뉴스 본문)를 이 도구는 쓰지 않는다. 비워 두지도, 지어내지도 않고 Gap 으로 넘긴다.
- **다음에 물을 것** — `followups`. 나머지 의도다.
- **사람의 승인** — 이 조립은 판정하지 않는다. 승인은 조의 **확정**이다.

## 🔒 조립은 **고르기만** 한다

문장의 글자는 `templates` 에, 숫자는 `slots` 의 자리에 있다. 여기서 하는 일은 장부 값을 보고 **어느 틀을 쓸지**
고르는 것뿐이다. guard 는 같은 고르기를 원천 값으로 따로 해 보고, 결론 · 카드 · 목록 · 빈칸이 **전부 같은지**
대조한다(2026-09-14 재검증 R-A). 고정 문장(`fixed`)은 `FIXED_TEXTS` 의 상수이고, 어느 칸에 어느 상수가 오는지도
guard 가 다시 정한다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from fractions import Fraction
from typing import Iterator, Mapping

from dashboard.agent import templates as T
from dashboard.agent.intent import INTENTS
from dashboard.agent.inventory import Evidence, Gap, Inventory, axes_in, gap_by_id, gaps_for
from dashboard.agent.redteam import STATUSES, Attack, attacks
from dashboard.agent.slots import cites_of, fill
from dashboard.explain import AXIS_MEANING, AXIS_NOT
from sector.scoring import AXES

__all__ = [
    "CARD_KINDS", "SLOT_NAMES", "DISCLAIMER_TEXT", "FIXED_TEXTS", "HEADLINE_FIXED",
    "Sentence", "Card", "Brief", "compose",
    "CAUSE_TEXT", "NO_SCORE_TEXT", "NO_SCORE_TRUST", "NEXT_KPI", "LIMIT_DEGRADED", "LIMIT_ONE_ETF",
    "LIMIT_ILLIQUID", "LIMIT_NONE", "STAB_MEANING", "STAB_COUNTER", "STAB_LIMIT",
    "STAB_SHORT_LIMIT", "STAB_NEXT", "CHANGE_COUNTER", "CHANGE_LIMIT", "CONTENTS_DEF",
    "CONTENTS_MEANING", "CONTENTS_CAUSE", "CONTENTS_COUNTER", "CONTENTS_LIMIT", "CONTENTS_NEXT",
    "STABILITY_TITLE", "CONTENTS_TITLE", "EXTRA_CITES",
]

_NEEDS_AXIS = ("axis_plain", "raw_missing", "contrib", "z_change")
CARD_KINDS: tuple[str, ...] = ("axis", "stability", "change", "contents")

SLOT_NAMES: tuple[str, ...] = ("관찰 사실", "의미", "가능한 원인 가설", "반대 해석", "한계", "다음 확인 지표")

#: 🔒 `explain.narrative` 의 마지막 문장과 **같은 글자**다. 화면이 어디서 그려도 같은 말로 끝난다.
DISCLAIMER_TEXT = ("이것은 **지나간 데이터를 정해진 규칙으로 요약한 것**이다. "
                   "앞으로 오른다는 뜻이 아니고, 무엇을 사라는 뜻도 아니다.")

CAUSE_TEXT = "왜 그렇게 움직였는지는 이 데이터로 말할 수 없다 — 원인은 조원이 근거 붙이기로 단다."
NO_SCORE_TEXT = "점수를 낼 수 없어 서술하지 않는다 — 아래 빈칸 목록이 이유다."
NO_SCORE_TRUST = "점수를 낼 수 없어 반론을 세우지 않는다 — 아래 빈칸 목록이 이유다."

NEXT_KPI: Mapping[str, str] = {
    "M": "다음에 볼 것 — 수익률이 시장 평균보다 앞선 채로 남는가.",
    "F": "다음에 볼 것 — 상장좌수가 다음 창에서도 느는가.",
    "B": "다음에 볼 것 — 오름세 종목 비율이 시장보다 계속 많은가.",
    "V": "다음에 볼 것 — 지수가 긴 평균에서 더 멀어지는가.",
}

LIMIT_DEGRADED = "이 축은 척도를 한 단 내렸다 — 섹터들이 거의 같은 값이라 작은 차이가 크게 보일 수 있다."
LIMIT_ONE_ETF = "이 섹터의 ETF 가 하나뿐이라 자금흐름 점수가 그 한 종목에 통째로 달려 있다."
LIMIT_ILLIQUID = "ETF 거래대금이 적어 ETF 로는 실제로 담기 어렵다 — 구성종목을 본다."
LIMIT_NONE = "이 축에 따로 알려진 한계는 없다 — 그래도 지나간 데이터의 요약이다."

STAB_MEANING = "하루치 순위와 여러 날의 순위를 가른다 — 대회 운용은 하루가 아니라 석 달이다."
STAB_COUNTER = "지금까지 꾸준했다고 앞으로도 꾸준하다는 뜻은 아니다."
STAB_LIMIT = "평균과 진폭만 본다 — 순위가 어느 쪽으로 움직였는지는 담지 않는다."
STAB_SHORT_LIMIT = "요청한 창보다 짧은 이력으로 냈다 — 표본이 적다."
STAB_NEXT = "다음에 볼 것 — 다음 주에도 순위가 같은 자리 근처에 남는가."

CHANGE_COUNTER = "순위와 σ 는 섹터들 사이의 상대 위치다 — 이 섹터가 그대로여도 다른 섹터가 움직이면 바뀐다."
CHANGE_LIMIT = "두 기준일의 값만 견준다 — 그 사이의 오르내림은 담지 않는다."

CONTENTS_DEF = ("구성은 사람이 sectors.yaml 에 적었고 원천 유니버스와 대조했다 — "
                "ETF 의 실제 구성 내역은 이 도구의 원천에 없다.")
CONTENTS_MEANING = "같은 섹터를 ETF 렌즈와 구성종목 렌즈 둘로 본다 — 둘이 갈라지면 갈라진 채로 보여준다."
CONTENTS_CAUSE = "왜 이렇게 묶었는지는 아래 '사람이 쓴 근거' 가 말한다."
CONTENTS_COUNTER = "사람이 고른 묶음이다 — 다른 사람은 다르게 묶을 수 있다."
CONTENTS_LIMIT = "ETF 가 실제로 무엇을 담았는지는 운용사 공시로 확인한다 — 이 도구는 그 원천을 부르지 않는다."
CONTENTS_NEXT = "다음에 볼 것 — 구성종목 중 거래가 멈추거나 이름이 바뀐 종목이 생기는가."

STABILITY_TITLE = "순위 안정성"
CONTENTS_TITLE = "이 섹터를 묶은 방식"

#: 🔒 `fixed` 문장이 될 수 있는 **유일한** 글자들.
FIXED_TEXTS: frozenset[str] = frozenset({
    CAUSE_TEXT, NO_SCORE_TEXT, NO_SCORE_TRUST, *NEXT_KPI.values(),
    LIMIT_DEGRADED, LIMIT_ONE_ETF, LIMIT_ILLIQUID, LIMIT_NONE,
    STAB_MEANING, STAB_COUNTER, STAB_LIMIT, STAB_SHORT_LIMIT, STAB_NEXT,
    CHANGE_COUNTER, CHANGE_LIMIT,
    CONTENTS_DEF, CONTENTS_MEANING, CONTENTS_CAUSE, CONTENTS_COUNTER, CONTENTS_LIMIT, CONTENTS_NEXT,
    *AXIS_MEANING.values(), *AXIS_NOT.values(),
})

#: 결론 칸에 올 수 있는 상수
HEADLINE_FIXED: Mapping[str, tuple[str, ...]] = {
    "why_rank": (NO_SCORE_TEXT,), "trust": (NO_SCORE_TRUST,), "changed": (), "contents": (CONTENTS_DEF,),
}


def EXTRA_CITES(key: str, axis: str | None = None) -> tuple[str, ...]:  # noqa: N802 — 표처럼 읽힌다
    """자리 밖에서 기대는 근거 — 🔒 숫자가 문장에 없어도 판정의 재료는 근거로 단다."""
    if key == "no_score":
        return ("EV-RANK", "EV-SCORE")
    if key in ("axis_plain", "raw_missing"):
        return (f"EV-{axis}-RAW",)
    if key == "stab_missing":
        return ("EV-STAB-MEAN", "EV-STAB-SPREAD")
    if key == "no_new_day":
        return ("EV-WINDOW",)
    if key == "rank_missing":
        return ("EV-RANK", "EV-PAST-RANK")
    if key == "rank_change":
        return ("EV-RANK-DIFF",)
    if key == "z_same":
        return tuple(f"EV-{a}-Z" for a in AXES) + tuple(f"EV-PAST-{a}-Z" for a in AXES)
    if key == "etf_unknown":
        return ("EV-ETF-N",)
    if key == "liquidity":
        return ("EV-LIQ",)
    return ()


@dataclass(frozen=True, slots=True)
class Sentence:
    key: str
    template: str
    text: str
    cites: tuple[str, ...] = ()
    axis: str | None = None

    def __post_init__(self) -> None:
        if self.key not in T.ROLE_OF:
            raise ValueError(f"모르는 문장 열쇠: {self.key}")
        if self.role in _NEEDS_AXIS and self.axis not in AXES:
            raise ValueError(f"{self.key} 문장은 축을 달아야 한다")
        if self.role in ("fixed", "disclaimer"):
            if cites_of(self.template) or self.template != self.text:
                raise ValueError("고정 문장에는 자리가 없고 템플릿과 글자가 같다")
        elif not self.cites:
            raise ValueError("근거를 달지 않은 문장은 만들지 않는다")

    @property
    def role(self) -> str:
        return T.ROLE_OF[self.key]


@dataclass(frozen=True, slots=True)
class Card:
    """해석 카드 — 여섯 칸."""

    kind: str
    title: str
    target: str
    observed: tuple[Sentence, ...]
    meaning: tuple[Sentence, ...]
    cause: tuple[Sentence, ...]
    counter: tuple[Sentence, ...]
    limits: tuple[Sentence, ...]
    next_kpi: tuple[Sentence, ...]
    axis: str | None = None

    def __post_init__(self) -> None:
        if self.kind not in CARD_KINDS:
            raise ValueError(f"모르는 카드 종류: {self.kind}")

    def slots(self) -> tuple[tuple[str, tuple[Sentence, ...]], ...]:
        return tuple(zip(SLOT_NAMES, (self.observed, self.meaning, self.cause, self.counter,
                                      self.limits, self.next_kpi), strict=True))


@dataclass(frozen=True, slots=True)
class Brief:
    intent: str
    sector_id: str
    label: str
    profile: str
    as_of: str
    headline: tuple[Sentence, ...]
    cards: tuple[Card, ...] = ()
    attacks: tuple[Attack, ...] = ()
    listing: tuple[Sentence, ...] = ()
    gaps: tuple[Gap, ...] = ()
    evidence: Mapping[str, Evidence] = field(default_factory=dict)
    followups: tuple[str, ...] = ()
    past_as_of: str | None = None
    window_days: int | None = None
    since_confirm: bool = False
    window_source: str = ""
    disclaimer: Sentence = Sentence("disclaimer", DISCLAIMER_TEXT, DISCLAIMER_TEXT)

    def labelled(self) -> Iterator[tuple[str, Sentence]]:
        """화면에 나가는 순서대로 — 🔒 면책이 **맨 끝**이다(구조가 그렇게 만든다)."""
        for index, sentence in enumerate(self.headline, 1):
            yield f"결론 {index}", sentence
        for card in self.cards:
            for slot_name, sentences in card.slots():
                for sentence in sentences:
                    yield f"카드 '{card.title}' · {slot_name}", sentence
        for sentence in self.listing:
            yield "목록", sentence
        yield "면책", self.disclaimer

    def with_gaps(self, gaps: tuple[Gap, ...]) -> "Brief":
        return Brief(intent=self.intent, sector_id=self.sector_id, label=self.label,
                     profile=self.profile, as_of=self.as_of, headline=self.headline,
                     cards=self.cards, attacks=self.attacks, listing=self.listing, gaps=gaps,
                     evidence=self.evidence, followups=self.followups,
                     past_as_of=self.past_as_of, window_days=self.window_days,
                     since_confirm=self.since_confirm, window_source=self.window_source,
                     disclaimer=self.disclaimer)

    def to_dict(self) -> dict:
        """골든 스냅샷의 모양. 🔒 여기 한 곳에서만 정한다."""
        return {
            "intent": self.intent, "sector_id": self.sector_id, "label": self.label,
            "profile": self.profile, "as_of": self.as_of, "past_as_of": self.past_as_of,
            "text": [f"[{where}] {s.text}" + (f"  ⟨{' '.join(s.cites)}⟩" if s.cites else "")
                     for where, s in self.labelled()],
            "attacks": [[a.id, a.case, a.target, a.status, a.defense, a.rule, list(a.cites), a.risk,
                         a.gap] for a in self.attacks],
            "gaps": [[g.id, g.kind, g.state, g.item, g.why] for g in self.gaps],
            "evidence": {k: [e.shown(), e.source, e.confidence] for k, e in self.evidence.items()},
            "followups": list(self.followups),
        }


class _Writer:
    """🔒 숫자는 자리로만 넣고, 글자는 장부 값으로 채운다."""

    def __init__(self, inventory: Inventory, extra: Mapping[str, Evidence] | None = None) -> None:
        book = dict(inventory.evidence)
        book.update(extra or {})
        self.values = {key: item.value for key, item in book.items()}
        self.label = inventory.label

    def say(self, key: str, template: str, axis: str | None = None) -> Sentence:
        return Sentence(key=key, template=template, text=fill(template, self.values),
                        cites=tuple(dict.fromkeys(cites_of(template) + EXTRA_CITES(key, axis))),
                        axis=axis)


def _fixed(text: str, axis: str | None = None) -> Sentence:
    return Sentence(key="fixed", template=text, text=text, axis=axis)


# ── 질문별 조립 ─────────────────────────────────────────────────────────────

def _limits(inventory: Inventory, axis: str) -> tuple[Sentence, ...]:
    out: list[Sentence] = []
    if axis in axes_in(inventory.value("EV-DEGRADED")):
        out.append(_fixed(LIMIT_DEGRADED, axis))
    if axis == "F" and inventory.value("EV-ETF-N") == 1:
        out.append(_fixed(LIMIT_ONE_ETF))
    if inventory.value("EV-LIQ") is False:
        out.append(_fixed(LIMIT_ILLIQUID))
    return tuple(out) or (_fixed(LIMIT_NONE),)


def _axis_card(inventory: Inventory, writer: _Writer, axis: str) -> Card:
    raw = inventory.value(f"EV-{axis}-RAW")
    contribution = inventory.value(f"EV-{axis}-CONTRIB")
    plain = (writer.say("raw_missing", T.RAW_MISSING, axis) if raw is None
             else writer.say("axis_plain", T.axis_plain(axis, raw), axis))
    share = writer.say("contrib", T.contrib(axis, contribution > 0), axis)
    return Card(
        kind="axis", title=T.axis_title(axis, contribution > 0), target=f"TH-{axis}", axis=axis,
        observed=(plain, share),
        meaning=(_fixed(AXIS_MEANING[axis], axis),),
        cause=(_fixed(CAUSE_TEXT),),
        counter=(_fixed(AXIS_NOT[axis], axis),),
        limits=_limits(inventory, axis),
        next_kpi=(_fixed(NEXT_KPI[axis], axis),))


def _stability_card(inventory: Inventory, writer: _Writer) -> Card:
    mean, spread = inventory.value("EV-STAB-MEAN"), inventory.value("EV-STAB-SPREAD")
    observed = (writer.say("stab_missing", T.STAB_MISSING) if mean is None or spread is None
                else writer.say("stability", T.stability(Fraction(spread))))
    requested = inventory.evidence["EV-STAB-DAYS"].origin[2]
    short = inventory.value("EV-STAB-DAYS") < requested
    return Card(kind="stability", title=STABILITY_TITLE, target="TH-RANK", observed=(observed,),
                meaning=(_fixed(STAB_MEANING),), cause=(_fixed(CAUSE_TEXT),),
                counter=(_fixed(STAB_COUNTER),),
                limits=(_fixed(STAB_LIMIT),) + ((_fixed(STAB_SHORT_LIMIT),) if short else ()),
                next_kpi=(_fixed(STAB_NEXT),))


def _why(inventory: Inventory, writer: _Writer) -> tuple[tuple[Sentence, ...], tuple[Card, ...]]:
    score = inventory.value("EV-SCORE")
    if inventory.value("EV-RANK") is None or score is None:
        return (writer.say("no_score", T.no_score(inventory.label)), _fixed(NO_SCORE_TEXT)), ()
    headline = (writer.say("rank_headline", T.rank_headline(inventory.label)),
                writer.say("sigma", T.sigma(score)))
    scored = [(axis, inventory.value(f"EV-{axis}-CONTRIB")) for axis in AXES
              if inventory.value(f"EV-{axis}-CONTRIB") is not None]
    cards: list[Card] = []
    if scored:
        # 🔒 `explain.narrative` 와 같은 고름 — 같으면 축 순서가 앞선 것
        best = max(scored, key=lambda pair: pair[1])
        worst = min(scored, key=lambda pair: pair[1])
        if best[1] > 0:
            cards.append(_axis_card(inventory, writer, best[0]))
        if worst[1] < 0 and worst[0] != best[0]:
            cards.append(_axis_card(inventory, writer, worst[0]))
    cards.append(_stability_card(inventory, writer))
    return headline, tuple(cards)


def _trust(inventory: Inventory) -> tuple[tuple[Sentence, ...], tuple[Attack, ...],
                                          dict[str, Evidence], tuple[Gap, ...]]:
    if inventory.value("EV-RANK") is None:
        writer = _Writer(inventory)
        return (writer.say("no_score", T.no_score(inventory.label)), _fixed(NO_SCORE_TRUST)), (), {}, ()
    found = attacks(inventory)
    counts = {status: sum(1 for a in found if a.status == status) for status in STATUSES}
    extra = {
        "EV-AT-TOTAL": Evidence("EV-AT-TOTAL", "세운 반론 수", len(found), "int", "이 답", "상",
                                ("brief", "attacks", "전체")),
        "EV-AT-PASS": Evidence("EV-AT-PASS", "데이터가 막은 반론", counts["통과"], "int", "이 답",
                               "상", ("brief", "attacks", "통과")),
        "EV-AT-WEAK": Evidence("EV-AT-WEAK", "데이터가 못 막은 반론", counts["취약"], "int",
                               "이 답", "상", ("brief", "attacks", "취약")),
        "EV-AT-OPEN": Evidence("EV-AT-OPEN", "더 봐야 하는 반론", counts["추가확인"], "int",
                               "이 답", "상", ("brief", "attacks", "추가확인")),
    }
    writer = _Writer(inventory, extra)
    headline = (writer.say("trust_rank", T.trust_rank(inventory.label)),
                writer.say("trust_counts", T.TRUST_COUNTS))
    # 🔴 [추가확인] 이 가리킨 빈칸은 빈칸 목록에 **반드시** 있다 — 없으면 브리프 전체가 숨었다(리뷰 O5)
    open_gaps = tuple(gap_by_id(a.gap) for a in found if a.gap)
    return headline, found, extra, open_gaps


def _changed(inventory: Inventory, writer: _Writer, since_confirm: bool,
             window_source: str) -> tuple[tuple[Sentence, ...], tuple[Card, ...]]:
    if inventory.past_as_of is None:
        return (writer.say("no_past", T.no_past(inventory.label)),), ()
    lead: list[Sentence] = []
    if since_confirm:
        if inventory.value("EV-WINDOW") == 0:
            return (writer.say("no_new_day", T.NO_NEW_DAY),), ()
        lead.append(writer.say("since", T.SINCE))
    elif window_source == "기본":
        # 🔒 알아서 고른 창은 **고른 사실을 말한다** (리뷰 R3)
        lead.append(writer.say("default_window", T.DEFAULT_WINDOW))

    now_rank, past_rank = inventory.value("EV-RANK"), inventory.value("EV-PAST-RANK")
    if now_rank is None or past_rank is None:
        lead.append(writer.say("rank_missing", T.RANK_MISSING))
    else:
        move = "up" if past_rank > now_rank else "down" if past_rank < now_rank else "same"
        lead.append(writer.say("rank_change", T.rank_change(inventory.label, move)))

    moves = []
    for axis in AXES:
        current, before = inventory.value(f"EV-{axis}-Z"), inventory.value(f"EV-PAST-{axis}-Z")
        if current is not None and before is not None and current != before:
            moves.append((axis, before, current))
    moves.sort(key=lambda m: (-abs(m[2] - m[1]), AXES.index(m[0])))
    cards: list[Card] = []
    for axis, before, current in moves[:2]:
        rising = current > before
        cards.append(Card(
            kind="change", title=T.change_title(axis, rising), target=f"TH-{axis}", axis=axis,
            observed=(writer.say("z_change", T.z_change(axis, rising), axis),),
            meaning=(_fixed(AXIS_MEANING[axis], axis),),
            cause=(_fixed(CAUSE_TEXT),),
            counter=(_fixed(CHANGE_COUNTER),),
            limits=(_fixed(CHANGE_LIMIT),),
            next_kpi=(_fixed(NEXT_KPI[axis], axis),)))
    if not moves:
        lead.append(writer.say("z_same", T.Z_SAME))
    return tuple(lead), tuple(cards)


def _contents(inventory: Inventory, writer: _Writer) -> tuple[tuple[Sentence, ...], tuple[Card, ...],
                                                               tuple[Sentence, ...]]:
    sector = inventory.sector
    if sector is None:
        return (writer.say("no_master", T.no_master(inventory.label)),), (), ()
    headline = (writer.say("contents_head", T.contents_head(inventory.label)), _fixed(CONTENTS_DEF))
    listing = tuple(writer.say("etf_line", T.etf_line(i)) for i in range(1, len(sector.etfs) + 1)) \
        + tuple(writer.say("member_line", T.member_line(i)) for i in range(1, len(sector.members) + 1))
    etf_n = inventory.value("EV-ETF-N")
    count = (writer.say("etf_unknown", T.ETF_UNKNOWN) if etf_n is None
             else writer.say("etf_count", T.ETF_COUNT))
    liquidity = writer.say("liquidity", T.liquidity(inventory.value("EV-LIQ")))
    card = Card(kind="contents", title=CONTENTS_TITLE, target="TH-DEF", observed=(count, liquidity),
                meaning=(_fixed(CONTENTS_MEANING),), cause=(_fixed(CONTENTS_CAUSE),),
                counter=(_fixed(CONTENTS_COUNTER),),
                limits=(_fixed(CONTENTS_LIMIT),) + ((_fixed(LIMIT_ONE_ETF),) if etf_n == 1 else ()),
                next_kpi=(_fixed(CONTENTS_NEXT),))
    return headline, (card,), listing


def compose(inventory: Inventory, intent: str, *, since_confirm: bool = False,
            window_days: int | None = None, window_source: str = "",
            extra_gaps: tuple[Gap, ...] = ()) -> Brief:
    """🔒 순수 함수다. 같은 장부 · 같은 의도면 같은 브리프다."""
    if intent not in INTENTS:
        raise ValueError(f"모르는 의도: {intent}")
    evidence = dict(inventory.evidence)
    writer = _Writer(inventory)
    found: tuple[Attack, ...] = ()
    cards: tuple[Card, ...] = ()
    listing: tuple[Sentence, ...] = ()
    open_gaps: tuple[Gap, ...] = ()
    if intent == "why_rank":
        headline, cards = _why(inventory, writer)
    elif intent == "trust":
        headline, found, extra, open_gaps = _trust(inventory)
        evidence.update(extra)
    elif intent == "changed":
        headline, cards = _changed(inventory, writer, since_confirm, window_source)
    else:
        headline, cards, listing = _contents(inventory, writer)

    gaps: list[Gap] = []
    for gap in (*extra_gaps, *gaps_for(inventory, intent), *open_gaps):
        if all(g.id != gap.id for g in gaps):
            gaps.append(gap)
    changed = intent == "changed"
    return Brief(intent=intent, sector_id=inventory.sector_id, label=inventory.label,
                 profile=inventory.profile, as_of=inventory.as_of, headline=headline,
                 cards=cards, attacks=found, listing=listing, gaps=tuple(gaps),
                 evidence=evidence, followups=tuple(i for i in INTENTS if i != intent),
                 past_as_of=inventory.past_as_of,
                 window_days=window_days if changed else None,
                 since_confirm=since_confirm if changed else False,
                 window_source=window_source if changed else "")
