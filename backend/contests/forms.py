"""대회 참가 신청 폼 (F-02 6장 · [ui/00-화면목록-라우팅] 2.1 #8).

화면 정의는 두 줄뿐이다 — **"별칭 입력 + 규칙 동의"**. API 페이로드가 그것을
확정한다 (A-02 2장): `{"nickname": "동원", "agree_rules": true}`.

★★ **왜 `ModelForm` 이 아닌가** ────────────────────────────────────────────

`Participation` 을 대상으로 `ModelForm` 을 쓰면 `contest` · `member` · `account` ·
`status` 까지 폼이 만질 수 있는 자리에 놓인다. 그중 하나라도 사용자가 보낸 값이
그대로 들어가면 **남의 대회에 남의 이름으로 참가**하는 요청을 만들 수 있다.

신청 화면이 실제로 받는 것은 두 칸뿐이고, 나머지는 전부 서버가 정한다. 그래서
평범한 `forms.Form` 으로 두 칸만 받고, 저장은 `services.join_contest()` 가 한다.

Django 관점 ─────────────────────────────────────────────────────────────────

FastAPI 였다면 `class JoinRequest(BaseModel)` 로 두 필드짜리 요청 모델을 만들고
경로 매개변수로 대회를 받았을 것이다. 여기서 `forms.Form` 이 그 요청 모델이고,
차이는 **HTML 입력칸과 에러 문구까지 같은 객체가 그려 준다**는 점이다.
"""

from django import forms

from contests.models import Participation

# accounts/forms.py 와 같은 클래스를 쓴다. 입력칸 모양이 화면마다 달라지면
# 사용자는 다른 서비스에 온 줄 안다.
_INPUT_ATTRS = {"class": "field-input"}


class JoinForm(forms.Form):
    """참가 신청 — 별칭 + 규칙 동의."""

    nickname = forms.CharField(
        label="별칭",
        required=False,
        # ★ 모델과 **같은 길이**여야 한다. 여기가 더 길면 DB 에서 잘리거나 터진다.
        #   `Participation.nickname` 은 `max_length=20` 이다.
        max_length=Participation._meta.get_field("nickname").max_length,
        widget=forms.TextInput(
            attrs={**_INPUT_ATTRS, "placeholder": "예: 드래곤플라이", "autocomplete": "off"}
        ),
        help_text=(
            "랭킹에 표시되는 이름입니다. 실명은 노출되지 않습니다. "
            "비워 두면 '참가자N' 이 자동으로 부여됩니다."
        ),
    )
    agree_rules = forms.BooleanField(
        label="위 대회 규칙을 읽었고 이에 동의합니다.",
        # ★★ `required=True` 가 곧 검증이다. Django 의 `BooleanField` 는 체크가
        #   안 되면 `False` 가 아니라 **"이 필드는 필수입니다"** 에러를 낸다.
        #   `required=False` 로 두면 동의 없이도 폼이 통과한다.
        required=True,
        error_messages={"required": "대회 규칙에 동의해야 참가할 수 있습니다."},
        widget=forms.CheckboxInput(
            attrs={"class": "h-4 w-4 rounded border-slate-300 text-brand"}
        ),
    )

    def clean_nickname(self) -> str:
        """앞뒤 공백을 떼고, 공백만 있는 별칭을 빈 값으로 만든다.

        ★ `" "` 를 그대로 두면 `UNIQUE(contest, nickname)` 자리를 공백 하나가
          차지해 **다음 사람이 공백 별칭을 쓸 수 없게** 된다. 빈 값이면 서비스가
          `참가자N` 을 자동으로 붙인다.
        """
        return (self.cleaned_data.get("nickname") or "").strip()
