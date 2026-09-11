"""`data/raw/` → `data/derived/sector_daily.parquet` · `market_daily.parquet`.

수집(`batch/fetch_daily.py`)과 **분리돼 있다.** 이쪽은 네트워크를 부르지 않는다 —
순수 함수라 몇 번이든 다시 돌릴 수 있고, 집계 로직을 고칠 때마다 원천을 다시
받지 않는다. 키당 일 10,000회 예산이 그래서 산다.

## 🔒 나가는 것은 파생값뿐이다

들어오는 것은 KRX 원천(`data/raw/`, gitignore)이고, 나가는 것은 **지수·비율·합계**다.
종가·NAV 같은 절대가격은 한 칸도 싣지 않는다 — 섹터에 ETF 가 하나뿐일 때 절대가격을
실으면 그 ETF 의 종가가 그대로 드러나기 때문이다 (약관 제11조② → ADR-SC-0006).

`data/derived/` 도 `/data/` 아래라 gitignore 다. HF 업로드는 M6 가 한다.

## 무엇을 읽는가

`etf_bydd_trd_*` · `stk_bydd_trd_*` · `ksq_bydd_trd_*` 를 **날짜로 묶어** 오름차순
프레임으로 만든다. 세 원천이 다 있는 날만 온전한 날이고, 빠진 것은 `is_partial` 이
말한다. 🔴 빠진 날을 **건너뛰지 않는다** — 건너뛰면 20일 창이 조용히 20일보다
긴 기간을 덮는다.
"""

from __future__ import annotations

import argparse
import re
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, get_args, get_type_hints

from sector.aggregate import AggregateResult, aggregate
from sector.sector_master import (
    SectorConfigError,
    load,
    read_raw_payload,
    default_raw_dir,
)
from sector.sources import krx_openapi, krx_stock
from sector.sources.krx_common import RESULT_BLOCK, KrxError, repo_root

#: 파일 이름에서 원천 종류와 날짜를 읽는다. `.json` · `.json.gz` 둘 다 받는다.
_NAME_RE = re.compile(r"^(etf|stk|ksq)_bydd_trd_([0-9]{8})\.json(\.gz)?$")


def derived_dir() -> Path:
    """`data/derived/`. `/data/` 아래라 gitignore 다 — 파생값도 저장소에 넣지 않는다."""
    return repo_root() / "data" / "derived"


def scan_raw(raw_dir: Path) -> dict[str, dict[str, Path]]:
    """`data/raw/` 를 훑어 `{bas_dd: {종류: 경로}}` 로 묶는다.

    `--probe` 가 남긴 평문과 배치가 남긴 gzip 이 같은 날짜에 둘 다 있으면
    **gzip 을 쓴다** — 배치가 나중에 받은 것이고, 평문은 M3 점검의 잔재다.
    """
    found: dict[str, dict[str, Path]] = defaultdict(dict)
    if not raw_dir.is_dir():
        return {}
    for path in sorted(raw_dir.iterdir()):
        matched = _NAME_RE.match(path.name)
        if not matched:
            continue
        kind, bas_dd, gz = matched.group(1), matched.group(2), matched.group(3)
        current = found[bas_dd].get(kind)
        if current is None or gz:
            found[bas_dd][kind] = path
    return dict(found)


def _traded(quotes: list[Any]) -> bool:
    """그날 **실제로 거래가 있었는가.** 행이 있다는 것만으로는 부족하다.

    🔴 원천을 관찰해 알아낸 것 (2026-09-11) — 시장 휴일에 `etp/etf_bydd_trd` 는
       **0행이 아니라 1000행 넘게** 돌려준다. 다만 그 행들은 종목 마스터에 가깝다:
       종가·NAV·등락률·거래량·거래대금이 **전부 빈 문자열**이고 `LIST_SHRS`(상장좌수)만
       전일과 같은 값으로 들어 있다. 주식 엔드포인트는 같은 날 정직하게 0행을 준다.

       그래서 "행이 있으면 영업일"로 보면 신정·설·추석이 영업일로 섞인다. 결과는
       두 가지로 샌다 —
       ① 자금흐름 축(`etf_shares_sum_T / etf_shares_sum_{T-20}`)의 "20영업일 전"이
          실제로는 20영업일이 아니게 된다. 좌수는 휴일에도 값이 차 있기 때문이다
       ② ETF 렌즈는 행이 있고 구성종목 렌즈는 없어, 두 렌즈의 날짜 축이 갈라진다

       달력이 아니라 **데이터로** 판정한다 — 등락률이 하나라도 있으면 장이 열린
       것이다. 공휴일 표를 코드에 박지 않아도 되고(대체공휴일·임시공휴일이 매년
       바뀐다), 원천이 이름을 바꿔도 무관하다.
    """
    return any(q.fluc_rt_bp is not None for q in quotes)


def build_frames(by_day: dict[str, dict[str, Path]], *, as_of: str) -> list[Any]:
    """날짜별 파일 묶음을 집계 엔진이 먹는 프레임으로 바꾼다 (오름차순).

    🔴 **장이 열리지 않은 날은 뺀다** (`_traded` 참조). 지수가 전진하지 않는 날을
       20일 창에 세면 "20영업일"이 달력상 더 긴 기간을 덮는다. 반면 장이 열렸는데
       일부 값이 빠진 날은 **남긴다** — 그건 휴일이 아니라 결측이고, `is_partial` 이
       말해야 한다.
    """
    frames = []
    for bas_dd in sorted(by_day):
        if bas_dd > as_of:
            continue
        paths = by_day[bas_dd]
        etf_quotes = []
        stock_quotes = []
        if (path := paths.get("etf")) is not None:
            etf_quotes = krx_openapi.parse_payload(
                read_raw_payload(path), expected_bas_dd=bas_dd
            )
        for key in ("stk", "ksq"):
            if (path := paths.get(key)) is None:
                continue
            stock_quotes += krx_stock.parse_payload(
                read_raw_payload(path), mkt=krx_stock.market(key), expected_bas_dd=bas_dd
            )
        if not _traded(etf_quotes) and not _traded(stock_quotes):
            continue  # 장이 열리지 않은 날이다
        frames.append((bas_dd, etf_quotes, stock_quotes))
    return frames


def pandas_dtypes(row_type: type) -> dict[str, str]:
    """dataclass 주석에서 pandas 자료형을 끌어낸다. 🔴 **float 로 새는 것을 막는다.**

    `None` 이 섞인 정수 열을 그냥 `DataFrame` 에 넣으면 pandas 가 `float64` 로
    올린다. 그러면 두 가지가 동시에 깨진다 —

    ① 규약이 금지한 float 가 저장 계층에 들어온다 (`AGENTS.md` 4장)
    ② 스냅샷이 `10000` 대신 `10000.0` 이 되어 골든 테스트가 환경마다 흔들린다

    그래서 nullable 정수(`Int64`)를 **명시로** 준다. 목록을 손으로 적지 않고
    주석에서 끌어내는 이유는, 필드가 늘 때 이 표를 고치는 걸 잊으면 그 열만
    조용히 float 로 돌아가기 때문이다.
    """
    hints = get_type_hints(row_type)
    dtypes: dict[str, str] = {}
    for name, hint in hints.items():
        args = set(get_args(hint)) or {hint}
        base = {a for a in args if a is not type(None)}
        if base == {int}:
            dtypes[name] = "Int64"      # nullable — None 이 있어도 정수로 남는다
        elif base == {bool}:
            dtypes[name] = "boolean"
        elif base == {str}:
            dtypes[name] = "string"
    return dtypes


def _write_parquet(rows: tuple[Any, ...], path: Path, *, columns: list[str]) -> int:
    import pandas as pd

    frame = pd.DataFrame([{c: getattr(r, c) for c in columns} for r in rows])
    # `source_ids` 는 튜플이라 그대로 두면 object 열이 된다. 리스트로 바꿔
    # pyarrow 가 `list<string>` 으로 적게 한다 — M6 가 읽을 때 타입이 살아 있다.
    if "source_ids" in frame.columns:
        frame["source_ids"] = frame["source_ids"].map(list)
    dtypes = pandas_dtypes(type(rows[0])) if rows else {}
    frame = frame.astype({k: v for k, v in dtypes.items() if k in frame.columns})
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(path, index=False)
    return len(frame)


def summarize(result: AggregateResult) -> str:
    """🔒 **값을 찍지 않는다.** 행수·기간·결측 비율만 (약관 제11조②)."""
    rows = result.sectors
    if not rows:
        return "행 0건 — data/raw/ 에 원천이 없거나 as_of 보다 전부 뒤다."
    partial = sum(1 for r in rows if r.is_partial)
    no_etf = sum(1 for r in rows if r.etf_idx_bp is None)
    no_breadth = sum(1 for r in rows if r.breadth_up_bp is None)
    sectors = len({r.sector_id for r in rows})
    lines = [
        f"영업일 {len(result.bas_dds)}일 ({result.bas_dds[0]} ~ {result.bas_dds[-1]})",
        f"sector_daily {len(rows)}행 = 섹터 {sectors}개 × 영업일",
        f"market_daily {len(result.market)}행",
        f"is_partial {partial}행 ({partial * 100 // len(rows)}%) "
        f"— 🔴 채우지 않은 칸이 있다는 표시다. 오류가 아니다",
        f"ETF 지수 없음 {no_etf}행 · 폭 판정 불가 {no_breadth}행 (창이 차기 전)",
    ]
    return "\n".join(lines)


_SECTOR_COLUMNS = [
    "bas_dd", "sector_id", "gics",
    "etf_n", "etf_idx_bp", "etf_ret_1d_bp", "etf_ret_20d_bp",
    "etf_shares_sum", "etf_nav_sum", "etf_value_sum", "etf_premium_bp",
    "member_n", "member_idx_bp", "member_ret_1d_bp", "member_ret_20d_bp",
    "breadth_up_bp", "breadth_n",
    "source_ids", "is_partial", "fetched_at",
]
_MARKET_COLUMNS = [
    "bas_dd", "stock_n", "mkt_idx_bp", "mkt_ret_1d_bp", "mkt_ret_20d_bp",
    "eqw_idx_bp", "eqw_ret_1d_bp", "breadth_up_bp", "breadth_n", "is_partial", "fetched_at",
]


def _shown(path: Path) -> Path:
    """저장소 안이면 상대경로로 짧게. 밖이면(`--out-dir`) 그대로 보여준다."""
    try:
        return path.relative_to(repo_root())
    except ValueError:
        return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m batch.build_sector_daily",
        description="data/raw/ 의 KRX 원천을 sector_daily 로 집계한다 (네트워크 없음)",
    )
    parser.add_argument(
        "--as-of", metavar="YYYYMMDD",
        help="이 날짜까지만 본다 (기본: 원천에 있는 마지막 날). 🔒 룩어헤드 차단",
    )
    parser.add_argument("--raw-dir", help="원천 폴더 (기본: data/raw/)")
    parser.add_argument("--out-dir", help="산출 폴더 (기본: data/derived/)")
    parser.add_argument("--dry-run", action="store_true", help="집계만 하고 쓰지 않는다")
    args = parser.parse_args(argv)

    raw_dir = Path(args.raw_dir) if args.raw_dir else default_raw_dir()
    out_dir = Path(args.out_dir) if args.out_dir else derived_dir()

    by_day = scan_raw(raw_dir)
    if not by_day:
        print(f"❌ {raw_dir} 에 원천이 없다. 먼저 `python -m batch.fetch_daily` 를 돌린다.",
              file=sys.stderr)
        return 2
    as_of = args.as_of or max(by_day)

    try:
        master = load()
        frames = build_frames(by_day, as_of=as_of)
    except (SectorConfigError, KrxError, ValueError) as exc:
        print(f"❌ {exc}", file=sys.stderr)
        return 1
    if not frames:
        print(f"❌ as_of={as_of} 까지 장이 열린 날이 없다.", file=sys.stderr)
        return 2

    fetched_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    result = aggregate(frames, master, as_of=as_of, fetched_at=fetched_at)

    print(f"설정: sectors.yaml {master.version} (sha256 {master.config_sha256[:12]}…)")
    print(f"as_of={as_of}")
    print(summarize(result))

    if args.dry_run:
        print("\n(--dry-run 이라 쓰지 않았다)")
        return 0

    sector_path = out_dir / "sector_daily.parquet"
    market_path = out_dir / "market_daily.parquet"
    n_sector = _write_parquet(result.sectors, sector_path, columns=_SECTOR_COLUMNS)
    n_market = _write_parquet(result.market, market_path, columns=_MARKET_COLUMNS)
    print(f"\n✅ {_shown(sector_path)} — {n_sector}행")
    print(f"✅ {_shown(market_path)} — {n_market}행")
    print("🔒 파생값만 들어 있다. 원천은 data/raw/ 를 떠나지 않는다 (약관 제11조②)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
