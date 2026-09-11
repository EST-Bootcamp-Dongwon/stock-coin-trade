"""accounts 서비스 계층 — 회원 가입 · 계좌 생성 (E-01 · F-01).

**규약 — 여러 테이블을 함께 바꾸는 일은 전부 이 계층에 둔다.**
뷰는 요청을 풀고 응답을 만들 뿐이고, 모델은 자기 한 행만 안다.
"회원 1행 + 계좌 3행"처럼 여러 행이 함께 성립해야 하는 규칙은 어느 쪽에도 자리가 없다.

★★ **시그널을 쓰지 않는 이유** ────────────────────────────────────────────

`post_save` 시그널로 "회원이 생기면 계좌를 만든다"를 붙이는 것이 Django 에서
가장 흔한 방식이고, 그래서 가장 흔한 사고이기도 하다.

    문제 ① 어디서든 몰래 발동한다
           `seed_demo` 커맨드 · 테스트 픽스처 · `loaddata` · Admin 의 회원 추가 —
           전부 `Member` 를 만든다. 계좌가 언제 생겼는지 추적할 수 없다
    문제 ② 실패해도 회원은 남는다
           시그널 안에서 예외가 나면 계좌 없는 회원이 생긴다.
           호출부는 자기가 뭘 실패했는지도 모른다
    문제 ③ 끄는 방법이 없다
           "계좌 없이 회원만 만들고 싶다"가 불가능해진다

**명시적인 함수 호출은 이 셋이 전부 사라진다.** 부르는 곳이 눈에 보이고,
같은 트랜잭션 안이라 실패하면 회원도 함께 사라지고, 안 부르면 안 만들어진다.
(→ E-01 4장 · 05 문서 6장의 "`AppConfig.ready()` 에 아무것도 붙이지 않는다"와 같은 원칙)
"""

from django.contrib.auth.models import Group
from django.db import transaction

from accounts.models import INITIAL_PRACTICE_CAPITAL, Account, Member
from core.constants import AccountMode

# 가입 시 만들어 주는 연습 계좌 3종 (F-00 5.3).
# 대회 계좌는 여기 없다 — **참가 승인(APPROVED) 시점**에 만든다.
PRACTICE_MODES = (
    AccountMode.PRACTICE_STOCK,
    AccountMode.PRACTICE_CRYPTO,
    AccountMode.PRACTICE_ALT,
)


@transaction.atomic
def register_member(
    *,
    username: str,
    email: str,
    password: str,
    display_name: str = "",
    initial_capital: int = INITIAL_PRACTICE_CAPITAL,
    **extra_fields,
) -> Member:
    """회원과 연습 계좌 3개를 **한 트랜잭션으로** 만든다.

    Args:
        username: 로그인 ID.
        email: 이메일. `Member.email` 에 UNIQUE 가 걸려 있어 중복이면 `IntegrityError`.
        password: **평문을 넘긴다.** 해싱은 `create_user()` 가 한다.
        display_name: 화면 표시명. 비우면 `username` 이 대신 쓰인다(`Member.__str__`).
        initial_capital: 연습 계좌 1개당 시작 자본. 기본 1억 (v1.0 `INITIAL_ASSET` 승계).

    Returns:
        만들어진 `Member`.

    ★ **`@transaction.atomic` 이 핵심이다.** 계좌 3개 중 하나라도 실패하면
    회원까지 통째로 롤백된다. **계좌 없는 회원은 존재할 수 없다** —
    v1.0 은 `member.asset` 한 칸이라 이런 문제가 아예 없었지만, 계좌를 분리한
    v2.0 에서는 이것이 데이터 정합성의 첫 번째 방어선이다.

    Django 관점 — FastAPI + SQLAlchemy 에서는 `session_scope()` 컨텍스트 매니저를
    직접 만들어 커밋·롤백을 관리했다. `@transaction.atomic` 은 데코레이터 한 줄로
    같은 일을 하고, **중첩되면 SAVEPOINT 로 알아서 처리**한다.

    ★ **`create_user()` 를 쓴다. `create()` 가 아니다.** `Member.objects.create()` 는
    비밀번호를 **평문 그대로** 넣는다. Django 에서 가장 흔한 보안 실수이고,
    조용히 성공하기 때문에 발견도 늦다.
    """
    member = Member.objects.create_user(
        username=username,
        email=email,
        password=password,          # create_user 가 해싱한다
        display_name=display_name,
        **extra_fields,
    )

    # 연습 계좌 3종. `bulk_create` 로 쿼리 1번에 끝낸다.
    #
    # ★ `Account` 에는 부분 유니크 제약 `account_uniq_practice_mode`
    #   (member, mode) WHERE contest_id IS NULL 이 걸려 있다.
    #   같은 회원에게 이 함수를 두 번 부르면 여기서 IntegrityError 로 막힌다 —
    #   조용히 계좌가 6개가 되는 것보다 훨씬 낫다.
    Account.objects.bulk_create([
        Account(
            member=member,
            contest=None,                   # 연습 계좌는 대회가 없다 (CHECK 제약이 강제한다)
            mode=mode,
            cash=initial_capital,
            initial_capital=initial_capital,
        )
        for mode in PRACTICE_MODES
    ])

    return member


@transaction.atomic
def reset_practice_account(account: Account) -> Account:
    """연습 계좌를 시작 자본으로 되돌린다 — **결함 D-5 해소** (F-06 6장).

    ★ v1.0 의 계좌 초기화는 `member.asset` 만 되돌리고 **코인 보유(`hold_crypto`)를
    지우지 않았다.** 초기화 후에도 코인이 남아 있어 자산이 늘어나는 결함이었다.

    v2.0 은 계좌가 자산군별로 나뉘어 있어 **그 계좌의 포지션만 지우면 된다.**
    구조가 결함을 원천 차단한 사례다.

    Raises:
        ValueError: 대회 계좌를 넘긴 경우. **대회 계좌는 초기화할 수 없다** —
            진행 중이면 순위 조작이고, 끝났으면 기록 훼손이다.
    """
    if account.is_contest:
        raise ValueError("대회 계좌는 초기화할 수 없습니다.")

    from django.utils import timezone

    # 이 계좌의 포지션을 전부 지운다. 주문 이력(`Order`)은 남긴다 —
    # 초기화는 "다시 시작"이지 "없던 일로"가 아니다.
    account.positions.all().delete()

    account.cash = account.initial_capital
    account.reset_at = timezone.now()
    account.save(update_fields=["cash", "reset_at", "updated_at"])
    return account


def grant_admin_role(member: Member, group_name: str) -> Member:
    """운영자 그룹을 부여한다 (F-19 4장).

    `is_staff=True` 를 함께 켠다 — **그룹만 주면 Admin 에 로그인조차 못 한다.**
    Django 는 `is_staff` 로 Admin 접근을 판정하고, 그룹은 그 안에서 무엇을 볼지만
    정하기 때문이다. 둘 중 하나를 빠뜨리는 것이 흔한 실수다.

    Raises:
        Group.DoesNotExist: 그룹이 없는 경우. 그룹은 마이그레이션
            `accounts/0004_seed_permission_groups` 가 만든다.
    """
    group = Group.objects.get(name=group_name)
    member.groups.add(group)
    if not member.is_staff:
        member.is_staff = True
        member.save(update_fields=["is_staff"])
    return member
