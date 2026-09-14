"""guard — **출력의 모든 숫자와 방향이 원천에 실재하는지** 대조한다.

## 🔴 왜 LLM 이 없는데 guard 가 필요한가

템플릿도 틀린다. 밸류 축은 정의가 `−(지수/120일평균 − 1)` 이라 **양수가 "평균보다 아래"** 인데,
그것을 거꾸로 적을 뻔한 일이 실제로 있었다(`explain.axis_plain` 머리주석). 첫 연기 시험에서는
guard 가 내 템플릿 실수(근거 없이 적은 반론 수)를 실제로 잡았다(ADR-SC-0013 Z8). 환각 방어를 모델
신뢰가 아니라 **구조**로 만든다 — 계획서 부록 C 의 요점이 이 파일이다.

## 🔒 세 겹이다

1. **장부 ↔ 원천** — 장부의 모든 값을 `origin` 대로 **점수 표 · 섹터 정의 · 원장에서 따로 다시 얻어**
   대조한다. `view` 를 거치지 않는다. 그리고 **이 질문 · 이 섹터라면 장부에 있어야 할 근거 ID** 를 원천으로
   다시 정해, 빠진 것도 어긋난 것으로 센다 — 근거를 지우면 그것에 기대던 한계와 반론이 함께 사라졌었다(재검증 R-B).
2. **구성 ↔ 원천** — 결론 · 카드(제목 · 관찰 · 여섯 칸 상수) · 목록 · 반론 일곱 · 빈칸 목록 · 비교 기준일을
   guard 가 원천 값으로 **따로 다시 정해 글자 그대로** 대조한다. 문장 틀의 글자는 `templates` 를 같이 쓰지만
   **어느 틀을 고를지**와 **틀 안 자리의 순서**는 guard 가 따로 가진다 — 같은 단위의 다른 근거로 바꿔치기 ·
   두 기준일 맞바꾸기 · 목록 밖 산문 · 문장 하나 더 끼우기가 여기서 걸린다(재검증 R-A · 리뷰 O3 · O4).
3. **문장 ↔ 원천** — 자리를 원천 값으로 다시 채워 글자까지 같아야 하고, 자리 밖에는 숫자가 없어야 하며
   (유니코드 숫자 포함), 자리마다 허락된 모양 · 뒤따르는 단위가 맞아야 한다(`slots` · 리뷰 R2). 방향 낱말은
   **역할과 상관없이** 찾아 그 낱말을 검사하는 역할에만 허락하고, 한 문장에 **한 번만** 나와야 하며, 원천
   부호와 맞아야 한다(리뷰 O1 · O2 · 재검증 R-G).

🔒 **통과하지 못하면 문장을 그리지 않는다** — 화면은 숫자 칸만 남기고 이유를 말한다(`evidence.py`).
🔒 새 문장 열쇠 · 방향 낱말 · 근거 자리를 더하면 `_headline_expected` · `_observed_expected` · `_ROLE_WORDS` ·
   `_SLOT_SHAPES` 도 함께 더한다. 표에 없는 것이 나오면 guard 가 거부한다 — 잊으면 드러난다.
"""

from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import dataclass
from fractions import Fraction
from typing import Any, Callable, Mapping

from dashboard.agent import compose as C
from dashboard.agent import ids
from dashboard.agent import redteam as R
from dashboard.agent import templates as T
from dashboard.agent.inventory import (
    EOK_WON,
    Evidence,
    Gap,
    axes_in,
    gap_axis,
    gap_by_id,
    gap_preset_from,
    gap_yaml_liquidity_from,
    kst_date,
)
from dashboard.agent.slots import SlotError, cites_of, fill, literal_of, slots_in
from dashboard.explain import AXIS_MEANING, AXIS_NOT
from sector.scoring import (
    AXES,
    LIQUIDITY_MIN_WON,
    LOOKBACK_LONG,
    LOOKBACK_SHORT,
    PRESETS,
    VALUE_WINDOW,
)

__all__ = ["BANNED", "verify"]

#: 🔴 쓰지 않는 표현 — "무엇 대비 얼마나" 를 말하지 않는 낱말 + 매매 · 예측 · 권유 어휘.
#: 🔒 면책 문장("무엇을 사라는 뜻도 아니다")만 예외다 — 그 문장은 글자 대조로 따로 지킨다.
BANNED: tuple[tuple[str, re.Pattern[str]], ...] = tuple((why, re.compile(pattern)) for why, pattern in (
    ("모호한 크기", r"크게\s*(?:증가|늘|올|개선)|대폭|큰\s*폭"),
    ("모호한 평가", r"매력적|압도적|견조|경쟁이\s*심화|저평가|고평가"),
    ("근거 없는 기대", r"수혜|성장\s*가능성|리스크\s*(?:가\s*)?존재|(?:상승|반등)\s*(?:여력|가능성)|기회다|놓치면"),
    ("권유", r"추천|유망|매수\s*(?:를\s*)?(?:권|하라|해라|하자|할\s*만|하세요)|매도\s*(?:를\s*)?(?:권|하라|해라|하자|하세요)"),
    ("권유", r"사야|사라(?!는)|사세요|담아(?:라|야)|담으세요|담을\s*만|비중\s*확대"),
    ("예측", r"오를\s*(?:것|거|수)|상승할|하락할|떨어질\s*(?:것|거)|전망|목표\s*주?가|앞으로\s*오른다(?!는)"),
    ("단정", r"확실|보장|호재|악재"),
    ("판정", r"Proceed|Watch|Drop"),
))

#: 🔴 빈 값이 글자가 된 흔적 — "None개" 가 통과했었다(리뷰 O6)
_LEAK = re.compile(r"None|\bnan\b|NaN|\bnull\b|<NA>")
#: 숫자 — 🔒 규칙 번호처럼 영문자에 붙은 숫자("V5" · "M4")는 치지 않는다
_DIGIT = re.compile(r"(?<![A-Za-z])[0-9]")
_APPROX = Fraction(1, 20) + Fraction(1, 10**9)   # 소수 첫째 자리 표기의 반올림 폭

#: 🔴 자리마다 허락된 모양과 **뒤따르는 단위** — "11개 섹터 중 21위" 처럼 자리를 맞바꾼 템플릿을 잡는다.
_SLOT_SHAPES: tuple[tuple[re.Pattern[str], tuple[str, ...], re.Pattern[str] | None], ...] = tuple(
    (re.compile(pattern), formats, re.compile(suffix) if suffix else None)
    for pattern, formats, suffix in (
        (r"EV-RANK-DIFF", ("int",), r"계단"),
        (r"EV-TOTAL", ("int",), r"개 (?:섹터|중)"),
        (r"EV-(?:PAST-)?RANK|EV-RANK-(?:BALANCED|MOMENTUM|CONTRARIAN)|EV-[MFBV]-RANK", ("int",),
         r"(?:\*\*)?위"),
        (r"EV-STAB-MEAN", ("dec1",), r"(?:\*\*)?위"),
        (r"EV-STAB-SPREAD", ("dec1",), r" 다"),
        (r"EV-STAB-DAYS|EV-WINDOW", ("int",), r"영업일"),
        (r"EV-RULE-SHORT", ("int",), r"영업일|·"),
        (r"EV-RULE-LONG|EV-RULE-VALUE", ("int",), r"일"),
        (r"EV-RULE-LIQ", ("int",), r"억"),
        (r"EV-RULE-SIGMA2", ("int",), r"σ"),
        (r"EV-SCORE|EV-(?:PAST-)?[MFBV]-Z", ("sigma",), r"σ"),
        (r"EV-M-RAW", ("pct2",), r"%p"),
        (r"EV-B-RAW", ("pct1",), r"%p"),
        (r"EV-[FV]-RAW", ("pct2",), r"%(?!p)"),
        (r"EV-[MFBV]-CONTRIB", ("signed",), r"\*\* 만큼| ·|\.$"),
        (r"EV-ETF-N|EV-ETF-COUNT|EV-MEMBER-COUNT|EV-AXES-USED|EV-AT-(?:TOTAL|PASS|WEAK|OPEN)", ("int",),
         r"(?:\*\*)?개"),
        (r"EV-(?:PAST-)?ASOF|EV-CONFIRMED-DATE", ("date",), r"\)"),
        (r"EV-LABEL|EV-GICS|EV-ETF-[0-9]+|EV-MEMBER-[0-9]+", ("text",), None),
    ))


class _Unreachable(Exception):
    """원천에서 다시 얻지 못했다."""


# ── 원천 ────────────────────────────────────────────────────────────────────

def _native(value: Any) -> Any:
    import pandas as pd

    if value is None:
        return None
    if not isinstance(value, str) and pd.isna(value):
        return None
    if hasattr(value, "item"):
        value = value.item()
    if isinstance(value, bool):
        return value
    if isinstance(value, float):
        if value != int(value):
            raise _Unreachable(f"정수 칸에 소수 {value} 가 있다")
        return int(value)
    return value


class _Source:
    """원천을 한 번씩만 훑는다. 🔒 `view` 를 쓰지 않는다(머리주석 1)."""

    def __init__(self, frame: Any, brief: C.Brief, master: Any, workspace: Any,
                 team_id: str | None) -> None:
        self.frame = frame
        self.brief = brief
        self.master = master
        self.workspace = workspace
        self.team_id = team_id
        self.days = sorted({str(d) for d in frame["bas_dd"]})
        self._rows: dict[str, Any] = {}
        self.sector = None if master is None else next(
            (s for s in master.sectors if s.id == brief.sector_id), None)

    def day(self, as_of: str) -> Any:
        if as_of not in self._rows:
            self._rows[as_of] = self.frame[self.frame["bas_dd"].astype(str) == as_of]
        return self._rows[as_of]

    def cell(self, as_of: str | None, column: str) -> Any:
        if as_of is None:
            raise _Unreachable(f"{column}: 기준일이 없다")
        rows = self.day(as_of)
        rows = rows[rows["sector_id"] == self.brief.sector_id]
        if len(rows) != 1:
            raise _Unreachable(f"{as_of} 의 {self.brief.sector_id} 행이 {len(rows)}개다")
        if column not in rows.columns:
            raise _Unreachable(f"열 {column} 이 없다")
        return _native(rows.iloc[0][column])

    def safe(self, as_of: str | None, column: str) -> Any:
        try:
            return self.cell(as_of, column)
        except _Unreachable:
            return None

    def need_sector(self) -> Any:
        if self.sector is None:
            raise _Unreachable("섹터 정의에 이 섹터가 없다")
        return self.sector

    def team(self) -> Any:
        return None if (self.workspace is None or not self.team_id) else self.workspace.team(self.team_id)

    def label(self) -> str:
        return self.sector.name_ko if self.sector is not None else self.brief.sector_id


@dataclass(frozen=True, slots=True)
class _Ctx:
    brief: C.Brief
    src: _Source
    values: Mapping[str, Any]    # 원천과 맞은 근거의 값 — 자리를 채운다
    truth: Mapping[str, Any]     # 견줄 값 — 소수 첫째 자리 글자는 Fraction 으로

    @property
    def label(self) -> str:
        return self.src.label()


# ── 층 1 — 장부 ↔ 원천 ──────────────────────────────────────────────────────

def _truth(item: Evidence, src: _Source) -> Any:
    """`origin` 대로 다시 얻는다. 🔒 소수 첫째 자리 값은 `("≈", Fraction)` 으로 돌려준다."""
    kind, *rest = item.origin
    brief = src.brief
    if kind == "label":
        return src.label()
    if kind == "column":
        value = src.cell(item.as_of, rest[0])
        return str(value) if item.unit in ("date", "text") and value is not None else value
    if kind == "axes":
        return "".join(axes_in(src.cell(item.as_of, rest[0])))
    if kind == "rule":
        name = rest[0]
        if name == "weight":
            return PRESETS[rest[1]][rest[2]]
        table = {"liquidity_eok": LIQUIDITY_MIN_WON // EOK_WON, "short": LOOKBACK_SHORT,
                 "long": LOOKBACK_LONG, "value": VALUE_WINDOW, "sigma2": 2}
        if name not in table:
            raise _Unreachable(f"모르는 규칙 {name}")
        return table[name]
    if kind == "derived":
        return _derived(item, rest, src)
    if kind == "config":
        sector = src.need_sector()
        name = rest[0]
        if name == "gics":
            found = next((g for g in src.master.gics_sectors if g.id == sector.gics), None)
            return found.name_ko if found is not None else sector.gics
        if name == "note":
            return sector.note
        if name == "liquidity_warning":
            return bool(sector.liquidity_warning)
        if name == "etf_count":
            return len(sector.etfs)
        if name == "member_count":
            return len(sector.members)
        if name in ("etf", "member"):
            pool = sector.etfs if name == "etf" else sector.members
            index = rest[1]
            if not 1 <= index <= len(pool):
                raise _Unreachable(f"{name} {index} 번이 정의에 없다")
            return f"{pool[index - 1].name} ({pool[index - 1].code})"
        raise _Unreachable(f"모르는 정의 칸 {name}")
    if kind == "ledger":
        if rest[0] == "comments":
            if src.workspace is None or not src.team_id:
                raise _Unreachable("조 원장을 읽지 않았다")
            return len(src.workspace.comments_of(src.team_id, sector_id=brief.sector_id))
        if rest[0] == "confirmed_kst_date":
            team = src.team()
            if team is None or not team.confirmed_at:
                raise _Unreachable("그 조는 확정하지 않았다")
            return kst_date(team.confirmed_at)
        raise _Unreachable(f"모르는 원장 칸 {rest[0]}")
    if kind == "brief":
        # 🔒 반론 개수는 브리프가 스스로 센다 — 대신 반론 하나하나를 guard 가 원천으로 다시 매긴다
        status = rest[1]
        if status == "전체":
            return len(brief.attacks)
        if status not in R.STATUSES:
            raise _Unreachable(f"모르는 상태 {status}")
        return sum(1 for a in brief.attacks if a.status == status)
    raise _Unreachable(f"모르는 출처 종류 {kind}")


def _derived(item: Evidence, rest: list[Any], src: _Source) -> Any:
    brief = src.brief
    name = rest[0]
    if name == "total":
        return len(src.day(item.as_of))
    if name == "contrib":
        profile, axis = rest[1], rest[2]
        weights = PRESETS[profile]
        zs = {a: src.cell(item.as_of, f"{a.lower()}_z_bp") for a in AXES}
        if zs[axis] is None:
            return None
        live = sum(weights[a] for a in AXES if zs[a] is not None) or 1
        return round(Fraction(zs[axis] * weights[axis], live))
    if name == "axis_rank":
        column = f"{rest[1].lower()}_z_bp"
        mine = src.cell(item.as_of, column)
        if mine is None:
            return None
        others = [v for v in (_native(x) for x in src.day(item.as_of)[column]) if v is not None]
        return sum(1 for z in others if z > mine) + 1
    if name in ("stab_days", "stab_mean", "stab_spread"):
        days = rest[-1]
        recent = src.days[-days:]
        if name == "stab_days":
            return len(recent)
        frame = src.frame
        rows = frame[(frame["sector_id"] == brief.sector_id) & frame["bas_dd"].astype(str).isin(recent)]
        ranks = [v for v in (_native(x) for x in rows[f"rank_{rest[1]}"]) if v is not None]
        if len(ranks) < len(recent) or not ranks:
            return ("≈", None)
        mean = Fraction(sum(ranks), len(ranks))
        if name == "stab_mean":
            return ("≈", mean)
        variance = sum((Fraction(r) - mean) ** 2 for r in ranks) / len(ranks)
        return ("≈", Fraction(math.sqrt(variance)))
    if name == "window":
        if brief.as_of not in src.days or item.as_of not in src.days:
            raise _Unreachable("비교 기준일이 표에 없다")
        return src.days.index(brief.as_of) - src.days.index(item.as_of)
    if name == "rank_diff":
        now = src.cell(brief.as_of, f"rank_{brief.profile}")
        before = src.cell(brief.past_as_of, f"rank_{brief.profile}")
        if now is None or before is None:
            return None
        return abs(before - now)
    raise _Unreachable(f"모르는 파생 {name}")


def _same(value: Any, truth: Any) -> bool:
    if isinstance(truth, tuple) and truth and truth[0] == "≈":
        if value is None or truth[1] is None:
            return value is None and truth[1] is None
        return abs(Fraction(str(value)) - truth[1]) <= _APPROX
    return value == truth and isinstance(value, bool) == isinstance(truth, bool)


def _layer_sources(brief: C.Brief, src: _Source) -> tuple[list[str], dict[str, Any], dict[str, Any]]:
    """(어긋난 것, 채울 값, 견줄 값). 🔒 원천과 맞은 근거만 두 표에 들어간다."""
    violations: list[str] = []
    values: dict[str, Any] = {}
    truth: dict[str, Any] = {}
    if not src.days or brief.as_of != src.days[-1]:
        violations.append(f"브리프 기준일 {brief.as_of} 가 점수 표의 마지막 날이 아니다")
    if brief.label != src.label():
        violations.append(f"섹터 이름 {brief.label!r} 이 정의({src.label()!r})와 다르다")
    for item in brief.evidence.values():
        if not ids.is_known(item.id):
            violations.append(f"{item.id}: 근거 ID 모양이 틀렸다")
            continue
        try:
            again = _truth(item, src)
        except _Unreachable as exc:
            violations.append(f"{item.id}: 원천에서 다시 얻지 못했다 — {exc}")
            continue
        if not _same(item.value, again):
            shown = again[1] if isinstance(again, tuple) else again
            violations.append(f"{item.id}: 장부 {item.value!r} ≠ 원천 {shown!r}")
            continue
        values[item.id] = item.value
        truth[item.id] = (Fraction(str(item.value)) if item.unit == "decimal1" and item.value
                          else item.value)
    return violations, values, truth


def _expected_past(ctx: _Ctx) -> str | None:
    """비교 기준일을 원천으로 다시 정한다. 🔒 가까운 날로 바꿔 끼우지 않는다."""
    brief, src = ctx.brief, ctx.src
    if brief.intent != "changed" or brief.as_of not in src.days:
        return None
    if brief.since_confirm:
        team = src.team()
        if team is None or not team.confirmed_at:
            return None
        earlier = [d for d in src.days if d < kst_date(team.confirmed_at)]
        past = earlier[-1] if earlier else None
    else:
        if brief.window_days is None:
            return None
        position = src.days.index(brief.as_of) - brief.window_days
        past = src.days[position] if position >= 0 else None
    if past is None:
        return None
    return past if (src.day(past)["sector_id"] == brief.sector_id).any() else None


def _expected_evidence(ctx: _Ctx) -> set[str]:
    """🔴 이 질문 · 이 섹터라면 장부에 **반드시** 있어야 할 근거 — 지우면 기대가 함께 사라졌었다(재검증 R-B)."""
    brief, src = ctx.brief, ctx.src
    needed = {"EV-LABEL", "EV-ASOF", "EV-CONFIG", "EV-TOTAL", "EV-RANK", "EV-SCORE", "EV-STAB-DAYS",
              "EV-STAB-MEAN", "EV-STAB-SPREAD", "EV-LIQ", "EV-ETF-N", "EV-AXES-USED", "EV-MISSING",
              "EV-DEGRADED", "EV-RULE-LIQ", "EV-RULE-SHORT", "EV-RULE-LONG", "EV-RULE-VALUE",
              "EV-RULE-SIGMA2"}
    needed |= {f"EV-RANK-{p.upper()}" for p in PRESETS}
    needed |= {f"EV-{a}-{k}" for a in AXES for k in ("RAW", "Z", "W", "CONTRIB", "RANK")}
    if src.sector is not None:
        needed |= {"EV-GICS", "EV-NOTE", "EV-YAML-LIQ", "EV-ETF-COUNT", "EV-MEMBER-COUNT"}
        needed |= {f"EV-ETF-{i}" for i in range(1, len(src.sector.etfs) + 1)}
        needed |= {f"EV-MEMBER-{i}" for i in range(1, len(src.sector.members) + 1)}
    if src.workspace is not None and src.team_id:
        needed.add("EV-COMMENTS")
    rank_now = src.safe(brief.as_of, f"rank_{brief.profile}")
    if brief.intent == "trust" and rank_now is not None:
        needed |= {"EV-AT-TOTAL", "EV-AT-PASS", "EV-AT-WEAK", "EV-AT-OPEN"}
    if brief.intent == "changed":
        team = src.team()
        if brief.since_confirm and team is not None and team.confirmed_at:
            needed.add("EV-CONFIRMED-DATE")
        past = _expected_past(ctx)
        if past is not None:
            needed |= {"EV-PAST-ASOF", "EV-WINDOW", "EV-PAST-RANK", "EV-PAST-SCORE"}
            needed |= {f"EV-PAST-{a}-Z" for a in AXES}
            if rank_now is not None and src.safe(past, f"rank_{brief.profile}") is not None:
                needed.add("EV-RANK-DIFF")
    return needed


def _layer_structure(ctx: _Ctx) -> list[str]:
    violations: list[str] = []
    expected, actual = _expected_evidence(ctx), set(ctx.brief.evidence)
    for key in sorted(expected - actual):
        violations.append(f"장부에 있어야 할 근거 {key} 가 없다")
    for key in sorted(actual - expected):
        violations.append(f"장부에 없어야 할 근거 {key} 가 있다")
    past = _expected_past(ctx)
    if ctx.brief.past_as_of != past:
        violations.append(f"비교 기준일 {ctx.brief.past_as_of} 이 원천으로 다시 정한 {past} 와 다르다")
    return violations


# ── 층 2 — 구성 ↔ 원천 ──────────────────────────────────────────────────────

@dataclass(frozen=True, slots=True)
class _Exp:
    key: str
    template: str
    slots: tuple[str, ...] = ()
    axis: str | None = None

    @property
    def cites(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(self.slots + C.EXTRA_CITES(self.key, self.axis)))


def _fixed(text: str, axis: str | None = None) -> _Exp:
    return _Exp("fixed", text, (), axis)


def _moves(truth: Mapping[str, Any]) -> list[tuple[str, int, int]]:
    moves = [(a, truth[f"EV-PAST-{a}-Z"], truth[f"EV-{a}-Z"]) for a in AXES
             if truth.get(f"EV-{a}-Z") is not None and truth.get(f"EV-PAST-{a}-Z") is not None
             and truth[f"EV-{a}-Z"] != truth[f"EV-PAST-{a}-Z"]]
    moves.sort(key=lambda m: (-abs(m[2] - m[1]), AXES.index(m[0])))
    return moves


def _headline_expected(ctx: _Ctx) -> list[_Exp]:
    """결론 — 🔒 틀은 `templates` 로 만들되, **어느 틀 · 자리 순서**는 여기서 따로 정한다."""
    brief, v, t, label = ctx.brief, ctx.values, ctx.truth, ctx.label
    if brief.intent == "why_rank":
        if v.get("EV-RANK") is None or v.get("EV-SCORE") is None:
            return [_Exp("no_score", T.no_score(label), ("EV-LABEL",)), _fixed(C.NO_SCORE_TEXT)]
        z = t["EV-SCORE"]
        return [_Exp("rank_headline", T.rank_headline(label), ("EV-LABEL", "EV-TOTAL", "EV-RANK")),
                _Exp("sigma", T.sigma(z), ("EV-SCORE",) + (("EV-RULE-SIGMA2",) if abs(z) >= 20000 else ()))]
    if brief.intent == "trust":
        if v.get("EV-RANK") is None:
            return [_Exp("no_score", T.no_score(label), ("EV-LABEL",)), _fixed(C.NO_SCORE_TRUST)]
        return [_Exp("trust_rank", T.trust_rank(label), ("EV-LABEL", "EV-TOTAL", "EV-RANK", "EV-AT-TOTAL")),
                _Exp("trust_counts", T.TRUST_COUNTS, ("EV-AT-PASS", "EV-AT-WEAK", "EV-AT-OPEN"))]
    if brief.intent == "changed":
        if brief.past_as_of is None:
            return [_Exp("no_past", T.no_past(label), ("EV-LABEL",))]
        out: list[_Exp] = []
        if brief.since_confirm:
            if t.get("EV-WINDOW") == 0:
                return [_Exp("no_new_day", T.NO_NEW_DAY, ("EV-CONFIRMED-DATE",))]
            out.append(_Exp("since", T.SINCE, ("EV-CONFIRMED-DATE",)))
        elif brief.window_source == "기본":
            out.append(_Exp("default_window", T.DEFAULT_WINDOW, ("EV-WINDOW",)))
        now, past = v.get("EV-RANK"), v.get("EV-PAST-RANK")
        if now is None or past is None:
            out.append(_Exp("rank_missing", T.RANK_MISSING, ("EV-WINDOW", "EV-PAST-ASOF", "EV-ASOF")))
        else:
            move = "up" if past > now else "down" if past < now else "same"
            order = ("EV-LABEL", "EV-WINDOW", "EV-PAST-ASOF", "EV-PAST-RANK", "EV-ASOF", "EV-RANK")
            out.append(_Exp("rank_change", T.rank_change(label, move),
                            order + (("EV-RANK-DIFF",) if move != "same" else ())))
        if not _moves(t):
            out.append(_Exp("z_same", T.Z_SAME))
        return out
    if ctx.src.sector is None:
        return [_Exp("no_master", T.no_master(label), ("EV-LABEL",))]
    return [_Exp("contents_head", T.contents_head(label), ("EV-LABEL", "EV-GICS", "EV-ETF-COUNT", "EV-MEMBER-COUNT")),
            _fixed(C.CONTENTS_DEF)]


def _listing_expected(ctx: _Ctx) -> list[_Exp]:
    sector = ctx.src.sector
    if ctx.brief.intent != "contents" or sector is None:
        return []
    return ([_Exp("etf_line", T.etf_line(i), (f"EV-ETF-{i}",)) for i in range(1, len(sector.etfs) + 1)]
            + [_Exp("member_line", T.member_line(i), (f"EV-MEMBER-{i}",))
               for i in range(1, len(sector.members) + 1)])


_AXIS_RULE_SLOTS: Mapping[str, tuple[str, ...]] = {
    "M": ("EV-RULE-SHORT", "EV-RULE-LONG"), "F": ("EV-RULE-SHORT",), "B": (), "V": ("EV-RULE-VALUE",),
}


def _observed_expected(card: C.Card, ctx: _Ctx) -> list[_Exp]:
    axis, v, t = card.axis, ctx.values, ctx.truth
    if card.kind == "axis":
        raw = v.get(f"EV-{axis}-RAW")
        first = (_Exp("raw_missing", T.RAW_MISSING, (), axis) if raw is None
                 else _Exp("axis_plain", T.axis_plain(axis, raw),
                           _AXIS_RULE_SLOTS[axis] + ((f"EV-{axis}-RAW",) if raw != 0 else ()), axis))
        contribution = t.get(f"EV-{axis}-CONTRIB") or 0
        return [first, _Exp("contrib", T.contrib(axis, contribution > 0),
                            ("EV-TOTAL", f"EV-{axis}-RANK", f"EV-{axis}-CONTRIB"), axis)]
    if card.kind == "stability":
        if v.get("EV-STAB-MEAN") is None or v.get("EV-STAB-SPREAD") is None:
            return [_Exp("stab_missing", T.STAB_MISSING, ("EV-STAB-DAYS",))]
        return [_Exp("stability", T.stability(t["EV-STAB-SPREAD"]),
                     ("EV-STAB-DAYS", "EV-STAB-MEAN", "EV-STAB-SPREAD"))]
    if card.kind == "change":
        now, before = t.get(f"EV-{axis}-Z") or 0, t.get(f"EV-PAST-{axis}-Z") or 0
        return [_Exp("z_change", T.z_change(axis, now > before), (f"EV-PAST-{axis}-Z", f"EV-{axis}-Z"), axis)]
    ok = v.get("EV-LIQ")
    count = (_Exp("etf_unknown", T.ETF_UNKNOWN) if v.get("EV-ETF-N") is None
             else _Exp("etf_count", T.ETF_COUNT, ("EV-ETF-N",)))
    liquidity_slots = ("EV-RULE-SHORT",) if ok is None else ("EV-RULE-LIQ",) if ok else ()
    return [count, _Exp("liquidity", T.liquidity(ok), liquidity_slots)]


def _compare(where: str, actual: tuple[C.Sentence, ...], expected: list[_Exp]) -> list[str]:
    violations: list[str] = []
    if len(actual) != len(expected):
        violations.append(f"{where}: 문장이 {len(actual)}개다 — 원천으로 다시 정하면 {len(expected)}개다")
    for index, (sentence, exp) in enumerate(zip(actual, expected), 1):
        here = f"{where} {index}"
        if (sentence.key, sentence.axis, sentence.template) != (exp.key, exp.axis, exp.template):
            violations.append(f"{here}: 원천으로 다시 정한 문장({exp.key})과 다르다")
            continue
        order = tuple(evidence_id for evidence_id, _, _ in slots_in(sentence.template))
        if order != exp.slots:
            violations.append(f"{here}: 자리 순서 {order} 가 {exp.slots} 이어야 한다")
        if sentence.cites != exp.cites:
            violations.append(f"{here}: 근거 {sentence.cites} 가 {exp.cites} 이어야 한다")
    return violations


def _expected_cards(ctx: _Ctx) -> list[tuple[str, str | None]]:
    brief, v, t = ctx.brief, ctx.values, ctx.truth
    if brief.intent == "why_rank":
        if v.get("EV-RANK") is None or v.get("EV-SCORE") is None:
            return []
        scored = [(a, t[f"EV-{a}-CONTRIB"]) for a in AXES if t.get(f"EV-{a}-CONTRIB") is not None]
        out: list[tuple[str, str | None]] = []
        if scored:
            best, worst = max(scored, key=lambda p: p[1]), min(scored, key=lambda p: p[1])
            if best[1] > 0:
                out.append(("axis", best[0]))
            if worst[1] < 0 and worst[0] != best[0]:
                out.append(("axis", worst[0]))
        return out + [("stability", None)]
    if brief.intent == "changed":
        if brief.past_as_of is None or (brief.since_confirm and t.get("EV-WINDOW") == 0):
            return []
        return [("change", a) for a, _, _ in _moves(t)[:2]]
    if brief.intent == "contents":
        return [("contents", None)] if ctx.src.sector is not None else []
    return []


def _card_spec(card: C.Card, ctx: _Ctx) -> tuple[str, str, dict[str, tuple[str, ...]]] | str:
    """(제목, 논점, 칸별 상수) — 원천으로 다시 정한다. 정할 수 없으면 사유 문자열."""
    axis, v, t, brief = card.axis, ctx.values, ctx.truth, ctx.brief
    if card.kind == "axis":
        contribution = t.get(f"EV-{axis}-CONTRIB")
        if not contribution:
            return "기여가 없거나 0 인데 축 카드를 냈다"
        limits = []
        if axis in axes_in(v.get("EV-DEGRADED")):
            limits.append(C.LIMIT_DEGRADED)
        if axis == "F" and v.get("EV-ETF-N") == 1:
            limits.append(C.LIMIT_ONE_ETF)
        if v.get("EV-LIQ") is False:
            limits.append(C.LIMIT_ILLIQUID)
        return T.axis_title(axis, contribution > 0), f"TH-{axis}", {
            "meaning": (AXIS_MEANING[axis],), "cause": (C.CAUSE_TEXT,), "counter": (AXIS_NOT[axis],),
            "limits": tuple(limits) or (C.LIMIT_NONE,), "next_kpi": (C.NEXT_KPI[axis],)}
    if card.kind == "stability":
        item = brief.evidence.get("EV-STAB-DAYS")
        window = t.get("EV-STAB-DAYS")
        short = item is not None and window is not None and window < item.origin[2]
        return C.STABILITY_TITLE, "TH-RANK", {
            "meaning": (C.STAB_MEANING,), "cause": (C.CAUSE_TEXT,), "counter": (C.STAB_COUNTER,),
            "limits": (C.STAB_LIMIT,) + ((C.STAB_SHORT_LIMIT,) if short else ()),
            "next_kpi": (C.STAB_NEXT,)}
    if card.kind == "change":
        now, before = t.get(f"EV-{axis}-Z"), t.get(f"EV-PAST-{axis}-Z")
        if now is None or before is None or now == before:
            return "σ 가 움직이지 않았는데 변화 카드를 냈다"
        return T.change_title(axis, now > before), f"TH-{axis}", {
            "meaning": (AXIS_MEANING[axis],), "cause": (C.CAUSE_TEXT,), "counter": (C.CHANGE_COUNTER,),
            "limits": (C.CHANGE_LIMIT,), "next_kpi": (C.NEXT_KPI[axis],)}
    return C.CONTENTS_TITLE, "TH-DEF", {
        "meaning": (C.CONTENTS_MEANING,), "cause": (C.CONTENTS_CAUSE,), "counter": (C.CONTENTS_COUNTER,),
        "limits": (C.CONTENTS_LIMIT,) + ((C.LIMIT_ONE_ETF,) if v.get("EV-ETF-N") == 1 else ()),
        "next_kpi": (C.CONTENTS_NEXT,)}


def _layer_composition(ctx: _Ctx) -> list[str]:
    brief = ctx.brief
    violations = _compare("결론", brief.headline, _headline_expected(ctx))
    violations += _compare("목록", brief.listing, _listing_expected(ctx))
    expected = _expected_cards(ctx)
    actual = [(card.kind, card.axis) for card in brief.cards]
    if actual != expected:
        violations.append(f"카드 구성 {actual} 이 원천으로 다시 정한 것 {expected} 과 다르다")
    for card in brief.cards:
        where = f"카드 '{card.title}'"
        spec = _card_spec(card, ctx)
        if isinstance(spec, str):
            violations.append(f"{where}: {spec}")
            continue
        title, target, fixed = spec
        if card.title != title:
            violations.append(f"{where}: 제목이 원천으로 정한 '{title}' 와 다르다")
        if card.target != target:
            violations.append(f"{where}: 논점 {card.target} 이 {target} 이어야 한다")
        violations += _compare(f"{where} 관찰", card.observed, _observed_expected(card, ctx))
        for name, texts in fixed.items():
            sentences = getattr(card, name)
            if tuple(s.text for s in sentences) != texts or any(s.key != "fixed" for s in sentences):
                violations.append(f"{where}: '{name}' 칸의 상수가 원천으로 정한 것과 다르다")
    if brief.disclaimer.key != "disclaimer" or brief.disclaimer.text != C.DISCLAIMER_TEXT:
        violations.append("면책 문장이 바뀌었다")
    return violations


def _attack_case(attack_id: str, values: Mapping[str, Any], truth: Mapping[str, Any]
                 ) -> tuple[str, str, tuple[str, ...], bool, tuple[str, ...]]:
    """(갈래, 논점, 보탠 축, 강등 여부, 기여 근거) — 🔒 `redteam` 의 판정을 쓰지 않고 따로 매긴다."""
    target = R.TARGET[attack_id]
    if attack_id == "AT-1":
        ranks = [values.get(f"EV-RANK-{p.upper()}") for p in PRESETS]
        case = "open" if any(r is None for r in ranks) else "pass" if max(ranks) - min(ranks) <= 3 else "weak"
        return case, target, (), False, ()
    if attack_id == "AT-2":
        spread = truth.get("EV-STAB-SPREAD")
        case = "open" if values.get("EV-STAB-MEAN") is None or spread is None else "pass" if spread <= 2 else "weak"
        return case, target, (), False, ()
    if attack_id == "AT-3":
        parts = [(a, truth.get(f"EV-{a}-CONTRIB")) for a in AXES]
        live = tuple(f"EV-{a}-CONTRIB" for a, c in parts if c is not None)
        positives = sorted(((a, c) for a, c in parts if c is not None and c > 0),
                           key=lambda p: (-p[1], AXES.index(p[0])))
        if not live:
            return "open", target, (), False, ()
        if not positives:
            return "none", target, (), False, live
        best, rest = positives[0][1], sum(c for _, c in positives[1:])
        case = "single" if len(positives) == 1 else "pass" if best <= 2 * rest else "heavy"
        return case, f"TH-{positives[0][0]}", tuple(a for a, _ in positives), False, ()
    if attack_id == "AT-4":
        ok = values.get("EV-LIQ")
        return ("open" if ok is None else "pass" if ok else "weak"), target, (), False, ()
    if attack_id == "AT-5":
        count = values.get("EV-ETF-N")
        return ("open" if count is None else "pass" if count >= 2 else "weak"), target, (), False, ()
    if attack_id == "AT-6":
        z = truth.get("EV-V-Z")
        return ("open" if z is None else "weak" if z <= -10000 else "pass"), target, (), False, ()
    used, degraded = values.get("EV-AXES-USED"), bool(values.get("EV-DEGRADED"))
    case = "open" if used is None else "pass" if used == len(AXES) and not degraded else "weak"
    return case, target, (), degraded, ()


def _layer_attacks(ctx: _Ctx) -> list[str]:
    brief, values, truth = ctx.brief, ctx.values, ctx.truth
    violations: list[str] = []
    expected = R.ATTACK_IDS if brief.intent == "trust" and values.get("EV-RANK") is not None else ()
    if tuple(a.id for a in brief.attacks) != expected:
        violations.append(f"반론 목록 {[a.id for a in brief.attacks]} 이 {list(expected)} 이어야 한다")
    gap_ids = {g.id for g in brief.gaps}
    for attack in brief.attacks:
        where = f"반론 {attack.id}"
        if attack.id not in R.ATTACK_IDS:
            continue
        case, target, positives, degraded, contributions = _attack_case(attack.id, values, truth)
        if attack.case != case:
            violations.append(f"{where}: 판정 갈래 {attack.case} 가 원천으로 다시 매긴 {case} 와 다르다")
            continue
        if attack.target != target:
            violations.append(f"{where}: 겨냥한 논점 {attack.target} 이 {target} 이어야 한다")
        template = R.defense_template(attack.id, case, positives=positives, degraded=degraded)
        if attack.defense_template != template:
            violations.append(f"{where}: 방어 문장 틀이 판정에 맞는 것과 다르다")
        violations += _shapes(where, attack.defense_template)
        cites = tuple(dict.fromkeys(cites_of(template) + R.extra_cites(attack.id, case,
                                                                        contributions=contributions)))
        if attack.cites != cites:
            violations.append(f"{where}: 방어 근거가 판정에 맞는 것과 다르다")
        try:
            if fill(template, values) != attack.defense:
                violations.append(f"{where}: 방어 문장이 원천 값으로 다시 채운 것과 다르다")
        except SlotError as exc:
            violations.append(f"{where}: 방어 자리를 채울 수 없다 — {exc}")
        gap = R.OPEN_GAP[attack.id] if case == "open" else None
        if attack.gap != gap:
            violations.append(f"{where}: 가리킨 빈칸 {attack.gap} 이 {gap} 이어야 한다")
        if gap is not None and gap not in gap_ids:
            violations.append(f"{where}: [추가확인] 이 가리킨 빈칸 {gap} 이 빈칸 목록에 없다")
        if _numeric(literal_of(attack.defense_template)) or _numeric(attack.rule):
            violations.append(f"{where}: 숫자를 자리 밖에 적었다")
        for text in (attack.question, attack.defense, attack.rule):
            violations += _banned(where, text)
    return violations


_CLOSED = dict(why="이번 답에서는 이 빈칸이 생기지 않았다", needed="없다", workaround="없다", affects="없다")


def _expected_gaps(ctx: _Ctx) -> list[Gap]:
    """빈칸 목록을 원천으로 다시 정한다. 🔒 순서까지 — 통째로 빼거나 바꿔 적은 목록이 걸린다(재검증 R-A)."""
    brief, v, t, src = ctx.brief, ctx.values, ctx.truth, ctx.src
    out: list[Gap] = []
    intent = brief.intent
    if intent == "changed" and brief.past_as_of is None:
        team = src.team()
        unconfirmed = team is None or not team.confirmed_at
        out.append(gap_by_id("GP-CONFIRM" if brief.since_confirm and unconfirmed else "GP-PAST"))
    if intent in ("why_rank", "trust"):
        out.extend(gap_axis(a) for a in axes_in(v.get("EV-MISSING")))
        if v.get("EV-STAB-MEAN") is None:
            out.append(gap_by_id("GP-STAB"))
    if intent in ("trust", "contents") and v.get("EV-LIQ") is None:
        out.append(gap_by_id("GP-LIQ"))
    if intent in ("why_rank", "changed"):
        out.append(gap_by_id("GP-CAUSE"))
    if intent == "trust":
        out.extend([gap_by_id("GP-INVESTOR"), gap_by_id("GP-FUNDAMENTAL")])
        preset = gap_preset_from(v)
        if preset is not None:
            out.append(preset)
    if intent in ("trust", "contents"):
        conflict = gap_yaml_liquidity_from(v)
        if conflict is not None:
            out.append(conflict)
    if intent == "contents":
        out.append(gap_by_id("GP-PDF" if src.sector is not None else "GP-MASTER"))
    if intent == "trust" and v.get("EV-RANK") is not None:
        for attack_id in R.ATTACK_IDS:
            if _attack_case(attack_id, v, t)[0] == "open":
                out.append(gap_by_id(R.OPEN_GAP[attack_id]))
    unique: list[Gap] = []
    for gap in out:
        if all(g.id != gap.id for g in unique):
            unique.append(gap)
    return unique


def _layer_gaps(ctx: _Ctx) -> list[str]:
    brief = ctx.brief
    violations: list[str] = []
    expected = _expected_gaps(ctx)
    open_gaps = [g for g in brief.gaps if g.state != "닫힘"]
    closed = [g for g in brief.gaps if g.state == "닫힘"]
    if [g.id for g in open_gaps] != [g.id for g in expected]:
        violations.append(f"빈칸 목록 {[g.id for g in open_gaps]} 이 원천으로 다시 정한 "
                          f"{[g.id for g in expected]} 과 다르다")
    for gap, exp in zip(open_gaps, expected):
        if gap.with_state("신규") != exp.with_state("신규"):
            violations.append(f"빈칸 {gap.id}: 칸의 글자가 원천으로 다시 정한 것과 다르다")
    if brief.gaps[:len(open_gaps)] != tuple(open_gaps):
        violations.append("닫힌 빈칸이 열린 빈칸 앞에 있다")
    expected_ids = {g.id for g in expected}
    for gap in closed:
        if gap.id in expected_ids or any(getattr(gap, k) != val for k, val in _CLOSED.items()):
            violations.append(f"빈칸 {gap.id}: 닫혔다고 적을 수 없는 빈칸이거나 닫힌 칸의 글자가 다르다")
    for gap in brief.gaps:
        if gap.why_template:
            violations += _shapes(f"빈칸 {gap.id}", gap.why_template)
    return violations


# ── 층 3 — 문장 ↔ 원천 ──────────────────────────────────────────────────────

#: 역할 → 그 역할이 **검사하는** 방향 낱말. 🔒 이 밖의 방향 낱말은 어느 문장에도 쓸 수 없다
_ROLE_WORDS: Mapping[str, tuple[str, ...]] = {
    "axis_plain": ("더 올랐다", "덜 올랐다", "늘었다", "줄었다", "많다", "적다", "**아래**", "**위**",
                   "같다", "그대로다", "같은 자리"),
    "sigma": ("높다", "낮다", "비슷하다"),
    "contrib": ("보탰다", "깎았다"),
    "rank_change": ("올랐다", "내려갔다", "그대로다"),
    "z_change": ("높아졌다", "낮아졌다"),
    "liquidity": ("충분", "미달", "안 찼다"),
    "stability": ("꾸준히", "오르내렸다", "흔들렸다"),
}
_ALL_WORDS: tuple[str, ...] = tuple(sorted(
    {w for words in _ROLE_WORDS.values() for w in words} | {"끌어올렸다", "끌어내렸다"},
    key=len, reverse=True))

_AXIS_PATTERNS: Mapping[str, tuple[tuple[str, Callable[[int], bool]], ...]] = {
    "M": ((r"%p 더 올랐다\.$", lambda v: v > 0), (r"%p 덜 올랐다\.$", lambda v: v < 0),
          (r"시장 평균과 같다\.$", lambda v: v == 0)),
    "F": ((r"% 늘었다 — 운용사가 설정을 늘렸다는 뜻이고, 실제로 돈이 들어온 자국이다\.$", lambda v: v > 0),
          (r"% 줄었다 — 돈이 빠져나간 자국이다\.$", lambda v: v < 0),
          (r"영업일 새 그대로다\.$", lambda v: v == 0)),
    "B": ((r"%p 많다\.$", lambda v: v > 0), (r"%p 적다\.$", lambda v: v < 0),
          (r"시장 전체와 같다\.$", lambda v: v == 0)),
    "V": ((r"% \*\*아래\*\*에 있다 — 최근 많이 오르지 않았다는 뜻이다\.$", lambda v: v > 0),
          (r"% \*\*위\*\*에 있다 — 최근 많이 올라 과열 쪽이라는 뜻이다\.$", lambda v: v < 0),
          (r"평균과 같은 자리에 있다\.$", lambda v: v == 0)),
}

_SIGMA_PATTERNS: tuple[tuple[str, Callable[[int], bool]], ...] = (
    (r"2σ 이상 높다'", lambda z: z >= 20000),
    (r"2σ 이상 낮다'", lambda z: z <= -20000),
    (r"뚜렷이 높다'", lambda z: 10000 <= z < 20000),
    (r"뚜렷이 낮다'", lambda z: -20000 < z <= -10000),
    (r"다소 높다'", lambda z: 4000 <= z < 10000),
    (r"다소 낮다'", lambda z: -10000 < z <= -4000),
    (r"비슷하다'", lambda z: abs(z) < 4000),
)


def _numeric(text: str) -> bool:
    """🔴 아라비아 숫자만 보지 않는다 — 전각 · 원문자 숫자도 숫자다(재검증 R-A)."""
    if _DIGIT.search(text):
        return True
    return any(not ch.isascii() and unicodedata.category(ch) in ("Nd", "Nl", "No") for ch in text)


def _banned(where: str, text: str) -> list[str]:
    out = [f"{where}: 쓰지 않는 표현({why}) '{hit.group(0)}'"
           for why, pattern in BANNED for hit in [pattern.search(text)] if hit]
    leak = _LEAK.search(text)
    if leak:
        out.append(f"{where}: 빈 값이 글자로 새었다 '{leak.group(0)}'")
    return out


def _shapes(where: str, template: str) -> list[str]:
    """자리마다 허락된 모양과 뒤따르는 단위."""
    out: list[str] = []
    for evidence_id, fmt, end in slots_in(template):
        spec = next((s for s in _SLOT_SHAPES if s[0].fullmatch(evidence_id)), None)
        if spec is None:
            out.append(f"{where}: 자리 {evidence_id} 의 모양 규칙이 없다 — guard 의 표에 더한다")
            continue
        _, formats, suffix = spec
        if fmt not in formats:
            out.append(f"{where}: 자리 {evidence_id} 를 {fmt} 로 적었다 — {' · '.join(formats)} 여야 한다")
        if suffix is not None and not suffix.match(template[end:]):
            out.append(f"{where}: 자리 {evidence_id} 뒤의 단위가 맞지 않는다")
    return out


def _one_of(where: str, text: str, table, value) -> list[str]:
    hits = [check for pattern, check in table if re.search(pattern, text)]
    if len(hits) != 1:
        return [f"{where}: 방향 낱말이 {len(hits)}개 맞는다 — 하나여야 한다"]
    return [] if hits[0](value) else [f"{where}: 방향 낱말이 원천의 값({value})과 반대다"]


def _is_none(values: Mapping[str, Any], key: str) -> bool:
    return key in values and values[key] is None


def _stray(where: str, sentence: C.Sentence) -> list[str]:
    """🔴 역할과 상관없이 방향 낱말을 찾는다 — 역할만 내리면 검사를 피했었다(리뷰 O1).

    🔴 허락된 낱말도 **한 번만** — "아래이고 … 많다" 처럼 한 문장에 둘을 섞으면 통과했었다(재검증 R-G).
    """
    literal = literal_of(sentence.template)
    allowed = sorted(_ROLE_WORDS.get(sentence.role, ()), key=len, reverse=True)
    out: list[str] = []
    if allowed:
        count = len(re.findall("|".join(re.escape(word) for word in allowed), literal))
        if count != 1:
            out.append(f"{where}: 방향 낱말이 {count}번 나온다 — 한 번이어야 한다")
    for word in allowed:
        literal = literal.replace(word, " ")
    out += [f"{where}: 방향 낱말 '{word}' 을 검사하지 않는 문장에 썼다" for word in _ALL_WORDS if word in literal]
    return out


def _direction(where: str, sentence: C.Sentence, ctx: _Ctx) -> list[str]:
    brief, values, truth = ctx.brief, ctx.values, ctx.truth
    role, text, axis = sentence.role, sentence.text, sentence.axis
    if role == "axis_plain":
        raw = truth.get(f"EV-{axis}-RAW")
        return (_one_of(where, text, _AXIS_PATTERNS[axis], raw) if raw is not None
                else [f"{where}: 원시값이 없는데 방향을 말했다"])
    if role == "raw_missing":
        return [] if _is_none(values, f"EV-{axis}-RAW") else [f"{where}: 원시값이 있는데 없다고 했다"]
    if role == "sigma":
        z = truth.get("EV-SCORE")
        return _one_of(where, text, _SIGMA_PATTERNS, z) if z is not None else [f"{where}: 점수가 없다"]
    if role == "contrib":
        value = truth.get(f"EV-{axis}-CONTRIB")
        table = ((r"보탰다\.$", lambda v: v > 0), (r"깎았다\.$", lambda v: v < 0))
        return _one_of(where, text, table, value) if value is not None else [f"{where}: 기여가 없다"]
    if role == "rank_change":
        before, now = truth.get("EV-PAST-RANK"), truth.get("EV-RANK")
        if before is None or now is None:
            return [f"{where}: 순위가 없는데 변화를 말했다"]
        table = ((r"계단 올랐다\.$", lambda d: d > 0), (r"계단 내려갔다\.$", lambda d: d < 0),
                 (r"순위가 그대로다\.$", lambda d: d == 0))
        return _one_of(where, text, table, before - now)
    if role == "rank_missing":
        return ([] if _is_none(values, "EV-RANK") or _is_none(values, "EV-PAST-RANK")
                else [f"{where}: 두 순위가 다 있는데 견주지 않았다"])
    if role == "z_change":
        before, now = truth.get(f"EV-PAST-{axis}-Z"), truth.get(f"EV-{axis}-Z")
        if before is None or now is None:
            return [f"{where}: σ 가 없는데 변화를 말했다"]
        table = ((r"로 높아졌다\.$", lambda d: d > 0), (r"로 낮아졌다\.$", lambda d: d < 0))
        return _one_of(where, text, table, now - before)
    if role == "z_same":
        moved = [a for a, _, _ in _moves(truth)]
        return [f"{where}: σ 가 움직인 축이 있는데 같다고 했다 — {''.join(moved)}"] if moved else []
    if role == "liquidity":
        if "EV-LIQ" not in values:
            return [f"{where}: 유동성 근거가 원천과 맞지 않았다"]
        table = ((r"^유동성: 충분", lambda v: v is True), (r"^🔴 유동성 미달", lambda v: v is False),
                 (r"안 찼다\)$", lambda v: v is None))
        return _one_of(where, text, table, values["EV-LIQ"])
    if role == "stability":
        spread = truth.get("EV-STAB-SPREAD")
        if spread is None:
            return [f"{where}: 진폭이 없는데 안정성을 말했다"]
        table = ((r"꾸준히 이 근처에 있었다\.$", lambda s: s <= 2),
                 (r"순위가 오르내렸다\.$", lambda s: 2 < s <= 5),
                 (r"순위가 많이 흔들렸다\.$", lambda s: s > 5))
        return _one_of(where, text, table, spread)
    if role == "stab_missing":
        return ([] if _is_none(values, "EV-STAB-MEAN") or _is_none(values, "EV-STAB-SPREAD")
                else [f"{where}: 안정성 값이 있는데 말할 수 없다고 했다"])
    if role == "no_score":
        return ([] if _is_none(values, "EV-RANK") or _is_none(values, "EV-SCORE")
                else [f"{where}: 점수가 있는데 낼 수 없었다고 했다"])
    if role == "no_past":
        gaps = {g.id for g in brief.gaps}
        return ([] if brief.past_as_of is None and gaps & {"GP-PAST", "GP-CONFIRM"}
                else [f"{where}: 비교 기준일이 있거나 이유가 빈칸 목록에 없다"])
    if role == "no_master":
        return [] if ctx.src.sector is None else [f"{where}: 섹터 정의가 있는데 읽지 못했다고 했다"]
    if role == "etf_unknown":
        return [] if _is_none(values, "EV-ETF-N") else [f"{where}: ETF 수가 있는데 모른다고 했다"]
    if role == "no_new_day":
        return [] if truth.get("EV-WINDOW") == 0 else [f"{where}: 새 기준일이 있는데 없다고 했다"]
    return []


def _sentence(where: str, sentence: C.Sentence, ctx: _Ctx) -> list[str]:
    brief = ctx.brief
    violations = _banned(where, sentence.text)
    if sentence.role in ("fixed", "disclaimer"):
        if sentence.role == "fixed" and sentence.text not in C.FIXED_TEXTS:
            violations.append(f"{where}: 상수 목록에 없는 고정 문장이다")
        return violations
    if _numeric(literal_of(sentence.template)):
        violations.append(f"{where}: 숫자를 자리 밖에 적었다")
    violations += _shapes(where, sentence.template)
    for cite in sentence.cites:
        if not ids.is_known(cite) or cite not in brief.evidence:
            violations.append(f"{where}: 장부에 없는 근거 {cite}")
    try:
        again = fill(sentence.template, ctx.values)
    except SlotError as exc:
        violations.append(f"{where}: 자리를 원천 값으로 채울 수 없다 — {exc}")
    else:
        if again != sentence.text:
            violations.append(f"{where}: 문장이 원천 값으로 다시 채운 것과 다르다")
    violations += _stray(where, sentence)
    violations += _direction(where, sentence, ctx)
    return violations


def verify(brief: C.Brief, *, frame: Any, master: Any = None, workspace: Any = None,
           team_id: str | None = None) -> tuple[str, ...]:
    """어긋난 것의 목록. 🔒 **빈 튜플만이 통과다.**"""
    src = _Source(frame, brief, master, workspace, team_id)
    violations, values, truth = _layer_sources(brief, src)
    ctx = _Ctx(brief=brief, src=src, values=values, truth=truth)
    violations += _layer_structure(ctx)
    violations += _layer_composition(ctx)
    violations += _layer_attacks(ctx)
    violations += _layer_gaps(ctx)
    for where, sentence in brief.labelled():
        violations += _sentence(where, sentence, ctx)
    for card in brief.cards:
        violations += _banned(f"카드 '{card.title}' 제목", card.title)
    return tuple(violations)
