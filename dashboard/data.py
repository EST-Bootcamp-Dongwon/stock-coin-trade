"""화면이 읽는 데이터 — **어디서 왔는지를 함께 돌려준다.**

## 🔴 왜 출처를 값과 함께 돌려주는가

이 앱은 두 곳에서 데이터를 읽을 수 있다 — 배포된 앱은 HF 의 `latest/` 를, 개발
중인 로컬은 `data/derived/` 를 본다. 둘은 **다를 수 있다**(로컬에서 배치를 돌리고
아직 게시하지 않았다면). 화면이 어느 쪽을 봤는지 말하지 않으면 "왜 숫자가
다르지" 를 아무도 풀 수 없다.

그래서 `load_scores()` 는 `(프레임, Source)` 를 돌려주고 화면이 `Source.label` 을
그대로 보여준다. 🔒 **조용한 폴백이 아니다** — 어디서 읽었는지 화면에 남는다.

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

from sector.datastore import hub
from sector.sources.krx_common import repo_root

__all__ = ["DataUnavailable", "Source", "load_scores", "load_sectors", "latest_day",
           "sector_names", "sector_notes"]

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


def load_scores() -> tuple[Any, Source]:
    """점수 표와 그 출처. 🔒 순서가 의도다 — **게시된 것이 먼저다.**

    로컬이 먼저면 배포 앱에서도 개발용 파일을 보게 되고, 팀원과 내가 다른 숫자를
    보면서 같은 것을 본다고 믿게 된다.
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
       아니고, 이 모듈은 `streamlit` 을 import 하지 않아 화면 없이 테스트된다.

    이름이 없는 것은 **점수가 없는 것과 다르다.** 점수를 못 읽으면 화면이 멈추지만
    (`DataUnavailable`), 이름을 못 읽으면 코드로라도 그릴 수 있다.
    """
    try:
        return load_sectors()
    except Exception:                    # noqa: BLE001 — 설정 파일 문제. 코드로 그린다
        return None


def sector_names() -> Any:
    """식별자 → 한국어 이름. 🔒 실패하면 빈 이름표라 `sector_label()` 이 코드를 준다."""
    from dashboard.view import Names

    master = _master()
    return Names.of(master) if master is not None else Names.empty()


def sector_notes() -> dict[str, str]:
    """`sectors.yaml` 의 `note` — 왜 이렇게 묶었나. 🔒 이 줄이 화면에 그대로 나간다."""
    master = _master()
    return {s.id: s.note for s in master.sectors} if master is not None else {}
