"""KRX 일별매매정보 1년치 수집 — `data/raw/` 까지만 간다.

집계(`batch/build_sector_daily.py`)와 **분리한 이유**: 성질이 다르다.
수집은 느리고(수백 요청) 네트워크에 의존하며 중간에 끊긴다. 집계는 순수 함수이고
빠르며 몇 번이든 다시 돌린다. 한 명령에 묶으면 집계 로직을 고칠 때마다 원천을
다시 받게 된다 — 키당 일 10,000회 예산을 그렇게 태운다.

## 멱등하다 — 다시 돌려도 같은 결과이고, 이미 받은 날은 묻지 않는다

파일이 있으면 건너뛴다. 그래서 끊긴 배치는 **그냥 다시 돌리면** 이어진다.
`--force` 로만 덮어쓴다.

## 🔴 "빈 응답"을 휴일로 굳히지 않는다

KRX 는 세 경우에 모두 HTTP 200 + `{"OutBlock_1": []}` 를 준다 —
① 휴일 ② 미래 날짜 ③ **아직 갱신되지 않은 직전 영업일**(영업일 익일 08:00 KST).

③ 을 "휴일"로 저장하면 **영구 결측**이 된다. 파일이 있으니 다시 묻지도 않는다.
그래서 최근 `_UNSETTLED_DAYS` 일 이내의 빈 응답은 **저장하지 않는다.** 그보다
과거의 빈 응답만 휴일로 확정해 저장한다 — 그러면 다음 실행이 공휴일을 다시 묻지
않아 예산도 아낀다 (→ ADR-SC-0007 "값을 지어내지 않는다").

주말은 애초에 요청하지 않는다. 달력이 확실히 아는 것을 원천에 묻지 않는다.

## 🔒 값을 찍지 않는다

진행 로그는 날짜·행수·상태까지다. 시세를 찍으면 `data/raw/`(gitignore)로 막아 둔
것이 터미널·세션 기록으로 새어 나간다 (약관 제11조② → ADR-SC-0006).
"""

from __future__ import annotations

import argparse
import gzip
import json
import sys
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from sector.sources import krx_openapi, krx_stock
from sector.sources.krx_common import (
    KrxAuthError,
    KrxError,
    KrxTransportError,
    assert_raw_output_allowed,
    fetch_json,
    raw_output_path,
    repo_root,
    result_rows,
    validate_bas_dd,
)

#: KST. 영업일 경계는 한국 시간으로 판단한다 (`AGENTS.md` 4장).
KST = timezone(timedelta(hours=9))

#: 이 기간 안의 빈 응답은 휴일로 확정하지 않는다. 원천이 **영업일 익일 08:00** 에
#: 갱신되므로 하루로는 모자라고(주말·연휴가 끼면 더), 사흘이면 연휴도 넘긴다.
_UNSETTLED_DAYS = 3

#: 요청 사이 간격. KRX 가 상한을 명시하지 않아 보수적으로 둔다 — 1년치 780요청에
#: 4분쯤 더할 뿐이고, 차단당하면 해제가 어렵다(V3 가 pykrx 사례로 경고한다).
DEFAULT_SLEEP_S = 0.3

#: 기본 수집 기간. 1년 + 여유 — 20일 이동평균·20일 수익률이 **1년치 첫날부터**
#: 계산되려면 그 앞에 20영업일이 더 있어야 한다.
DEFAULT_DAYS = 425


@dataclass(frozen=True, slots=True)
class Endpoint:
    """수집 대상 하나. 경로·저장 접두·401 메시지용 이름을 묶는다."""

    key: str
    path: str
    raw_prefix: str
    label: str


def endpoints() -> tuple[Endpoint, ...]:
    """🔒 어댑터가 가진 좌표를 그대로 쓴다 — 경로를 여기 다시 적지 않는다.

    두 곳에 적으면 한쪽만 고쳐지고, 그 순간 배치는 옛 경로를 계속 부른다.
    """
    out = [
        Endpoint(
            "etf",
            krx_openapi.PATH_ETF_BYDD_TRD,
            krx_openapi.RAW_PREFIX,
            krx_openapi.API_LABEL,
        )
    ]
    out += [Endpoint(m.key, m.path, m.raw_prefix, m.label) for m in krx_stock.MARKETS]
    return tuple(out)


def business_days(start: date, end: date) -> list[str]:
    """주말을 뺀 날짜 목록(`"YYYYMMDD"`). 공휴일은 원천에 물어본다.

    한국 공휴일 표를 코드에 박지 않는 이유 — 대체공휴일·임시공휴일이 매년 바뀌고,
    표가 틀리면 **조용히** 하루가 빈다. 원천이 답하게 두면 틀릴 수가 없다.
    """
    days: list[str] = []
    cursor = start
    while cursor <= end:
        if cursor.weekday() < 5:  # 0=월 … 4=금
            days.append(cursor.strftime("%Y%m%d"))
        cursor += timedelta(days=1)
    return days


def _is_unsettled(bas_dd: str, today: date) -> bool:
    """이 날짜의 빈 응답을 "휴일"로 확정해도 되는가."""
    day = datetime.strptime(bas_dd, "%Y%m%d").date()
    return (today - day).days < _UNSETTLED_DAYS


def _write(path: Path, payload: Any) -> int:
    """gzip 으로 저장한다. 🔒 `data/raw/` 밖이면 쓰기 전에 던진다."""
    assert_raw_output_allowed(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # 원자적으로 쓴다 — 중간에 끊긴 반쪽 파일이 남으면 다음 실행이 "받았다"고 믿는다.
    tmp = path.with_suffix(path.suffix + ".part")
    with gzip.open(tmp, "wt", encoding="utf-8") as fp:
        json.dump(payload, fp, ensure_ascii=False)
    tmp.replace(path)
    return path.stat().st_size


def fetch_range(
    days: list[str],
    *,
    targets: tuple[Endpoint, ...],
    force: bool = False,
    sleep_s: float = DEFAULT_SLEEP_S,
    today: date | None = None,
    log: Callable[[str], None] = print,
    fetch: Callable[..., Any] = fetch_json,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, int]:
    """날짜 × 엔드포인트를 훑는다. 반환은 **건수 집계만** — 값을 담지 않는다."""
    now = today if today is not None else datetime.now(KST).date()
    tally = {"saved": 0, "skipped": 0, "holiday": 0, "unsettled": 0, "failed": 0}

    for bas_dd in days:
        for ep in targets:
            path = raw_output_path(ep.raw_prefix, bas_dd, compressed=True)
            plain = raw_output_path(ep.raw_prefix, bas_dd)
            if not force and (path.exists() or plain.exists()):
                tally["skipped"] += 1
                continue
            try:
                payload = fetch(ep.path, bas_dd, api_label=ep.label)
                rows = result_rows(payload, api_label=ep.label)
            except KrxAuthError:
                raise  # 401 은 재시도해도 같다. 전체를 멈춘다
            except (KrxError, ValueError) as exc:
                tally["failed"] += 1
                log(f"  ⚠️  {bas_dd} {ep.key}: {exc}")
                continue

            if not rows:
                if _is_unsettled(bas_dd, now):
                    # 🔴 갱신 전일 수 있다. 저장하면 영구 결측이 된다
                    tally["unsettled"] += 1
                    log(f"  …  {bas_dd} {ep.key}: 0건 — 갱신 전일 수 있어 저장하지 않는다")
                    continue
                tally["holiday"] += 1
            _write(path, payload)
            tally["saved"] += 1
            if rows:
                log(f"  ✅ {bas_dd} {ep.key}: {len(rows)}행")
            else:
                log(f"  🗓  {bas_dd} {ep.key}: 0건 — 휴일로 확정")
            sleep(sleep_s)
    return tally


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m batch.fetch_daily",
        description="KRX 일별매매정보를 data/raw/ 로 수집한다 (멱등 · 재개 가능)",
    )
    parser.add_argument("--from", dest="start", metavar="YYYYMMDD", help="시작 영업일")
    parser.add_argument("--to", dest="end", metavar="YYYYMMDD", help="끝 영업일")
    parser.add_argument(
        "--days", type=int, default=None,
        help=f"--from 없이 최근 N 일 (기본 {DEFAULT_DAYS} — 1년 + 이동평균 여유)",
    )
    parser.add_argument(
        "--market", action="append", choices=[e.key for e in endpoints()],
        help="수집 대상 (반복 지정 가능 · 기본: 전부)",
    )
    parser.add_argument("--force", action="store_true", help="이미 받은 날도 다시 받는다")
    parser.add_argument("--sleep", type=float, default=DEFAULT_SLEEP_S, help="요청 간격(초)")
    parser.add_argument("--dry-run", action="store_true", help="무엇을 받을지만 보여준다")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    today = datetime.now(KST).date()

    try:
        end = (
            datetime.strptime(validate_bas_dd(args.end), "%Y%m%d").date()
            if args.end else today
        )
        if args.start:
            start = datetime.strptime(validate_bas_dd(args.start), "%Y%m%d").date()
        else:
            start = end - timedelta(days=args.days if args.days else DEFAULT_DAYS)
    except ValueError as exc:
        print(f"❌ {exc}", file=sys.stderr)
        return 1
    if start > end:
        print(f"❌ --from({start}) 이 --to({end}) 보다 뒤다", file=sys.stderr)
        return 1

    targets = tuple(e for e in endpoints() if not args.market or e.key in args.market)
    days = business_days(start, end)
    print(
        f"수집 범위: {start:%Y%m%d} ~ {end:%Y%m%d} · 주말 제외 {len(days)}일 × "
        f"{len(targets)}개 원천 = 최대 {len(days) * len(targets)}요청 "
        f"(키당 일 10,000회)"
    )
    print(f"대상: {', '.join(e.key for e in targets)} → data/raw/ (gitignore)")

    if args.dry_run:
        have = sum(
            1
            for d in days
            for e in targets
            if raw_output_path(e.raw_prefix, d, compressed=True).exists()
            or raw_output_path(e.raw_prefix, d).exists()
        )
        print(f"이미 있는 파일 {have}개 · 새로 받을 것 {len(days) * len(targets) - have}개")
        print("(--dry-run 이라 네트워크를 부르지 않았다)")
        return 0

    started = time.monotonic()
    try:
        tally = fetch_range(
            days, targets=targets, force=args.force, sleep_s=args.sleep, today=today
        )
    except KrxAuthError as exc:
        print(f"\n❌ {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\n중단됐다. 받은 데까지 남아 있으니 같은 명령을 다시 돌리면 이어진다.")
        return 130

    elapsed = time.monotonic() - started
    print(
        f"\n저장 {tally['saved']} (휴일 확정 {tally['holiday']}) · "
        f"건너뜀 {tally['skipped']} · 갱신 전 {tally['unsettled']} · "
        f"실패 {tally['failed']} · {elapsed / 60:.1f}분"
    )
    if tally["failed"]:
        print("⚠️  실패가 있다. 같은 명령을 다시 돌리면 실패분만 재시도한다.", file=sys.stderr)
        return 1
    print(f"원천 위치: {repo_root() / 'data' / 'raw'}  🔒 저장소·HF 에 올리지 않는다")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
