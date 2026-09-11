"""Core 테스트 — 내부 잡 엔드포인트의 **문지기**를 검증한다 (F-20 3.1 · 5장).

★ 왜 하필 여기에 테스트를 쓰는가 — 이 프로젝트에서 **틀렸을 때 조용히 위험해지는**
  코드가 여기이기 때문이다. 적재 로직이 틀리면 숫자가 이상해져 눈에 띈다. 반면
  토큰 검사가 틀리면 **아무 증상 없이** 잡 엔드포인트가 공개된다. 증상이 없는 결함은
  사람이 못 잡으므로 테스트가 잡아야 한다.

Django 관점 — FastAPI 는 `TestClient(app)` 로 앱을 통째로 감싸 부른다.
Django 의 `self.client` 도 같은 일을 하는데, **미들웨어 체인을 실제로 통과**시킨다.
그래서 미들웨어 자체를 시험할 수 있다. 반면 `RequestFactory` 는 미들웨어를 건너뛰고
뷰만 직접 부르므로, 뷰 로직만 볼 때 쓴다. 아래에서 둘을 나눠 쓴다.

    python manage.py test core
"""

import json

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import Client, RequestFactory, TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from core.constants import SyncStatus, TriggeredBy
from core.job_health import STUCK_AFTER, collect_job_alerts
from core.jobs import ExternalDataError, SyncResult
from core.jobs_http import JobSpec, as_bool, run_job_http
from core.models import DataSyncLog

TEST_TOKEN = "test-token-" + "x" * 40


@override_settings(INTERNAL_JOB_TOKEN=TEST_TOKEN)
class InternalEndpointGateTests(TestCase):
    """`InternalEndpointMiddleware` — 못 들어오는 것이 정상이다."""

    def setUp(self):
        # 미들웨어는 핸들러를 만들 때 설정을 한 번 읽는다. override_settings 가
        # 적용된 뒤에 클라이언트를 만들어야 새 토큰이 반영된다.
        self.client = Client()

    def test_토큰이_없으면_404(self):
        response = self.client.get("/internal/jobs/")
        self.assertEqual(response.status_code, 404)

    def test_토큰이_틀리면_404(self):
        response = self.client.get("/internal/jobs/", headers={"x-internal-token": "wrong"})
        self.assertEqual(response.status_code, 404)

    def test_거부_응답은_없는_경로의_404_와_구별되지_않는다(self):
        """403 이 아니라 404 이고, **본문까지 같아야** 한다 (F-20 3.1).

        ★ 예전에는 "본문이 비어 있다"를 검사했는데, 그게 오히려 구멍이었다.
          Django 기본 404 는 본문이 179 B 인데 우리만 0 B 를 주면, 상태코드로
          숨긴 것을 **길이가 알려준다.** 공격자는 길이 0 인 404 만 골라
          `/internal/` 아래가 실재함을 확인할 수 있다.
          그래서 검사 대상을 "비어 있다"가 아니라 **"없는 경로와 같다"** 로 바꿨다.
        """
        denied = self.client.get("/internal/jobs/", headers={"x-internal-token": "wrong"})
        absent = self.client.get("/이-경로는-없다/")

        self.assertEqual(denied.status_code, 404)
        self.assertEqual(absent.status_code, 404)
        self.assertEqual(denied.content, absent.content)

        # ★ 본문만 맞추면 **헤더로 들킨다.** 조기 반환이라 아래쪽 미들웨어
        #   (`XFrameOptionsMiddleware`)를 거치지 않기 때문이다.
        for header in ("X-Frame-Options", "Content-Type"):
            self.assertEqual(
                denied.headers.get(header), absent.headers.get(header), f"{header} 가 다르다"
            )

    def test_비ASCII_토큰도_500_이_아니라_404(self):
        """★ 운영에서 실제로 500 이 나왔다 (2026-08-16).

        `hmac.compare_digest` 는 str 을 받으면 양쪽이 ASCII 여야 하고, 아니면
        `TypeError` 를 던진다. 헤더는 바깥에서 오는 값이라 한글이 들어올 수 있고,
        그대로 두면 예외가 미들웨어를 뚫고 나가 500 이 된다.

            X-Internal-Token: 한글토큰값  → 500   ← 경로가 실재한다는 신호
            X-Internal-Token: plainwrong  → 404

        틀린 토큰 하나로 404 와 500 이 갈리면 **404 로 숨긴 의미가 없다.**
        """
        for bogus in ("한글토큰값", "café-token", "🔑", "토큰\x00값"):
            with self.subTest(token=bogus):
                response = self.client.get(
                    "/internal/jobs/", headers={"x-internal-token": bogus}
                )
                self.assertEqual(response.status_code, 404)

    def test_판정이_터져도_통과시키지_않는다(self):
        """토큰 판정 중 예외가 나면 **열리는 게 아니라 닫힌다** (fail closed)."""
        from unittest.mock import patch

        from core.middleware import InternalEndpointMiddleware

        with patch.object(
            InternalEndpointMiddleware, "_rejection_reason", side_effect=RuntimeError("펑")
        ):
            with self.assertLogs("core.middleware", level="ERROR"):
                response = self.client.get(
                    "/internal/jobs/", headers={"x-internal-token": TEST_TOKEN}
                )
        self.assertEqual(response.status_code, 404)

    def test_토큰이_맞으면_통과(self):
        response = self.client.get("/internal/jobs/", headers={"x-internal-token": TEST_TOKEN})
        self.assertEqual(response.status_code, 200)
        slugs = [job["slug"] for job in response.json()["jobs"]]
        self.assertIn("sync-stock-master", slugs)
        self.assertIn("sync-upbit-markets", slugs)

    def test_헤더_이름은_대소문자를_가리지_않는다(self):
        """pg_net 이 헤더를 어떤 표기로 보내든 통과해야 한다."""
        response = self.client.get("/internal/jobs/", headers={"X-INTERNAL-TOKEN": TEST_TOKEN})
        self.assertEqual(response.status_code, 200)

    def test_끝_슬래시가_없어도_같은_뷰(self):
        """APPEND_SLASH 리다이렉트에 걸리면 POST 본문·헤더가 사라진다."""
        response = self.client.get("/internal/jobs", headers={"x-internal-token": TEST_TOKEN})
        self.assertEqual(response.status_code, 200)

    def test_모르는_잡은_목록을_알려준다(self):
        """토큰을 통과한 요청에는 오타를 짚어 준다 (규약 8.5)."""
        response = self.client.post(
            "/internal/jobs/sync-stock-mastr", headers={"x-internal-token": TEST_TOKEN}
        )
        self.assertEqual(response.status_code, 404)
        self.assertIn("sync-stock-master", response.json()["available"])


@override_settings(INTERNAL_JOB_TOKEN="")
class MissingTokenSettingTests(TestCase):
    """토큰 설정이 비어 있으면 **열리는 게 아니라 닫힌다** (fail closed)."""

    def test_서버에_토큰이_없으면_전부_404(self):
        client = Client()
        response = client.get("/internal/jobs/", headers={"x-internal-token": "anything"})
        self.assertEqual(response.status_code, 404)


class JobHttpViewTests(TestCase):
    """`run_job_http` — 미들웨어를 통과한 뒤의 뷰 로직.

    가짜 서비스 함수를 쓴다. 진짜 잡을 부르면 KRX·업비트에 네트워크로 나가므로
    **테스트가 남의 서버 상태에 좌우된다.** 여기서 볼 것은 껍데기의 동작이다.
    """

    def setUp(self):
        self.factory = RequestFactory()
        self.calls: list[dict] = []

    def _spec(self, service=None, **kwargs) -> JobSpec:
        return JobSpec(
            job_name="fake_job",
            service=service or self._service,
            **kwargs,
        )

    def _service(self, *, triggered_by, dry_run, on_progress, **options):
        """서비스 규약을 흉내 낸다 — 진행 상황은 콜백으로, 결과는 SyncResult 로."""
        self.calls.append({"triggered_by": triggered_by, "dry_run": dry_run, **options})
        on_progress("업종이 매겨진 종목 2,729종")
        result = SyncResult(created=2, updated=3)
        result.note("배치가 건드리지 않는 필드: is_featured")
        return result

    def _post(self, spec, body=None):
        request = self.factory.post(
            "/internal/jobs/fake",
            data=json.dumps(body) if body is not None else "",
            content_type="application/json",
        )
        return run_job_http(request, spec)

    # ── 정상 경로 ────────────────────────────────────────────────
    def test_성공하면_숫자와_품질정보를_함께_돌려준다(self):
        """★ 세션 7 리뷰 지적 — pg_cron 경로에서 notes·progress 가 사라지면 안 된다."""
        response = self._post(self._spec())
        self.assertEqual(response.status_code, 200)

        payload = json.loads(response.content)
        self.assertEqual(payload["status"], "SUCCESS")
        self.assertEqual((payload["created"], payload["updated"], payload["rows"]), (2, 3, 5))
        self.assertIn("배치가 건드리지 않는 필드: is_featured", payload["notes"])
        self.assertIn("업종이 매겨진 종목 2,729종", payload["progress"])

    def test_기본_실행주체는_CRON(self):
        self._post(self._spec())
        self.assertEqual(self.calls[0]["triggered_by"], TriggeredBy.CRON)

    def test_CLI_는_HTTP_로_사칭할_수_없다(self):
        response = self._post(self._spec(), {"triggered_by": TriggeredBy.CLI})
        self.assertEqual(response.status_code, 400)

    def test_잡별_인자가_서비스로_전달된다(self):
        response = self._post(self._spec(params={"with_sectors": as_bool}), {"with_sectors": True})
        self.assertEqual(response.status_code, 200)
        self.assertIs(self.calls[0]["with_sectors"], True)

    # ── 잘못된 호출 ──────────────────────────────────────────────
    def test_GET_은_405(self):
        """잡은 DB 를 바꾼다. 링크 한 번에 실행되면 안 된다."""
        request = self.factory.get("/internal/jobs/fake")
        self.assertEqual(run_job_http(request, self._spec()).status_code, 405)

    def test_깨진_JSON_은_400(self):
        request = self.factory.post(
            "/internal/jobs/fake", data="{not json", content_type="application/json"
        )
        self.assertEqual(run_job_http(request, self._spec()).status_code, 400)

    def test_모르는_인자는_400_이고_쓸_수_있는_것을_알려준다(self):
        """오타 난 인자를 조용히 무시하면 '성공했는데 안 먹은' 상태가 된다."""
        response = self._post(self._spec(params={"with_sectors": as_bool}), {"with_sector": True})
        self.assertEqual(response.status_code, 400)
        self.assertIn("with_sectors", json.loads(response.content)["error"])

    def test_형식이_틀린_값은_400(self):
        response = self._post(self._spec(params={"with_sectors": as_bool}), {"with_sectors": "언젠가"})
        self.assertEqual(response.status_code, 400)

    # ── 실패 경로 ────────────────────────────────────────────────
    def test_외부_장애는_503_이고_힌트를_준다(self):
        def broken(**_kwargs):
            raise ExternalDataError("KRX 로그인 실패", hint="KRX_ID · KRX_PW 를 확인하십시오.")

        response = self._post(self._spec(service=broken))
        self.assertEqual(response.status_code, 503)
        payload = json.loads(response.content)
        self.assertEqual(payload["status"], "FAILED")
        self.assertEqual(payload["hint"], "KRX_ID · KRX_PW 를 확인하십시오.")

    def test_코드_버그는_500_이고_스택트레이스를_노출하지_않는다(self):
        def buggy(**_kwargs):
            raise KeyError("close_price")

        with self.assertLogs("core.jobs_http", level="ERROR"):
            response = self._post(self._spec(service=buggy))
        self.assertEqual(response.status_code, 500)
        body = response.content.decode()
        self.assertIn("KeyError", body)
        self.assertNotIn("Traceback", body)       # 파일 경로가 새어나가면 안 된다

    # ── 연속 실패 자동 비활성화 (F-20 5장) ────────────────────────
    def _fail_times(self, count: int, job_name: str = "fake_job"):
        now = timezone.now()
        for i in range(count):
            DataSyncLog.objects.create(
                job_name=job_name, started_at=now, finished_at=now,
                status=SyncStatus.FAILED, error="ExternalDataError: 외부 장애",
            )

    def test_3회_연속_실패하면_409_로_건너뛴다(self):
        self._fail_times(3)
        with self.assertLogs("core.jobs_http", level="WARNING"):
            response = self._post(self._spec())
        self.assertEqual(response.status_code, 409)
        self.assertEqual(json.loads(response.content)["status"], "SKIPPED")
        self.assertEqual(self.calls, [])          # 서비스는 아예 불리지 않았다

    def test_2회_실패까지는_그대로_실행한다(self):
        self._fail_times(2)
        self.assertEqual(self._post(self._spec()).status_code, 200)

    def test_force_는_자동_비활성화를_넘어선다(self):
        """운영자가 복구를 시도하는 경로. Admin 재실행 버튼이 이걸 쓴다."""
        self._fail_times(3)
        self.assertEqual(self._post(self._spec(), {"force": True}).status_code, 200)

    def test_dry_run_은_비활성화_상태에서도_돈다(self):
        """무엇이 바뀔지 보는 것은 DB 를 건드리지 않는다 — 막을 이유가 없다."""
        self._fail_times(3)
        response = self._post(self._spec(), {"dry_run": True})
        self.assertEqual(response.status_code, 200)
        self.assertIs(self.calls[0]["dry_run"], True)


class JobHealthTests(TestCase):
    """Admin 경고 배너의 재료 (F-20 5장)."""

    def _log(self, job_name, status, *, minutes_ago=1, error=""):
        started = timezone.now() - timezone.timedelta(minutes=minutes_ago)
        return DataSyncLog.objects.create(
            job_name=job_name, started_at=started,
            finished_at=None if status == SyncStatus.RUNNING else started,
            status=status, error=error,
        )

    def test_문제가_없으면_배너를_띄우지_않는다(self):
        self._log("sync_upbit_markets", SyncStatus.SUCCESS)
        self.assertFalse(collect_job_alerts()["has_problem"])

    def test_마지막_실행만_본다(self):
        """어제 실패했다가 오늘 성공한 잡은 이미 해결된 것이다."""
        self._log("sync_stock_master", SyncStatus.FAILED, minutes_ago=120)
        self._log("sync_stock_master", SyncStatus.SUCCESS, minutes_ago=5)
        self.assertFalse(collect_job_alerts()["has_problem"])

    def test_마지막_실행이_실패면_사유_한_줄을_뽑는다(self):
        self._log(
            "sync_stock_master", SyncStatus.FAILED,
            error="Traceback (most recent call last):\n  ...\nExternalDataError: KRX 로그인 실패",
        )
        alert = collect_job_alerts()["alerts"][0]
        self.assertEqual(alert.job_name, "sync_stock_master")
        self.assertEqual(alert.detail, "ExternalDataError: KRX 로그인 실패")
        self.assertFalse(alert.disabled)

    def test_3회_연속_실패는_비활성화로_표시된다(self):
        for _ in range(3):
            self._log("sync_stock_master", SyncStatus.FAILED)
        self.assertTrue(collect_job_alerts()["alerts"][0].disabled)

    def test_오래_RUNNING_인_잡을_정체로_잡는다(self):
        """프로세스가 통째로 죽으면 finally 도 못 돌아 RUNNING 이 영원히 남는다."""
        minutes = int(STUCK_AFTER.total_seconds() / 60) + 10
        self._log("settle_daily", SyncStatus.RUNNING, minutes_ago=minutes)
        alert = collect_job_alerts()["alerts"][0]
        self.assertEqual(alert.kind, "STUCK")

    def test_방금_시작한_잡은_정체가_아니다(self):
        self._log("settle_daily", SyncStatus.RUNNING, minutes_ago=1)
        self.assertFalse(collect_job_alerts()["has_problem"])

    def test_pg_cron_이_없어도_배너가_깨지지_않는다(self):
        """로컬 개발 DB 에는 pg_cron 이 없다. 그건 에러가 아니라 정상이다."""
        cron = collect_job_alerts()["cron"]
        self.assertFalse(cron["available"])
        self.assertIn("정상", cron["note"])


class AdminDashboardBannerTests(TestCase):
    """대시보드 배너가 **실제로 그려지는지** 본다 (F-20 5장).

    `collect_job_alerts()` 만 시험하면 템플릿의 오타·블록 이름 실수를 못 잡는다.
    화면에 안 뜨는 경고는 없는 것과 같으므로 여기까지 확인한다.
    """

    @classmethod
    def setUpTestData(cls):
        cls.admin = get_user_model().objects.create_superuser(
            username="ops-test", email="ops@example.com", password="test-pass-1234!"
        )

    def setUp(self):
        self.client.force_login(self.admin)

    def test_문제가_없으면_이상없음을_적는다(self):
        response = self.client.get("/admin/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "배치 상태")
        self.assertContains(response, "마지막 실행이 실패한 잡이 없다")

    def test_실패한_잡이_배너에_뜬다(self):
        now = timezone.now()
        for _ in range(3):
            DataSyncLog.objects.create(
                job_name="sync_stock_master", started_at=now, finished_at=now,
                status=SyncStatus.FAILED, error="ExternalDataError: KRX 로그인 실패",
            )

        response = self.client.get("/admin/")
        self.assertContains(response, "sync_stock_master")
        self.assertContains(response, "KRX 로그인 실패")
        self.assertContains(response, "스케줄러 건너뜀")     # 3회 연속 배지

    def test_pg_cron_이_없다는_사실을_화면에_적는다(self):
        """비어 있는 칸을 보고 '스케줄러가 죽었나' 로 오해하지 않게 한다."""
        response = self.client.get("/admin/")
        self.assertContains(response, "pg_cron 이 없습니다")

    def test_다른_Admin_화면은_배너_쿼리를_돌리지_않는다(self):
        """`each_context` 는 **모든** Admin 화면에서 불린다.

        지연 객체로 감싸지 않으면 종목 목록을 넘길 때마다, 회원 상세를 열 때마다
        배치 이력 조회가 따라붙는다. 배너 쿼리의 표식인 `DISTINCT ON` 이
        대시보드가 아닌 화면에서는 나오지 않아야 한다.
        """
        with CaptureQueriesContext(connection) as captured:
            response = self.client.get("/admin/core/datasynclog/")
        self.assertEqual(response.status_code, 200)
        joined = " ".join(query["sql"] for query in captured.captured_queries).upper()
        self.assertNotIn("DISTINCT ON", joined)

    def test_대시보드에서는_배너_쿼리가_실제로_돈다(self):
        """위 테스트의 짝 — '안 돈다'만 보면 배너가 죽어도 통과한다."""
        with CaptureQueriesContext(connection) as captured:
            self.client.get("/admin/")
        joined = " ".join(query["sql"] for query in captured.captured_queries).upper()
        self.assertIn("DISTINCT ON", joined)
