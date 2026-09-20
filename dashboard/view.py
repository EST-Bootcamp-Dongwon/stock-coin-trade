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
from sector.datastore.gate import SCORE_PUBLISHED_COLUMNS
from sector.scoring import AXES, PRESETS, rank_scores, scoring_axes, weighted_score_bp

__all__ = ["PROFILES", "SCORE_COLUMN", "RANK_COLUMN", "SCORE_AXES_COLUMN",
           "UNCLASSIFIED", "Names", "ViewError",
           "check_frame", "latest_frame", "scored", "rank_stability", "stability_window",
           "sector_story", "podium", "Bars", "score_bars", "arithmetic_table",
           "Arithmetic",
           "ranking_table", "axis_breakdown", "gics_options", "visible_ids",
           "gics_distribution", "int_or_none"]


class ViewError(RuntimeError):
    """화면에 그릴 수 없는 입력이다. 🔒 조용히 한 줄 버리고 그리지 않는다."""


#: `scored()` 가 붙이는 열. 🔒 **새 이름**이다 — `score_balanced_bp` 를 덮어쓰지 않는다.
#:    덮으면 에이전트 guard 의 "`view` 를 거치지 않고 원천에서 다시 얻는다"
#:    (ADR-SC-0013 ④-1)가 그 순간 거짓이 된다.
SCORE_COLUMN = "score_bp"
RANK_COLUMN = "rank"

#: 이 가중치에서 **실제로 점수에 들어간** 축 수. 🔒 저장 열 `n_axes_used` 와 **다른 열**이다 —
#:    그 열은 `z` 가 있는 축을 셀 뿐 가중치를 보지 않아, 커스텀에서 한 축만 남겨도 4 라고
#:    말했다(이슈 #4). 프리셋 셋은 네 축이 전부 0 보다 커서 두 값이 같다.
SCORE_AXES_COLUMN = "score_axes_n"


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


def _axes_used_n(frame: Any, weights: Mapping[str, int]) -> list[int]:
    """행마다 **실제로 점수에 들어간 축 수** — `scoring_axes` 와 같은 규칙이다.

    🔒 규칙의 정본은 `sector.scoring.scoring_axes` 다. 여기서 다시 세는 이유는 **속도**
       하나뿐이다 — 5985행을 행 단위로 돌면 슬라이더 한 칸이 다시 느려진다(이슈 #1).
       그래서 열 단위로 `notna()` 를 더한다. 🔒 둘이 같은 답을 내는지는 테스트가 지킨다
       (`test_축수는_scoring_axes_와_한_글자도_다르지_않다`).
    """
    live = [axis for axis in AXES if weights.get(axis, 0) > 0]
    total = None
    for axis in live:
        # 🔒 `.to_numpy()` 로 **자리**로 다룬다 — 색인이 중복된 프레임에서 라벨로 맞추면
        #    한 값이 여러 행으로 퍼진다(점수 열이 실제로 그랬다 · 2026-09-17)
        present = frame[f"{_AXIS_PREFIX[axis]}_z_bp"].notna().to_numpy()
        total = present.astype("int64") if total is None else total + present
    # 🔒 가중치가 전부 0 이면 점수도 없다 — 축수는 0 이지 결측이 아니다
    return [0] * len(frame) if total is None else [int(v) for v in total]


#: 프렐류드가 대조하는 **저장 순위 열.** 🔒 셋을 **함께** 본다 — 하나만 보면 그 프리셋
#:    기준 상위 k 만 남긴 부분집합이 조밀해서 통과한다(실측: `rank_balanced <= 5` 로 자른
#:    프레임을 balanced 는 놓치고 momentum·contrarian 이 잡았다). 게시 계약이 셋을 함께
#:    싣는다 (`gate.SCORE_PUBLISHED_COLUMNS`).
_RANK_COLUMNS = ("rank_balanced", "rank_momentum", "rank_contrarian")

#: 행을 식별하는 **키.** 🔴 값이 아니라 키다 — 비면 `is_partial` 로 표시할 자리조차 없다.
_KEY_COLUMNS = ("bas_dd", "sector_id")


#: 부분 프레임 진단의 **꼬리는 읽는 사람에 따라 다르다** (이슈 #12).
#: 🔴 `scored()` 에 거른 프레임을 넘긴 것은 **개발자**이지만, 데이터 경계에서 걸린 것은
#:    **팀원 7명이 본다** — 그들은 아무 프레임도 "넘기지" 않았다. 같은 문장을 두 곳에
#:    쓰면 한쪽에서 반드시 거짓말이 된다.
_CALLER_HINT = " — 거르지 않은 전체 프레임을 넘겨야 한다"
_STORE_HINT = " — 파생본을 다시 만들어야 한다"


def check_frame(frame: Any, *, origin: str = "") -> None:
    """화면이 읽을 수 있는 프레임인가 — **계약을 지키는 유일한 문.**

    `scored()` 가 부르고, `dashboard/data.py` 가 **데이터 경계**에서도 부른다
    (이슈 #12). 두 자리의 차이는 `origin` 하나다.

    ## 🔒 `origin` — 프레임을 **읽어 온 곳**(사람이 읽을 한 줄)

    주면 두 가지가 바뀐다 — ① 부분 프레임 진단의 꼬리가 개발자용에서 운영용으로
    바뀌고(`_CALLER_HINT` 머리주석) ② 실패 메시지에 **읽은 곳이 붙는다.**

    🔴 ②가 없으면 화면은 "파생본을 다시 만들어야 한다" 고만 말하고 **어느 파생본인지
       말하지 않는다.** HF 게시본이면 다시 게시해야 하고 로컬이면 `build_scores` 를
       다시 돌려야 하는데, 그 둘은 다른 행동이다. `data.py` 머리주석이 값과 출처를
       함께 돌려주는 이유가 그대로 실패 경로에도 적용된다.
    """
    try:
        _check_frame(frame, hint=_STORE_HINT if origin else _CALLER_HINT)
    except ViewError as exc:
        if not origin:
            raise
        # 🔒 `theme.failure` 가 escape 한 HTML 블록으로 그린다 (ADR-SC-0012 ④)
        raise ViewError(f"{exc}\n읽은 곳 — {origin}") from exc


def _check_frame(frame: Any, *, hint: str = _CALLER_HINT) -> None:
    """`scored()` 의 입력 계약을 **입구 한 곳에서** 지킨다 (이슈 #7 · #9).

    ## 🔒 순서가 계약의 일부다

    뒤 검사가 앞 검사의 전제 위에 선다. 바꾸면 검사 자체가 틀린 말을 한다 — 전부 실측이다.

    1. **열 중복이 먼저다.** 열이 둘이면 `frame["bas_dd"]` 가 Series 가 아니라 DataFrame 이라
       `isna().any()` 가 Series 를 돌려주고 `if` 가 *truth value is ambiguous* 로 터진다.
       즉 검사 자체가 깨진다.
    2. **결측이 타입보다 먼저다.** `object` 열에 `None` 이 섞이면 `is_string_dtype` 이
       **`False`** 다(pandas 3.0.5 실측). 타입을 먼저 보면 "비어 있다" 를 "문자열이 아니다"
       라고 **틀리게 진단한다.**
    3. **중복이 조밀성보다 먼저다.** 중복 프레임은 저장 순위도 겹쳐 보여 조밀성 검사가
       "부분 프레임" 으로 **오진한다.** 원인은 중복이다.

    ## 🔴 이 검사가 보증하지 **않는** 것

    `*_z_bp` 가 저장 순위와 같은 시점인지는 모른다. z 열만 옛 값인 프레임은 그대로
    통과한다. **부분 프레임을 잡는 검사이지 파생본의 모든 오염을 잡는 검사가 아니다.**
    """
    import numpy as np
    import pandas as pd

    # ① 열 중복 — 무엇보다 먼저다 (머리주석 1)
    for column in _KEY_COLUMNS:
        if column not in frame.columns:
            raise ViewError(f"프레임에 {column} 열이 없다 — 게시된 score_daily 가 아니다")
        if not isinstance(frame[column], pd.Series):
            raise ViewError(f"{column} 열이 {frame.columns.tolist().count(column)}개다 "
                            f"— 파생본이 깨졌다")

    # ② 결측 — 타입보다 먼저다 (머리주석 2)
    #    🔴 여기서 `None` 으로 넘기지 않고 **던진다.** 키가 빈 행은 어느 날의 횡단면에도
    #       속하지 못해 순위를 낼 수 없고, 화면에서는 "창이 안 찬 새 섹터"(실데이터
    #       399행)의 빈칸과 구별되지 않는다. 옛 `groupby` 는 그 행을 **조용히 버렸다** —
    #       `ViewError` 머리주석이 금지하는 바로 그 행동이다. 실데이터 결측은 0행이고
    #       생산자가 dtype 을 못박으므로(`batch/build_scores.py`) 정상 상태가 아니다.
    for column in _KEY_COLUMNS:
        missing = int(frame[column].isna().sum())
        if missing:
            raise ViewError(f"{column} 이 비어 있는 행이 {missing}건 있다 — 파생본이 깨졌다")

    # ③ 타입 — 🔴 **한 날이 두 표기로 갈리는 것을 막는 방어가 여기 하나다.**
    #    `"20260909"` 와 `20260909.0` 이 섞이면 그 하루가 두 횡단면으로 쪼개져
    #    **같은 날에 1위가 둘** 나온다 — 예외 없이 조용히(실측). 결측이 하나만 있어도
    #    pandas 가 정수 열을 float64 로 올리므로 실제로 닿는 경로다.
    #    🔒 아래 `days`·`sector_ids` 가 `str()` 없이 값을 그대로 쓰는 근거도 이 검사다 —
    #       순서가 거꾸로가 아니다. 이 검사가 없으면 `str()` 제거는 오히려 해롭다.
    #    🔒 값 루프가 아니라 dtype 으로 본다 (0.011ms vs 값 루프 3.1ms).
    for column in _KEY_COLUMNS:
        if not pd.api.types.is_string_dtype(frame[column]):
            raise ViewError(f"{column} 이 문자열이 아니다 (dtype={frame[column].dtype}) "
                            f"— 게시본은 언제나 문자열이다")

    # ④ 중복 — 조밀성보다 먼저다 (머리주석 3). 🔒 프리셋 경로도 지난다. 옛 구현은 이
    #    검사가 커스텀 루프 **안에만** 있어서 프리셋은 중복 프레임을 조용히 통과시켰다
    duplicated = frame.duplicated(list(_KEY_COLUMNS)).to_numpy()
    if duplicated.any():
        first = int(np.flatnonzero(duplicated)[0])
        day = frame["bas_dd"].to_numpy()[first]
        sector_id = frame["sector_id"].to_numpy()[first]
        raise ViewError(f"{day} 에 섹터 {sector_id} 가 두 번 있다 — 파생본이 깨졌다")

    # ⑤ 열 — 🔴 **게시 계약 전체를 본다.** 키 2열과 순위 3열만 보던 때는 `m_z_bp` 나
    #    `score_balanced_bp` 가 빠진 옛 파생본이 프렐류드를 지나 화면 깊은 곳에서
    #    `KeyError` 로 터졌다(실측). 그 예외는 `ViewError` 가 아니라서 페이지가 잡지
    #    못하고, 이 변경이 없애려던 **트레이스백 화면**이 그대로 남았다.
    #    🔒 정본은 `gate.SCORE_PUBLISHED_COLUMNS` 하나다 — 열 계약을 두 곳에 적지 않는다
    absent = [c for c in SCORE_PUBLISHED_COLUMNS if c not in frame.columns]
    if absent:
        raise ViewError(f"게시된 score_daily 가 아니다 — 없는 열 {len(absent)}개: "
                        f"{', '.join(absent[:5])}")

    # ⑥ 완전성 — 부분 프레임을 거절한다 (이슈 #7)
    _check_whole(frame, hint=hint)


def _check_whole(frame: Any, *, hint: str = _CALLER_HINT) -> None:
    """날짜별 저장 순위가 **조밀한 1..k** 인가 — 부분 프레임이면 깨진다.

    🔴 이것은 휴리스틱이 아니라 **게시 계약의 재확인**이다. `rank_scores` 가 점수 있는
       것만 1..k 로 매기고(`sector/scoring.py`), 게시 게이트가 그것을 다시 검사한다
       (`gate._check_score_rank_consistent`). 실데이터 285일 × 3열 = 855건 위반 0건.
       섹터로 거른 프레임은 원래 전역 순위를 그대로 들고 있어 구멍이 뚫린다.

    🔒 **`groupby` 로 짜지 않는다.** 날짜별 루프는 38.4ms · `groupby().agg()` 는 6.2ms ·
       `factorize` + `bincount` 는 **0.72ms** 다(5,985행 실측). 프리셋 경로는 한 rerun 에
       여러 번 돌고 그 경로 전체가 1.6ms 라, 여기서 느려지면 이슈 #1 을 되돌린다.
    """
    import numpy as np
    import pandas as pd

    # 🔒 순위 열의 **존재**는 `_check_frame` ⑤ 가 이미 봤다 (게시 계약 전체)
    codes, days = pd.factorize(frame["bas_dd"].to_numpy())
    n_days = len(days)
    for column in _RANK_COLUMNS:
        values = frame[column].to_numpy(dtype="float64", na_value=np.nan)
        graded = ~np.isnan(values)
        if not graded.any():
            # 그날 점수가 하나도 없으면 순위도 없어야 정상이다 (실데이터 19일)
            continue
        ranks = values[graded].astype("int64")
        day_of = codes[graded]
        # ① 양수인가 — 🔒 **조밀성만으로는 못 잡는다.** `{1,2,4,-5}` 는 개수 4 · 최댓값 4
        #    라 아래 ② 를 그대로 통과하고, 그대로 두면 ③ 의 `key` 가 음수가 되어
        #    `bincount` 가 `ValueError` 로 터진다 — `ViewError` 가 아니라 트레이스백이다
        if ranks.min() < 1:
            raise ViewError(f"{column} 에 1 보다 작은 순위가 있다 — 파생본이 깨졌다")

        # ② 조밀성 — 🔒 **③ 보다 먼저다. 이 순서가 메모리 안전의 근거다.**
        #    ② 를 지나면 날짜별 최댓값 == 그날 행 수이므로 최대순위 ≤ 전체 행 수이고,
        #    ③ 의 칸 수(날짜수 × (최대순위+1))가 **구조적으로** 묶인다.
        #    🔴 순서를 뒤집으면 손상된 순위 하나(2천만)가 `bincount` 에 **42.5 GiB** 를
        #       요구한다(실측 · 3ms 만에 MemoryError). Streamlit Cloud 는 2.7GB 라
        #       거기서는 프로세스가 통째로 죽고 `except ViewError` 로 잡히지 않는다 —
        #       가장 깨진 입력에서 계약이 먼저 무너진다. 가드를 더 세우지 않고
        #       **순서로** 막는다
        count = np.bincount(day_of, minlength=n_days)
        top = np.zeros(n_days, dtype="int64")
        np.maximum.at(top, day_of, ranks)
        # 서로 다른 양의 정수 k 개의 최댓값이 k 라면 그 집합은 정확히 1..k 다
        broken = np.flatnonzero((count > 0) & (top != count))
        if len(broken):
            day = days[int(broken[0])]
            # 🔒 마크다운을 쓰지 않는다 — `theme.failure` 가 HTML 블록으로 그려서
            #    백틱·별표가 **글자 그대로** 팀원 화면에 나온다(ADR-SC-0012 ④ 경로)
            raise ViewError(
                f"{day} 의 {column} 이 1..{count[broken[0]]} 로 이어지지 않는다"
                f"(최대 {top[broken[0]]}). 섹터로 거른 부분 프레임이거나 순위가 손상됐다"
                f"{hint}")

        # ③ 유일성 — 🔒 ② 만으로는 `{4,4,1,1}` 이 1..4 를 흉내 낸다(개수 4 · 최댓값 4).
        #    🔒 `np.unique` 로 정렬하지 않는다 — (날짜, 순위)를 정수 하나로 접어 **세면**
        #       O(n) 이다. ② 덕에 칸 수는 285일 × 22 = 6,270 으로 묶여 있다
        key = day_of.astype("int64") * (int(ranks.max()) + 1) + ranks
        if np.bincount(key).max() > 1:
            raise ViewError(f"{column} 에 같은 날 같은 순위가 둘 있다 — 파생본이 깨졌다")


def scored(frame: Any, weighting: Weighting) -> Any:
    """`score_bp` · `rank` 열을 붙인 프레임. **화면의 표·막대·등수는 이것만 읽는다.**

    ## 🔒 입력은 **거르지 않은 전체 프레임**이다 (이슈 #7)

    날짜로 자른 것은 되고, **섹터로 거른 것은 안 된다.** 두 경로가 등수의 뜻이 다르기
    때문이다 — 프리셋은 저장된 **전역** 등수를 읽고, 커스텀은 주어진 프레임 **안에서**
    다시 매긴다. 한 행짜리 프레임에서 프리셋은 3위, 커스텀은 1위였다(실측).
    거르려면 **행을 숨기는 `only=`** 로 한다 (ADR-SC-0014 ⑦ — 필터는 점수를 다시 매기지
    않는다). 계약은 `_check_frame` 이 **기계로도** 지킨다.

    ## 🔴 프리셋은 저장된 열을 그대로 읽는다 — 다시 계산하지 않는다

    앱이 프리셋까지 재계산하면, HF 에 **옛 파생본**이 올라가 있는 동안 화면 위쪽
    표(재계산)와 아래쪽 근거·에이전트(저장 열)가 **다른 숫자를 말한다.** 2026-09-17
    실측에서 최근 20영업일 창의 첫날이 갈려 한 섹터의 순위 진폭이 표에서 4.4,
    근거에서 4.5 로 나왔다. guard 는 장부와 저장 열을 대조하므로 그것을 못 잡는다.

    그래서 프리셋은 **게시된 값이 정답**이고, 그 값이 게시된 z 로 재현된다는 보증은
    화면이 아니라 **게시 게이트**(`gate._check_score_reproducible`)가 선다.

    커스텀 가중치에는 저장된 열이 없으므로 그때만 `weighted_score_bp` 로 다시 낸다 —
    배치가 쓰는 바로 그 함수다. 🔒 두 화면이 점수를 각자 구현하지 않는다(`AGENTS.md` 6장).

    ## 🔴 축수(`SCORE_AXES_COLUMN`)는 점수와 반대로 **언제나 다시 센다**

    점수는 게시된 값이 정답이라 프리셋이면 읽어 오지만, 축수의 저장 열(`n_axes_used`)은
    **다른 질문에 답한다** — "`z` 가 있는 축이 몇인가" 이지 "이 가중치에서 몇이 점수에
    들어갔나" 가 아니다. 지금 둘이 같아 보이는 것은 프리셋 셋의 네 축이 전부 0 보다
    크기 때문일 뿐이다. 🔒 저장 열은 **덮지 않는다** (ADR-SC-0014 ③).
    """
    import pandas as pd

    # 🔒 **두 경로 공통이다.** 계약이 하나이므로 검사도 한 곳이다 (이슈 #7 · #9)
    # 🔒 `origin` 을 주지 않는다 — 여기 걸리는 프레임은 **호출자가 넘긴 것**이다
    check_frame(frame)

    out = frame.copy()
    axes_n = pd.array(_axes_used_n(frame, weighting.weights), dtype="Int64")

    # 🔒 **자리로 넣는다.** 색인 라벨로 맞추면 색인이 중복된 프레임에서 한 값이 여러
    #    행으로 퍼진다 — 실제로 커스텀 경로가 21행에 같은 점수를 조용히 넣었다(2026-09-17).
    #    `.array` 는 `Int64` 를 지키면서 자리로 들어간다.
    if weighting.is_preset:
        out[SCORE_COLUMN] = frame[weighting.column("score")].array
        out[RANK_COLUMN] = frame[weighting.column("rank")].array
        # 🔒 프리셋에서도 **다시 센다.** 저장 열 `n_axes_used` 를 읽지 않는 이유는, 그 값이
        #    맞는 것이 "프리셋 넷이 전부 0 보다 크다" 는 **지금의 우연**에 기대기 때문이다.
        #    어느 프리셋에 0 이 하나 생기면 그날부터 표가 조용히 거짓을 말한다.
        #    ⚠️ 점수·순위와는 규율이 다르다 — 저것은 **게시된 값이 정답**이라 읽어 오지만,
        #       축수는 게시된 열이 아예 다른 질문에 답한다(가중치를 보지 않는다).
        out[SCORE_AXES_COLUMN] = axes_n
        return out

    # 🔴 **열을 먼저 파이썬 리스트로 꺼낸다.** `itertuples` 를 날짜별 그룹마다 부르면 pandas 가
    #    **그룹마다 열 수만큼** `_ixs` 를 탄다 — 285그룹 × 26열 = **7,411번**(실측 · 새 경로는 6번).
    #    361ms 였고, 슬라이더는 한 칸 움직일 때마다 rerun 이라 그대로 체감 지연이 됐다 (이슈 #1).
    #    ⚠️ 비용이 **그룹 수**에 비례하므로 창을 자르면 줄기는 한다 — 그래도 자르지 않는다.
    #       이유는 성능이 아니라 계약이다(ADR-SC-0014 ⑦ — 하단에는 원본 프레임이 간다).
    #    🔒 결과는 글자 그대로 같다 — 같은 `weighted_score_bp` · `rank_scores` 에 같은 값을 준다
    # 🔒 **`str()` 을 씌우지 않는다.** 프렐류드 ③ 이 이미 문자열임을 보증했으므로
    #    강제변환은 **하는 일이 없다** — 지운 것은 방어가 아니라 군더더기다.
    #    🔴 방어는 ③ dtype 검사 **하나**다. 착각하기 쉬운데, ③ 없이 `str()` 만 지우면
    #       오히려 나빠진다 — 정수 `20260901` 과 문자열 `"20260901"` 이 **다른 키**가
    #       되어 하루가 둘로 갈린다(`str()` 이 있을 때는 같은 키로 합쳐져 무해했다).
    #       즉 이 두 줄은 ③ 이 있기 때문에만 옳다 (이슈 #9)
    days: list[str] = frame["bas_dd"].tolist()
    sector_ids: list[str] = frame["sector_id"].tolist()
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
            # 🔒 하루에 같은 섹터가 둘인 경우는 **프렐류드**가 이미 거절했다 —
            #    옛 구현은 이 자리에서만 봐서 프리셋 경로가 그대로 통과했다
            sector_id = sector_ids[position]
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
    out[SCORE_AXES_COLUMN] = axes_n
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
    """실제로 쓸 수 있는 영업일 수. 요청한 `days` 보다 작을 수 있다.

    🔴 **결측을 영업일로 세지 않는다.** `unique()` 는 `<NA>` 를 값 하나로 세어, 8영업일
       + 결측 1행인 프레임에서 **9** 를 돌려줬다(실측). 화면이 "최근 9영업일" 이라 적으면
       그것이 곧 없는 날을 지어낸 것이고, 이 함수는 정확히 그 거짓말을 막으려고 있다.
    """
    return min(days, int(frame["bas_dd"].nunique()))


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
        # 🔴 저장 열 `n_axes_used` 가 아니라 `scored()` 가 붙인 열이다 — 저장 열은
        #    가중치를 보지 않아 커스텀에서 점수를 설명하지 못했다 (이슈 #4)
        SCORE_AXES_COLUMN: "축수",
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
       답해진다.

    ## 🔒 「점수에 들어간 축」은 `scoring_axes` 가 정한다 — 여기서 다시 적지 않는다

    🔴 옛 구현은 **`z` 가 있는지만** 봤다. 그래서 가중치를 0 으로 내린 축에도
       «기여 0» 을 적었는데, 그 축은 점수에 **더해지지도 않았다** — 「0 만큼 보탰다」와
       「애초에 안 들어갔다」가 화면에서 같아진다(ADR-SC-0007 · 이슈 #15).
       이슈 #4 가 '축수' 칸에서 고친 것과 **같은 결함**이 '기여' 칸에 남아 있었다.

    🔒 **분모는 변하지 않는다.** 빠지는 축은 가중치가 0 이라 `Σw` 에 0 을 보태고 있었다
       (실측 29,925 (행×가중치) 전수 불변 · 최신일 1,344 (섹터×가중치)에서 살아 있는
       축의 기여가 바뀐 칸 **0**). 즉 이 규칙은 **w=0 축의 기여만 `None` 으로** 바꾸고
       살아 있는 축의 값은 한 칸도 건드리지 않는다.

    🔒 기여는 **`Fraction`** 으로 낸다 — `guard._derived` 와 같은 산술이어야 한다
       (AGENTS.md 4장 · `float` 금지). 실측으로는 57,657 축-칸에서 float·Fraction·
       Decimal 이 전부 같았지만, `_residual_bound` 의 증명이 **정확 반올림**을 전제한다.
    """
    from fractions import Fraction

    latest = latest_frame(frame)
    rows = latest[latest["sector_id"] == sector_id]
    if len(rows) == 0:
        return []
    row = rows.iloc[0]

    z_bp = {a: int_or_none(row.get(f"{_AXIS_PREFIX[a]}_z_bp")) for a in AXES}
    live = scoring_axes(z_bp, weights)
    # 🔒 `or 1` 을 두지 않는다 — live 의 축은 전부 `w > 0` 이라 합이 0 일 수 없고,
    #    live 가 비면 아래에서 분모를 **쓰지 않는다.** 조용한 기본값은 조건이 바뀐 날
    #    0 으로 나눌 자리를 1 로 눙친다 (ADR-SC-0016 이 조용한 기본값을 지운 이유)
    total_weight = sum(weights[a] for a in live)

    out = []
    for axis in AXES:
        raw = row.get(f"{_AXIS_PREFIX[axis]}_raw_bp")
        z = z_bp[axis]
        out.append({
            "axis": axis,
            "raw_bp": int(raw) if _notna(raw) else None,
            "z_bp": z,
            "weight": weights[axis],
            # 🔒 점수에 안 들어간 축은 기여가 0 이 아니라 **없음**이다 — 결측 축이든
            #    가중치 0 축이든 같다
            "contribution_bp": (round(Fraction(z * weights[axis], total_weight))
                                if axis in live else None),
            # 🔒 축 순위는 **재기만 하면 사실**이다 — 가중치가 0 이어도 z 가 있으면 남긴다
            "rank": _axis_rank(latest, axis, z) if z is not None else None,
        })
    return out


@dataclass(frozen=True)
class Arithmetic:
    """④ 열의 세로 합과 총점이 **얼마나, 왜** 다른가.

    🔴 화면은 오랫동안 「합계 = 총점」을 **등호로** 적었다. 실데이터 16,758
       (행×프리셋) 중 **5,276건(31.5%)** 에서 거짓이었고, 그날 1위만 봐도 균형
       **74/266일** · 역발상 87/266일이다. 원인은 반올림 **횟수**다 — 총점은 한 번,
       기여는 축마다 반올림한다. `round(a)+round(b)+…` 와 `round(a+b+…)` 는 같지 않다.

    ## 🔒 원인을 단정하지 않는다

    잔차에는 두 가지가 섞일 수 있다 —

    - **(a) 축마다 bp 로 반올림한 것** — 상한이 `_residual_bound(n)` 으로 **구조적**이다
    - **(b) 게시된 총점이 게시된 z 와 갈린 것** — 상한이 없다

    (b) 를 "반올림" 이라 부르면 **원인을 지어내는 것**이다(ADR-SC-0007). `explained`
    가 그 경계이고, 넘으면 화면은 "반올림" 대신 "파생본을 다시 만들어야 한다" 고 말한다.

    ⚠️ **(b) 는 저장 열을 읽는 프리셋에서만 생긴다.** 커스텀 가중치는 총점도 기여도
       같은 행의 같은 z 에서 나오므로 `|잔차| ≤ bound` 가 **항상** 성립한다
       (실측 6벌 41,895 계산에서 한도 위반 0건). 지금 이 판정을 내는 곳은
       `sector_story` 하나이고 그것은 프리셋 전용이다.
    """

    parts_sum_bp: int | None
    total_bp: int | None
    residual_bp: int | None
    bound_bp: int
    explained: bool


def _residual_bound(n: int) -> int:
    """반올림만으로 생길 수 있는 `|총점 − Σ기여|` 의 상한 (bp). `n` 은 **점수에 들어간 축 수**다.

    축마다 `|round(e_a) − e_a| ≤ ½` 이고 총점도 `|round(E) − E| ≤ ½` 이므로
    `|Σc − total| ≤ (n+1)/2` 이고, 정수라 `⌊(n+1)/2⌋` 다. 양쪽 반올림이 모두
    `ROUND_HALF_EVEN` 이라(`sector/scoring.py` 의 `_CONTEXT`) 이 전제가 성립한다.

    🔴 **`n ≤ 1` 은 0 이다.** 축이 하나면 분모 `W = w` 라 기여 `= round(z·w/w) = z` 이고
       총점도 `z` 다 — 두 반올림이 같은 자리에서 일어나 잔차가 **구조적으로 0** 이다
       (가중치 7벌 × `|z| ≤ 30000` 전 구간 60,004 사례 전수 0). `⌊(n+1)/2⌋` 을 그대로
       쓰면 단일 축에서 **±1 오염을 «설명됨» 으로 통과시킨다** — ADR-SC-0014 가
       "그 1bp 가 최근 20영업일 창의 첫날에서 순위를 뒤집었다" 고 기록한 크기다.
       프리셋에도 `n=1` 인 행이 53행 있고(`axes_missing='MFV'`), `F` 단독 커스텀은
       5,464행이 그렇다.

    🔒 **`n` 의 정의가 한 가지여야 한다** — `scoring_axes` 가 센 축 수다. 옛
       `axis_breakdown` 처럼 가중치 0 축까지 세면 `n` 이 부풀어 **bound 가 실재 오염을
       흡수한다**(예: 단일 축 커스텀에서 n=4 → 한도 2). 그래서 이슈 #15 가 먼저다.

    실측 — 실데이터 16,758 (행×프리셋) 에서 상한 위반 **0건**
    (n=1 최대 0 · n=2 최대 1 · n=3 최대 1 · n=4 최대 2).
    """
    return 0 if n <= 1 else (n + 1) // 2


def _arithmetic(parts: list[dict[str, Any]], total_bp: int | None) -> Arithmetic:
    """합계·잔차 판정. 🔒 **공개 자유 함수로 두지 않는다.**

    `parts` 와 `total_bp` 를 호출자가 **짝지어** 넘기는 모양이면 ① 둘이 같은 가중치·
    같은 날에서 나왔는지 함수가 알 수 없고 ② 부르지 않고 직접 더해 등호를 말하는
    경로가 그대로 남는다. 실제로 `pages/howto.py` 가 손으로 더하고 있었다(이슈 #13).
    판정은 `sector_story` 의 **반환값에 실려** 나간다 — ADR-SC-0016 의 규율과 같다
    (규칙으로 막지 않고 경로를 없앤다).
    """
    live = [p["contribution_bp"] for p in parts if p["contribution_bp"] is not None]
    parts_sum = sum(live) if live else None
    bound = _residual_bound(len(live))
    if total_bp is None:
        # 🔒 총점이 없으면 맞춰 볼 것이 없다 — «설명 안 됨» 이 아니다
        return Arithmetic(parts_sum_bp=parts_sum, total_bp=None, residual_bp=None,
                          bound_bp=bound, explained=True)
    if parts_sum is None:
        # 🔴 더한 것이 하나도 없는데 총점이 있다 — **크기와 무관하게** 설명되지 않는다.
        #    `abs(residual) <= bound` 에 맡기면 총점이 마침 0 인 날 깃발은 «설명됨»,
        #    문장은 «재현되지 않는다» 로 **갈린다**(적대적 구현 리뷰 ⑥). `narrative` 가
        #    바로 이 깃발로 문장을 여닫으므로 경계가 두 벌이면 화면이 스스로 모순된다
        return Arithmetic(parts_sum_bp=None, total_bp=total_bp, residual_bp=total_bp,
                          bound_bp=bound, explained=False)
    residual = total_bp - parts_sum
    return Arithmetic(parts_sum_bp=parts_sum, total_bp=total_bp, residual_bp=residual,
                      bound_bp=bound, explained=abs(residual) <= bound)


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

    # 🔒 `parts` 와 `score_bp` 의 **짝이 정해지는 유일한 자리**다. 잔차 판정도 여기서
    #    함께 내어 반환값에 실어 보낸다 — 화면이 직접 더해 등호를 말할 길을 없앤다
    #    (이슈 #13 · `_arithmetic` 머리주석)
    parts = axis_breakdown(frame, sector_id, weights=PRESETS[profile])
    score_bp = int_or_none(row.get(f"score_{profile}_bp"))
    return {
        "label": names.sector_label(sector_id),
        "rank": int_or_none(row.get(f"rank_{profile}")),
        "total": len(latest),
        "score_bp": score_bp,
        "parts": parts,
        "arithmetic": _arithmetic(parts, score_bp),
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


@dataclass(frozen=True)
class Bars:
    """막대 차트가 그리는 것 **전부** — 표 · 기준선 · 기준선을 낸 값의 개수.

    ## 🔒 왜 셋을 하나로 묶는가 — 규칙을 구조로 바꾼다

    기준선(중앙값)은 언제나 **거르지 않은** 그날 전체에서 나와야 하고(ADR-SC-0014 ⑤ —
    필터는 행을 숨길 뿐 점수를 다시 매기지 않는다), 표는 **걸러진** 것이어야 한다.

    🔴 이 둘을 **따로 내는 함수 둘**로 두면 호출부가 걸러진 프레임을 기준선 쪽에
       넘기는 **두 번째 입구**가 남는다. `_render_bars` 는 바로 윗줄에서 `only=keep`
       을 쓰고 있어 그 입구가 손에 닿는 거리에 있다. 여기서는 `only` 가 적용되기
       **전에** 중앙값이 정해지므로 그 입구가 존재하지 않는다 (ADR-SC-0016 과 같은
       논리 — 규칙으로 막지 않고 경로를 없앤다).
    """

    table: Any
    """화면이 그대로 그리는 프레임 — 색인은 한국어 이름, 값은 σ. 🔒 **걸러진** 것이다."""

    middle_sigma: float | None
    """그날 **거르지 않은** 전체 섹터 점수의 중앙값 — σ. 낼 수 없으면 `None`."""

    graded_n: int
    """중앙값을 낸 값의 개수. 🔒 «가운데가 몇 위인가» 를 **이 수가** 정한다."""


def score_bars(frame: Any, *, names: Names | None = None,
               only: "frozenset[str] | None" = None) -> Bars:
    """막대 차트가 그대로 받는 것 — 표 · 기준선 · 기준선을 낸 개수.

    🔴 **bp 가 아니라 σ 로 넘긴다.** 막대는 눈금을 읽히려고 두는 것이 아니라
       간격을 보이려고 두는 것이고, `12522` 는 사람이 즉시 못 읽는다. σ 는
       읽는법 화면이 이미 가르친 단위다.

    🔒 순위 순서 그대로 돌려준다. 화면은 `sort=None` 으로 그려 이 순서를 지킨다 —
       알파벳순으로 다시 정렬되면 "위에서부터 1등" 이라는 유일한 읽는 법이 깨진다.

    ## 🔴 0 은 가운데가 아니다 — 그래서 기준선을 함께 낸다 (이슈 #14)

    `z` 는 **축마다** 중앙값을 빼므로 각 축의 중앙값은 0 이다. 그러나 **중앙값은
    선형이 아니라**(`median(a+b) != median(a)+median(b)`) 가중합의 중앙값은 0 에서
    비켜 있다 — 한 섹터가 네 축에서 동시에 가운데인 일이 드물기 때문이다.

    실측(2026-09-19 · 파생본 266영업일) — 「막대가 왼쪽이면 가운데보다 낮다」가
    균형 **159/266일**에서 거짓이었고(하루 평균 1.03개 · 최대 5개),
    역발상은 **188/266일 · 최대 7개**였다. 일별 중앙값 `|평균|` 은 **0.110σ** 다.

    🔒 중앙값을 `only` **적용 전에** 낸다. 걸러진 집합에서 내면 "21개 중 3위" 가
       "5개 중 1위" 가 되는 것과 같은 종류의 거짓이 된다.
    🔒 개수는 **중앙값을 실제로 낸 값들**로 센다(`score` 결측 제외). 순위로 세면
       값과 라벨의 모집단이 갈릴 수 있다 — 지금은 `rank_scores` 가 점수 있는 것에만
       순위를 주어 둘이 같고, 그 사실을 테스트가 지킨다.
    🔴 `median()` 은 낼 수 없을 때 `None` 이 아니라 **`pd.NA` 를 준다**(열이 `Int64`).
       `is not None` 으로 거르면 **참**이 되어 값이 `null` 인 점선이 그려지고, 축 제목은
       있지도 않은 그 점선을 가리킨다 — 절대 제약 8 이 금지하는 화면이다. 실데이터
       5985행 중 **399행**이 그 모양이고(창이 안 찬 19영업일), 섹터를 새로 넣으면
       그날부터 또 그 모양이다.
    """
    import pandas as pd

    names = names or Names.empty()
    latest = latest_frame(frame).set_index("sector_id")
    column = SCORE_COLUMN
    # 🔒 **`only` 보다 먼저.** 기준선은 거르지 않은 그날 전체에서 나온다
    graded = latest[latest[column].notna()][column]
    middle = graded.median()
    pool = latest if only is None else latest[latest.index.isin(only)]
    ordered = pool[pool[RANK_COLUMN].notna()].sort_values(RANK_COLUMN)
    return Bars(
        table=pd.DataFrame(
            {"점수(σ)": [_float_or_none(v) / 10000 if _float_or_none(v) is not None else None
                         for v in ordered[column]]},
            index=pd.Index([names.sector_label(s) for s in ordered.index], name="섹터"),
        ),
        # 🔴 `pd.isna` 다 — `is not None` 은 `pd.NA` 를 통과시킨다 (머리주석)
        middle_sigma=None if pd.isna(middle) else float(middle) / 10000,
        graded_n=int(len(graded)),
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


def arithmetic_table(parts: list[dict[str, Any]]) -> Any:
    """한 섹터의 **산수를 그대로 편 표** — 원시값 → σ → 가중치 → 기여.

    🔒 **`parts` 를 받는다**(프레임·가중치가 아니다). 표와 그 아래 합계 문장이 서로
       다른 가중치·다른 날의 값을 그리는 경로를 **없앤다** — 둘 다 `sector_story` 가
       만든 같은 목록에서 나온다.

    🔴 ④ 열의 세로 합은 총점과 **정확히 같지 않다.** 축마다 bp 로 반올림하기 때문이고,
       얼마나 다를 수 있는지는 `_residual_bound` 가 말한다. 화면 문장은
       `explain.arithmetic_text` 하나가 쓴다 (이슈 #13).

    🔒 결측 축과 **가중치 0 축**은 기여가 `None` 이라 합에서 빠진다. 0 으로 적으면
       덧셈은 맞아 보이지만 "점수에 안 들어간 축" 과 "0 을 보탠 축" 이 같아진다.
    🔴 **`.sum()` 으로 검산하지 마라** — `None` 이 하나 섞이면 열 dtype 이 `float64` 로
       올라가 `.sum()` 이 결측을 **건너뛴다**(실측: `[2900, nan, 1425, 1316]` 의 합이
       5641 인데 같은 행의 총점은 6060). 합계는 `Arithmetic` 이 정수로 낸다.
    """
    import pandas as pd

    from dashboard.explain import axis_raw_text
    from sector.scoring import AXIS_NAMES

    return pd.DataFrame(
        [{
            "축": f"{AXIS_NAMES[p['axis']]} ({p['axis']})",
            "① 원시값": axis_raw_text(p["axis"], p["raw_bp"]),
            "② σ": None if p["z_bp"] is None else p["z_bp"] / 10000,
            "③ 가중치": p["weight"],
            "④ 기여(bp)": p["contribution_bp"],
        } for p in parts]
    ).set_index("축")
