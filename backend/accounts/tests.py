"""계정 · 인증 테스트 (F-01).

    python manage.py test accounts

★ 여기서 보는 것은 **화면과 서비스가 제대로 이어졌는가** 다.
  "회원 1행 + 계좌 3행" 규칙 자체는 `accounts/services.py` 의 몫이고,
  이 파일은 **가입 폼이 그 서비스를 실제로 부르는지**를 확인한다.
  둘을 나눠 보지 않으면, 서비스는 멀쩡한데 뷰가 `Member.objects.create()` 를
  직접 부르는 회귀가 테스트를 통과해 버린다.
"""

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from accounts.models import INITIAL_PRACTICE_CAPITAL, Account
from accounts.services import PRACTICE_MODES, register_member
from core.constants import AccountMode

Member = get_user_model()


class RegisterViewTests(TestCase):
    """회원가입 화면 (F-01 3장)."""

    def setUp(self):
        self.url = reverse("account:register")
        self.payload = {
            "username": "newbie",
            "email": "newbie@example.com",
            "display_name": "새내기",
            "password1": "kospi-2026-test",
            "password2": "kospi-2026-test",
        }

    def test_가입하면_연습계좌_3종이_함께_만들어진다(self):
        """★ F-01 3장의 핵심 — 가입 = 회원 1행 + 연습 계좌 3행."""
        response = self.client.post(self.url, self.payload)

        self.assertRedirects(response, reverse("home"))
        member = Member.objects.get(username="newbie")

        accounts = member.accounts.all()
        self.assertEqual(accounts.count(), len(PRACTICE_MODES))
        self.assertEqual(
            sorted(account.mode for account in accounts), sorted(PRACTICE_MODES)
        )
        # 대회 계좌는 이 시점에 없다 — 참가 승인 때 만들어진다 (F-00 5.3).
        self.assertFalse(accounts.filter(mode=AccountMode.CONTEST).exists())
        for account in accounts:
            self.assertEqual(account.cash, INITIAL_PRACTICE_CAPITAL)
            self.assertIsNone(account.contest_id)

    def test_가입_직후_로그인_상태가_된다(self):
        """v1.0 동작 승계 — 방금 비밀번호를 친 사람에게 또 치라고 하지 않는다."""
        self.client.post(self.url, self.payload)
        self.assertEqual(int(self.client.session["_auth_user_id"]), Member.objects.get(username="newbie").pk)

    def test_비밀번호는_평문으로_저장되지_않는다(self):
        """★ `create_user()` 가 아니라 `create()` 를 쓰는 회귀를 잡는다.

        조용히 성공하는 종류의 사고라 테스트가 아니면 발견이 늦다.
        """
        self.client.post(self.url, self.payload)
        member = Member.objects.get(username="newbie")
        self.assertNotEqual(member.password, self.payload["password1"])
        self.assertTrue(member.check_password(self.payload["password1"]))

    def test_비밀번호_불일치는_해당_칸에_에러가_붙는다(self):
        """F-01 3.1 — 필드 단위 에러."""
        response = self.client.post(
            self.url, {**self.payload, "password2": "different-password"}
        )
        self.assertEqual(response.status_code, 200)          # 다시 폼을 보여준다
        self.assertIn("password2", response.context["form"].errors)
        self.assertFalse(Member.objects.filter(username="newbie").exists())

    def test_이메일_중복은_이메일_칸에_에러가_붙는다(self):
        register_member(
            username="existing", email="newbie@example.com", password="kospi-2026-test"
        )
        response = self.client.post(self.url, self.payload)

        self.assertEqual(response.status_code, 200)
        self.assertIn("email", response.context["form"].errors)
        self.assertFalse(Member.objects.filter(username="newbie").exists())

    def test_약한_비밀번호는_거부된다(self):
        """`AUTH_PASSWORD_VALIDATORS` 가 폼에 실제로 물려 있는지 본다."""
        response = self.client.post(
            self.url, {**self.payload, "password1": "12345678", "password2": "12345678"}
        )
        self.assertIn("password1", response.context["form"].errors)

    def test_검증_실패시_계좌가_남지_않는다(self):
        """가입이 실패하면 아무것도 만들어지지 않아야 한다."""
        self.client.post(self.url, {**self.payload, "email": "not-an-email"})
        self.assertEqual(Account.objects.count(), 0)

    def test_HTMX_요청은_폼_조각만_돌려준다(self):
        """실패 시 페이지 전체가 아니라 폼만 다시 그린다 (규약 2장 프래그먼트)."""
        response = self.client.post(
            self.url,
            {**self.payload, "password2": "different-password"},
            headers={"HX-Request": "true"},
        )
        self.assertTemplateUsed(response, "accounts/_register_form.html")
        self.assertTemplateNotUsed(response, "base.html")

    def test_HTMX_성공은_HX_Redirect_로_이동시킨다(self):
        """★ 302 를 주면 HTMX 가 목적지 HTML 을 폼 자리에 쑤셔 넣는다
        (core/htmx.py 의 `hx_redirect` 주석 참조)."""
        response = self.client.post(self.url, self.payload, headers={"HX-Request": "true"})
        self.assertEqual(response.status_code, 204)
        self.assertEqual(response.headers["HX-Redirect"], reverse("home"))

    def test_이미_로그인했으면_홈으로_보낸다(self):
        register_member(username="already", email="already@example.com", password="kospi-2026-test")
        self.client.login(username="already", password="kospi-2026-test")
        self.assertRedirects(self.client.get(self.url), reverse("home"))


class LoginLogoutTests(TestCase):
    """로그인 · 로그아웃 (F-01 4장)."""

    PASSWORD = "kospi-2026-test"

    def setUp(self):
        self.member = register_member(
            username="trader", email="trader@example.com", password=self.PASSWORD
        )
        self.login_url = reverse("account:login")

    def test_로그인_성공(self):
        response = self.client.post(
            self.login_url, {"username": "trader", "password": self.PASSWORD}
        )
        self.assertRedirects(response, reverse("home"))
        self.assertEqual(int(self.client.session["_auth_user_id"]), self.member.pk)

    def test_틀린_비밀번호는_아이디_존재여부를_알려주지_않는다(self):
        """★ 계정 열거 방지 — "없는 아이디" 와 "틀린 비밀번호" 를 구분하지 않는다."""
        wrong_password = self.client.post(
            self.login_url, {"username": "trader", "password": "wrong-password"}
        )
        no_such_user = self.client.post(
            self.login_url, {"username": "nobody", "password": "wrong-password"}
        )
        self.assertEqual(
            wrong_password.context["form"].non_field_errors(),
            no_such_user.context["form"].non_field_errors(),
        )

    def test_비활성_회원은_로그인할_수_없다(self):
        """규약 5.1 — 탈퇴는 삭제가 아니라 `is_active=False` 다.
        그 처리가 실제로 로그인을 막는지 본다."""
        self.member.is_active = False
        self.member.save(update_fields=["is_active"])

        response = self.client.post(
            self.login_url, {"username": "trader", "password": self.PASSWORD}
        )
        self.assertNotIn("_auth_user_id", self.client.session)
        self.assertTrue(response.context["form"].errors)

    def test_next_로_원래_가려던_곳으로_돌아간다(self):
        response = self.client.post(
            self.login_url,
            {"username": "trader", "password": self.PASSWORD, "next": "/fragments/market/"},
        )
        self.assertRedirects(response, "/fragments/market/")

    def test_외부_주소로는_보내지_않는다(self):
        """★ 오픈 리다이렉트 방어 (accounts/views.py 의 `_safe_next` 주석)."""
        response = self.client.post(
            self.login_url,
            {
                "username": "trader",
                "password": self.PASSWORD,
                "next": "https://evil.example.com/phish",
            },
        )
        self.assertRedirects(response, reverse("home"))

    def test_로그아웃은_GET_으로_되지_않는다(self):
        """★ GET 로그아웃은 링크·이미지만으로 남을 로그아웃시킬 수 있다."""
        self.client.login(username="trader", password=self.PASSWORD)
        response = self.client.get(reverse("account:logout"))
        self.assertEqual(response.status_code, 405)
        self.assertIn("_auth_user_id", self.client.session)

    def test_로그아웃하면_세션이_끊긴다(self):
        self.client.login(username="trader", password=self.PASSWORD)
        response = self.client.post(reverse("account:logout"))
        self.assertRedirects(response, reverse("home"))
        self.assertNotIn("_auth_user_id", self.client.session)
