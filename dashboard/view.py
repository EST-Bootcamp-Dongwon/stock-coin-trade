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

from dashboard.weights import Weighting
from sector.scoring import AXES, PRESETS, rank_scores, weighted_score_bp

__all__ = ["PROFILES", "SCORE_COLUMN", "RANK_COLUMN", "UNCLASSIFIED", "Names", "ViewError",
           "latest_frame", "scored", "rank_stability", "stability_window",
           "sector_story", "podium", "score_bars", "arithmetic_table",
           "ranking_table", "axis_breakdown", "gics_options", "visible_ids",
           "gics_distribution", "int_or_none"]


class ViewError(RuntimeError):
    """화면에 그릴 수 없는 입력이다. 🔒 조용히 한 줄 버리고 그리지 않는다."""


#: `scored()` 가 붙이는 열. 🔒 **새 이름**이다 — `score_balanced_bp` 를 덮어쓰지 않는다.
#:    덮으면 에이전트 guard 의 "`view` 를 거치지 않고 원천에서 다시 얻는다"
#:    (ADR-SC-0013 ④-1)가 그 순간 거짓이 된다.
SCORE_COLUMN = "score_bp"
RANK_COLUMN = "rank"


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


def scored(frame: Any, weighting: Weighting) -> Any:
    """`score_bp` · `rank` 열을 붙인 프레임. **화면의 표·막대·등수는 이것만 읽는다.**

    ## 🔴 프리셋은 저장된 열을 그대로 읽는다 — 다시 계산하지 않는다

    앱이 프리셋까지 재계산하면, HF 에 **옛 파생본**이 올라가 있는 동안 화면 위쪽
    표(재계산)와 아래쪽 근거·에이전트(저장 열)가 **다른 숫자를 말한다.** 2026-09-17
    실측에서 최근 20영업일 창의 첫날이 갈려 한 섹터의 순위 진폭이 표에서 4.4,
    근거에서 4.5 로 나왔다. guard 는 장부와 저장 열을 대조하므로 그것을 못 잡는다.

    그래서 프리셋은 **게시된 값이 정답**이고, 그 값이 게시된 z 로 재현된다는 보증은
    화면이 아니라 **게시 게이트**(`gate._check_score_reproducible`)가 선다.

    커스텀 가중치에는 저장된 열이 없으므로 그때만 `weighted_score_bp` 로 다시 낸다 —
    배치가 쓰는 바로 그 함수다. 🔒 두 화면이 점수를 각자 구현하지 않는다(`AGENTS.md` 6장).
    """
    import pandas as pd

    out = frame.copy()
    # 🔒 **자리로 넣는다.** 색인 라벨로 맞추면 색인이 중복된 프레임에서 한 값이 여러
    #    행으로 퍼진다 — 실제로 커스텀 경로가 21행에 같은 점수를 조용히 넣었다(2026-09-17).
    #    `.array` 는 `Int64` 를 지키면서 자리로 들어간다.
    if weighting.is_preset:
        out[SCORE_COLUMN] = frame[weighting.column("score")].array
        out[RANK_COLUMN] = frame[weighting.column("rank")].array
        return out

    # 🔴 **열을 먼저 파이썬 리스트로 꺼낸다.** `itertuples` 를 날짜별 그룹마다 부르면 pandas 가
    #    **그룹마다 열 수만큼** `_ixs` 를 탄다 — 285그룹 × 26열 = **7,411번**(실측 · 새 경로는 6번).
    #    361ms 였고, 슬라이더는 한 칸 움직일 때마다 rerun 이라 그대로 체감 지연이 됐다 (이슈 #1).
    #    ⚠️ 비용이 **그룹 수**에 비례하므로 창을 자르면 줄기는 한다 — 그래도 자르지 않는다.
    #       이유는 성능이 아니라 계약이다(ADR-SC-0014 ⑦ — 하단에는 원본 프레임이 간다).
    #    🔒 결과는 글자 그대로 같다 — 같은 `weighted_score_bp` · `rank_scores` 에 같은 값을 준다
    days = [str(d) for d in frame["bas_dd"]]
    sector_ids = [str(v) for v in frame["sector_id"]]
    z_of_axis = {a: _int_list(frame[f"{_AXIS_PREFIX[a]}_z_bp"]) for a in AXES}

    #: 자리(0..n-1)를 날짜별로 묶는다. 🔒 **자리로 다룬다** — 색인 라벨로 맞추면 색인이
    #:  중복된 프레임에서 한 값이 여러 행으로 퍼진다(2026-09-17에 실제로 그랬다)
    by_day: dict[str, list[int]] = {}
    for position, day in enumerate(days):
        by_day.setdefault(day, []).append(position)

    scores: list[int | None] = [None] * len(days)
    ranks: list[int | None] = [None] * len(days)
    for day, positions in by_day.items():
        again: dict[str, int | None] = {}
        at: dict[str, int] = {}
        for position in positions:
            sector_id = sector_ids[position]
            if sector_id in again:
                # 🔴 하루에 같은 섹터가 둘이면 순위가 조용히 한 줄을 잃는다
                raise ViewError(f"{day} 에 섹터 {sector_id} 가 두 번 있다 — 파생본이 깨졌다")
            at[sector_id] = position
            again[sector_id] = weighted_score_bp(
                {a: z_of_axis[a][position] for a in AXES}, weighting.weights)
        ranked = rank_scores(again)
        for sector_id, position in at.items():
            scores[position] = again[sector_id]
            ranks[position] = ranked[sector_id]

    # 🔒 nullable `Int64` 다. `None` 이 섞인 정수 열을 pandas 가 `float64` 로 올리면
    #    규약이 금지한 float 가 화면 계층에 들어온다 (V26 · `AGENTS.md` 4장)
    out[SCORE_COLUMN] = pd.array(scores, dtype="Int64")
    out[RANK_COLUMN] = pd.array(ranks, dtype="Int64")
    return out


def gics_options(frame: Any, names: "Names | None" = None) -> list[tuple[str, str]]:
    """그날 표에 실재하는 GICS 대분류 — `(id, 한국어 이름)` 을 이름순으로.

    🔒 `sectors.yaml` 전체가 아니라 **프레임에 있는 것**만 준다. 없는 대분류를
       필터 목록에 두면 고르는 순간 표가 비고, 사용자는 이유를 모른다.
    """
    names = names or Names.empty()
    present = sorted({str(g) for g in latest_frame(frame)["gics"] if str(g)})
    return [(g, names.gics_label(g)) for g in present]


def visible_ids(frame: Any, *, gics: "frozenset[str] | None" = None,
                hide_illiquid: bool = False, hide_single_etf: bool = False) -> frozenset[str]:
    """화면에 **보일** 섹터 id. 🔴 점수를 다시 매기지 않는다 — 행을 숨기는 판정뿐이다.

    🔴 **`liquidity_ok is False` 만 숨긴다.** `None` 은 20영업일 창이 안 차서 아직
       판정할 수 없다는 뜻이고, 미달과 같은 것이 아니다 (ADR-SC-0007 ·
       `scoring._liquidity_ok` · `test_유동성은_판정불가와_미달을_구별한다`).
       둘을 한 조건에 묶으면 신규 상장 ETF 가 이유 없이 화면에서 사라진다.
    """
    latest = latest_frame(frame).set_index("sector_id")
    keep = set(latest.index)
    if gics:
        keep &= {sid for sid in latest.index if str(latest.loc[sid, "gics"]) in gics}
    if hide_illiquid:
        keep -= {sid for sid in latest.index
                 if _bool_or_none(latest.loc[sid, "liquidity_ok"]) is False}
    if hide_single_etf:
        keep -= {sid for sid in latest.index if int_or_none(latest.loc[sid, "etf_n"]) == 1}
    return frozenset(keep)


#: 🔒 대분류가 비어 있는 섹터의 이름. **숨기지 않고 드러낸다** — 값이 없는 것도 사실이다
UNCLASSIFIED = "미분류"


def _gics_name(gics: Any, names: "Names") -> str:
    """대분류 id → 화면 이름. 🔒 비었거나 결측이면 `미분류` 다."""
    if not _notna(gics) or not str(gics).strip():
        return UNCLASSIFIED
    return names.gics_label(str(gics))


def gics_distribution(frame: Any, *, names: "Names | None" = None,
                      only: "frozenset[str] | None" = None) -> Any:
    """GICS 대분류별 **테마 순위의 분포.** 🔒 입력은 `scored()` 를 지난 프레임이다.

    ## 🔴 대분류 점수를 내지 않는다 — 요약 통계도 만들지 않는다

    대분류 ETF 는 살 수 없어 **매매 단위가 아니다**(ADR-SC-0014 ⑤). 그래서 롤업 점수를
    내지 않는다. 평균·중위도 두지 않는다 — 21개 테마가 9개 대분류에 7/3/2/2/2/2/1/1/1 로
    흩어져 있어(20260910 실측) n≤7 에서 요약값은 정보를 늘리지 않으면서 **점수처럼
    읽힌다.** 그것이 금지한 롤업으로 미끄러지는 길이다.

    `소재 2개 — 1 · 2` 한 줄이 곧 결론이다. **목록이 분포다.**

    🔒 정렬 정본은 정수 열 `최고순위`다. 목록 문자열(`순위`)은 **표시 전용**이다 —
       화면에서 그 열로 다시 정렬하면 사전순이 되어 `"10 · …"` 이 `"2 · …"` 앞에 온다.

    🔒 필터를 따른다(`only=`) — 표·등수·막대와 **같은 규칙**이다. 한 화면에서 칸마다
       규칙이 다르면 캡션으로 설명될 차이가 아니다. 🔴 그래도 **순위 자체는 그날 21개
       횡단면 값** 그대로다. 행을 숨길 뿐 다시 매기지 않는다.

    🔒 에이전트는 이 표를 읽지 않는다 — 장부가 대조할 저장 열이 없다(ADR-SC-0013 ④-1).
    """
    import pandas as pd

    names = names or Names.empty()
    latest = latest_frame(frame).set_index("sector_id")
    pool = latest if only is None else latest[latest.index.isin(only)]

    rows = []
    best: list[int | None] = []
    # 🔴 **화면 이름으로 묶는다.** 원시 `gics` 로 묶으면 두 가지가 깨진다 —
    #    ① pandas 기본 `dropna=True` 가 NaN 그룹을 버려 그 섹터가 화면에서 **조용히
    #       사라지고** "테마수 합계 = 보이는 섹터 수" 가 깨진다(적대적 리뷰 실측: 3개 중 2개)
    #    ② `""` 와 `None` 이 각각 그룹이 되어 `미분류` 가 **두 줄**로 나오고 색인이
    #       중복돼 `table.loc["미분류"]` 가 Series 가 아니라 DataFrame 이 된다
    #    🔒 이름으로 묶으면 색인이 **구조적으로** 유일하다. 서로 다른 id 가 같은
    #       한국어 이름을 갖는 것은 `sector_master._check_ids` 가 막는다
    #    🔴 **`Series` 로 넘긴다.** 리스트로 주면 pandas 가 "그루퍼 목록" 으로 읽어
    #       그룹이 하나일 때 키가 `('Industrials',)` **튜플**로 나오고 색인이 그 꼴이 된다
    #       (실측 — 필터로 한 대분류만 남긴 화면에서 그랬다)
    labels = pd.Series([_gics_name(g, names) for g in pool["gics"]],
                       index=pool.index, name="대분류")
    for label, block in pool.groupby(labels, sort=False):
        ranks = sorted(int_or_none(v) for v in block[RANK_COLUMN] if _notna(v))
        # 🔒 순위가 없는 섹터를 **최악 순위로 취급하지 않는다**. 따로 센다 (ADR-SC-0007)
        best.append(ranks[0] if ranks else None)
        rows.append({
            "대분류": str(label),
            "테마수": len(block),
            "순위": " · ".join(str(r) for r in ranks) if ranks else "",
            "순위없음": len(block) - len(ranks),
        })
    # 🔴 **`최고순위` 를 프레임 밖에서 만든다.** `rows` 에 섞어 넣으면 `DataFrame` 이
    #    `None` 을 `nan` 으로 눕혀 `float64` 열이 되고, 규약이 금지한 float 가 화면
    #    계층에 들어온다(V26). 실제로 `int(nan)` 에서 `ValueError` 로 터졌다
    table = pd.DataFrame(rows, columns=["대분류", "테마수", "순위", "순위없음"])
    table.insert(2, "최고순위", pd.array(best, dtype="Int64"))
    if len(table) == 0:
        return table.set_index("대분류")
    # 🔒 순위가 하나도 없는 대분류는 맨 뒤 — 0 이나 999 로 채우지 않는다
    return table.sort_values("최고순위", na_position="last").set_index("대분류")


def rank_stability(frame: Any, *, days: int = 20) -> Any:
    """최근 `days` 영업일의 평균 순위와 진폭(표준편차). 🔒 입력은 `scored()` 를 지난 프레임이다.

    🔒 `days` 가 부족한 섹터는 **채우지 않는다** — 결과가 `NaN` 으로 남고 화면이
       `—` 로 그린다. 짧은 이력을 긴 이력인 척하면 안정성이 거짓이 된다.

    🔴 **요청한 `days` 만큼 이력이 없을 수 있다.** 그때 조용히 9일로 계산하고
       "최근 20영업일" 이라 이름 붙이면 그것이 곧 거짓말이다. 그래서 `rank_days`
       열에 **실제로 몇 일을 봤는지** 담아 돌려주고, 화면이 그 숫자를 쓴다.
    """
    import pandas as pd

    column = RANK_COLUMN
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


def ranking_table(frame: Any, *, days: int = 20, names: Names | None = None) -> Any:
    """랭킹 화면이 그대로 그리는 표. 🔒 입력은 `scored()` 를 지난 프레임이다.

    🔒 열을 여기서 고른다 — 페이지가 프레임을 자유롭게 파면 화면마다 다른 열이
       나오고, "무엇을 보여주는가" 를 아무도 결정하지 않은 상태가 된다.

    🔴 **필터링은 여기서 하지 않는다.** 순위·점수는 언제나 그날 21개 섹터 횡단면에서
       나온 값이고, 필터는 페이지가 **행을 숨기는** 일일 뿐이다. 여기서 걸러 버리면
       z 와 순위가 고른 범위 안에서 다시 매겨진 것처럼 읽힌다.
    """
    names = names or Names.empty()
    latest = latest_frame(frame).set_index("sector_id")
    stability = rank_stability(frame, days=days)
    joined = latest.join(stability, how="left")
    # 🔴 한국어 이름을 열로 넣는다. 코드는 색인으로 남아 원장·yaml 과 대조된다
    joined.insert(0, "섹터", [names.sector_label(sid) for sid in joined.index])
    joined["gics"] = [names.gics_label(g) for g in joined["gics"]]

    columns = {
        "섹터": "섹터",
        RANK_COLUMN: "순위",
        "gics": "GICS",
        SCORE_COLUMN: "점수bp",
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


def axis_breakdown(frame: Any, sector_id: str, *,
                   weights: Mapping[str, int]) -> list[dict[str, Any]]:
    """한 섹터의 4축 분해 — 원시값 · z · 가중치 · 기여.

    🔴 **기여도가 이 표의 요점이다.** "왜 1위인가" 는 어느 축이 점수를 끌어올렸나로
       답해진다. 결측 축은 가중치를 다시 나누므로 기여 합이 점수와 맞는다.
    """
    latest = latest_frame(frame)
    rows = latest[latest["sector_id"] == sector_id]
    if len(rows) == 0:
        return []
    row = rows.iloc[0]

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


def _int_list(column: Any) -> list[int | None]:
    """열 하나를 `int | None` 리스트로. 🔒 결측은 **`None`** 이지 0 이 아니다.

    🔴 `Int64` 열의 `.tolist()` 는 결측을 `pd.NA` 로 준다. `float64` 로 올라온 열은
       `nan` 이다 — 둘을 한 번에 접는다.
    """
    import pandas as pd

    return [None if v is None or pd.isna(v) else int(v) for v in column.tolist()]


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
    # 🔒 **프리셋 전용이다.** 에이전트 guard 가 근거의 출처를 `rank_{profile}` 이라는
    #    저장 열 이름으로 적으므로(ADR-SC-0013 · `inventory.py`), 이름 없는 가중치로는
    #    대조할 원천이 없다. 그래서 여기서 `scored()` 를 프리셋으로 한 번 지난다
    stability = rank_stability(scored(frame, Weighting.preset(profile)), days=days)
    stat = stability.loc[sector_id] if sector_id in stability.index else None

    return {
        "label": names.sector_label(sector_id),
        "rank": int_or_none(row.get(f"rank_{profile}")),
        "total": len(latest),
        "score_bp": int_or_none(row.get(f"score_{profile}_bp")),
        "parts": axis_breakdown(frame, sector_id, weights=PRESETS[profile]),
        "mean_rank": _float_or_none(stat["rank_mean"]) if stat is not None else None,
        "spread": _float_or_none(stat["rank_spread"]) if stat is not None else None,
        "window": stability_window(frame, days=days),
        "liquidity_ok": _bool_or_none(row.get("liquidity_ok")),
        "etf_n": int_or_none(row.get("etf_n")),
        "missing": row.get("axes_missing") or None,
        "degraded": row.get("axes_degraded") or None,
    }


def podium(frame: Any, *, weighting: Weighting, top: int = 3,
           names: Names | None = None,
           only: "frozenset[str] | None" = None) -> list[dict[str, Any]]:
    """상위 `top` 섹터 — **등수 카드가 그리는 것.**

    🔴 표만 있으면 21행을 눈으로 훑어야 "1등이 누구인가" 를 안다. 개발자가 아닌
       팀원 7명에게 그 훑기가 곧 진입 장벽이다. 그래서 맨 위 셋을 크게 뽑는다.

    🔒 등수만 크게 그리고 끝내지 않는다 — `lead_axis`(무엇이 끌어올렸나)와
       `liquidity_ok`(실제로 살 수 있나)를 함께 담는다. 등수만 보면 "1위 = 사면
       오른다" 로 읽히고, 그것이 이 도구의 가장 큰 위험이다.
    """
    names = names or Names.empty()
    latest = latest_frame(frame).set_index("sector_id")
    column = RANK_COLUMN
    pool = latest if only is None else latest[latest.index.isin(only)]
    ordered = pool[pool[column].notna()].nsmallest(top, column)

    out = []
    for sector_id in ordered.index:
        row = ordered.loc[sector_id]
        # 🔴 `frame` 을 넘긴다 — 걸러진 것이 아니다. `axis_breakdown` 의 축 순위는
        #    그날 21개 횡단면에서 나와야 한다. 고른 범위에서 다시 세면 "F 축 1위"가
        #    "고른 다섯 중 1위"가 되고, 화면은 그 차이를 말하지 않는다
        parts = axis_breakdown(frame, sector_id, weights=weighting.weights)
        scored = [p for p in parts if p["contribution_bp"] is not None]
        best = max(scored, key=lambda p: p["contribution_bp"]) if scored else None
        out.append({
            "sector_id": sector_id,
            "label": names.sector_label(sector_id),
            "rank": int_or_none(row[column]),
            "score_bp": int_or_none(row[SCORE_COLUMN]),
            # 🔒 기여가 음수뿐이면 "끌어올린 축" 은 없다. 지어내지 않는다
            "lead_axis": best["axis"] if best and best["contribution_bp"] > 0 else None,
            "liquidity_ok": _bool_or_none(row.get("liquidity_ok")),
            "etf_n": int_or_none(row.get("etf_n")),
        })
    return out


def score_bars(frame: Any, *, names: Names | None = None,
               only: "frozenset[str] | None" = None) -> Any:
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
    column = SCORE_COLUMN
    pool = latest if only is None else latest[latest.index.isin(only)]
    ordered = pool[pool[RANK_COLUMN].notna()].sort_values(RANK_COLUMN)
    return pd.DataFrame(
        {"점수(σ)": [_float_or_none(v) / 10000 if _float_or_none(v) is not None else None
                     for v in ordered[column]]},
        index=pd.Index([names.sector_label(s) for s in ordered.index], name="섹터"),
    )


def int_or_none(value: Any) -> int | None:
    """표의 한 칸을 `int | None` 으로. 🔒 **결측을 파이썬 `None` 으로 내리는 유일한 문.**

    🔴 **공개다.** 화면이 `int(table.loc[sid, "순위"])` 처럼 칸을 직접 캐스팅하면
       `pd.NA` 에서 `TypeError` 로 **페이지가 통째로 죽는다** — 실제로 랭킹 화면이
       세 자리에서 그랬다(이슈 #3). 파생본의 정수 열은 전부 nullable `Int64` 이고
       결측은 정상 상태다(창이 안 찬 초기 영업일 · 새로 넣은 섹터).
    """
    return int(value) if _notna(value) else None


def _float_or_none(value: Any) -> float | None:
    return float(value) if _notna(value) else None


def _bool_or_none(value: Any) -> bool | None:
    return bool(value) if _notna(value) else None


def arithmetic_table(frame: Any, sector_id: str, *,
                     weights: Mapping[str, int] = PRESETS["balanced"]) -> Any:
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

    parts = axis_breakdown(frame, sector_id, weights=weights)
    return pd.DataFrame(
        [{
            "축": f"{AXIS_NAMES[p['axis']]} ({p['axis']})",
            "① 원시값": axis_raw_text(p["axis"], p["raw_bp"]),
            "② σ": None if p["z_bp"] is None else p["z_bp"] / 10000,
            "③ 가중치": p["weight"],
            "④ 기여(bp)": p["contribution_bp"],
        } for p in parts]
    ).set_index("축")
