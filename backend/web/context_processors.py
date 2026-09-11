"""전 화면 공통 컨텍스트 (U-01).

`settings.TEMPLATES` 의 `context_processors` 에 등록해 두면, **모든 뷰의 응답이
이 함수의 반환값을 컨텍스트에 합쳐** 받는다. 뷰마다 `{"nav_groups": …}` 를
넣어 주지 않아도 된다.

Django 관점 ─────────────────────────────────────────────────────────────────

Next.js 에서는 `layout.tsx` 가 서버에서 데이터를 읽어 children 에 내려주거나,
React Context 로 전역 값을 뿌렸다. Django 의 컨텍스트 프로세서가 그 자리다.

★ 다만 **모든 요청에서 실행된다**는 점이 다르다. 여기서 무심코 DB 를 조회하면
  그 비용이 **전 화면·전 요청**에 붙는다. 정적 파일 요청에는 안 붙지만, HTMX
  프래그먼트 폴링(10~60초마다)에는 전부 붙는다. 그래서 아래 규칙을 지킨다.

    · 메뉴 구성은 **DB 를 전혀 보지 않는다** (파이썬 자료구조 + `reverse()`)
    · DB 가 필요한 값(대회 배지)은 `SimpleLazyObject` 로 감싸
      **템플릿이 실제로 꺼내 쓸 때만** 쿼리가 나가게 한다
"""

from django.utils.functional import SimpleLazyObject

from core.time import today_kst
from web.navigation import build_account_menu, build_menu


def _contest_badge(user):
    """GNB 의 진행 중 대회 배지 (U-01 2.2) — `RFM 1회 · D-12 · 7위`.

    Returns:
        `{"name", "contest_id", "d_day", "rank", "rank_delta"}` 또는 `None`.

    ★ **여러 대회에 동시 참가 중이면 가장 먼저 끝나는 대회**를 띄운다.
      가장 급한 것이 가장 먼저 보여야 한다.

    ★ 순위는 `ContestRanking` 을 **그대로 읽는다.** 조회 시점에 계산하지 않는다
      (F-05 7장). 랭킹은 10분마다 도는 잡이 만들어 둔 스냅샷이 정본이고,
      화면이 제 나름대로 계산하면 랭킹 화면과 홈이 다른 숫자를 말하게 된다.
    """
    # 지연 임포트 — 모듈 최상단에서 모델을 import 하면 앱 로딩 순서에 걸린다.
    from contests.models import ContestRanking, ContestStatus, Participation, ParticipationStatus

    participation = (
        Participation.objects.filter(
            member=user,
            status=ParticipationStatus.APPROVED,
            contest__status=ContestStatus.ONGOING,
        )
        .select_related("contest")
        .order_by("contest__end_date")
        .first()
    )
    if participation is None:
        return None

    contest = participation.contest
    ranking = (
        ContestRanking.objects.filter(participation=participation).order_by("-date").first()
    )

    return {
        "contest_id": contest.id,
        "name": contest.name,
        # D-day 는 **종료일까지 남은 일수**다. 종료 당일이면 0, 지났으면 음수.
        "d_day": (contest.end_date - today_kst()).days,
        "rank": ranking.rank if ranking else None,
        # 전일 대비 순위 변동. 양수 = 올랐다(등수 숫자가 줄었다).
        "rank_delta": (
            ranking.prev_rank - ranking.rank
            if ranking and ranking.prev_rank and ranking.rank
            else None
        ),
    }


def navigation(request):
    """모든 템플릿에 들어가는 공통 값."""
    user = getattr(request, "user", None)
    is_authenticated = bool(user and user.is_authenticated)

    return {
        "nav_groups": build_menu(is_authenticated=is_authenticated),
        "nav_account_menu": build_account_menu(is_authenticated=is_authenticated),
        # ★ `SimpleLazyObject` — 템플릿에서 `{{ nav_contest_badge }}` 를 실제로
        #   건드리는 순간에만 안쪽 함수가 돈다. 건드리지 않으면 쿼리가 0 이다.
        #   비로그인 사용자는 아예 None 이라 그마저도 없다.
        #
        #   Django 관점 — `request.user` 자체가 같은 방식으로 구현돼 있다.
        #   인증 미들웨어는 사용자를 바로 읽지 않고 lazy 로 걸어 두어, 로그인이
        #   필요 없는 화면에서는 세션 조회조차 일어나지 않게 한다.
        "nav_contest_badge": (
            SimpleLazyObject(lambda: _contest_badge(user)) if is_authenticated else None
        ),
    }
