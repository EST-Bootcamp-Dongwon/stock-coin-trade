"""템플릿 필터 — 폼 렌더링과 숫자 표시 (U-01 4장).

★★ **커스텀 템플릿 필터가 무엇인가** ────────────────────────────────────────

Django 템플릿은 일부러 파이썬을 못 쓰게 막아 놨다. `{{ a if b else c }}` 도,
함수 호출에 인자를 넘기는 것도 안 된다. 로직이 템플릿으로 새어 들어오는 것을
막기 위한 설계다. 그래서 표시에 필요한 작은 변환은 **필터로 등록**해서 쓴다.

    {% load form_extras %}
    {{ form.email|with_error }}
    {{ card.profit_pct|signed_pct }}

Django 관점 ─────────────────────────────────────────────────────────────────

React 였다면 `<Input error={!!errors.email} />` 처럼 컴포넌트에 prop 을 넘겼고,
포맷팅도 JSX 안에서 그냥 함수를 불렀다. Django 는 그 자리를 필터가 대신한다.

★ **파일이 `core/templatetags/` 에 있다.** 앱이 `INSTALLED_APPS` 에 있으면
  **어느 앱의 템플릿에서든** `{% load form_extras %}` 로 쓸 수 있다.
  규약 1.1 의 의존 방향(core 는 아무것도 참조하지 않는다)을 지키면서
  모두가 쓰는 자리다.
"""

from decimal import Decimal, InvalidOperation

from django import template
from django.utils.safestring import mark_safe

register = template.Library()


@register.filter
def with_error(bound_field):
    """검증에 실패한 입력칸에 에러 테두리 클래스를 덧붙여 렌더한다.

    ★ **색만으로 알리지 않는다.** 빨간 글씨만 쓰면 색각 이상이 있는 사용자가
      어느 칸이 문제인지 알 수 없다. 테두리 굵기·링까지 함께 바꾼다
      (`.field-input-error` — static/css/app.src.css 참조).

    폼 정의(`widgets = {...}`) 시점에는 에러가 있는지 알 수 없기 때문에
    **렌더 시점**에 클래스를 덧붙여야 한다. `as_widget(attrs=…)` 가 그 통로다.
    """
    if not bound_field.errors:
        return bound_field
    existing = bound_field.field.widget.attrs.get("class", "")
    return bound_field.as_widget(attrs={"class": f"{existing} field-input-error".strip()})


@register.filter
def signed_pct(value, places: int = 2):
    """등락률 — 부호를 항상 붙인다. `+8.20%` · `-3.15%` · `0.00%`.

    ★ 양수에 `+` 를 붙이는 것이 핵심이다. 없으면 `8.20` 이 상승인지 절대값인지
      모른다. 0 에는 붙이지 않는다 (`+0.00%` 는 이상하다).
    """
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return "—"
    quant = Decimal(1).scaleb(-int(places))
    number = number.quantize(quant)
    sign = "+" if number > 0 else ""
    return f"{sign}{number:,f}%"


@register.filter
def pnl_class(value):
    """손익 부호 → 색 클래스. **상승 빨강 / 하락 파랑** (국내 관행, U-01 4장).

    ★ 미국 관행과 반대다. Tailwind 의 `text-red-600` 을 템플릿에 직접 쓰지 않고
      의미 이름(`text-up`)을 거치는 이유이기도 하다 — 색을 바꿔도 이름은 안 바뀐다.
    """
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return "text-flat"
    if number > 0:
        return "text-up"
    if number < 0:
        return "text-down"
    return "text-flat"


@register.filter
def order_side_class(side):
    """매수/매도 → 색 클래스. **매수 빨강 / 매도 파랑** (국내 관행, `pnl_class` 와 같은 축).

    ★ 값 문자열(`"BUY"`)을 템플릿에 흩뿌리지 않기 위한 필터다. `core.constants`
      의 `OrderSide` 가 바뀌면 여기 한 곳만 고치면 된다.
    """
    return "text-up" if str(side).upper() == "BUY" else "text-down"


@register.filter
def krw_short(value):
    """금액을 사람이 읽는 단위로 줄인다 — `123,456,789` → `1억 2,346만`.

    ★ 카드·요약처럼 **자리 폭이 좁은 곳**에만 쓴다. 표의 금액 열이나 주문 확인
      화면에서는 원 단위 그대로(`intcomma`) 보여준다 — 정확한 숫자가 필요한
      자리에서 반올림하면 사용자가 계산을 못 맞춘다.
    """
    try:
        number = int(value)
    except (TypeError, ValueError):
        return "—"
    sign = "-" if number < 0 else ""
    number = abs(number)
    if number >= 100_000_000:
        eok, rest = divmod(number, 100_000_000)
        man = round(rest / 10_000)
        return f"{sign}{eok:,}억" + (f" {man:,}만" if man else "")
    if number >= 10_000:
        return f"{sign}{round(number / 10_000):,}만"
    return f"{sign}{number:,}"


@register.filter
def d_day(days):
    """남은 일수 → `D-12` · `D-DAY` · `D+3`.

    `days` 는 `종료일 - 오늘` 의 일수다. 음수면 이미 지났다는 뜻이다.
    """
    try:
        number = int(days)
    except (TypeError, ValueError):
        return ""
    if number > 0:
        return f"D-{number}"
    if number == 0:
        return "D-DAY"
    return f"D+{abs(number)}"


@register.simple_tag
def progress_bar(percent: int, *, css: str = "bg-brand") -> str:
    """진행률 막대. 학습 진행률·편입비처럼 0~100 을 그리는 곳에 쓴다.

    ★ `simple_tag` 은 필터와 달리 **인자를 여러 개** 받을 수 있다.
      `{% progress_bar learning.percent %}` 처럼 쓴다.

    ★ `mark_safe` 를 쓰므로 **바깥에서 온 문자열을 여기 넣지 않는다.**
      `percent` 는 정수로 강제하고 `css` 는 우리가 정한 값만 넘긴다.
    """
    try:
        value = max(0, min(100, int(percent)))
    except (TypeError, ValueError):
        value = 0
    return mark_safe(
        '<div class="h-2 w-full overflow-hidden rounded-full bg-slate-200">'
        f'<div class="h-full rounded-full {css}" style="width:{value}%"></div>'
        "</div>"
    )
