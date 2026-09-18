"""화면이 읽는 데이터 — **어디서 왔는지를 함께 돌려준다.**

## 🔴 왜 출처를 값과 함께 돌려주는가

이 앱은 두 곳에서 데이터를 읽을 수 있다 — 배포된 앱은 HF 의 `latest/` 를, 개발
중인 로컬은 `data/derived/` 를 본다. 둘은 **다를 수 있다**(로컬에서 배치를 돌리고
아직 게시하지 않았다면). 화면이 어느 쪽을 봤는지 말하지 않으면 "왜 숫자가
다르지" 를 아무도 풀 수 없다.

그래서 `load_scores()` 는 `(프레임, Source)` 를 돌려주고 화면이 `Source.label` 을
그대로 보여준다. 🔒 **조용한 폴백이 아니다** — 어디서 읽었는지 화면에 남는다.

## 🔒 이 모듈은 **streamlit 을 요구한다**

`load_scores` 가 `st.cache_data` 를 쓴다 (이슈 #2). `dashboard/view.py`·`weights.py` 와 달리
화면 없이는 import 되지 않는다 — `sector/`·`batch/`·`dashboard/agent/` 는 이 모듈을
**쓰지 않으므로** "`sector` 는 Streamlit 에 의존하지 않는다" 는 규율은 그대로다.

## 🔒 시크릿은 `secret_access` 하나로만

`st.secrets` 는 파일이 없으면 **접근만으로** 던진다(V19 · `.get()` 도 던진다).
여기서는 `hub.read_token()` 을 부르고 그 예외를 **잡아서 출처를 로컬로 바꾼다** —
토큰이 없는 것은 로컬 개발에서 정상이기 때문이다. 다만 Streamlit Cloud 에는
`data/derived/` 가 없으므로 거기서는 다음 단계에서 정직하게 실패한다.
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import streamlit as st

from sector.datastore import hub
from sector.sources.krx_common import repo_root

__all__ = ["DataUnavailable", "SCORES_SPINNER", "Source", "load_scores", "load_sectors",
           "latest_day", "sector_master", "sector_names", "sector_notes"]

#: 점수 표를 다시 읽기까지의 시간(초). 🔴 **왜 캐시하는가** — 위젯을 하나 만질 때마다
#:    `hf_hub_download`(ETag 재검증 왕복) → 745KB → `read_parquet` 이 통째로 돌았다
#:    (실측 220ms). M9 가 이 화면에 슬라이더 4 · 체크박스 2 · 멀티셀렉트 1 · 라디오 1 ·
#:    셀렉트박스 1 을 얹어 rerun 이 잦아지면서 그 값이 곧 체감 지연이 됐다 (이슈 #2).
#: 🔒 **짧게 잡는다.** 배치는 하루 한 번 게시하지만, 로컬에서 `build_scores` 를 돌린 뒤
#:    화면이 10분 넘게 옛 값을 보여 주면 "왜 안 바뀌지" 를 디버깅하게 된다.
#:    급하면 Streamlit 메뉴의 **Clear cache** 로 즉시 비운다.
SCORES_TTL_SECONDS = 300

#: 점수를 읽는 동안 화면이 하는 말. 🔴 **왜 문구를 주는가** — 팀원 7명은 개발자가
#:    아니다. 첫 로드는 실측 **6,367ms**(HF `latest/` · 5,985행)인데 그동안 우상단
#:    RUNNING 표시 말고는 안내가 없어 "멈췄나" 로 읽힌다 (이슈 #8).
#: 🔒 **깜빡임 대가가 없다.** Streamlit 은 `DELAY_SECS = 0.5` 가 지난 뒤에야 문구를
#:    띄우고(`elements/spinner.py`), TTL 이 만료된 뒤의 재읽기는 실측 **260.6ms** 다 —
#:    모듈과 HF 로컬 캐시가 이미 더워져 ETag 왕복만 남기 때문이다. 그 문턱에 닿지
#:    않으므로 이 문구는 **정말 오래 걸릴 때만** 나온다.
#: 🔒 캐시가 **맞으면 아예 지나가지 않는다** — 스피너는 미스 경로에만 있다
#:    (`cache_utils._get_or_create_cached_value`). 평소 rerun 에는 비용이 0 이다.
#: 🔒 `True` 가 아니라 **문구**를 준다. `True` 면 Streamlit 이 ``Running
#:    `load_scores()`.`` 라는 영어 함수 이름을 그린다 — 팀원에게 할 말이 아니다.
#: 🔒 이것은 **우리가 쓴 글**이라 마크다운으로 나가도 된다. 사람이 입력한 글에
#:    적용되는 HTML 블록 규율(ADR-SC-0012 ④)의 대상이 아니다.
SCORES_SPINNER = "점수 표를 읽는 중…"

#: HF 에 게시된 앱 전용 파일. 🔒 앱은 `latest/` 만 읽는다 — 월별 샤드를 전부
#:    받으면 메모리 2.7GB 한도에 닿는다.
_HF_SCORES = "latest/score_daily_latest.parquet"
_LOCAL_SCORES = "data/derived/score_daily.parquet"


class DataUnavailable(RuntimeError):
    """데이터를 어디에서도 읽지 못했다.

    🔴 **가짜 숫자를 만들지 않는다.** 화면은 이 예외를 받아 "무엇을 확인하라" 를
       보여준다 (→ ADR-SC-0007).
    """


@dataclass(frozen=True, slots=True)
class Source:
    """데이터가 어디서 왔는가. 화면에 그대로 나간다."""

    kind: str      # "supabase" | "hf" | "local"
    label: str     # 사람이 읽을 한 줄
    detail: str = ""

    @property
    def is_local(self) -> bool:
        return self.kind == "local"


def _read_parquet(raw: bytes, columns: list[str] | None = None) -> Any:
    import pandas as pd

    return pd.read_parquet(io.BytesIO(raw), columns=columns)


def _from_hf() -> tuple[Any, Source] | None:
    """HF `latest/` 에서. 토큰이 없거나 파일이 없으면 `None` — 던지지 않는다."""
    try:
        api = hub.dataset_api(hub.read_token())
        raw = hub.download_bytes(api, _HF_SCORES)
    except hub.HubError:
        return None                      # 토큰 없음 = 로컬 개발. 정상 경로다
    except Exception:                    # noqa: BLE001 — 네트워크·권한. 로컬로 넘긴다
        return None
    if raw is None:
        return None
    return _read_parquet(raw), Source(
        kind="hf",
        label="Hugging Face(비공개)에 게시된 최신 파생값",
        detail=f"{hub.REPO_ID} · {_HF_SCORES}",
    )


def _from_local() -> tuple[Any, Source] | None:
    path = repo_root() / _LOCAL_SCORES
    if not path.is_file():
        return None
    import pandas as pd

    return pd.read_parquet(path), Source(
        kind="local",
        label="로컬에서 계산한 값 — 아직 게시되지 않았을 수 있다",
        detail=str(path),
    )


@st.cache_data(ttl=SCORES_TTL_SECONDS, show_spinner=SCORES_SPINNER)
def load_scores() -> tuple[Any, Source]:
    """점수 표와 그 출처. 🔒 순서가 의도다 — **게시된 것이 먼저다.**

    로컬이 먼저면 배포 앱에서도 개발용 파일을 보게 되고, 팀원과 내가 다른 숫자를
    보면서 같은 것을 본다고 믿게 된다.

    ## 🔒 캐시는 `SCORES_TTL_SECONDS` 로 만료된다

    🔒 `st.cache_data` 는 **사본**을 돌려준다 — 화면이 프레임을 고쳐도 캐시가 오염되지
       않는다. `Source` 도 함께 캐시되므로 "이 화면이 읽은 것" 문구가 값과 어긋나지 않는다.
    🔒 **예외는 캐시되지 않는다.** 토큰이 없어 `DataUnavailable` 이 나는 동안 시크릿을
       채워 넣으면 다음 rerun 에 바로 읽힌다.
    🔒 인자가 없으므로 캐시는 **서버 프로세스 하나에 한 칸**이다. 게시된 파생값은 모든
       팀원에게 같으니 그것이 맞다 — 사람마다 다른 값을 줄 이유가 없다.
    """
    for reader in (_from_hf, _from_local):
        result = reader()
        if result is not None:
            return result
    raise DataUnavailable(
        "섹터 점수를 불러올 수 없다.\n"
        "  · 배포 환경이라면 — App settings → Secrets 에 `HF_TOKEN_READ` 를 넣는다\n"
        "  · 로컬이라면 — `python -m batch.build_scores` 를 먼저 돌린다"
    )


def load_sectors() -> Any:
    """`sectors.yaml` 의 섹터 정의. `note`(사람이 쓴 근거)가 여기 있다."""
    from sector.sector_master import load

    return load()


def latest_day(frame: Any) -> str:
    """마지막 기준일. 🔴 비어 있으면 던진다 — 빈 화면에 오늘 날짜를 적지 않는다."""
    if frame is None or len(frame) == 0:
        raise DataUnavailable("점수 표가 비어 있다. 배치가 한 번도 돌지 않았을 수 있다")
    return str(frame["bas_dd"].max())


# ── 협업 원장 ───────────────────────────────────────────────────────────────

def workspace_store() -> tuple[Any, Source]:
    """조별 원장을 읽고 쓸 저장소. 🔒 출처를 함께 돌려준다 (머리주석과 같은 이유).

    🔒 **순서가 의도다** — Supabase → HF → 로컬. 팀 원장의 집은 Supabase 이고
       (ADR-SC-0010 ④) HF 는 옛 집이다. 로컬은 내 컴퓨터에만 남으므로 팀원에게
       보이지 않는다 — 화면이 그 사실을 말해야 한다.

    🔴 **Supabase 가 설정돼 있으면 폴백하지 않는다.** 시크릿이 **하나라도** 있으면
       거기로 붙고, 형식이 틀렸거나 반쪽만 채워졌으면 **그대로 던진다.** 조용히 HF
       로 내려가면 팀이 서로 다른 원장에 쓰면서 같은 것을 본다고 믿게 된다 —
       `service_role` 키를 붙여넣은 사고도 그 침묵에 묻힌다(`_require_anon_key`).
       못 붙는 것과 잘못 붙는 것은 다르게 다뤄야 한다.

    🔴 옛 HF 원장을 **옮기지 않았다**(V43). 거기 있던 3건은 전부 같은 날
       스모크 테스트(`test_team`)라 옮길 것이 없었다. 옛 형식은 읽히되
       `fold.anomalies` 가 "참가할 수 없다" 고 말한다.
    """
    from sector.secret_access import get_secret
    from sector.workspace.store import (
        SUPABASE_ANON_KEY_SECRET,
        SUPABASE_URL_SECRET,
        HubStore,
        LocalStore,
        SupabaseStore,
    )

    if any(get_secret(name, required=False)
           for name in (SUPABASE_URL_SECRET, SUPABASE_ANON_KEY_SECRET)):
        store = SupabaseStore()          # 🔴 던지면 그대로 올린다 (머리주석)
        return store, Source(
            kind="supabase",
            label="Supabase 팀 원장 — 팀원에게 보인다",
            detail=store.url.removeprefix("https://"),
        )

    try:
        api = hub.dataset_api(hub.write_token())
        return HubStore(api), Source(
            kind="hf",
            label="Hugging Face(비공개) 조별 원장 — 팀원에게 보인다 (옛 집)",
            detail=hub.WORKSPACE_REPO_ID,
        )
    except hub.HubError:
        return LocalStore(), Source(
            kind="local",
            label="로컬 원장 — 🔴 이 컴퓨터에만 남고 팀원에게 보이지 않는다",
            detail=str(repo_root() / "data" / "workspace"),
        )


# ── 한국어 이름과 사람이 쓴 근거 ────────────────────────────────────────────
# 🔴 **여기 한 곳에서만 읽는다.** 페이지마다 `load_sectors()` 를 부르면 캐시가
#    갈라지고, 어떤 화면은 `steel` 을 어떤 화면은 `철강` 을 그리게 된다 —
#    실제로 M8 직후가 그 상태였다(랭킹·확정 둘 다 코드를 그렸다).

@lru_cache(maxsize=1)
def _master() -> Any:
    """`sectors.yaml`. 🔒 못 읽어도 화면은 살아 있어야 한다 — `None` 을 돌려준다.

    🔒 `st.cache_data` 가 아니라 `lru_cache` 다 — `SectorMaster` 는 직렬화 대상이
       아니다 — `st.cache_data` 는 값을 피클하는데 `SectorMaster` 는 그럴 물건이 아니다.

    이름이 없는 것은 **점수가 없는 것과 다르다.** 점수를 못 읽으면 화면이 멈추지만
    (`DataUnavailable`), 이름을 못 읽으면 코드로라도 그릴 수 있다.
    """
    try:
        return load_sectors()
    except Exception:                    # noqa: BLE001 — 설정 파일 문제. 코드로 그린다
        return None


def sector_master() -> Any:
    """`sectors.yaml` 값 객체 — 에이전트가 섹터 이름 사전 · 구성을 읽는다. 못 읽으면 `None`."""
    return _master()


def sector_names() -> Any:
    """식별자 → 한국어 이름. 🔒 실패하면 빈 이름표라 `sector_label()` 이 코드를 준다."""
    from dashboard.view import Names

    master = _master()
    return Names.of(master) if master is not None else Names.empty()


def sector_notes() -> dict[str, str]:
    """`sectors.yaml` 의 `note` — 왜 이렇게 묶었나. 🔒 이 줄이 화면에 그대로 나간다."""
    master = _master()
    return {s.id: s.note for s in master.sectors} if master is not None else {}
