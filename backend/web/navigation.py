"""GNB 메뉴 정의 (U-01 2장).

v1.0 은 `common.js` 의 `navGroups` 배열이 메뉴를 만들었고, **Pine 화면 2개가
거기 빠져 있었다**(결함 D-2). 화면은 존재하는데 아무도 갈 수 없었다.

v2.0 은 메뉴를 **서버에서 한 곳에 정의**한다. 화면을 추가할 때 이 파일을 함께
고치지 않으면 메뉴에 안 뜨는 것은 같지만, 최소한 **모든 화면 목록이 한 눈에**
들어온다. 아래 표가 곧 [ui/00-화면목록-라우팅] 2장의 사본이다.

★★ **아직 없는 화면을 어떻게 다루는가** ────────────────────────────────────

이번 세션에 실제로 만드는 화면은 홈·로그인·회원가입 셋뿐이다. 나머지 27개는
URL 조차 등록돼 있지 않다. 템플릿에서 `{% url 'contests:list' %}` 를 쓰면
`NoReverseMatch` 로 **500 에러가 난다.**

세 가지 선택지가 있었다.

    ① 만든 화면만 메뉴에 넣는다        → 전체 구조가 안 보인다
    ② 빈 화면(준비 중 페이지)을 27개 만든다 → 클릭했더니 빈 화면. 시간 낭비
    ③ **메뉴에는 다 띄우되 '준비 중' 으로 표시하고 링크를 걸지 않는다**  ← 채택

③ 이 규약 원칙 2("화면에 보이지 않는 사실은 없는 것과 같다")에 맞다. 무엇이
있고 무엇이 아직 없는지가 화면에 그대로 드러난다. URL 이 등록되는 순간
`resolve()` 가 성공해 **코드 수정 없이 자동으로 링크가 살아난다.**
"""

from dataclasses import dataclass, field

from django.urls import NoReverseMatch, reverse


@dataclass(frozen=True)
class NavItem:
    """메뉴 항목 하나.

    Args:
        label: 화면에 찍히는 이름.
        url_name: `urls.py` 의 `name=`. 네임스페이스를 포함한다(`account:login`).
        login_required: 비로그인 사용자에게는 감춘다.
        badge: 항목 옆에 붙는 짧은 표시 (`★` 등).
    """

    label: str
    url_name: str
    login_required: bool = False
    badge: str = ""

    def resolve(self) -> str | None:
        """URL 을 만든다. **아직 등록되지 않은 화면이면 `None`.**

        `reverse()` 는 이름으로 실제 경로를 되찾는 함수다.

        Django 관점 — FastAPI 에서는 `app.url_path_for("login")` 이 같은 일을 했다.
        Django 쪽이 훨씬 자주 쓰인다. 템플릿의 `{% url %}` 도 내부적으로 이 함수다.
        """
        try:
            return reverse(self.url_name)
        except NoReverseMatch:
            return None


@dataclass(frozen=True)
class NavGroup:
    """1차 메뉴 하나와 그 아래 2차 항목들."""

    label: str
    items: list[NavItem] = field(default_factory=list)
    login_required: bool = False


# ── 메뉴 정의 (U-01 2.1 표 그대로) ──────────────────────────────
#
# ★ = v1.0 에서 메뉴에 없던 화면 (결함 D-2 해소)
MENU: list[NavGroup] = [
    NavGroup(
        label="대회",
        items=[
            NavItem("대회 목록", "contests:list"),
            NavItem("내 대회", "account:contests", login_required=True),
        ],
    ),
    NavGroup(
        label="연습",
        login_required=True,
        items=[
            NavItem("주식", "practice:stock"),
            NavItem("코인", "practice:crypto"),
            NavItem("대체자산", "practice:alternatives"),
            NavItem("Pine 전략", "practice:pine", badge="★"),
        ],
    ),
    NavGroup(
        label="자산",
        login_required=True,
        items=[
            NavItem("전체 자산", "portfolio:index"),
            NavItem("거래 이력", "orders:index"),
        ],
    ),
    NavGroup(
        label="학습",
        items=[
            NavItem("학습 홈", "learn:index"),
            NavItem("투자분석 14레슨", "learn:analysis"),
            NavItem("가이드", "learn:guides"),
        ],
    ),
    NavGroup(
        label="도구",
        items=[
            NavItem("물타기 계산기", "tools:avg_down"),
            NavItem("웹 데이터 수집기", "tools:web_scraper"),
            NavItem("API 문서", "api-docs"),
        ],
    ),
]

# 우측(계정) 메뉴 — GNB 본체와 분리한다. 성격이 다르고 표시 위치도 다르다.
ACCOUNT_MENU: list[NavItem] = [
    NavItem("프로필", "account:profile", login_required=True),
    NavItem("API 키", "account:api_keys", login_required=True),
    NavItem("내 대회", "account:contests", login_required=True),
]


def build_menu(*, is_authenticated: bool) -> list[dict]:
    """템플릿이 그대로 순회할 수 있는 모양으로 메뉴를 만든다.

    Returns:
        `[{"label", "ready"(bool), "items": [{"label", "url", "ready", "badge"}]}, …]`

        `url` 이 `None` 이면 아직 만들지 않은 화면이라 템플릿이 '준비 중' 으로 그린다.

    ★ **로그인 필요한 그룹을 통째로 감추지 않는다.** 비로그인 사용자에게 "연습"
      메뉴가 아예 안 보이면 이 서비스가 무엇을 하는 곳인지 알 수 없다.
      U-01 2.3 이 정한 대로 **보여주되 잠겼음을 표시**하고 로그인으로 유도한다.
    """
    groups = []
    for group in MENU:
        # ★ 그룹에 걸린 로그인 조건은 **그 아래 항목 전부에 내려간다.**
        #   "연습" 그룹이 로그인 전용이면 그 안의 주식·코인·Pine 도 전부 잠긴 것이다.
        #   항목마다 `login_required=True` 를 반복해 적으면 하나 빠뜨렸을 때
        #   **잠겨야 할 링크가 열린 채로** 렌더된다.
        group_locked = group.login_required and not is_authenticated
        items = []
        for item in group.items:
            url = item.resolve()
            items.append(
                {
                    "label": item.label,
                    "url": url,
                    # 링크를 실제로 누를 수 있는가 = 화면이 있고 + 권한이 되는가
                    "ready": url is not None,
                    "locked": group_locked or (item.login_required and not is_authenticated),
                    "badge": item.badge,
                }
            )
        groups.append(
            {
                "label": group.label,
                "items": items,
                "locked": group_locked,
                # 그룹 안에 갈 수 있는 곳이 하나라도 있는가
                "ready": any(entry["ready"] for entry in items),
            }
        )
    return groups


def build_account_menu(*, is_authenticated: bool) -> list[dict]:
    """우측 계정 메뉴. 비로그인이면 빈 목록을 준다 (표시할 것이 없다)."""
    if not is_authenticated:
        return []
    return [
        {"label": item.label, "url": item.resolve(), "ready": item.resolve() is not None}
        for item in ACCOUNT_MENU
    ]
