"""정산 잡 4종 — F-20 표의 4 · 7 · 9 · 10.

    잡 4  snapshot_intraday   장중 10분    장중 수익률 추이 (F-05 2.3)
    잡 7  settle_daily        영업일 15:40 일별 정산 (F-05 4.1)
    잡 9  settle_weekly       매주 월 06:00 주간 회전율 확정 (F-04 5장)
    잡 10 settle_contest      매일 06:00   대회 상태 전이 + 최종 정산 (F-05 4.3)

`services.py` 가 "무엇을 계산하고 무엇을 저장하는가" 를 안다면, 이 파일은
**"언제 돌고, 실패를 어떻게 다루고, 무엇을 보고하는가"** 를 안다.
`trading/jobs.py` 와 같은 역할 분담이다.

★★ **네 잡 모두 대회 하나가 실패해도 나머지는 진행한다** ────────────────────

한 대회의 데이터가 깨졌다고 다른 대회의 정산이 멈추면, **아무 잘못 없는 참가자
전원의 순위가 하루 비게 된다.** 대회 단위로 예외를 잡아 로그와 `notes` 에 남기고
다음 대회로 간다. 잡이 통째로 실패로 기록되는 것은 잡 자체가 못 돌 때뿐이다.

    ★ 다만 **삼킨 실패는 반드시 `notes` 에 남긴다.** 조용히 건너뛰면 성공으로
      보이고, 며칠 뒤에야 "이 대회만 스냅샷이 없네" 를 발견하게 된다.

★★ **왜 `on_progress` 와 `notes` 를 이렇게 챙기는가** ───────────────────────

이 잡들은 pg_cron 이 부른다. `DataSyncLog` 에는 정수 하나(`rows_affected`)만
남으므로, "실격이 2명 나왔다" · "가격을 못 구한 종목이 3개였다" 같은 **품질 정보는
응답 본문에 실어야만 남는다** (`core/jobs_http.py` 모듈 docstring).

Django 관점 ─────────────────────────────────────────────────────────────────

FastAPI + Celery 였다면 `@shared_task` 로 감싸고 재시도·기록을 Celery 설정에
맡겼을 것이다. 여기서는 브로커도 워커도 없이 `core.jobs.job_run` 컨텍스트 매니저
하나가 그 일을 한다 — **의존이 줄어든 만큼 실패 처리를 우리가 명시해야 한다.**
"""

import logging
from datetime import date as date_type, timedelta
from typing import Callable

from django.db import transaction

from contests import scoring, services
from contests.models import (
    Contest,
    ContestStatus,
    IntradaySnapshot,
    Participation,
    WeeklyTurnover,
)
from core.constants import TriggeredBy
from core.jobs import SyncResult, job_run
from core.time import today_kst, week_start_kst

logger = logging.getLogger(__name__)

ProgressFn = Callable[[str], None]

# 주(week)는 월요일 00:00 ~ 일요일 24:00 KST 다 (F-04 5.1 확정).
_ONE_WEEK = timedelta(days=7)
_SIX_DAYS = timedelta(days=6)


def _noop(_message: str) -> None:
    """`on_progress` 를 안 넘겼을 때 쓰는 빈 콜백."""


# ─────────────────────────────────────────────────────────────────
# 잡 4 — 장중 스냅샷 (F-20 표 4 · F-05 2.3)
# ─────────────────────────────────────────────────────────────────


def snapshot_intraday(
    *,
    triggered_by: str = TriggeredBy.CRON,
    dry_run: bool = False,
    on_progress: ProgressFn = _noop,
) -> SyncResult:
    """장중 10분 간격 수익률을 찍는다.

    Returns:
        `SyncResult` — `created`/`updated` 는 `IntradaySnapshot` 행,
        `skipped` 는 계좌가 없어 건너뛴 참가자.

    ★★ **`coalesce_idle=True` 인 이유** — cron 등록은 UTC 시간대를 넉넉히 잡는다
      (`0-6` 시 = KST 09~15시대). 그러면 장 마감 뒤 몇 회차와 휴장일 전체가
      "아무 일도 안 한 실행" 이 되는데, 그걸 다 행으로 남기면 배치 이력이
      무변화로 덮인다. 조용한 회차는 직전 행의 구간을 늘린다 (E-37).

    ★ **`is_simulated` 시세는 여기서도 배제된다** (`services.closing_prices`).
      장중 수익률 차트에 가짜 가격이 섞이면 참가자가 그것을 실적으로 읽는다.

    ★★ **아직 아무도 지우지 않는다 — 잡 14 가 없다** ──────────────────────────

    F-05 2.3 은 *"당일 것만 보관하고 다음 영업일 시작 시 삭제한다"* 로 정했고,
    그 삭제는 F-20 잡 14(`cleanup`, 매일 04:00)의 몫이다. **잡 14 는 아직 구현되지
    않았으므로 이 테이블은 계속 쌓인다.**

        참가자 100명 × 하루 40행 × 20영업일 = 8만 행

    한 대회로는 문제가 되지 않는 규모지만, 잡 14 를 만들 때까지는 **차트가 당일치만
    보이도록 조회 쪽에서 날짜를 걸어야 한다.** 쌓인 행 자체가 틀린 값은 아니다.
    """
    result = SyncResult()
    with job_run("snapshot_intraday", triggered_by, dry_run=dry_run, coalesce_idle=True) as record:
        if not services.is_intraday_window():
            # ★ 장외는 **실패가 아니다.** 조용히 0건으로 끝낸다.
            result.note("장중이 아니라 장중 스냅샷을 찍지 않았습니다 (영업일 09:00~15:30 KST)")
            on_progress("장외 — 건너뜁니다")
            record["rows"] = 0
            return result

        slot = services.intraday_slot()
        day = today_kst()
        contests = services.contests_in_progress(day)
        on_progress(f"{slot:%H:%M} 칸 · 진행 중 대회 {len(contests)}개")

        for contest in contests:
            try:
                _intraday_one_contest(contest, slot, result, dry_run=dry_run,
                                      on_progress=on_progress)
            except Exception as exc:            # noqa: BLE001 — 대회 하나가 잡을 죽이지 않게
                logger.exception("대회 %s 장중 스냅샷 실패", contest.slug)
                result.note(f"[{contest.slug}] 장중 스냅샷 실패 — {type(exc).__name__}: {exc}")

        record["rows"] = result.rows
    return result


def _intraday_one_contest(
    contest: Contest, slot, result: SyncResult, *, dry_run: bool, on_progress: ProgressFn
) -> None:
    """대회 하나의 장중 스냅샷."""
    participations = _participations_with_account(contest)
    if not participations:
        return

    valuations = _evaluate_all(contest, participations, result, on_progress=on_progress)
    for participation, valuation in valuations:
        initial = participation.account.initial_capital or contest.initial_capital
        nav = scoring.nav_for(valuation.total_asset, initial)
        if dry_run:
            result.updated += 1
            continue
        _row, created = IntradaySnapshot.objects.update_or_create(
            participation=participation,
            at=slot,
            defaults={"nav": nav, "return_pct": scoring.cumulative_return_pct(nav)},
        )
        if created:
            result.created += 1
        else:
            result.updated += 1


# ─────────────────────────────────────────────────────────────────
# 잡 7 — 일별 정산 (F-20 표 7 · F-05 4.1)
# ─────────────────────────────────────────────────────────────────


def settle_daily(
    *,
    triggered_by: str = TriggeredBy.CRON,
    dry_run: bool = False,
    on_progress: ProgressFn = _noop,
    target_date: date_type | None = None,
) -> SyncResult:
    """일별 정산 — 스냅샷 · NAV · 순위 · 위반 · 주간 회전율 잠정치.

    Args:
        target_date: 정산할 영업일. 생략하면 오늘(KST).

    ★★ **`target_date` 를 명시하면 휴장일 판정을 건너뛴다** ────────────────────

    cron 은 날짜를 넘기지 않으므로 휴장일에는 아무것도 하지 않는다. 반면 운영자가
    `--date 2026-08-15` 처럼 **날짜를 찍어 부르는 것은 "그날을 정산하라" 는 지시**다
    (달력이 아직 안 채워졌거나, 공휴일 판정이 틀렸을 때 손으로 메우는 경로).
    자동과 수동의 기대가 다르므로 기본값 하나로 억지로 맞추지 않는다
    (`config/internal_urls.py` 의 `_sync_trading_calendar` 와 같은 판단).

    ★ **멱등하다.** 같은 날짜로 몇 번을 돌려도 행이 늘지 않는다. 16:00 의 종목
      마스터 적재(잡 8) 뒤에 한 번 더 돌리면 **더 정확한 종가로 덮어쓴다**
      (`services.closing_prices` 참조).
    """
    result = SyncResult()
    day = target_date or today_kst()

    with job_run("settle_daily", triggered_by, dry_run=dry_run) as record:
        from market.sessions import is_business_day       # noqa: PLC0415

        if target_date is None and not is_business_day(day):
            result.note(f"{day} 는 영업일이 아니라 정산하지 않았습니다")
            on_progress("휴장일 — 건너뜁니다")
            record["rows"] = 0
            return result

        contests = services.contests_in_progress(day)
        on_progress(f"{day} 정산 · 진행 중 대회 {len(contests)}개")
        if not contests:
            result.note(f"{day} 기준으로 진행 중인 대회가 없습니다")

        for contest in contests:
            try:
                _settle_one_contest(contest, day, result, dry_run=dry_run,
                                    on_progress=on_progress)
            except Exception as exc:            # noqa: BLE001 — 대회 하나가 잡을 죽이지 않게
                logger.exception("대회 %s 일별 정산 실패", contest.slug)
                result.note(f"[{contest.slug}] 일별 정산 실패 — {type(exc).__name__}: {exc}")

        record["rows"] = result.rows
    return result


def _settle_one_contest(
    contest: Contest, day: date_type, result: SyncResult, *, dry_run: bool,
    on_progress: ProgressFn,
) -> None:
    """대회 하나의 일별 정산."""
    participations = _participations_with_account(contest)
    if not participations:
        result.note(f"[{contest.slug}] 계좌가 있는 참가자가 없습니다")
        return

    account_ids = [item.account_id for item in participations]
    trades = services.daily_trade_amounts(account_ids, day)
    sector_limits = services.sector_limit_map(contest)
    smalls = services.small_cap_symbols(contest)
    valuations = _evaluate_all(contest, participations, result, on_progress=on_progress)

    violation_count = 0
    for participation, valuation in valuations:
        violations = services.evaluate_violations(contest, valuation, sector_limits, smalls)
        violation_count += len(violations)
        if dry_run:
            result.updated += 1
            continue
        _snapshot, created = services.write_daily_snapshot(
            participation, day, valuation, trades.get(participation.account_id, {}), violations
        )
        if created:
            result.created += 1
        else:
            result.updated += 1

    if dry_run:
        result.note(f"[{contest.slug}] dry-run — 참가자 {len(valuations)}명분을 계산만 했습니다")
        return

    ranked = services.rebuild_rankings(contest, day)
    on_progress(f"[{contest.slug}] 스냅샷 {len(valuations)}명 · 순위 {ranked}행")

    # ── 이번 주 회전율 잠정치 갱신 (F-04 5.2) ────────────────────
    #
    # ★ **매일 덮어쓴다.** 화면의 "예상 위반" 이 이 값이다. 주가 끝나야 확정되지만
    #   (잡 9), 참가자는 오늘 시점의 회전율을 보고 남은 날에 매매를 조절한다.
    week = week_start_kst(day)
    for participation, _valuation in valuations:
        services.refresh_weekly_turnover(participation, week, confirm=False)

    if violation_count:
        result.note(
            f"[{contest.slug}] 한도 위반 {violation_count}건 — "
            f"가격 변동에 의한 초과는 경고만 하고 강제 매도하지 않습니다 (F-04 3.4)"
        )


# ─────────────────────────────────────────────────────────────────
# 잡 9 — 주간 정산 (F-20 표 9 · F-04 5장)
# ─────────────────────────────────────────────────────────────────


def settle_weekly(
    *,
    triggered_by: str = TriggeredBy.CRON,
    dry_run: bool = False,
    on_progress: ProgressFn = _noop,
    week_start: date_type | None = None,
) -> SyncResult:
    """지난 주 회전율을 **확정**하고 4회 위반자를 자동 정지한다 (F-04 5.4).

    Args:
        week_start: 확정할 주의 월요일(KST). 생략하면 **지난 주**.

    ★ 월요일 06:00 에 도는 잡이라 기본값이 "지난 주" 다. 이번 주는 아직 하루도
      지나지 않았으므로 확정할 것이 없다.

    ★★ **대상을 대회 상태가 아니라 스냅샷으로 고른다** ─────────────────────

    "그 주에 `DailySnapshot` 이 있는 참가자" 가 곧 그 주에 대회를 뛴 사람이다.
    대회 상태(`ONGOING`)로 고르면 **주말 사이에 종료된 대회의 마지막 주가 통째로
    확정되지 않는다** — 월요일 06:00 에는 이미 `SETTLING` 이거나 `CLOSED` 일 수
    있기 때문이다. 그 주의 위반이 누락되면 실격 판정이 틀린다.
    """
    result = SyncResult()
    target_week = week_start or (week_start_kst() - _ONE_WEEK)

    with job_run("settle_weekly", triggered_by, dry_run=dry_run) as record:
        from contests.models import DailySnapshot       # noqa: PLC0415

        week_end = target_week + _SIX_DAYS
        participation_ids = list(
            DailySnapshot.objects.filter(date__gte=target_week, date__lte=week_end)
            .values_list("participation_id", flat=True)
            .distinct()
        )
        on_progress(f"{target_week} ~ {week_end} 확정 대상 {len(participation_ids)}명")
        if not participation_ids:
            result.note(f"{target_week} 주에 스냅샷이 있는 참가자가 없습니다")
            record["rows"] = 0
            return result

        participations = list(
            Participation.objects.filter(pk__in=participation_ids).select_related("contest")
        )

        disqualified: list[str] = []
        violators = 0
        for participation in participations:
            if dry_run:
                # ★ **계산만 하고 저장하지 않는다.** 커맨드가 "DB 에 아무것도 쓰지
                #   않았습니다" 라고 말하는데 회전율 행이 늘어나면 그 약속이 거짓이 된다.
                #   대신 **누가 잘릴 뻔했는지**까지 읽기만으로 알려준다 —
                #   dry-run 으로 확인하고 싶은 것이 바로 그것이기 때문이다.
                values = services.compute_weekly_turnover(participation, target_week)
                result.updated += 1
                if values["is_violation"]:
                    violators += 1
                projected = services.projected_violation_total(
                    participation, target_week, values["is_violation"]
                )
                if projected > services.turnover_violation_limit(participation):
                    disqualified.append(
                        f"{participation.contest.slug}/{participation.nickname}({projected}회)"
                    )
                continue

            try:
                with transaction.atomic():
                    row, created = services.refresh_weekly_turnover(
                        participation, target_week, confirm=True
                    )
                    total = services.renumber_violations(participation)
                    if services.apply_turnover_disqualification(participation, total):
                        disqualified.append(f"{participation.contest.slug}/{participation.nickname}")
            except Exception as exc:            # noqa: BLE001 — 한 명이 잡을 죽이지 않게
                logger.exception("참가자 %s 주간 정산 실패", participation.pk)
                result.skipped += 1
                result.note(f"참가자 {participation.pk} 주간 정산 실패 — {type(exc).__name__}: {exc}")
                continue

            if created:
                result.created += 1
            else:
                result.updated += 1
            if row.is_violation:
                violators += 1

        if violators:
            result.note(
                f"회전율 기준 미달 {violators}명 — 월요일 아침에는 매매가 있을 수 없어 "
                f"금주 예상값이 1회 위반으로 보이는 것이 정상입니다 (F-04 5.2)"
            )
        if disqualified:
            # ★★ 실격은 **가장 눈에 띄어야 하는 결과**다. 사람의 대회 참가가 끝나는
            #    일이라 숫자 뒤에 묻히면 안 된다. 별칭까지 적는다.
            verb = "자동 정지될 예정" if dry_run else "자동 정지"
            result.note(f"★ 회전율 허용 횟수 초과로 {verb} {len(disqualified)}명: "
                        + " · ".join(disqualified))
        if dry_run:
            result.note("dry-run — 계산만 했습니다. 회전율 행도 실격 처리도 저장하지 않았습니다")

        record["rows"] = result.rows
    return result


# ─────────────────────────────────────────────────────────────────
# 잡 10 — 대회 상태 전이 + 최종 정산 (F-20 표 10 · F-05 4.3 · F-02 2장)
# ─────────────────────────────────────────────────────────────────


def settle_contest(
    *,
    triggered_by: str = TriggeredBy.CRON,
    dry_run: bool = False,
    on_progress: ProgressFn = _noop,
    target_date: date_type | None = None,
) -> SyncResult:
    """대회 생명주기를 하루 한 번 밀어 준다.

        UPCOMING → ONGOING    시작일 도달 (+ 유니버스 고정)
        ONGOING  → SETTLING   종료일 경과
        SETTLING → CLOSED     최종 정산 완료 (+ 계좌 동결)

    Returns:
        `SyncResult` — `created` = 확정한 `ContestResult` 행,
        `updated` = 상태가 바뀐 대회 수.

    ★★ **운영자가 매일 손으로 상태를 바꾸는 구조는 주말·공휴일에 깨진다** (F-02 2장).
      토요일에 끝난 대회가 월요일까지 `ONGOING` 으로 남으면 그 이틀 동안 주문이
      들어온다. 자동으로 미는 이유다.

    ★ **`DRAFT → UPCOMING` 은 자동으로 하지 않는다.** 그건 "이 대회를 공개한다" 는
      운영자의 결정이다. 시스템이 대신할 판단이 아니다.
    """
    result = SyncResult()
    day = target_date or today_kst()

    with job_run("settle_contest", triggered_by, dry_run=dry_run) as record:
        opened = _open_due_contests(day, result, dry_run=dry_run, on_progress=on_progress)
        moved = _move_finished_to_settling(day, result, dry_run=dry_run, on_progress=on_progress)
        closed = _finalize_settling(result, dry_run=dry_run, on_progress=on_progress)

        if not (opened or moved or closed):
            result.note(f"{day} 기준으로 상태를 바꿀 대회가 없습니다")
        record["rows"] = result.rows
    return result


def _open_due_contests(
    day: date_type, result: SyncResult, *, dry_run: bool, on_progress: ProgressFn
) -> int:
    """시작일이 된 `UPCOMING` 대회를 `ONGOING` 으로 올리고 유니버스를 얼린다."""
    due = list(Contest.objects.filter(status=ContestStatus.UPCOMING, start_date__lte=day))
    for contest in due:
        if dry_run:
            result.note(f"[{contest.slug}] dry-run — ONGOING 으로 전환할 예정입니다")
            result.updated += 1
            continue
        try:
            with transaction.atomic():
                if contest.universe_frozen_at is None:
                    symbols, sectors = services.freeze_universe(contest)
                    result.note(
                        f"[{contest.slug}] 유니버스 고정 — 종목 {symbols:,}개 · 섹터 {sectors}개"
                    )
                    if symbols == 0:
                        # ★ 종목 마스터가 비어 있으면 **모든 주문이 막힌다.**
                        #   조용히 넘기면 대회 첫날에 아무도 매수하지 못하고,
                        #   원인이 여기라는 것을 아무도 모른다.
                        result.note(
                            f"★ [{contest.slug}] 유니버스가 비었습니다 — "
                            f"`manage.py sync_stock_master --with-sectors` 를 먼저 돌리십시오"
                        )
                contest.status = ContestStatus.ONGOING
                contest.save(update_fields=["status", "updated_at"])
        except Exception as exc:            # noqa: BLE001
            logger.exception("대회 %s 개시 실패", contest.slug)
            result.note(f"[{contest.slug}] 개시 실패 — {type(exc).__name__}: {exc}")
            result.skipped += 1
            continue
        on_progress(f"[{contest.slug}] UPCOMING → ONGOING")
        result.updated += 1
    return len(due)


def _move_finished_to_settling(
    day: date_type, result: SyncResult, *, dry_run: bool, on_progress: ProgressFn
) -> int:
    """종료일이 지난 `ONGOING` 대회를 `SETTLING` 으로 옮긴다.

    ★ **`end_date < day` 다 (`<=` 가 아니다).** 종료일 당일 15:40 에 마지막 정산이
      돌아야 하므로, 그날은 아직 `ONGOING` 이어야 한다. 다음 날 06:00 에 이 잡이
      옮긴다 — F-05 4.3 의 *"대회 종료일 다음 영업일 06:00"* 이 그 뜻이다.
    """
    due = list(Contest.objects.filter(status=ContestStatus.ONGOING, end_date__lt=day))
    for contest in due:
        if dry_run:
            result.note(f"[{contest.slug}] dry-run — SETTLING 으로 전환할 예정입니다")
            result.updated += 1
            continue
        contest.status = ContestStatus.SETTLING
        contest.save(update_fields=["status", "updated_at"])
        on_progress(f"[{contest.slug}] ONGOING → SETTLING")
        result.updated += 1
    return len(due)


def _finalize_settling(
    result: SyncResult, *, dry_run: bool, on_progress: ProgressFn
) -> int:
    """`SETTLING` 대회를 확정하고 `CLOSED` 로 닫는다."""
    due = list(Contest.objects.filter(status=ContestStatus.SETTLING))
    for contest in due:
        if dry_run:
            count = Participation.objects.filter(
                contest=contest, status__in=services.SETTLED_STATUSES
            ).count()
            result.note(f"[{contest.slug}] dry-run — 참가자 {count}명을 확정할 예정입니다")
            result.updated += 1
            continue
        # ★★ **확정 전에 주간 회전율을 마감하고 실격을 적용한다** (2026-08-16 추가) ──
        #
        #   `finalize_contest` 는 순위·등급을 확정하고 계좌를 얼어붙인다. 되돌리는
        #   경로가 앱 안에 없다(`ContestAdmin.readonly_fields` 에 `status`,
        #   `ContestResultAdmin` 은 읽기 전용). **그래서 그 앞에서 끝낼 일은 다 끝내야 한다.**
        #
        #   회전율 4회 위반 자동 실격은 `settle_weekly` 안에서만 일어나는데,
        #   그 잡은 **주 1회(월요일 06:00 KST)** 다. 반면 이 잡은 **매일 06:10** 돈다.
        #   즉 두 잡의 10분 간격이 순서를 만들어 주는 것은 **월요일에 끝난 대회뿐**이다.
        #
        #       금요일 종료 → 토요일 06:10 에 여기가 확정   ← 마지막 주가 미확정
        #       월요일 종료 → 화요일 06:10 에 여기가 확정   ← 마지막 주가 미확정
        #
        #   마지막 주에 4회째 위반을 한 참가자가 **실격되지 않은 채 등급을 받고**,
        #   그 사람이 백분위 모수에 남아 **아래 참가자 전원의 등급이 함께 틀어진다.**
        #
        #   앞 잡이 돌기를 기다리는 대신 **여기서 스스로 마감한다.** 단 `settle_weekly`
        #   가 이미 확정한 주는 건드리지 않는다 — 메우는 것은 **미확정 주뿐**이다
        #   (그쪽 ★★ 참조). 실격 적용은 이미 실격이면 no-op 이라 두 번 찍히지 않는다.
        failed = _confirm_weeks_before_finalize(contest, result, on_progress=on_progress)
        if failed:
            # ★★ **한 명이라도 확정에 실패하면 이 대회는 오늘 닫지 않는다.**
            #
            #   백분위는 **모수가 맞아야** 뜻이 있다. 실격됐어야 할 사람이 미확정으로
            #   남아 랭킹 모수에 끼면 그 사람만 틀리는 게 아니라 **아래 참가자 전원의
            #   등급이 함께 이동한다.** 그런데 `finalize_contest` 는 `CLOSED` 로 닫고
            #   계좌를 얼려서, 앱 안에 되돌릴 경로가 없다
            #   (`ClosedContestGuardMixin` 이 CLOSED 대회의 참가 편집을 막는다).
            #
            #   반면 **미루는 비용은 0 이다.** 대회는 `SETTLING` 으로 남고
            #   `_finalize_settling` 은 매일 그 상태를 다시 긁으므로 내일 재시도된다.
            #   되돌릴 수 없는 것과 하루 늦는 것 사이에서는 후자가 항상 싸다.
            result.note(
                f"★ [{contest.slug}] 회전율 확정에 실패한 참가자가 {failed}명 있어 "
                f"최종 정산을 **미룹니다**. 원인을 고치면 다음 실행에서 자동으로 재시도합니다 "
                f"(대회는 SETTLING 으로 남아 있습니다)"
            )
            result.skipped += 1
            continue

        try:
            count = services.finalize_contest(contest)
        except Exception as exc:            # noqa: BLE001
            logger.exception("대회 %s 최종 정산 실패", contest.slug)
            result.note(f"★ [{contest.slug}] 최종 정산 실패 — {type(exc).__name__}: {exc}")
            result.skipped += 1
            continue
        result.created += count
        result.updated += 1
        on_progress(f"[{contest.slug}] SETTLING → CLOSED · 확정 {count}명")
        result.note(
            f"[{contest.slug}] 최종 정산 완료 — 참가자 {count}명 · 대회 계좌를 동결했습니다"
        )
    return len(due)


def _confirm_weeks_before_finalize(
    contest: Contest, result: SyncResult, *, on_progress: ProgressFn
) -> int:
    """대회가 **온전히 덮는 주**를 확정하고 실격을 적용한다.

    Returns:
        **확정에 실패한 참가자 수.** 0 이 아니면 호출부가 이 대회의 최종 정산을
        미룬다 — 모수가 틀린 채로 백분위를 굳히지 않기 위해서다.


    `_finalize_settling` 이 `finalize_contest` 를 부르기 직전에만 쓴다.
    호출 이유는 그쪽 ★★ 주석 참조 — 요약하면 **`settle_weekly`(주 1회)를 기다리면
    월요일에 끝나지 않은 대회는 마지막 주가 미확정인 채 등급이 굳는다.**

    ★ 실격돼도 정산 대상에서 빠지지 않는다. `DISQUALIFIED` 는 `SETTLED_STATUSES`
      안에 있어 `ContestResult` 행은 그대로 만들어지고, `is_ranked=False` 때문에
      **백분위 모수에서만** 빠진다 (`services.finalize_contest` 의 ★ 참조).
      실격자에게도 "내 최종 수익률이 얼마였나" 는 보여줘야 한다.

    ★★ **참가자 한 명이 대회 전체를 죽이지 않게** 한 명씩 트랜잭션을 끊는다.
      `settle_weekly` 가 같은 이유로 같은 모양을 쓴다 — 한 사람의 데이터가 이상해서
      확정이 통째로 멈추면, 멀쩡한 참가자 수십 명의 결과까지 함께 막힌다.
    """
    participations = _participations_with_account(contest)
    if not participations:
        return 0

    # ★★ **대회가 주 전체를 덮는 주만 확정한다 — 토막 주는 건드리지 않는다** ────
    #
    #   `compute_weekly_turnover` 의 분모는 **평균** 운용금액이지 합계가 아니다.
    #   그래서 하루짜리 토막 주도 5일 주와 **똑같은 절대 매매금액**을 요구한다.
    #   대회 마지막 날 매매를 멈추는 것은 지극히 정상인데, 그 주를 확정하면
    #   자동으로 위반 1회가 붙는다.
    #
    #   그 자체는 이 함수가 만든 문제가 아니다(`settle_weekly` 도 같은 계산을 쓴다).
    #   다만 여기서 확정하면 **그 위반이 등급을 결정하고 되돌릴 수 없게** 된다 —
    #   원래는 대회가 닫힌 **뒤** 확정돼 등급에 영향이 없던 값이다.
    #   확정을 앞당기는 것이 이 함수의 일이지, **판정 기준을 바꾸는 것은 아니다.**
    #
    #   → 토막 주는 예전처럼 `settle_weekly` 에 맡긴다. 여기서 메우는 것은
    #     "대회가 온전히 덮었는데 아직 아무도 확정하지 않은 주" 뿐이다.
    #
    #   ★ 토막 주를 위반으로 세는 규칙 자체가 온당한가는 **별개의 명세 문제**다
    #     (F-04 5.1 에 규정이 없다 · 변경노트 E-61). 첫 주도 같은 사각에 있다.
    weeks: list[date_type] = []
    week = week_start_kst(contest.start_date)
    while week <= contest.end_date:
        if week >= contest.start_date and week + _SIX_DAYS <= contest.end_date:
            weeks.append(week)
        week += _ONE_WEEK

    # ★★ **이미 확정된 주는 건드리지 않는다** ────────────────────────────────
    #
    #   `settle_weekly` 가 확정한 주를 여기서 다시 계산할 이유가 없다. 두 가지
    #   이유로 좁힌다:
    #
    #     ① **비용** — 3개월 대회(13주) × 참가자 100명이면 1,300회의 집계 쿼리가
    #        확정 직전 한 번에 몰린다. 서버리스 실행 한도에 닿을 수 있다.
    #        미확정 주만 보면 보통 **마지막 한 주**뿐이다.
    #     ② **확정의 주인** — 지난 주의 판정은 `settle_weekly` 의 몫이다. 뒤늦게
    #        덮어쓰면 그 사이에 밖에서 손본 값(운영자 shell·SQL)이 조용히 사라진다.
    #
    #   여기가 메우는 것은 **아무도 확정하지 않은 주**다. 그게 이 함수의 존재 이유다.
    already_confirmed = set(
        WeeklyTurnover.objects.filter(
            participation__in=participations, is_confirmed=True, week_start__in=weeks
        ).values_list("participation_id", "week_start")
    )

    disqualified: list[str] = []
    failed = 0
    for participation in participations:
        try:
            with transaction.atomic():
                for week in weeks:
                    if (participation.pk, week) in already_confirmed:
                        continue
                    services.refresh_weekly_turnover(participation, week, confirm=True)
                total = services.renumber_violations(participation)
                if services.apply_turnover_disqualification(participation, total):
                    disqualified.append(participation.nickname)
        except Exception as exc:            # noqa: BLE001 — 한 명이 대회를 죽이지 않게
            # ★ 여기서 `continue` 하는 것은 **다른 참가자를 마저 처리하기 위해서**지
            #   실패를 눈감기 위해서가 아니다. 실패 수는 위로 올려 보내고,
            #   호출부가 그 대회의 확정을 미룬다.
            logger.exception("참가자 %s 최종 회전율 확정 실패", participation.pk)
            failed += 1
            result.skipped += 1
            result.note(
                f"★ [{contest.slug}] 참가자 {participation.pk} 최종 회전율 확정 실패 — "
                f"{type(exc).__name__}: {exc}"
            )
            continue

    on_progress(
        f"[{contest.slug}] 최종 회전율 확정 — {len(weeks)}주 × {len(participations)}명"
        + (f" · 실격 {len(disqualified)}명" if disqualified else "")
    )
    if disqualified:
        # ★★ 실격은 사람의 대회 참가가 끝나는 일이라 가장 눈에 띄어야 한다.
        result.note(
            f"★ [{contest.slug}] 최종 확정 중 회전율 실격 {len(disqualified)}명 — "
            + ", ".join(disqualified)
        )
    return failed


# ─────────────────────────────────────────────────────────────────
# 공통 보조
# ─────────────────────────────────────────────────────────────────


def _participations_with_account(contest: Contest) -> list[Participation]:
    """정산 대상 참가자. **계좌가 없는 참가는 뺀다.**

    `PENDING`(승인 대기) 참가자는 계좌가 아직 없다 (`Participation.account` 주석).
    평가할 자산이 없으므로 스냅샷을 만들 수 없다.
    """
    return list(
        Participation.objects.filter(
            contest=contest, status__in=services.SETTLED_STATUSES, account__isnull=False
        )
        .select_related("account", "contest")
        .order_by("pk")
    )


def _evaluate_all(
    contest: Contest, participations: list[Participation], result: SyncResult, *,
    on_progress: ProgressFn,
) -> list[tuple[Participation, services.Valuation]]:
    """참가자 전원을 **쿼리 몇 번으로** 평가한다 (잡 4·7 공용).

    ★ 시세·유니버스·포지션을 각각 한 번씩만 읽는다. 참가자 수만큼 쿼리가 늘면
      장중 10분 잡(잡 4)이 DB 를 계속 두드리게 된다.
    """
    account_ids = [item.account_id for item in participations]
    positions = services.positions_by_account(account_ids)
    symbols = sorted({
        position.symbol for rows in positions.values() for position in rows
    })
    prices, fallback = services.closing_prices(symbols)
    names = services.universe_map(contest)

    missing = [symbol for symbol in symbols if symbol not in prices]
    if missing:
        # ★ 가격을 못 구한 종목은 **평단으로 평가된다** (`evaluate_account`).
        #   숫자가 나오긴 하므로 조용히 두면 아무도 모른다. 반드시 남긴다.
        result.note(
            f"[{contest.slug}] 가격을 구하지 못해 매입단가로 평가한 종목 {len(missing)}개: "
            + " · ".join(missing[:10]) + (" …" if len(missing) > 10 else "")
        )
    if fallback:
        result.note(
            f"[{contest.slug}] 시세 캐시가 없어 종목 마스터의 종가로 평가한 종목 "
            f"{len(fallback)}개 — 15:40 정산에서는 흔한 일입니다"
        )

    pairs = []
    for participation in participations:
        valuation = services.evaluate_account(
            participation.account,
            positions.get(participation.account_id, []),
            prices,
            names,
            estimated=fallback,
        )
        pairs.append((participation, valuation))

    total = sum(item[1].total_asset for item in pairs)
    on_progress(
        f"[{contest.slug}] 참가자 {len(pairs)}명 · 종목 {len(symbols)}개 · "
        f"순자산 합계 {total:,}원"
    )
    return pairs


__all__ = [
    "settle_contest",
    "settle_daily",
    "settle_weekly",
    "snapshot_intraday",
]
