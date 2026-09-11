"""`data/derived/sector_daily` · `market_daily` → `score_daily.parquet`.

수집(`fetch_daily`) · 집계(`build_sector_daily`) · 게시(`publish`) 와 나란한
**네 번째 한 걸음**이다. 이쪽도 네트워크를 부르지 않는다 — 순수 함수라 몇 번이든
다시 돌릴 수 있고, 축의 정의를 고칠 때마다 원천을 다시 받지 않는다.

## 왜 집계에 합치지 않았나

`build_sector_daily` 가 점수까지 내면 "수집과 집계를 나눠 둔" 이유가 무너진다.
축을 고치는 일은 자주 있고(가중치·창·정규화), 그때마다 5985행 집계를 다시 만들
이유가 없다. 무엇보다 **집계는 사실이고 점수는 판단이다** — 판단이 바뀌었다고
사실을 다시 쓰게 만들면, 어느 쪽이 바뀌었는지 나중에 구별할 수 없다.

## 🔒 나가는 것

`score_daily` 에는 원시값(bp)·z·점수·순위와 **유동성 판정 결과**만 있다.
판정의 재료였던 거래대금(`etf_value_sum`)은 여기서 `liquidity_ok` 로 바뀌어
로컬에 남는다 — 그 덕에 M7 에서 게시 허용목록 한 줄을 뺄 수 있었다(V31).

## 쓰는 법

    python -m batch.build_scores --dry-run     # 무엇이 나올지만 본다
    python -m batch.build_scores               # → data/derived/score_daily.parquet
"""

from __future__ import annotations

import argparse
import dataclasses
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from batch.build_sector_daily import derived_dir, pandas_dtypes
from sector import scoring
from sector.scoring import AXES, AXIS_NAMES, ScoreRow, ScoringError
from sector.sector_master import SectorConfigError, load
from sector.sources.krx_common import repo_root

#: 🔒 열 순서는 `gate.SCORE_PUBLISHED_COLUMNS` 와 같아야 한다. 게시 투영이 이 순서를
#:    다시 잡지만, 여기서부터 맞춰 두면 로컬 parquet 을 열어 볼 때도 읽기 쉽다.
_SCORE_COLUMNS = [f.name for f in dataclasses.fields(ScoreRow)]

_KINDS = ("sector_daily", "market_daily")


def _load_frames(directory: Path) -> tuple[Any, Any]:
    import pandas as pd

    frames = []
    for kind in _KINDS:
        path = directory / f"{kind}.parquet"
        if not path.is_file():
            raise FileNotFoundError(
                f"{path} 가 없다. 먼저 `python -m batch.build_sector_daily` 를 돌린다."
            )
        frames.append(pd.read_parquet(path))
    return frames[0], frames[1]


def _write_parquet(rows: tuple[ScoreRow, ...], path: Path) -> int:
    import pandas as pd

    frame = pd.DataFrame([{c: getattr(r, c) for c in _SCORE_COLUMNS} for r in rows])
    dtypes = pandas_dtypes(ScoreRow)
    frame = frame.astype({k: v for k, v in dtypes.items() if k in frame.columns})
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(path, index=False)
    return len(frame)


def summarize(rows: tuple[ScoreRow, ...]) -> str:
    """🔒 값이 아니라 **구조**를 보고한다 — 몇 축으로 쟀고 무엇이 빠졌는가."""
    if not rows:
        return "행 0건 — 채점할 날이 없다."
    days = sorted({r.bas_dd for r in rows})
    sectors = len({r.sector_id for r in rows})
    axes = Counter()
    for row in rows:
        for axis in AXES:
            if axis not in row.axes_missing:
                axes[axis] += 1
    degraded = sum(1 for r in rows if r.axes_degraded)
    scored = sum(1 for r in rows if r.score_balanced_bp is not None)
    lines = [
        f"영업일 {len(days)}일 ({days[0]} ~ {days[-1]})",
        f"score_daily {len(rows)}행 = 섹터 {sectors}개 × 영업일",
        f"점수가 나온 행 {scored} · 축이 하나도 안 선 행 {len(rows) - scored}",
        "축별 가용: " + " · ".join(
            f"{AXIS_NAMES[a]}({a}) {axes[a]}" for a in AXES
        ),
        f"중앙값절대편차가 0 이라 평균절대편차로 표준화한 행 {degraded} "
        f"— 🔴 오류가 아니다. 폭 축에서 실제로 생긴다",
        f"유동성 경고 {sum(1 for r in rows if r.liquidity_ok is False)}행 · "
        f"판정 불가 {sum(1 for r in rows if r.liquidity_ok is None)}행 (창이 차기 전)",
    ]
    return "\n".join(lines)


def _shown(path: Path) -> Path:
    try:
        return path.relative_to(repo_root())
    except ValueError:
        return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m batch.build_scores",
        description="sector_daily 를 4축으로 채점한다 (네트워크 없음)",
    )
    parser.add_argument(
        "--as-of", metavar="YYYYMMDD",
        help="이 날짜까지만 본다 (기본: 집계에 있는 마지막 날). 🔒 룩어헤드 차단",
    )
    parser.add_argument(
        "--days", type=int,
        help="마지막 N 영업일만 채점한다 (기본: 전 기간). 과거 행은 어느 쪽이든 같다",
    )
    parser.add_argument("--derived-dir", help="파생값 폴더 (기본: data/derived/)")
    parser.add_argument("--out-dir", help="산출 폴더 (기본: --derived-dir 과 같은 곳)")
    parser.add_argument("--dry-run", action="store_true", help="채점만 하고 쓰지 않는다")
    args = parser.parse_args(argv)

    directory = Path(args.derived_dir) if args.derived_dir else derived_dir()
    out_dir = Path(args.out_dir) if args.out_dir else directory
    if "raw" in directory.parts:
        print(f"❌ 원천 폴더를 채점할 수 없다: {directory}", file=sys.stderr)
        return 1

    try:
        sector, market = _load_frames(directory)
        config = load()
    except (FileNotFoundError, SectorConfigError) as exc:
        print(f"❌ {exc}", file=sys.stderr)
        return 2

    as_of = args.as_of or max(str(d) for d in sector["bas_dd"])
    if len(as_of) != 8 or not as_of.isdigit():
        print(f"❌ --as-of 는 YYYYMMDD 여야 한다: {as_of!r}", file=sys.stderr)
        return 2

    fetched_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    try:
        rows = scoring.score_history(
            sector, market, as_of=as_of, config=config,
            fetched_at=fetched_at, days=args.days,
        )
    except ScoringError as exc:
        print(f"❌ {exc}", file=sys.stderr)
        return 1

    print(f"설정: sectors.yaml {config.version} (sha256 {config.config_sha256[:12]}…)")
    print(f"as_of={as_of} · 가중치 프리셋 {', '.join(scoring.PRESETS)}")
    print(summarize(rows))

    if args.dry_run:
        print("\n(--dry-run 이라 쓰지 않았다)")
        return 0
    if not rows:
        print("❌ 쓸 행이 없다.", file=sys.stderr)
        return 2

    path = out_dir / "score_daily.parquet"
    written = _write_parquet(rows, path)
    print(f"\n✅ {_shown(path)} — {written}행")
    print("🔒 점수·z·판정만 들어 있다. 거래대금은 liquidity_ok 로 바뀌어 로컬에 남는다")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
