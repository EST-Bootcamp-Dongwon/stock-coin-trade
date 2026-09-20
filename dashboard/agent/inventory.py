"""근거 인벤토리 — 한 섹터에 대해 **무엇을 어디서 알았나** 의 장부.

## GIC 에서 옮긴 것 (구조만 · ADR-SC-0013)

- **증거 장부** — 한 줄 = 한 사실. 무엇 · 값 · 출처 · 신뢰도 · 기준일 · 어떻게 다시 얻나(`origin`).
- **자료 확보의 단계** — 받은 자료 → 찾아본 자료 → 그래도 없으면 빈칸. 🔒 이 도구는 **검색하지 않는다.**
  게시된 파생값 · 커밋된 섹터 정의 · 조 원장만 읽고 곧바로 빈칸으로 간다. 검색하지 못하는 쪽은 그 사실을
  밝혀 두게 한 장치를 따라, `SEARCH_NOTICE` 가 화면에 한 줄을 남긴다.
- **신뢰도 상 · 중 · 하** — GIC 의 등급은 출처의 거리(1차 · 2차 · 사용자 제공)다. 우리에게 2차 매체가
  없어서 **중** 을 계산 품질로 옮겼다(2026-09-14 사용자 결정):
  상 = 창이 온전한 거래소 파생값 · 규칙 상수 · 원장 기록 /
  중 = 척도를 내린 축 · 표본일이 모자란 안정성 · 결측 축이 섞인 총점 · 사람이 적고 원천과 대조한 식별자 /
  하 = 사람이 쓴 판단(`note` · yaml 경고)
- **Gap Log** — 여섯 칸이 최소다. 결측 유형은 셋 — 미확보 / 구조적 비공시 / 출처 충돌.
  🔒 **구조적 비공시로 두려면 그 값을 내는 체계가 없다는 증거(`basis`)가 있어야 한다.** 찾다가 실패한 것은
  미확보로 둔다.

## 🔒 `origin` 이 요점이다

guard 는 이 장부의 값을 **믿지 않는다.** `origin` 을 보고 점수 표 · 정의 · 원장에서 스스로 다시 얻어
대조한다 — 앞 단계가 넘긴 값을 사실로 삼지 않는 GIC 의 장치를 코드로 옮긴 것이다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping

from dashboard import view
from dashboard.agent import ids
from dashboard.agent.slots import fill, slot
from sector.scoring import (
    AXES,
    AXIS_NAMES,
    LIQUIDITY_MIN_WON,
    LOOKBACK_LONG,
    LOOKBACK_SHORT,
    PRESETS,
    VALUE_WINDOW,
)

__all__ = [
    "SOURCES", "CONFIDENCE", "UNITS", "GAP_KINDS", "GAP_STATES", "SEARCH_NOTICE", "PRESET_KO",
    "Evidence", "Gap", "Inventory", "collect", "with_past", "gaps_for", "gap_axis", "gap_by_id",
    "axes_in", "kst_date", "EOK_WON",
]

SOURCES: tuple[str, ...] = ("거래소 파생", "규칙", "사람 판단", "조 원장", "이 답")
CONFIDENCE: tuple[str, ...] = ("상", "중", "하")
#: 값을 글자로 옮기는 방식 — 표에 그릴 때 쓴다(`Evidence.shown`). 문장은 `slots` 의 자리 모양을 따른다.
UNITS: tuple[str, ...] = ("date", "text", "int", "sigma", "decimal1", "bool", "axis")
GAP_KINDS: tuple[str, ...] = ("미확보", "구조적 비공시", "출처 충돌")
GAP_STATES: tuple[str, ...] = ("신규", "유지", "닫힘")

PRESET_KO: Mapping[str, str] = {"balanced": "균형", "momentum": "모멘텀 중시", "contrarian": "역발상"}

EOK_WON = 100_000_000
_KST = timezone(timedelta(hours=9))

SEARCH_NOTICE = ("이 답은 검색하지 않았다 — 게시된 파생값 · 섹터 정의 · 조 원장만 읽었고, "
                 "거기 없는 것은 빈칸 목록에 적었다.")


@dataclass(frozen=True, slots=True)
class Evidence:
    """장부 한 줄. 🔒 `value` 에 float 가 없다 — 소수는 글자(`decimal1`)로 든다."""

    id: str
    label: str
    value: int | str | bool | None
    unit: str
    source: str
    confidence: str
    #: 어떻게 다시 얻나 — ("column", 열) · ("derived", 종류, …) · ("rule", 이름, …) ·
    #: ("config", 칸, …) · ("ledger", 종류) · ("axes", 열) · ("brief", 종류, …) · ("label",)
    origin: tuple[Any, ...]
    as_of: str | None = None
    axis: str | None = None
    note: str = ""

    def __post_init__(self) -> None:
        if not ids.EVIDENCE_ID.match(self.id):
            raise ValueError(f"근거 ID 모양이 틀렸다: {self.id}")
        if self.source not in SOURCES:
            raise ValueError(f"{self.id}: 모르는 출처 {self.source}")
        if self.confidence not in CONFIDENCE:
            raise ValueError(f"{self.id}: 신뢰도는 상 · 중 · 하 셋이다")
        if self.unit not in UNITS:
            raise ValueError(f"{self.id}: 모르는 단위 {self.unit}")
        if self.unit == "axis" and self.axis not in AXES:
            raise ValueError(f"{self.id}: 축 단위인데 축이 없다")
        if isinstance(self.value, float):
            raise ValueError(f"{self.id}: float 를 장부에 넣지 않는다 — bp 정수나 글자로 든다")
        if not self.origin:
            raise ValueError(f"{self.id}: 다시 얻는 길(origin)이 없는 근거는 대조할 수 없다")

    def shown(self) -> str:
        """근거 표에 그릴 글자. 🔒 없으면 `—` 다."""
        from dashboard.explain import axis_raw_text

        value = self.value
        if value is None or value == "":
            return "—"
        if self.unit == "bool":
            return "예" if value else "아니오"
        if self.unit == "sigma" and isinstance(value, int):
            return f"{value / 10000:+.2f}σ"
        if self.unit == "axis" and isinstance(value, int) and self.axis:
            return axis_raw_text(self.axis, value)
        if self.unit == "date" and isinstance(value, str) and len(value) == 8:
            return f"{value[:4]}-{value[4:6]}-{value[6:]}"
        return str(value)


@dataclass(frozen=True, slots=True)
class Gap:
    """Gap Log 한 줄. 🔒 여섯 칸이 최소다 — 빈 칸이 있으면 만들어지지 않는다.

    🔒 숫자가 들어가는 칸은 `why` 뿐이고, 그때는 `why_template` 의 자리로만 넣는다(`slots`).
    """

    id: str
    item: str
    kind: str
    why: str
    needed: str
    workaround: str
    affects: str
    basis: str = ""
    why_template: str = ""
    state: str = "신규"

    def __post_init__(self) -> None:
        if not self.id.startswith("GP-") or self.id not in ids.CATALOG:
            raise ValueError(f"Gap ID 가 대장에 없다: {self.id}")
        if self.kind not in GAP_KINDS:
            raise ValueError(f"{self.id}: 결측 유형은 미확보 · 구조적 비공시 · 출처 충돌 셋이다")
        if self.state not in GAP_STATES:
            raise ValueError(f"{self.id}: 상태는 신규 · 유지 · 닫힘 셋이다")
        for name in ("item", "why", "needed", "workaround", "affects"):
            if not str(getattr(self, name)).strip():
                raise ValueError(f"{self.id}: '{name}' 칸이 비었다 — Gap Log 는 여섯 칸이 최소다")
        if self.kind == "구조적 비공시" and not self.basis.strip():
            raise ValueError(
                f"{self.id}: 구조적 비공시로 두려면 그 값을 내는 체계가 없다는 증거(basis)가 "
                f"있어야 한다 — 찾다가 실패한 것은 미확보다")

    def texts(self) -> tuple[str, ...]:
        """숫자가 없어야 하는 칸들."""
        return (self.item, self.needed, self.workaround, self.affects, self.basis)

    def with_state(self, state: str) -> "Gap":
        return Gap(id=self.id, item=self.item, kind=self.kind, why=self.why, needed=self.needed,
                   workaround=self.workaround, affects=self.affects, basis=self.basis,
                   why_template=self.why_template, state=state)


@dataclass(frozen=True, slots=True)
class Inventory:
    sector_id: str
    label: str
    profile: str
    as_of: str
    evidence: Mapping[str, Evidence]
    story: Mapping[str, Any]
    #: `sector_master.Sector` — 정의를 못 읽었으면 None
    sector: Any | None = None
    past_as_of: str | None = None

    def value(self, key: str) -> Any:
        item = self.evidence.get(key)
        return item.value if item is not None else None

    def has(self, key: str) -> bool:
        return key in self.evidence


def axes_in(raw: object) -> tuple[str, ...]:
    """`axes_missing` · `axes_degraded` 를 축 기호로. 🔒 `"BV"` 와 `"B|V"` 를 같게 읽는다."""
    text = "" if raw is None else str(raw)
    return tuple(axis for axis in AXES if axis in text)


def kst_date(at: str) -> str:
    """원장 시각(UTC ISO) → KST 날짜 `YYYYMMDD`. 🔒 벽시계를 읽지 않는다 — 기록된 시각만 옮긴다."""
    return datetime.fromisoformat(at).astimezone(_KST).strftime("%Y%m%d")


def _int(value: Any) -> int | None:
    # 🔒 NA 판정을 뷰와 **같은 규칙으로** — 장부의 숫자와 화면의 숫자가 갈리면
    #    guard 의 대조가 대조이기를 그친다
    return view.int_or_none(value)


def _find_sector(master: Any, sector_id: str) -> Any | None:
    if master is None:
        return None
    return next((s for s in master.sectors if s.id == sector_id), None)


def _gics_name(master: Any, gics_id: str) -> str:
    found = next((g for g in master.gics_sectors if g.id == gics_id), None)
    return found.name_ko if found is not None else gics_id


def collect(frame: Any, sector_id: str, *, profile: str, days: int, names: Any,
            master: Any = None, workspace: Any = None, team_id: str | None = None,
            ) -> Inventory | None:
    """한 섹터의 장부. 그날 표에 없는 섹터면 `None` — 🔒 빈 장부를 지어내지 않는다."""
    latest = view.latest_frame(frame)
    rows = latest[latest["sector_id"] == sector_id]
    if len(rows) == 0:
        return None
    row = rows.iloc[0]
    as_of = str(row["bas_dd"])
    story = view.sector_story(frame, sector_id, profile=profile, days=days, names=names)
    parts = {p["axis"]: p for p in story["parts"]}
    missing = axes_in(story["missing"])
    degraded = axes_in(story["degraded"])
    thin = bool(missing or degraded)
    label = names.sector_label(sector_id)

    book: dict[str, Evidence] = {}

    def add(item: Evidence) -> None:
        book[item.id] = item

    add(Evidence("EV-LABEL", "섹터 이름", label, "text", "사람 판단", "상", ("label",)))
    add(Evidence("EV-ASOF", "기준일", as_of, "date", "거래소 파생", "상",
                 ("column", "bas_dd"), as_of))
    add(Evidence("EV-CONFIG", "섹터 정의 버전", str(row.get("config_version") or ""), "text",
                 "규칙", "상", ("column", "config_version"), as_of))
    add(Evidence("EV-TOTAL", "그날 섹터 수", story["total"], "int", "거래소 파생", "상",
                 ("derived", "total"), as_of))

    thin_note = "결측 · 척도를 내린 축이 섞인 점수다" if thin else ""
    add(Evidence("EV-RANK", f"순위 ({PRESET_KO.get(profile, profile)})", story["rank"], "int",
                 "거래소 파생", "중" if thin else "상", ("column", f"rank_{profile}"), as_of,
                 note=thin_note))
    add(Evidence("EV-SCORE", f"총점 ({PRESET_KO.get(profile, profile)})", story["score_bp"],
                 "sigma", "거래소 파생", "중" if thin else "상",
                 ("column", f"score_{profile}_bp"), as_of, note=thin_note))
    for preset in PRESETS:
        add(Evidence(f"EV-RANK-{preset.upper()}", f"순위 ({PRESET_KO.get(preset, preset)})",
                     _int(row.get(f"rank_{preset}")), "int", "거래소 파생",
                     "중" if thin else "상", ("column", f"rank_{preset}"), as_of))

    for axis in AXES:
        part = parts[axis]
        low = axis.lower()
        name = AXIS_NAMES[axis]
        confidence = "중" if axis in degraded else "상"
        note = "척도를 한 단 내렸다(섹터들이 거의 같은 값이었다)" if axis in degraded else ""
        add(Evidence(f"EV-{axis}-RAW", f"{name} 원시값", part["raw_bp"], "axis", "거래소 파생",
                     confidence, ("column", f"{low}_raw_bp"), as_of, axis=axis, note=note))
        add(Evidence(f"EV-{axis}-Z", f"{name} σ", part["z_bp"], "sigma", "거래소 파생",
                     confidence, ("column", f"{low}_z_bp"), as_of, axis=axis, note=note))
        add(Evidence(f"EV-{axis}-W", f"{name} 가중치", part["weight"], "int", "규칙", "상",
                     ("rule", "weight", profile, axis), axis=axis))
        add(Evidence(f"EV-{axis}-CONTRIB", f"{name} 기여(bp)", part["contribution_bp"], "int",
                     "거래소 파생", confidence, ("derived", "contrib", profile, axis), as_of,
                     axis=axis))
        add(Evidence(f"EV-{axis}-RANK", f"{name} 축 순위", part["rank"], "int", "거래소 파생",
                     confidence, ("derived", "axis_rank", axis), as_of, axis=axis))

    window = int(story["window"])
    short = window < days
    stab_confidence = "중" if short else "상"
    stab_note = "요청한 창보다 짧은 이력으로 냈다" if short else ""
    mean, spread = story["mean_rank"], story["spread"]
    add(Evidence("EV-STAB-DAYS", "안정성 표본일", window, "int", "거래소 파생", "상",
                 ("derived", "stab_days", days), as_of))
    add(Evidence("EV-STAB-MEAN", "최근 평균 순위", None if mean is None else f"{mean:.1f}",
                 "decimal1", "거래소 파생", stab_confidence,
                 ("derived", "stab_mean", profile, days), as_of, note=stab_note))
    add(Evidence("EV-STAB-SPREAD", "최근 순위 진폭", None if spread is None else f"{spread:.1f}",
                 "decimal1", "거래소 파생", stab_confidence,
                 ("derived", "stab_spread", profile, days), as_of, note=stab_note))
    add(Evidence("EV-LIQ", "유동성 충분", story["liquidity_ok"], "bool", "거래소 파생", "상",
                 ("column", "liquidity_ok"), as_of))
    add(Evidence("EV-ETF-N", "점수에 쓴 ETF 수", story["etf_n"], "int", "거래소 파생", "상",
                 ("column", "etf_n"), as_of))
    add(Evidence("EV-AXES-USED", "점수에 쓴 축 수", _int(row.get("n_axes_used")), "int",
                 "거래소 파생", "상", ("column", "n_axes_used"), as_of))
    add(Evidence("EV-MISSING", "계산하지 못한 축", "".join(missing), "text", "거래소 파생", "상",
                 ("axes", "axes_missing"), as_of))
    add(Evidence("EV-DEGRADED", "척도를 내린 축", "".join(degraded), "text", "거래소 파생", "상",
                 ("axes", "axes_degraded"), as_of))

    add(Evidence("EV-RULE-LIQ", "유동성 기준(억 원)", LIQUIDITY_MIN_WON // EOK_WON, "int", "규칙",
                 "상", ("rule", "liquidity_eok")))
    add(Evidence("EV-RULE-SHORT", "짧은 창(영업일)", LOOKBACK_SHORT, "int", "규칙", "상",
                 ("rule", "short")))
    add(Evidence("EV-RULE-LONG", "긴 창(영업일)", LOOKBACK_LONG, "int", "규칙", "상",
                 ("rule", "long")))
    add(Evidence("EV-RULE-VALUE", "밸류 창(영업일)", VALUE_WINDOW, "int", "규칙", "상",
                 ("rule", "value")))
    sector = _find_sector(master, sector_id)
    if sector is not None:
        checked = "원천 유니버스와 대조했다(M4)"
        add(Evidence("EV-GICS", "GICS 대분류", _gics_name(master, sector.gics), "text",
                     "사람 판단", "중", ("config", "gics")))
        add(Evidence("EV-NOTE", "사람이 쓴 근거", sector.note, "text", "사람 판단", "하",
                     ("config", "note")))
        add(Evidence("EV-YAML-LIQ", "sectors.yaml 유동성 경고", bool(sector.liquidity_warning),
                     "bool", "사람 판단", "하", ("config", "liquidity_warning")))
        add(Evidence("EV-ETF-COUNT", "정의한 ETF 수", len(sector.etfs), "int", "사람 판단", "중",
                     ("config", "etf_count"), note=checked))
        add(Evidence("EV-MEMBER-COUNT", "정의한 구성종목 수", len(sector.members), "int",
                     "사람 판단", "중", ("config", "member_count"), note=checked))
        for index, item in enumerate(sector.etfs, 1):
            add(Evidence(f"EV-ETF-{index}", f"ETF {index}", f"{item.name} ({item.code})", "text",
                         "사람 판단", "중", ("config", "etf", index), note=checked))
        for index, item in enumerate(sector.members, 1):
            add(Evidence(f"EV-MEMBER-{index}", f"구성종목 {index}", f"{item.name} ({item.code})",
                         "text", "사람 판단", "중", ("config", "member", index), note=checked))

    if workspace is not None and team_id:
        add(Evidence("EV-COMMENTS", "조원이 붙인 근거 수",
                     len(workspace.comments_of(team_id, sector_id=sector_id)), "int", "조 원장",
                     "상", ("ledger", "comments")))

    return Inventory(sector_id=sector_id, label=label, profile=profile, as_of=as_of,
                     evidence=book, story=story, sector=sector)


def with_past(inventory: Inventory, frame: Any, *, window_days: int, since_confirm: bool,
              workspace: Any = None, team_id: str | None = None,
              ) -> tuple[Inventory, tuple[Gap, ...]]:
    """"무엇이 바뀌었나" 의 과거 한 점을 장부에 더한다.

    🔒 **가까운 날로 바꿔 끼우지 않는다.** 창 밖이거나 그날 행이 없으면 Gap 이다.
    🔴 "확정한 뒤로" 의 기준일은 **확정한 날(KST) 전의 마지막 기준일**이다. 그날 데이터는 다음 날
       아침에 게시되므로 확정 당시 화면에 있던 날에 가장 가깝다 — 그러나 정확히 같다고 말하지
       않는다. 화면이 이 정의를 그대로 적는다.
    """
    days_all = sorted({str(d) for d in frame["bas_dd"]})
    book = dict(inventory.evidence)
    index = days_all.index(inventory.as_of)

    def done(past: str | None, gaps: tuple[Gap, ...]) -> tuple[Inventory, tuple[Gap, ...]]:
        return Inventory(sector_id=inventory.sector_id, label=inventory.label,
                         profile=inventory.profile, as_of=inventory.as_of, evidence=book,
                         story=inventory.story, sector=inventory.sector, past_as_of=past), gaps

    if since_confirm:
        team = workspace.team(team_id) if (workspace is not None and team_id) else None
        if team is None or not team.confirmed_at:
            return done(None, (GAP_CONFIRM,))
        confirmed = kst_date(team.confirmed_at)
        book["EV-CONFIRMED-DATE"] = Evidence(
            "EV-CONFIRMED-DATE", "확정한 날(KST)", confirmed, "date", "조 원장", "상",
            ("ledger", "confirmed_kst_date"))
        earlier = [d for d in days_all if d < confirmed]
        past = earlier[-1] if earlier else None
    else:
        position = index - window_days
        past = days_all[position] if position >= 0 else None
    if past is None:
        return done(None, (GAP_PAST,))

    rows = frame[(frame["bas_dd"].astype(str) == past)
                 & (frame["sector_id"] == inventory.sector_id)]
    if len(rows) == 0:
        return done(None, (GAP_PAST,))
    row = rows.iloc[0]
    profile = inventory.profile
    book["EV-PAST-ASOF"] = Evidence("EV-PAST-ASOF", "비교 기준일", past, "date", "거래소 파생",
                                    "상", ("column", "bas_dd"), past)
    book["EV-WINDOW"] = Evidence("EV-WINDOW", "비교 간격(영업일)", index - days_all.index(past),
                                 "int", "거래소 파생", "상", ("derived", "window"), past)
    book["EV-PAST-RANK"] = Evidence("EV-PAST-RANK", "그때 순위", _int(row.get(f"rank_{profile}")),
                                    "int", "거래소 파생", "상", ("column", f"rank_{profile}"), past)
    book["EV-PAST-SCORE"] = Evidence("EV-PAST-SCORE", "그때 총점",
                                     _int(row.get(f"score_{profile}_bp")), "sigma", "거래소 파생",
                                     "상", ("column", f"score_{profile}_bp"), past)
    for axis in AXES:
        low = axis.lower()
        book[f"EV-PAST-{axis}-Z"] = Evidence(
            f"EV-PAST-{axis}-Z", f"그때 {AXIS_NAMES[axis]} σ", _int(row.get(f"{low}_z_bp")),
            "sigma", "거래소 파생", "상", ("column", f"{low}_z_bp"), past, axis=axis)
    now_rank, past_rank = inventory.value("EV-RANK"), book["EV-PAST-RANK"].value
    if now_rank is not None and past_rank is not None:
        book["EV-RANK-DIFF"] = Evidence("EV-RANK-DIFF", "순위 변화(계단)", abs(past_rank - now_rank),
                                        "int", "거래소 파생", "상", ("derived", "rank_diff"),
                                        inventory.as_of)
    return done(past, ())


# ── Gap Log ─────────────────────────────────────────────────────────────────
# 🔒 **칸에 숫자를 적지 않는다** — guard 가 확인한다. 숫자가 꼭 필요하면 `why_template` 의 자리로 넣는다.
#    규칙 번호처럼 영문자에 붙은 숫자("V5")는 숫자로 치지 않는다.

def gap_axis(axis: str) -> Gap:
    return Gap(
        id=f"GP-AXIS-{axis}", item=f"{AXIS_NAMES[axis]} 축", kind="미확보",
        why="창 안에 관측이 하나라도 빠지면 그 축을 계산하지 않는다 — 전일값으로 메우지 않는다",
        needed="창 안의 모든 영업일 관측",
        workaround="이 축 없이 나머지 축의 가중치를 다시 나눴다",
        affects="총점 · 순위 · 이 축의 서술")


GAP_STAB = Gap(
    id="GP-STAB", item="순위 안정성", kind="미확보",
    why="평균 순위를 낼 만큼 이력이 차지 않았다 — 짧은 이력을 긴 척하지 않는다",
    needed="요청한 창만큼의 영업일 순위",
    workaround="오늘만 반짝인 것인지는 말하지 않는다",
    affects="오늘만 반짝였나 하는 반론")

GAP_LIQ = Gap(
    id="GP-LIQ", item="유동성 판정", kind="미확보",
    why="거래대금 평균을 낼 창이 아직 차지 않았다",
    needed="창 안의 모든 영업일 거래대금",
    workaround="모름으로 둔다 — 미달로도 충분으로도 적지 않는다",
    affects="실제로 거래할 수 있나 하는 반론")

GAP_ETF_N = Gap(
    id="GP-ETF-N", item="점수에 쓴 ETF 수", kind="미확보",
    why="기준일 점수 행에 ETF 수가 비어 있다",
    needed="그날 집계에 들어간 ETF 목록",
    workaround="자금흐름이 몇 종목에 기대는지 말하지 않는다",
    affects="자금흐름이 한 종목에 달렸나 하는 반론")

GAP_AXES = Gap(
    id="GP-AXES", item="점수에 쓴 축", kind="미확보",
    why="기준일 점수 행에 쓴 축 수나 축별 값이 비어 있다",
    needed="그날 채점 결과의 축별 값",
    workaround="축에 기대는 반론은 판정하지 않는다",
    affects="가중치 · 쏠림 · 축 온전성에 대한 반론")

GAP_PAST = Gap(
    id="GP-PAST", item="비교할 과거 기준일", kind="미확보",
    why="요청한 만큼 거슬러 올라가면 게시된 창 밖이거나, 그날 이 섹터의 점수 행이 없다",
    needed="그날의 점수 행",
    workaround="비교하지 않는다 — 가까운 날로 바꿔 끼우지 않는다",
    affects="무엇이 바뀌었나")

GAP_CONFIRM = Gap(
    id="GP-CONFIRM", item="확정한 날", kind="미확보",
    why="이 화면은 조의 확정 기록을 읽지 않았거나, 조가 아직 확정하지 않았다",
    needed="조 원장의 확정 기록 — 조 페이지에서 묻는다",
    workaround="확정 이후 비교를 하지 않는다",
    affects="무엇이 바뀌었나")

GAP_MASTER = Gap(
    id="GP-MASTER", item="섹터 정의", kind="미확보",
    why="sectors.yaml 을 읽지 못했다",
    needed="섹터 정의 파일",
    workaround="이름 대신 코드를 쓰고 구성은 말하지 않는다",
    affects="무엇이 들어 있나")

GAP_INVESTOR = Gap(
    id="GP-INVESTOR", item="투자자별 매매동향 · 외국인 보유", kind="구조적 비공시",
    why="이 도구의 원천에 그 항목을 내는 API 가 없다",
    needed="투자자별 매매 통계 원천",
    workaround="자금흐름은 ETF 상장좌수 변화로만 본다",
    affects="자금흐름 축의 해석",
    basis="KRX Open API 에 투자자별 매매동향 · 외국인 보유량 API 가 없다(확정 사실 V5)")

GAP_FUNDAMENTAL = Gap(
    id="GP-FUNDAMENTAL", item="이익 기반 밸류(PER · PBR)", kind="구조적 비공시",
    why="이 도구의 원천에 이익 · 장부가 통계가 없다",
    needed="재무 원천과 섹터 합산 규칙",
    workaround="밸류 축은 가격이 긴 평균보다 얼마나 위 · 아래인지로 잰다 — 이익 대비 싸다는 뜻이 아니다",
    affects="밸류 축의 해석",
    basis="KRX Open API 에 PER · PBR 이 없다(확정 사실 V5)")

GAP_PDF = Gap(
    id="GP-PDF", item="ETF 실제 구성(PDF) · 지수 구성종목", kind="구조적 비공시",
    why="원천에 구성 정보가 없어 사람이 적었다",
    needed="운용사가 공시하는 ETF 구성 내역",
    workaround="구성종목은 sectors.yaml 에 사람이 적고 원천 유니버스와 대조했다",
    affects="무엇이 들어 있나 · 구성종목 렌즈",
    basis="KRX Open API 에 지수 구성종목 · ETF PDF 가 없다(확정 사실 V5)")

GAP_CAUSE = Gap(
    id="GP-CAUSE", item="왜 그렇게 움직였는가", kind="구조적 비공시",
    why="원인을 말하려면 뉴스 · 공시 본문이 필요한데 이 도구는 그것을 점수 · 서술의 재료로 쓰지 않는다",
    needed="조원이 붙인 근거 링크",
    workaround="원인은 말하지 않는다 — 조원이 근거 붙이기로 단다",
    affects="해석 카드의 원인 가설 칸",
    basis="네이버 뉴스는 저장 · 가공 · AI 입력이 약관으로 금지되고(확정 사실 V6), "
          "공시 건수는 좋은 소식과 나쁜 소식의 부호가 섞여 축이 될 수 없다(AGENTS 확정된 것)")

_FIXED_GAPS: Mapping[str, Gap] = {gap.id: gap for gap in (
    GAP_STAB, GAP_LIQ, GAP_ETF_N, GAP_AXES, GAP_PAST, GAP_CONFIRM, GAP_MASTER, GAP_INVESTOR,
    GAP_FUNDAMENTAL, GAP_PDF, GAP_CAUSE)}


def gap_by_id(gap_id: str) -> Gap:
    """반론의 [추가확인] 이 가리키는 Gap 을 빈칸 목록에 넣을 때 쓴다."""
    if gap_id.startswith("GP-AXIS-"):
        return gap_axis(gap_id.removeprefix("GP-AXIS-"))
    if gap_id not in _FIXED_GAPS:
        raise ValueError(f"여기서 만들 수 없는 Gap 이다: {gap_id}")
    return _FIXED_GAPS[gap_id]


def gap_preset_from(values: Mapping[str, Any]) -> Gap | None:
    """🔒 출처 충돌 — 같은 섹터가 가중치에 따라 다른 자리에 있다. 셋을 다 적는다.

    값 표(근거 ID → 값)를 받는다 — guard 가 원천 값으로 같은 판단을 따로 해 본다.
    """
    keys = tuple(f"EV-RANK-{p.upper()}" for p in PRESETS)
    ranks = [values.get(key) for key in keys]
    if any(rank is None for rank in ranks) or max(ranks) - min(ranks) <= 3:
        return None
    template = ("세 가중치의 순위가 " + " · ".join(f"{slot(key)}위" for key in keys)
                + "로 세 계단 넘게 갈린다")
    return Gap(
        id="GP-PRESET", item="가중치별 순위", kind="출처 충돌",
        why=fill(template, values), why_template=template,
        needed="없다 — 어느 가중치를 쓸지는 판단의 문제다",
        workaround="셋을 함께 보여준다 — 한 값으로 고르지 않는다",
        affects="이 순위를 얼마나 믿나")


def _gap_preset(inventory: Inventory) -> Gap | None:
    return gap_preset_from({k: e.value for k, e in inventory.evidence.items()})


def _gap_yaml_liquidity(inventory: Inventory) -> Gap | None:
    return gap_yaml_liquidity_from({k: e.value for k, e in inventory.evidence.items()})


def gap_yaml_liquidity_from(values: Mapping[str, Any]) -> Gap | None:
    """🔒 출처 충돌 — 사람이 적은 경고와 실측 판정이 다르다(세션 문서 4.5 · 아직 결정 전)."""
    yaml_warn, measured = values.get("EV-YAML-LIQ"), values.get("EV-LIQ")
    if yaml_warn is None or measured is None:
        return None
    if yaml_warn and measured:
        why = "sectors.yaml 은 유동성 경고를 켰는데 실측 거래대금은 기준을 넘는다"
    elif not yaml_warn and not measured:
        why = "sectors.yaml 에는 경고가 없는데 실측 거래대금이 기준에 못 미친다"
    else:
        return None
    return Gap(
        id="GP-YAML-LIQ", item="유동성 경고", kind="출처 충돌", why=why,
        needed="어느 쪽을 정본으로 둘지의 결정",
        workaround="둘 다 보여준다 — 판정은 실측이고 yaml 경고는 사람이 적은 과거 판단이다",
        affects="유동성 경고 배지")


def gaps_for(inventory: Inventory, intent: str) -> tuple[Gap, ...]:
    """그 질문이 채우지 못한 것. 🔒 순서가 고정이다 — 골든이 흔들리지 않게."""
    out: list[Gap] = []
    missing = axes_in(inventory.value("EV-MISSING"))
    if intent in ("why_rank", "trust"):
        out.extend(gap_axis(axis) for axis in missing)
        if inventory.value("EV-STAB-MEAN") is None:
            out.append(GAP_STAB)
    if intent in ("trust", "contents") and inventory.value("EV-LIQ") is None:
        out.append(GAP_LIQ)
    if intent in ("why_rank", "changed"):
        out.append(GAP_CAUSE)
    if intent == "trust":
        out.extend([GAP_INVESTOR, GAP_FUNDAMENTAL])
        preset = _gap_preset(inventory)
        if preset is not None:
            out.append(preset)
    if intent in ("trust", "contents"):
        conflict = _gap_yaml_liquidity(inventory)
        if conflict is not None:
            out.append(conflict)
    if intent == "contents":
        out.append(GAP_PDF if inventory.sector is not None else GAP_MASTER)
    return tuple(out)
