"""섹터 정의 검증 — 🔴 **실패하면 배치가 한 줄도 올리지 않는다.**

    python -m batch.validate_config                      # data/raw 스냅샷으로 실재 확인
    python -m batch.validate_config --allow-no-universe  # 형식만 (실재 확인 건너뜀)

규칙과 등급은 `sector/sector_master.py` 에 있다. 이 파일은 **입구**다 —
읽고, 검증하고, 사람이 읽을 형태로 찍고, 종료코드로 말한다.

## 종료코드

| 코드 | 뜻 |
|---|---|
| 0 | 통과 (`warn`·`info` 는 있을 수 있다) |
| 1 | `error` 가 있다 — 고치기 전까지 집계·업로드를 돌리지 않는다 |
| 2 | 검증을 **시작조차 못 했다** — 설정 모양이 틀렸거나 유니버스가 없다 |

🔒 2 를 1 과 섞지 않는다. "검증에서 떨어진 것"과 "검증을 못 한 것"은 다른
   사건이고, 후자를 통과로 읽으면 확인되지 않은 코드가 그대로 올라간다.

🔒 **이 출력은 KRX 값을 찍지 않는다.** 유니버스가 금액을 담지 않으므로
   (`Universe` 는 이름과 게이트 통과 여부만 담는다) 시세가 표준출력으로
   새어 나갈 경로 자체가 없다 — 약관 제11조② (→ ADR-SC-0006).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from sector.sector_master import (
    GICS_SECTORS,
    Finding,
    SectorConfigError,
    SectorMaster,
    Universe,
    default_config_path,
    default_raw_dir,
    has_errors,
    load,
    load_universe,
    validate,
)


def _print_universe_basis(universe: Universe, raw_dir: Path) -> None:
    if not universe:
        print(f"유니버스: 없음 ({raw_dir} 에 스냅샷이 없다)")
        return
    span = (f"{universe.bas_dds[0]}~{universe.bas_dds[-1]}" if universe.days > 1
            else universe.bas_dds[0])
    print(
        f"유니버스: {span} · {universe.days}영업일 · "
        f"ETF {len(universe.etf_names)}종 · 종목 {len(universe.stock_names)}종"
    )
    if universe.days < 20:
        # 자금흐름·모멘텀이 20영업일 창을 쓴다. 유동성도 같은 창으로 보는 것이 맞다.
        print(
            f"  ⚠️  스냅샷이 {universe.days}영업일뿐이다. 유동성 게이트는 '일평균'이므로 "
            f"20영업일이 모이면 다시 돌린다 — 하루 특이값이 경고를 켜거나 끌 수 있다."
        )


def _print_coverage(master: SectorMaster) -> None:
    """GICS 대분류별 테마 섹터. 빈 대분류가 왜 비었는지까지 한눈에 보인다."""
    print("\nGICS 대분류 → 테마 섹터")
    by_gics: dict[str, list[str]] = {}
    for sector in master.sectors:
        by_gics.setdefault(sector.gics, []).append(sector.name_ko)
    for gics in master.gics_sectors:
        names = by_gics.get(gics.id, [])
        if names:
            print(f"  {gics.name_ko:12s} ({gics.id:24s}) {len(names)}개 · {' · '.join(names)}")
        else:
            reason = (gics.empty_reason or "").strip()
            # 여러 줄 note 의 첫 줄만 보여준다 — 전문은 설정 파일에 있다
            first_line = reason.splitlines()[0] if reason else "(이유 없음)"
            print(f"  {gics.name_ko:12s} ({gics.id:24s}) 비어 있음 — {first_line}")
    # GICS_SECTORS 를 import 해 두는 이유: 선언 누락은 규칙이 잡지만, 사람이
    # 보는 요약에서도 11 이라는 수가 맞는지 눈으로 확인되게 한다.
    print(f"  선언된 대분류 {len(master.gics_sectors)} / GICS {len(GICS_SECTORS)}")


def _print_findings(findings: list[Finding]) -> None:
    counts = {level: sum(1 for f in findings if f.level == level)
              for level in ("error", "warn", "info")}
    if findings:
        print("\n검증 결과")
        for finding in findings:
            print(f"  {finding.render()}")
    print(
        f"\nerror {counts['error']}건 · warn {counts['warn']}건 · info {counts['info']}건"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m batch.validate_config",
        description="섹터 정의(sectors.yaml)를 규칙과 KRX 원천에 맞춰 검증한다",
    )
    parser.add_argument("--config", default=None, metavar="PATH",
                        help="기본: sector/config/sectors.yaml")
    parser.add_argument("--raw-dir", default=None, metavar="DIR", help="기본: data/raw")
    parser.add_argument(
        "--allow-no-universe",
        action="store_true",
        help="스냅샷이 없어도 형식 검사만으로 진행한다. 🔴 실재 확인이 빠진다",
    )
    args = parser.parse_args(argv)

    config_path = Path(args.config) if args.config else default_config_path()
    raw_dir = Path(args.raw_dir) if args.raw_dir else default_raw_dir()

    try:
        master = load(config_path)
        universe = load_universe(raw_dir)
    except SectorConfigError as exc:
        print(f"🔴 검증을 시작할 수 없다 — 설정의 모양이 틀렸다.\n{exc}", file=sys.stderr)
        return 2

    print(f"설정: {config_path} · version {master.version}")
    print(f"config_sha256: {master.config_sha256}")
    print(f"섹터 {len(master.sectors)}개 · ETF {sum(len(s.etfs) for s in master.sectors)}건 "
          f"· 구성종목 {sum(len(s.members) for s in master.sectors)}건")
    _print_universe_basis(universe, raw_dir)

    if not universe and not args.allow_no_universe:
        print(
            "\n🔴 원천 스냅샷이 없어 **코드 실재 확인을 할 수 없다.** 형식만 맞는 설정을 "
            "'통과'로 보고하지 않는다 — 확인되지 않은 코드가 그대로 집계로 올라간다.\n"
            "   먼저 스냅샷을 만든다:\n"
            "     python -m sector.sources.krx_openapi --probe --date YYYYMMDD\n"
            "   형식만 보려면 그렇게 말한다:\n"
            "     python -m batch.validate_config --allow-no-universe",
            file=sys.stderr,
        )
        return 2

    findings = validate(master, universe if universe else None)
    _print_coverage(master)
    _print_findings(findings)

    if has_errors(findings):
        print(
            "\n🔴 실패. 고치기 전까지 집계(M5)·업로드(M6)를 돌리지 않는다 — "
            "틀린 섹터 정의로 만든 점수는 근거가 아니다.",
            file=sys.stderr,
        )
        return 1
    print("\n✅ 통과. 모든 코드가 원천에 실재하고, 모든 섹터에 근거(note)가 있다."
          if universe else "\n✅ 형식 통과 (실재 확인은 건너뛰었다).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
