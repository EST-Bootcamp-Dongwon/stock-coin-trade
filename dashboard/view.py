"""화면에 그릴 **표를 만드는 순수 함수들.**

## 🔒 왜 Streamlit 과 갈라 두는가

`st.*` 가 섞이면 화면 없이 테스트할 수 없고, 그러면 "1위가 왜 1위인가" 를 검증하는
비용이 화면을 띄우는 비용이 된다. 여기 있는 것은 **프레임을 받아 프레임을 돌려주는**
함수뿐이다. 페이지는 이것을 그리기만 한다.

## 🔴 순위 안정성을 왜 같이 내는가

신고서는 나흘 뒤지만 **운용은 3개월이다.** 하루치 순위로 핵심 섹터를 고르면
"오늘만 1등" 과 "계속 1등" 을 구별하지 못한다. 그 차이가 대회 3개월을 가른다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from sector.scoring import AXES, PRESETS

__all__ = ["PROFILES", "Names", "latest_frame", "rank_stability", "stability_window",
           "sector_story", "podium", "score_bars", "arithmetic_table",
           "ranking_table", "axis_breakdown"]


@dataclass(frozen=True, slots=True)
class Names:
    """식별자 → **한국어 이름.**

    🔴 팀은 한국어로 종목을 찾는다. `steel` · `Materials` 는 코드이지 사람이 쓰는
       말이 아니다. 그렇다고 코드를 지우지는 않는다 — `sectors.yaml` 과 원장에는
       코드가 들어가고, 화면에서 둘을 대조할 수 있어야 한다.
    """

    sector: Mapping[str, str]
    gics: Mapping[str, str]

    @classmethod
    def of(cls, master: Any) -> "Names":
        return cls(
            sector={s.id: s.name_ko for s in master.sectors},
            gics={g.id: g.name_ko for g in master.gics_sectors},
        )

    @classmethod
    def empty(cls) -> "Names":
        return cls(sector={}, gics={})

    def sector_label(self, sector_id: str) -> str:
        """`철강` — 이름이 없으면 코드를 그대로 준다. 🔒 지어내지 않는다."""
        return self.sector.get(sector_id, sector_id)

    def sector_full(self, sector_id: str) -> str:
        """`철강 (steel)` — 코드까지 봐야 하는 자리에서 쓴다."""
        name = self.sector.get(sector_id)
        return f"{name} ({sector_id})" if name else sector_id

    def gics_label(self, gics: str) -> str:
        return self.gics.get(gics, gics)

#: 🔒 **셋을 동시에 보여준다.** 하나만 보면 가중치를 바꿔 아무 섹터나 1위로 만들 수 있고,
#:    셋 다 상위인 섹터가 있다면 그것은 가중치에 기대지 않는 신호다 (계획서 R11).
PROFILES: tuple[str, ...] = tuple(PRESETS)

_AXIS_PREFIX = {"M": "m", "F": "f", "B": "b", "V": "v"}


def latest_frame(frame: Any) -> Any:
    """마지막 기준일의 21행만."""
    return frame[frame["bas_dd"] == frame["bas_dd"].max()].copy()


def rank_stability(frame: Any, *, profile: str = "balanced", days: int = 20) -> Any:
    """최근 `days` 영업일의 평균 순위와 진폭(표준편차).

    🔒 `days` 가 부족한 섹터는 **채우지 않는다** — 결과가 `NaN` 으로 남고 화면이
       `—` 로 그린다. 짧은 이력을 긴 이력인 척하면 안정성이 거짓이 된다.

    🔴 **요청한 `days` 만큼 이력이 없을 수 있다.** 그때 조용히 9일로 계산하고
       "최근 20영업일" 이라 이름 붙이면 그것이 곧 거짓말이다. 그래서 `rank_days`
       열에 **실제로 몇 일을 봤는지** 담아 돌려주고, 화면이 그 숫자를 쓴다.
    """
    import pandas as pd

    column = f"rank_{profile}"
    recent_days = sorted(frame["bas_dd"].unique())[-days:]
    recent = frame[frame["bas_dd"].isin(recent_days)]
    grouped = recent.groupby("sector_id")[column]
    out = pd.DataFrame({
        "rank_mean": grouped.mean(),
        "rank_spread": grouped.std(ddof=0),
        "rank_days": grouped.count(),
    })
    # 창이 안 찬 섹터는 평균을 내지 않는다 (머리주석)
    short = out["rank_days"] < len(recent_days)
    out.loc[short, ["rank_mean", "rank_spread"]] = float("nan")
    return out


def stability_window(frame: Any, *, days: int) -> int:
    """실제로 쓸 수 있는 영업일 수. 요청한 `days` 보다 작을 수 있다."""
    return min(days, len(frame["bas_dd"].unique()))


def ranking_table(frame: Any, *, profile: str = "balanced", days: int = 20,
                  names: Names | None = None) -> Any:
    """랭킹 화면이 그대로 그리는 표.

    🔒 열을 여기서 고른다 — 페이지가 프레임을 자유롭게 파면 화면마다 다른 열이
       나오고, "무엇을 보여주는가" 를 아무도 결정하지 않은 상태가 된다.
    """
    names = names or Names.empty()
    latest = latest_frame(frame).set_index("sector_id")
    stability = rank_stability(frame, profile=profile, days=days)
    joined = latest.join(stability, how="left")
    # 🔴 한국어 이름을 열로 넣는다. 코드는 색인으로 남아 원장·yaml 과 대조된다
    joined.insert(0, "섹터", [names.sector_label(sid) for sid in joined.index])
    joined["gics"] = [names.gics_label(g) for g in joined["gics"]]

    columns = {
        "섹터": "섹터",
        f"rank_{profile}": "순위",
        "gics": "GICS",
        f"score_{profile}_bp": "점수bp",
        "rank_mean": "평균순위",
        "rank_spread": "진폭",
        "rank_days": "표본일",     # 🔴 평균을 실제로 몇 일에서 냈는지
        **{f"{_AXIS_PREFIX[a]}_z_bp": f"{a}" for a in AXES},
        "liquidity_ok": "유동성",
        "etf_n": "ETF수",
        "n_axes_used": "축수",
        "axes_missing": "결측축",
        "axes_degraded": "강등축",
        "is_partial": "부분",
    }
    table = joined[list(columns)].rename(columns=columns)
    return table.sort_values("순위")


def axis_breakdown(frame: Any, sector_id: str, *, profile: str = "balanced") -> list[dict[str, Any]]:
    """한 섹터의 4축 분해 — 원시값 · z · 가중치 · 기여.

    🔴 **기여도가 이 표의 요점이다.** "왜 1위인가" 는 어느 축이 점수를 끌어올렸나로
       답해진다. 결측 축은 가중치를 다시 나누므로 기여 합이 점수와 맞는다.
    """
    latest = latest_frame(frame)
    rows = latest[latest["sector_id"] == sector_id]
    if len(rows) == 0:
        return []
    row = rows.iloc[0]
    weights = PRESETS[profile]

    live = [a for a in AXES if _notna(row.get(f"{_AXIS_PREFIX[a]}_z_bp"))]
    total_weight = sum(weights[a] for a in live) or 1

    out = []
    for axis in AXES:
        z_raw = row.get(f"{_AXIS_PREFIX[axis]}_z_bp")
        raw = row.get(f"{_AXIS_PREFIX[axis]}_raw_bp")
        has = _notna(z_raw)
        z = int(z_raw) if has else None
        out.append({
            "axis": axis,
            "raw_bp": int(raw) if _notna(raw) else None,
            "z_bp": z,
            "weight": weights[axis],
            # 결측 축은 분모에서 빠졌으므로 기여도 0 이 아니라 **없음**이다
            "contribution_bp": int(round(z * weights[axis] / total_weight)) if has else None,
            "rank": _axis_rank(latest, axis, z) if has else None,
        })
    return out


def _notna(value: Any) -> bool:
    import pandas as pd

    return value is not None and not pd.isna(value)


def _axis_rank(latest: Any, axis: str, z_bp: int | None) -> int | None:
    """그 축의 횡단면 순위. 🔒 동점은 같은 순위를 준다 — 임의로 가르지 않는다."""
    if z_bp is None:
        return None
    column = latest[f"{_AXIS_PREFIX[axis]}_z_bp"].dropna()
    return int((column > z_bp).sum()) + 1


# ── 등수와 서술을 위한 재료 ──────────────────────────────────────────────────
# 🔴 **페이지마다 조립하지 않는다.** 랭킹·확정·읽는법 세 화면이 같은 섹터를 두고
#    서로 다른 재료로 서로 다른 말을 하면, 팀원은 어느 쪽이 맞는지 판단할 방법이
#    없다. 조립은 여기 한 곳이고 화면은 그리기만 한다 (머리주석과 같은 이유).

def sector_story(frame: Any, sector_id: str, *, profile: str = "balanced",
                 days: int = 20, names: Names | None = None) -> dict[str, Any]:
    """`explain.narrative(**story)` 에 그대로 넘길 재료.

    🔒 없는 값은 **`None` 으로 남긴다.** 0 으로 바꾸면 서술이 "자금흐름이 0 이다"
       라고 단언하는데, 실제로는 재지 못한 것이다 (ADR-SC-0007).
    """
    names = names or Names.empty()
    latest = latest_frame(frame).set_index("sector_id")
    if sector_id not in latest.index:
        return {}
    row = latest.loc[sector_id]
    stability = rank_stability(frame, profile=profile, days=days)
    stat = stability.loc[sector_id] if sector_id in stability.index else None

    return {
        "label": names.sector_label(sector_id),
        "rank": _int_or_none(row.get(f"rank_{profile}")),
        "total": len(latest),
        "score_bp": _int_or_none(row.get(f"score_{profile}_bp")),
        "parts": axis_breakdown(frame, sector_id, profile=profile),
        "mean_rank": _float_or_none(stat["rank_mean"]) if stat is not None else None,
        "spread": _float_or_none(stat["rank_spread"]) if stat is not None else None,
        "window": stability_window(frame, days=days),
        "liquidity_ok": _bool_or_none(row.get("liquidity_ok")),
        "etf_n": _int_or_none(row.get("etf_n")),
        "missing": row.get("axes_missing") or None,
        "degraded": row.get("axes_degraded") or None,
    }


def podium(frame: Any, *, profile: str = "balanced", top: int = 3,
           names: Names | None = None) -> list[dict[str, Any]]:
    """상위 `top` 섹터 — **등수 카드가 그리는 것.**

    🔴 표만 있으면 21행을 눈으로 훑어야 "1등이 누구인가" 를 안다. 개발자가 아닌
       팀원 7명에게 그 훑기가 곧 진입 장벽이다. 그래서 맨 위 셋을 크게 뽑는다.

    🔒 등수만 크게 그리고 끝내지 않는다 — `lead_axis`(무엇이 끌어올렸나)와
       `liquidity_ok`(실제로 살 수 있나)를 함께 담는다. 등수만 보면 "1위 = 사면
       오른다" 로 읽히고, 그것이 이 도구의 가장 큰 위험이다.
    """
    names = names or Names.empty()
    latest = latest_frame(frame).set_index("sector_id")
    column = f"rank_{profile}"
    ordered = latest[latest[column].notna()].nsmallest(top, column)

    out = []
    for sector_id in ordered.index:
        row = ordered.loc[sector_id]
        parts = axis_breakdown(frame, sector_id, profile=profile)
        scored = [p for p in parts if p["contribution_bp"] is not None]
        best = max(scored, key=lambda p: p["contribution_bp"]) if scored else None
        out.append({
            "sector_id": sector_id,
            "label": names.sector_label(sector_id),
            "rank": _int_or_none(row[column]),
            "score_bp": _int_or_none(row[f"score_{profile}_bp"]),
            # 🔒 기여가 음수뿐이면 "끌어올린 축" 은 없다. 지어내지 않는다
            "lead_axis": best["axis"] if best and best["contribution_bp"] > 0 else None,
            "liquidity_ok": _bool_or_none(row.get("liquidity_ok")),
            "etf_n": _int_or_none(row.get("etf_n")),
        })
    return out


def score_bars(frame: Any, *, profile: str = "balanced",
               names: Names | None = None) -> Any:
    """막대 차트가 그대로 받는 프레임 — 색인은 한국어 이름, 값은 σ.

    🔴 **bp 가 아니라 σ 로 넘긴다.** 막대는 눈금을 읽히려고 두는 것이 아니라
       간격을 보이려고 두는 것이고, `12522` 는 사람이 즉시 못 읽는다. σ 는
       읽는법 화면이 이미 가르친 단위다.

    🔒 순위 순서 그대로 돌려준다. 화면은 `sort=False` 로 그려 이 순서를 지킨다 —
       알파벳순으로 다시 정렬되면 "위에서부터 1등" 이라는 유일한 읽는 법이 깨진다.
    """
    import pandas as pd

    names = names or Names.empty()
    latest = latest_frame(frame).set_index("sector_id")
    column = f"score_{profile}_bp"
    ordered = latest[latest[f"rank_{profile}"].notna()].sort_values(f"rank_{profile}")
    return pd.DataFrame(
        {"점수(σ)": [_float_or_none(v) / 10000 if _float_or_none(v) is not None else None
                     for v in ordered[column]]},
        index=pd.Index([names.sector_label(s) for s in ordered.index], name="섹터"),
    )


def _int_or_none(value: Any) -> int | None:
    return int(value) if _notna(value) else None


def _float_or_none(value: Any) -> float | None:
    return float(value) if _notna(value) else None


def _bool_or_none(value: Any) -> bool | None:
    return bool(value) if _notna(value) else None


def arithmetic_table(frame: Any, sector_id: str, *, profile: str = "balanced") -> Any:
    """한 섹터의 **산수를 그대로 편 표** — 원시값 → σ → 가중치 → 기여.

    🔴 읽는법 화면의 예시가 이것을 쓴다. 팀원이 가장 자주 묻는 것은 "이 숫자가
       어디서 나왔나" 이고, 그 답은 설명이 아니라 **덧셈이 맞아떨어지는 것을
       보여주는 일**이다 — 기여의 합이 총점과 정확히 같다
       (`test_기여도_합이_점수와_같다` 가 그것을 고정한다).

    🔒 결측 축은 기여가 `None` 이라 합에서 빠진다. 0 으로 적으면 덧셈은 맞아 보이지만
       "재지 못한 축" 과 "0 인 축" 이 화면에서 같아진다.
    """
    import pandas as pd

    from dashboard.explain import axis_raw_text
    from sector.scoring import AXIS_NAMES

    parts = axis_breakdown(frame, sector_id, profile=profile)
    return pd.DataFrame(
        [{
            "축": f"{AXIS_NAMES[p['axis']]} ({p['axis']})",
            "① 원시값": axis_raw_text(p["axis"], p["raw_bp"]),
            "② σ": None if p["z_bp"] is None else p["z_bp"] / 10000,
            "③ 가중치": p["weight"],
            "④ 기여(bp)": p["contribution_bp"],
        } for p in parts]
    ).set_index("축")
