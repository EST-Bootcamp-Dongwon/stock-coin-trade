"""회원가입 · 로그인 폼 (F-01 3장).

★★ **Django 의 Form 이 무엇을 대신해 주는가** ───────────────────────────────

FastAPI 에서는 Pydantic 모델로 요청 본문을 검증하고, 에러가 나면 422 를 돌려주고,
화면에 그리는 일은 React 가 했다. Django 의 `Form` 은 **검증 + HTML 렌더 + 에러
표시**를 한 객체가 다 한다.

    form.is_valid()      검증 실행
    form.cleaned_data    검증을 통과한 값 (형변환까지 끝난 상태)
    form.errors          `{"email": ["이미 사용 중인 이메일입니다."]}`  ← F-01 3.1 이 원한 모양
    {{ form.email }}     `<input name="email" …>` 을 그려 준다

특히 `form.errors` 의 구조가 **v1.0 이 손으로 만들던 `{"field": …, "error": …}` 와
사실상 같다.** 명세가 "이미 이 모양이다" 라고 적은 게 이 뜻이다.

`ModelForm` 은 여기서 한 걸음 더 간다 — 모델의 필드 정의(길이·unique·choices)를
읽어 **검증 규칙을 자동으로 만든다.** `email` 의 UNIQUE 중복 검사를 우리가 쓰지
않아도 되는 이유다.
"""

from django import forms
from django.contrib.auth.forms import AuthenticationForm
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError

from accounts.models import Member
from accounts.services import register_member

# 모든 입력칸에 같은 CSS 를 먹인다. 템플릿에서 필드마다 class 를 적으면
# 스타일이 바뀔 때 전 템플릿을 뒤져야 한다.
_INPUT_ATTRS = {"class": "field-input"}


class RegisterForm(forms.ModelForm):
    """회원가입.

    ★ **Django 기본 `UserCreationForm` 을 상속하지 않는다.** 그 폼의 `save()` 는
      `Member` 한 행만 만들고 끝난다. 우리에게 가입은 "회원 1행 + 연습 계좌 3행"
      이고, 그 규칙은 `accounts.services.register_member()` 에 있다.
      기본 폼을 쓰면 **계좌 없는 회원**이 만들어지는 경로가 하나 생긴다.
    """

    password1 = forms.CharField(
        label="비밀번호",
        widget=forms.PasswordInput(attrs={**_INPUT_ATTRS, "autocomplete": "new-password"}),
        help_text="8자 이상. 숫자로만 이루어지거나 너무 흔한 비밀번호는 거부된다.",
    )
    password2 = forms.CharField(
        label="비밀번호 확인",
        widget=forms.PasswordInput(attrs={**_INPUT_ATTRS, "autocomplete": "new-password"}),
    )

    class Meta:
        model = Member
        fields = ["username", "email", "display_name"]
        labels = {"username": "아이디", "email": "이메일", "display_name": "표시명"}
        help_texts = {
            # AbstractUser.username 의 기본 도움말은 영어라 덮어쓴다.
            "username": "로그인에 쓴다. 영문·숫자·@/./+/-/_ 만 가능하다.",
            "display_name": "비워 두면 아이디가 대신 쓰인다. 대회에서는 별칭을 따로 정한다.",
        }
        widgets = {
            "username": forms.TextInput(attrs={**_INPUT_ATTRS, "autocomplete": "username"}),
            "email": forms.EmailInput(attrs={**_INPUT_ATTRS, "autocomplete": "email"}),
            "display_name": forms.TextInput(attrs=_INPUT_ATTRS),
        }
        error_messages = {
            # ModelForm 이 UNIQUE 위반에 붙이는 기본 문구는 "이미 존재합니다" 라
            # 무엇이 문제인지 덜 분명하다.
            "username": {"unique": "이미 사용 중인 아이디입니다."},
            "email": {"unique": "이미 사용 중인 이메일입니다."},
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # 표시명은 선택이다. 모델에서 `blank=True` 라 ModelForm 이 알아서 required=False
        # 로 잡지만, 화면에 "(선택)" 을 붙여 사용자가 알 수 있게 한다.
        self.fields["display_name"].required = False
        self.fields["display_name"].label = "표시명 (선택)"

    def clean_password1(self):
        """Django 의 비밀번호 검증기를 태운다 (`settings.AUTH_PASSWORD_VALIDATORS`).

        ★ **`clean_<필드명>` 이라는 이름이 규칙이다.** Django 가 검증 중에 이
          이름의 메서드를 자동으로 찾아 부른다. 반환값이 `cleaned_data` 에 들어간다.

        Django 관점 — Pydantic 의 `@field_validator("password1")` 과 같은 자리다.
        다른 점은 이름 규칙으로 찾는다는 것(데코레이터가 없다).
        """
        password = self.cleaned_data.get("password1")
        if password:
            # `validate_password` 는 문제가 있으면 ValidationError 를 던진다.
            # 그대로 흘려보내면 Django 가 이 필드의 에러로 담는다.
            validate_password(password)
        return password

    def clean(self):
        """필드 하나로는 판단할 수 없는 규칙 — 두 비밀번호가 같은가.

        ★ `clean_password2` 가 아니라 `clean()` 에 두는 이유: 여기서는
          `cleaned_data` 에 **모든 필드가 이미 채워져** 있다. 필드 단위 `clean_`
          메서드는 정의 순서대로 돌아서 뒤 필드를 아직 못 볼 수 있다.
        """
        cleaned = super().clean()
        password1 = cleaned.get("password1")
        password2 = cleaned.get("password2")
        if password1 and password2 and password1 != password2:
            # 특정 필드에 붙이면 그 입력칸 아래에 뜬다 (F-01 3.1 의 필드 단위 에러).
            self.add_error("password2", ValidationError("비밀번호가 서로 다릅니다."))
        return cleaned

    def save(self, commit: bool = True) -> Member:
        """검증을 통과한 값으로 **서비스 계층을 호출**한다.

        ★ `ModelForm.save()` 를 덮어쓰지만 하는 일은 전혀 다르다 — 기본 구현은
          `self.instance.save()` 로 한 행을 쓰고 끝이고, 우리는 계좌 3개까지 함께
          만드는 트랜잭션을 부른다. 뷰가 폼과 서비스를 각각 부르게 하지 않고
          여기서 잇는 이유는, **가입 경로가 늘어도(초대 가입 등) 폼 하나만 쓰면
          계좌 생성이 따라오게** 하기 위해서다.

        `commit` 인자는 ModelForm 의 규약이라 시그니처만 맞춘다. 우리 서비스는
        트랜잭션 단위가 고정이라 "검증만 하고 저장은 나중에" 를 지원하지 않는다.
        """
        if not commit:
            raise NotImplementedError(
                "가입은 회원과 계좌 3개를 한 트랜잭션으로 만든다 — commit=False 를 지원하지 않는다."
            )
        return register_member(
            username=self.cleaned_data["username"],
            email=self.cleaned_data["email"],
            password=self.cleaned_data["password1"],
            display_name=self.cleaned_data.get("display_name", ""),
        )


class LoginForm(AuthenticationForm):
    """로그인.

    `AuthenticationForm` 이 이미 다 해 준다 — `authenticate()` 호출, 비밀번호 대조,
    `is_active=False` 인 회원 차단까지. 우리는 **문구와 CSS 만** 바꾼다.

    ★ **`is_active=False` 차단이 공짜로 딸려오는 것이 중요하다.** 규약 5.1 이
      "탈퇴는 삭제가 아니라 `is_active=False`" 라고 정했다. 로그인 폼을 직접
      만들었다면 이 검사를 빠뜨리기 쉽고, 빠뜨리면 **탈퇴한 회원이 로그인된다.**
    """

    error_messages = {
        # ★ "아이디가 없습니다" 와 "비밀번호가 틀렸습니다" 를 구분하지 않는다.
        #   구분하면 공격자가 **어떤 아이디가 존재하는지** 알아낼 수 있다(계정 열거).
        #   Django 기본값이 이미 이 방침이고, 문구만 한국어로 바꾼다.
        "invalid_login": "아이디 또는 비밀번호가 올바르지 않습니다.",
        "inactive": "비활성화된 계정입니다. 운영자에게 문의하세요.",
    }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["username"].label = "아이디"
        self.fields["username"].widget.attrs.update(
            {**_INPUT_ATTRS, "autocomplete": "username", "autofocus": True}
        )
        self.fields["password"].label = "비밀번호"
        self.fields["password"].widget.attrs.update(
            {**_INPUT_ATTRS, "autocomplete": "current-password"}
        )
