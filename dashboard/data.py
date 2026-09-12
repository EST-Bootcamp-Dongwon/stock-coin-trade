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
from pathlib import Path
from typing import Any

from sector.datastore import hub
from sector.sources.krx_common import repo_root

__all__ = ["DataUnavailable", "Source", "load_scores", "load_sectors", "latest_day"]

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

    kind: str      # "hf" | "local"
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

    🔴 쓰기 토큰이 있어야 **쓸 수 있다.** 없으면 로컬 원장으로 내려가는데, 그것은
       내 컴퓨터에만 남으므로 팀원에게 보이지 않는다 — 화면이 그 사실을 말해야 한다.
    """
    from sector.workspace.store import HubStore, LocalStore

    try:
        api = hub.dataset_api(hub.write_token())
        return HubStore(api), Source(
            kind="hf",
            label="Hugging Face(비공개) 조별 원장 — 팀원에게 보인다",
            detail=hub.WORKSPACE_REPO_ID,
        )
    except hub.HubError:
        return LocalStore(), Source(
            kind="local",
            label="로컬 원장 — 🔴 이 컴퓨터에만 남고 팀원에게 보이지 않는다",
            detail=str(repo_root() / "data" / "workspace"),
        )
