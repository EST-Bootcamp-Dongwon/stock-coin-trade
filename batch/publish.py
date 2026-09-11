"""`data/derived/` → HF `stock-coin-trade/sector-scores` (private).

수집(`fetch_daily`) · 집계(`build_sector_daily`) 와 마찬가지로 **한 가지만 한다** —
이미 만들어진 파생값을 올린다. 여기서 집계를 다시 하지 않는다.

## 🔴 멱등하다 — 두 번째 실행에서 올라가는 파일이 0개여야 정상이다

그 성질은 규율이 아니라 **구조**에서 나온다.

1. **`--date` 가 필수다.** 게시되는 모든 값이 `--date` 와 데이터에서만 나온다.
   벽시계를 읽는 곳이 한 군데도 없다 — `datetime.now()` 가 이 파일에 없는 것이
   그 계약이다. 같은 날짜 + 같은 원천 = 같은 바이트.
2. **파일마다 `content_sha256` 을 원격 `MANIFEST.json` 과 대조한다.** 같으면
   그 파일의 작업을 만들지 않고, MANIFEST 항목을 **원격 것 그대로 이어받는다**
   (그래야 MANIFEST 가 원격 바이트를 정확히 서술한다).
3. **MANIFEST 자신도 바이트가 같으면 올리지 않는다.** 그래서 아무것도 안 바뀐
   날은 작업이 0건이 되고, `commit()` 이 아예 불리지 않는다.

### `content_sha256` 이 바이트 해시와 다른 이유

`fetched_at` 은 데이터가 아니라 **빌드 시각**이다. 집계를 다시 돌리면 값이 그대로여도
이 열만 바뀌어 15개월치 샤드가 전부 "바뀐 파일"이 된다. 그래서 멱등성 판정은
`fetched_at` 을 뺀 내용으로 한다(`gate.BUILD_STAMP_COLUMN`). 건너뛴 파일의 원격
`fetched_at` 은 **그 내용이 처음 만들어진 시각**으로 남는다 — 오히려 더 정확하다.

## 🔴 검사기를 스스로 돌린다. 그리고 "돌렸는지"를 확인한다

`sector/datastore/gate.py` 를 **모듈 최상단에서 import 한다.** 파일이 없으면
ImportError 로 죽는다 — 통과가 아니라 실패다.

그런데 더 위험한 것은 검사기가 **조용히 아무것도 검사하지 않고** 통과시키는
경우다(이 저장소는 `pytest.ini` 0건 수집으로 그 교훈을 이미 적어 뒀다). 그래서
게이트는 *실제로 돌린 검사 이름*을 돌려주고, 여기서 `REQUIRED_CHECKS` 와
대조한다. 하나라도 빠지면 **판정 불가**이고, 판정 불가는 통과가 아니다.

## 🔒 올라가는 것

`data/derived/` 의 파생값뿐이다. `data/raw/` 는 한 파일도 올라가지 않는다 —
경로 허용목록이 `hub.commit()` 문 앞에 있고, 열 허용목록이 `gate.py` 에 있다
(약관 제11조② → ADR-SC-0006).

## 쓰는 법

    python -m batch.publish --date 20260910 --dry-run   # 무엇을 올릴지만 본다
    python -m batch.publish --date 20260910             # 올린다
    python -m batch.publish --date 20260910             # ★ 두 번째 — 업로드 0건
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# 🔴 게이트는 조건부 import 가 아니다. 없으면 여기서 죽는다 — 그것이 의도다.
from sector.datastore import gate, hub
from sector.sector_master import SectorConfigError, load
from sector.sources.krx_common import repo_root

#: 앱이 읽는 창. 🔒 앱은 `latest/` 만 읽는다 — 월별 샤드는 보관용이다.
LATEST_BUSINESS_DAYS = 400

MANIFEST_PATH = "MANIFEST.json"
README_PATH = "README.md"
SNAPSHOT_PATH = "latest/snapshot.json"
SCHEMA_VERSION = 1

#: 약관 제10조③ — 화면에 출처를 표시할 의무가 있다. 데이터 옆에도 적어 둔다.
SOURCE_NOTICE = (
    "한국거래소 통계정보(KRX Open API)를 집계한 파생값이다. "
    "원천 데이터는 포함하지 않는다."
)

_KINDS = ("sector_daily", "market_daily")


@dataclass(frozen=True, slots=True)
class Shard:
    """올릴 파일 하나. 바이트를 메모리에 들고 있다 (전부 합쳐 수백 KB 다)."""

    path: str
    data: bytes
    content_sha256: str      # 🔴 멱등성 판정의 기준 — `fetched_at` 을 뺀 내용
    rows: int | None
    bas_dd_first: str | None
    bas_dd_last: str | None

    @property
    def sha256(self) -> str:
        return _sha256(self.data)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _json_bytes(obj: Any) -> bytes:
    """결정적 JSON. `sort_keys` 가 없으면 같은 내용이 다른 바이트가 된다."""
    return (json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _parquet_bytes(frame: Any) -> bytes:
    """프레임 → parquet 바이트. 디스크를 거치지 않는다.

    `source_ids` 는 그대로 두면 object 열이 되어 pyarrow 가 자료형을 잃는다.
    리스트로 바꿔 `list<string>` 으로 적게 한다 (집계가 쓰는 방식과 같다).
    """
    out = frame
    if "source_ids" in out.columns:
        out = out.copy()
        out["source_ids"] = out["source_ids"].map(list)
    buffer = io.BytesIO()
    out.to_parquet(buffer, index=False)
    return buffer.getvalue()


def _content_sha(frame: Any) -> str:
    """🔴 `fetched_at` 을 뺀 내용의 해시. 머리주석 참조."""
    columns = [c for c in frame.columns if c != gate.BUILD_STAMP_COLUMN]
    return _sha256(_parquet_bytes(frame[columns]))


def _span(frame: Any) -> tuple[str | None, str | None]:
    if len(frame) == 0:
        return None, None
    days = frame["bas_dd"]
    return str(days.min()), str(days.max())


def _shard(path: str, frame: Any) -> Shard:
    return Shard(
        path=path,
        data=_parquet_bytes(frame),
        content_sha256=_content_sha(frame),
        rows=len(frame),
        bas_dd_first=_span(frame)[0],
        bas_dd_last=_span(frame)[1],
    )


def _doc_shard(path: str, data: bytes) -> Shard:
    """문서·메타 파일. 벽시계를 담지 않으므로 바이트 해시가 곧 내용 해시다."""
    return Shard(path=path, data=data, content_sha256=_sha256(data),
                 rows=None, bas_dd_first=None, bas_dd_last=None)


# ── 샤드 만들기 ─────────────────────────────────────────────────────────────

def _month_path(kind: str, month: str) -> str:
    """`sector_daily/year=2026/month=09/sector_daily_202609.parquet`.

    Hive 식 디렉터리를 쓰는 이유는 나중에 `read_parquet(폴더)` 로 통째로 읽을 때
    연·월이 열로 살아나기 때문이다. 지금은 앱이 `latest/` 만 읽으므로 보관용이다.
    """
    return f"{kind}/year={month[:4]}/month={month[4:]}/{kind}_{month}.parquet"


def build_shards(
    sector: Any,
    market: Any,
    *,
    latest_days: int = LATEST_BUSINESS_DAYS,
) -> list[Shard]:
    """게시용 프레임 두 개 → 올릴 파일 목록. **네트워크를 부르지 않는다.**"""
    shards: list[Shard] = []
    for kind, frame in (("sector_daily", sector), ("market_daily", market)):
        months = sorted({str(d)[:6] for d in frame["bas_dd"]})
        for month in months:
            part = frame[frame["bas_dd"].astype("string").str[:6] == month]
            shards.append(_shard(_month_path(kind, month), part))

    # `latest/` — 앱이 읽는 유일한 곳. 최근 N 영업일만 담는다.
    days = sorted({str(d) for d in sector["bas_dd"]})
    window = set(days[-latest_days:])
    for kind, frame in (("sector_daily", sector), ("market_daily", market)):
        part = frame[frame["bas_dd"].astype("string").isin(window)]
        shards.append(_shard(f"latest/{kind}_latest.parquet", part))
    return shards


def snapshot(sector: Any, market: Any, *, as_of: str, published_date: str,
             config: Any, latest_days: int) -> dict[str, Any]:
    """`latest/snapshot.json` — 앱이 "데이터 상태"를 그릴 재료.

    🔒 값을 담지 않는다. 건수·기간·설정 지문까지다.
    🔒 벽시계를 담지 않는다 — `published_date` 는 `--date` 이고, `as_of` 는 데이터의
       마지막 영업일이다. 둘 다 같은 입력에서 같은 값이 나온다.
    """
    days = sorted({str(d) for d in sector["bas_dd"]})
    return {
        "schema_version": SCHEMA_VERSION,
        "as_of": as_of,
        "published_date": published_date,
        "business_days": len(days),
        "first_bas_dd": days[0] if days else None,
        "last_bas_dd": days[-1] if days else None,
        "sector_rows": int(len(sector)),
        "sector_count": int(sector["sector_id"].nunique()),
        "market_rows": int(len(market)),
        "partial_sector_rows": int(sector["is_partial"].sum()),
        "latest_window_days": latest_days,
        "config_version": config.version,
        "config_sha256": config.config_sha256,
        "source_notice": SOURCE_NOTICE,
        "read_this": {
            "sector_daily": "latest/sector_daily_latest.parquet",
            "market_daily": "latest/market_daily_latest.parquet",
        },
    }


def readme(*, repo_id: str) -> bytes:
    """데이터셋 카드. **열 목록을 허용목록에서 끌어온다** — 손으로 적으면 어긋난다."""
    def columns(names: tuple[str, ...]) -> str:
        return "\n".join(f"- `{c}`" for c in names)

    withheld = "\n".join(
        f"- `{c}` — {why}" for c, why in sorted(gate.DELIBERATELY_WITHHELD.items())
    )
    text = f"""---
license: other
language:
- ko
tags:
- korea
- etf
- sector
pretty_name: 섹터 ETF 레이더 — 파생값
---

# {repo_id}

키움증권 모의투자 대회용 **섹터 ETF 레이더**(`stock-coin-trade` v3.0)가 매일 올리는
집계·파생값이다. 🔒 **비공개 저장소로 유지한다.**

## 🔴 출처와 제약

{SOURCE_NOTICE}

- 한국거래소 **이용약관 제11조②** — 제공받은 정보를 제3자에게 제공할 수 없다.
  그래서 **원천 시세·ETF 일별매매정보는 여기에 한 파일도 없다.** 로컬에만 둔다.
- **제6조②** — 비상업적 목적으로만 이용한다.
- **제10조③** — 이 데이터로 만든 화면에는 "한국거래소 통계정보" 출처를 표시한다.
- 🔴 **투자 권유가 아니다.** 과거 데이터의 요약이다.

## 무엇이 들어 있나

| 경로 | 내용 |
|---|---|
| `latest/sector_daily_latest.parquet` | 최근 {LATEST_BUSINESS_DAYS}영업일 섹터 집계. **앱은 이것만 읽는다** |
| `latest/market_daily_latest.parquet` | 같은 창의 시장 기준선 |
| `latest/snapshot.json` | 기준일·건수·설정 지문 |
| `sector_daily/year=YYYY/month=MM/…` | 월별 보관본 |
| `market_daily/year=YYYY/month=MM/…` | 월별 보관본 |
| `MANIFEST.json` | 파일별 행수·해시·게시일 |

## 열

값은 **정수**다. 비율은 **bp**(1bp = 0.01%), 지수는 **기준일 = 10000**.
`float` 를 쓰지 않는 이유는 누적 반올림이 새기 때문이다.

### `sector_daily` (1행 = 1섹터 × 1영업일)

{columns(gate.SECTOR_PUBLISHED_COLUMNS)}

### `market_daily` (1행 = 1영업일)

{columns(gate.MARKET_PUBLISHED_COLUMNS)}

### 일부러 내보내지 않는 열

{withheld}

## 읽는 법

`is_partial=true` 는 그날 설정된 것 전부를 담지 못했다는 **표시**이지 오류가 아니다.
🔴 빠진 값을 0·전일값·평균으로 채우지 않는다. 없으면 없다고 둔다.
"""
    return text.encode("utf-8")


# ── 계획 ────────────────────────────────────────────────────────────────────

@dataclass(frozen=True, slots=True)
class Plan:
    upload: tuple[Shard, ...]
    skipped: tuple[str, ...]
    stale: tuple[str, ...]          # 원격에 있으나 이번에 만들지 않은 파일
    manifest: dict[str, Any]
    manifest_bytes: bytes
    manifest_changed: bool


def plan_publish(
    shards: list[Shard],
    remote_manifest: dict[str, Any] | None,
    remote_manifest_bytes: bytes | None,
    *,
    as_of: str,
    published_date: str,
    repo_id: str,
    config: Any,
) -> Plan:
    """무엇을 올리고 무엇을 건너뛸지 정한다. **네트워크를 부르지 않는다.**"""
    previous: dict[str, Any] = dict((remote_manifest or {}).get("files") or {})

    upload: list[Shard] = []
    skipped: list[str] = []
    entries: dict[str, Any] = {}

    for shard in sorted(shards, key=lambda s: s.path):
        prior = previous.get(shard.path)
        if isinstance(prior, dict) and prior.get("content_sha256") == shard.content_sha256:
            # 🔒 원격 항목을 **그대로** 이어받는다. 새로 적으면 MANIFEST 가
            #    올리지도 않은 바이트를 서술하게 된다.
            entries[shard.path] = prior
            skipped.append(shard.path)
            continue
        upload.append(shard)
        entries[shard.path] = {
            "rows": shard.rows,
            "bytes": len(shard.data),
            "sha256": shard.sha256,
            "content_sha256": shard.content_sha256,
            "bas_dd_first": shard.bas_dd_first,
            "bas_dd_last": shard.bas_dd_last,
            "published_date": published_date,
        }

    manifest = {
        "schema_version": SCHEMA_VERSION,
        "repo_id": repo_id,
        "as_of": as_of,
        "notice": SOURCE_NOTICE,
        "config": {"version": config.version, "sha256": config.config_sha256},
        "files": entries,
    }
    manifest_bytes = _json_bytes(manifest)
    stale = tuple(sorted(set(previous) - set(entries)))
    return Plan(
        upload=tuple(upload),
        skipped=tuple(skipped),
        stale=stale,
        manifest=manifest,
        manifest_bytes=manifest_bytes,
        manifest_changed=manifest_bytes != remote_manifest_bytes,
    )


# ── 게이트 ──────────────────────────────────────────────────────────────────

def run_gate(sector: Any, market: Any, *, as_of: str) -> None:
    """🔴 검사기를 돌리고, **돌았는지까지** 확인한다. 안 돌았으면 통과가 아니다."""
    report = gate.check(sector=sector, market=market, as_of=as_of)
    missing = gate.REQUIRED_CHECKS - report.checks_run
    if missing:
        raise hub.PublishBlocked(
            f"🔴 검사기가 {sorted(missing)} 를 돌리지 않았다 — 판정 불가다.",
            hint="`sector/datastore/gate.py` 의 `_CHECKS` 를 확인한다. "
                 "판정 불가는 통과가 아니다",
        )
    if report.violations:
        raise hub.PublishBlocked(
            "🔴 업로드 게이트가 막았다 — 한 파일도 올리지 않는다:\n"
            + "\n".join(f"    · {v}" for v in report.violations),
            hint="집계(`python -m batch.build_sector_daily`)를 다시 확인한다",
        )


# ── CLI ─────────────────────────────────────────────────────────────────────

def derived_dir() -> Path:
    return repo_root() / "data" / "derived"


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


def _human(n: int) -> str:
    return f"{n / 1024:.1f}KB" if n < 1024 * 1024 else f"{n / 1024 / 1024:.2f}MB"


def _config_is_newer(directory: Path) -> bool:
    """설정을 고치고 집계를 다시 돌리지 않았는가.

    ⚠️ mtime 기반이라 `git checkout` 만으로도 참이 될 수 있다. 그래서 **경고까지만**
       한다 — 막으면 애먼 날에 배치가 선다.
    """
    config = repo_root() / "sector" / "config" / "sectors.yaml"
    built = directory / "sector_daily.parquet"
    if not config.is_file() or not built.is_file():
        return False
    return config.stat().st_mtime > built.stat().st_mtime


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m batch.publish",
        description="data/derived/ 의 파생값을 HF private dataset 에 올린다",
    )
    parser.add_argument(
        "--date", metavar="YYYYMMDD", required=True,
        help="이 날짜까지의 세상을 게시한다. 🔒 필수다 — 같은 날짜면 같은 바이트",
    )
    parser.add_argument("--derived-dir", help="파생값 폴더 (기본: data/derived/)")
    parser.add_argument("--repo-id", default=hub.REPO_ID, help=f"기본: {hub.REPO_ID}")
    parser.add_argument(
        "--latest-days", type=int, default=LATEST_BUSINESS_DAYS,
        help=f"latest/ 에 담을 영업일 수 (기본: {LATEST_BUSINESS_DAYS})",
    )
    parser.add_argument("--dry-run", action="store_true", help="계획만 세우고 올리지 않는다")
    parser.add_argument(
        "--prune", action="store_true",
        help="원격에만 남은 옛 파일을 같은 커밋에서 지운다 (기본: 경고만)",
    )
    args = parser.parse_args(argv)

    as_of_arg = str(args.date)
    if len(as_of_arg) != 8 or not as_of_arg.isdigit():
        print(f"❌ --date 는 YYYYMMDD 여야 한다: {as_of_arg!r}", file=sys.stderr)
        return 2

    directory = Path(args.derived_dir) if args.derived_dir else derived_dir()
    if "raw" in directory.parts:
        # 🔒 원천 폴더를 가리키는 실수를 여기서 끊는다 (ADR-SC-0006 ①).
        print(f"❌ 원천 폴더를 게시할 수 없다: {directory}", file=sys.stderr)
        return 1

    try:
        raw_sector, raw_market = _load_frames(directory)
        config = load()
    except (FileNotFoundError, SectorConfigError) as exc:
        print(f"❌ {exc}", file=sys.stderr)
        return 2

    # 🔒 룩어헤드 차단 — `--date` 보다 뒤의 날은 애초에 싣지 않는다.
    raw_sector = raw_sector[raw_sector["bas_dd"].astype("string") <= as_of_arg]
    raw_market = raw_market[raw_market["bas_dd"].astype("string") <= as_of_arg]
    if len(raw_sector) == 0 or len(raw_market) == 0:
        print(f"❌ --date={as_of_arg} 이하의 행이 없다. 집계 범위를 확인한다.", file=sys.stderr)
        return 2

    sector = gate.project_sector(raw_sector)
    market = gate.project_market(raw_market)

    try:
        run_gate(sector, market, as_of=as_of_arg)
    except hub.PublishBlocked as exc:
        print(f"❌ {exc}", file=sys.stderr)
        return 1

    as_of = max(str(d) for d in sector["bas_dd"])
    if as_of != as_of_arg:
        print(f"⚠️  --date={as_of_arg} 이지만 원천의 마지막 영업일은 {as_of} 다 "
              f"(휴일이거나 아직 갱신 전).")
    if _config_is_newer(directory):
        print("⚠️  sectors.yaml 이 집계 결과보다 새롭다 — "
              "`python -m batch.build_sector_daily` 를 다시 돌리는 편이 낫다.")

    shards = build_shards(sector, market, latest_days=args.latest_days)
    shards.append(_doc_shard(
        SNAPSHOT_PATH,
        _json_bytes(snapshot(sector, market, as_of=as_of, published_date=as_of_arg,
                             config=config, latest_days=args.latest_days)),
    ))
    shards.append(_doc_shard(README_PATH, readme(repo_id=args.repo_id)))

    print(f"저장소: {args.repo_id} (dataset · private 전제)")
    print(f"설정  : sectors.yaml {config.version} (sha256 {config.config_sha256[:12]}…)")
    print(f"as_of : {as_of} · 영업일 {sector['bas_dd'].nunique()}일 · "
          f"sector {len(sector)}행 · market {len(market)}행")
    print(f"게이트: {len(gate.REQUIRED_CHECKS)}개 검사 통과")

    # ── 원격 상태 읽기 ──────────────────────────────────────────────────────
    try:
        api = hub.dataset_api(hub.write_token())
    except hub.HubError as exc:
        print(f"❌ {exc}", file=sys.stderr)
        return 1

    created_repo = False
    try:
        if not hub.repo_exists(api, args.repo_id):
            if args.dry_run:
                print(f"\n(원격 저장소가 없다 — 실제 실행 시 **private** 으로 만든다)")
                remote_manifest, remote_bytes = None, None
            else:
                created_repo = hub.ensure_private_repo(api, args.repo_id)
                remote_manifest, remote_bytes = None, None
        else:
            hub.assert_private(api, args.repo_id)      # 빨리 실패하기 위한 조기 확인
            remote_bytes = hub.download_bytes(api, MANIFEST_PATH, repo_id=args.repo_id)
            remote_manifest = hub.read_json(api, MANIFEST_PATH, repo_id=args.repo_id)
    except hub.HubError as exc:
        print(f"❌ {exc}", file=sys.stderr)
        return 1

    plan = plan_publish(
        shards, remote_manifest, remote_bytes,
        as_of=as_of, published_date=as_of_arg, repo_id=args.repo_id, config=config,
    )

    total = sum(len(s.data) for s in plan.upload)
    print(f"\n계획  : 파일 {len(shards)}개 — 올림 {len(plan.upload)} · "
          f"건너뜀 {len(plan.skipped)} · MANIFEST {'바뀜' if plan.manifest_changed else '그대로'}")
    for shard in plan.upload:
        rows = f"{shard.rows}행" if shard.rows is not None else "—"
        print(f"  + {shard.path}  {rows}  {_human(len(shard.data))}")
    if plan.stale:
        verb = "지운다" if args.prune else "그대로 둔다 (지우려면 --prune)"
        print(f"  ⚠️  원격에만 남은 파일 {len(plan.stale)}개 — {verb}")
        for path in plan.stale[:10]:
            print(f"      - {path}")

    if not plan.upload and not plan.manifest_changed and not (args.prune and plan.stale):
        print("\n✅ 바뀐 것이 없다. **업로드 0건** — 멱등성이 지켜졌다.")
        return 0

    if args.dry_run:
        print(f"\n(--dry-run 이라 올리지 않았다 · 올릴 바이트 {_human(total)})")
        return 0

    from huggingface_hub import CommitOperationAdd, CommitOperationDelete

    operations: list[Any] = [
        CommitOperationAdd(path_in_repo=s.path, path_or_fileobj=s.data) for s in plan.upload
    ]
    if plan.manifest_changed:
        operations.append(
            CommitOperationAdd(path_in_repo=MANIFEST_PATH, path_or_fileobj=plan.manifest_bytes)
        )
    if args.prune:
        operations += [CommitOperationDelete(path_in_repo=p) for p in plan.stale]

    message = f"publish {as_of} — 파일 {len(operations)}건"
    try:
        # 🔴 단일 커밋. `hub.commit()` 이 경로를 검사하고 private 을 **다시** 묻는다.
        oid = hub.commit(api, operations, message=message, repo_id=args.repo_id,
                         description=SOURCE_NOTICE)
    except hub.HubError as exc:
        print(f"❌ {exc}", file=sys.stderr)
        return 1

    if created_repo:
        print(f"\n🆕 {args.repo_id} 를 **private** 으로 만들었다.")
    print(f"\n✅ 단일 커밋 {len(operations)}건 올림 ({_human(total)}) {oid[:12] if oid else ''}")
    print(f"   https://huggingface.co/datasets/{args.repo_id}")
    print("🔒 파생값만 올라갔다. KRX 원천은 data/raw/ 를 떠나지 않았다 (약관 제11조②)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
