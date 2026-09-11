"""web 앱 설정.

★★ **왜 앱을 하나 더 만드는가** ─────────────────────────────────────────────

규약 1.1 은 의존 방향을 이렇게 못 박았다.

    core     ← 추상 모델·상수만. **다른 앱을 참조하지 않는다**
    market   ← 다른 앱을 참조하지 않는다
    accounts ⇄ contests · trading · learning · insight

그런데 홈 대시보드(F-21)와 GNB(U-01)는 **대회 + 계좌 + 시세 + 학습을 한 화면에
모은다.** 이 코드를 어디에 두어도 위 방향이 깨진다.

    core/views.py 에 두면    → core → contests 의존이 생겨 규약 위반
    contests/views.py 에 두면 → 대회 앱이 학습 진행률을 알게 된다
    각 앱에 흩으면          → 홈 화면 하나가 5개 파일에 흩어진다

**그래서 모든 도메인 앱 위에 얹히는 층을 하나 만든다.** `web` 은 아무도 참조하지
않고, 대신 누구든 참조할 수 있다. `config/internal_urls.py` 가 "앱을 서로 엮는
일은 조립 지점의 몫" 이라며 잡 레지스트리를 config 에 둔 것과 같은 판단이다.
다른 점은 그쪽은 파일 하나로 끝났고, 화면은 앞으로 계속 늘어난다는 것이다.

    web/navigation.py         GNB 메뉴 정의 (한 곳)
    web/context_processors.py 전 화면 공통 컨텍스트
    web/services.py           홈 화면 데이터 조립
    web/views.py              홈 · HTMX 프래그먼트
    web/urls.py               `/` 와 `/fragments/*`

**모델이 없는 앱이다.** `migrations/` 도 없다 — Django 는 모델 없는 앱을 아무
문제 없이 받아들인다.

Django 관점 ─────────────────────────────────────────────────────────────────

Next.js 였다면 `app/page.tsx` 가 각 도메인의 서버 함수를 import 해서 조립했고,
"앱" 이라는 단위 자체가 없었다. Django 의 앱은 **재사용·의존 경계**를 나누는
단위라, 경계를 넘는 코드에는 그 코드를 위한 앱을 따로 만드는 것이 정석이다.
"""

from django.apps import AppConfig


class WebConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "web"
    verbose_name = "사용자 화면"
