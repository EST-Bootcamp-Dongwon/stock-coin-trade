"""KIS 연결 점검 — **키를 발급받은 직후 가장 먼저 돌리는 커맨드** (api_발급_가이드 2장).

    python manage.py kis_probe                  # 토큰 + 삼성전자 호가·현재가
    python manage.py kis_probe --symbol 000660  # 다른 종목으로
    python manage.py kis_probe --refresh        # 캐시된 토큰을 버리고 새로 발급
    python manage.py kis_probe --token-only     # 토큰만 (시세 호출 없이)

★★ **왜 별도 커맨드인가** ───────────────────────────────────────────────────

폴링 잡으로 확인하면 실패했을 때 원인이 여러 겹이다 — 키가 틀렸나, 구독 종목이
없나, 장이 닫혔나, 유량인가. 이 커맨드는 **한 번에 하나씩** 확인하고 각 단계에서
무엇이 잘못됐는지 말한다.

    ① .env 를 읽었는가        (앱키 길이·모드·도메인만 보여준다 — **값은 보여주지 않는다**)
    ② 토큰을 받았는가          (받았으면 만료 시각. 캐시 재사용도 표시)
    ③ 시세가 오는가            (현재가 → 호가 순. 장외에는 호가가 비어 있을 수 있다)

★ **자격증명을 화면에 찍지 않는다.** 터미널 기록·스크린샷·화면 공유로 새는 것이
  가장 흔한 유출 경로다. 앞 4글자조차 찍지 않고 **길이만** 보여준다 —
  "키가 들어갔는가"를 확인하는 데는 그것으로 충분하다.
"""

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from core.constants import BrokerProvider
from core.jobs import ExternalDataError
from market import kis
from market.models import ExternalToken


class Command(BaseCommand):
    help = "KIS Developers 연결을 점검한다 (토큰 발급 · 현재가 · 호가)"

    def add_arguments(self, parser):
        parser.add_argument("--symbol", default="005930", help="점검에 쓸 종목코드 (기본 005930 삼성전자)")
        parser.add_argument("--refresh", action="store_true", help="캐시된 토큰을 무시하고 새로 발급받는다")
        parser.add_argument("--token-only", action="store_true", help="토큰까지만 확인하고 시세는 부르지 않는다")

    def handle(self, *args, **options):
        symbol = options["symbol"]

        # ── ① 설정 ──────────────────────────────────────────────
        self.stdout.write(self.style.MIGRATE_HEADING("① 설정 (backend/.env)"))
        try:
            config = kis.load_config()
        except kis.KisNotConfigured as exc:
            # ★ 여기서 끝나는 것이 **정상적인 경우**다 (아직 키를 발급받지 않음).
            #   CommandError 로 죽이지 않고 무엇을 해야 하는지 안내한다.
            self.stdout.write(self.style.WARNING(f"  ⚠ {exc}"))
            if exc.hint:
                self.stdout.write(f"    {exc.hint}")
            return

        self.stdout.write(f"  · 모드      {config.environment} (KIS_MODE)")
        self.stdout.write(f"  · 도메인    {config.host}")
        self.stdout.write(f"  · 앱키      길이 {len(config.app_key)}자 (값은 표시하지 않습니다)")
        self.stdout.write(f"  · 시크릿    길이 {len(config.app_secret)}자")
        self.stdout.write(
            f"  · 유량      초당 {config.rate_limit}건 "
            f"(KIS_RATE_LIMIT 로 조절 · 예산 카운터 이름 {config.budget_name})"
        )

        # ── ② 토큰 ──────────────────────────────────────────────
        self.stdout.write(self.style.MIGRATE_HEADING("② 접근토큰"))
        cached = ExternalToken.objects.filter(
            provider=BrokerProvider.KIS, environment=config.environment
        ).first()
        had_valid_cache = bool(cached and cached.access_token and cached.is_valid)

        try:
            kis.get_access_token(config, force_refresh=options["refresh"])
        except ExternalDataError as exc:
            self.stdout.write(self.style.ERROR(f"  ✖ {exc}"))
            if exc.hint:
                self.stdout.write(f"    {exc.hint}")
            raise CommandError("토큰을 받지 못했습니다 — 위 안내를 확인하십시오.") from exc

        row = ExternalToken.objects.get(
            provider=BrokerProvider.KIS, environment=config.environment
        )
        reused = had_valid_cache and not options["refresh"]
        self.stdout.write(self.style.SUCCESS(
            f"  ✔ 토큰 {'재사용' if reused else '발급'} — 만료 "
            f"{timezone.localtime(row.expires_at):%Y-%m-%d %H:%M:%S} KST "
            f"(남은 {_remaining(row.expires_at)})"
        ))
        if reused:
            self.stdout.write(
                "    ExternalToken 에 있던 값을 그대로 썼습니다. "
                "새로 받으려면 --refresh 를 주십시오 (발급은 1분에 1회 제한입니다)."
            )

        if options["token_only"]:
            return

        # ── ③ 시세 ──────────────────────────────────────────────
        self.stdout.write(self.style.MIGRATE_HEADING(f"③ 시세 — {symbol}"))
        try:
            quote = kis.fetch_quote(symbol, config=config)
        except ExternalDataError as exc:
            self.stdout.write(self.style.ERROR(f"  ✖ 현재가 실패 — {exc}"))
            if exc.hint:
                self.stdout.write(f"    {exc.hint}")
        else:
            self.stdout.write(self.style.SUCCESS(
                f"  ✔ 현재가 {quote.price:,.0f}원 "
                f"(전일 {quote.prev_close:,.0f} · {quote.change:+,.0f} · {quote.change_pct:+.2f}%) "
                f"· 거래량 {quote.volume:,}"
            ))

        try:
            book = kis.fetch_orderbook(symbol, config=config)
        except ExternalDataError as exc:
            # ★ 장외에는 호가가 비어 있는 것이 **정상**이다. 실패로 단정하지 않는다.
            self.stdout.write(self.style.WARNING(f"  ⚠ 호가 없음 — {exc}"))
            self.stdout.write(
                "    장중(09:00~15:30 KST)이 아니면 정상입니다. "
                "대회 체결은 장중에만 이루어집니다 (F-16 3.3)."
            )
        else:
            best = book.levels[0]
            self.stdout.write(self.style.SUCCESS(
                f"  ✔ 호가 10단계 — 매도1 {best['ask_price']:,}({best['ask_qty']:,}) / "
                f"매수1 {best['bid_price']:,}({best['bid_qty']:,}) · "
                f"총잔량 매도 {book.total_ask_qty:,.0f} · 매수 {book.total_bid_qty:,.0f}"
            ))

        self.stdout.write("")
        self.stdout.write(self.style.MIGRATE_HEADING("다음 단계"))
        self.stdout.write("  · 폴링 1회        python manage.py poll_orderbook")
        self.stdout.write("  · 대회 상시 기동  python manage.py run_trading_loop --loop")


def _remaining(expires_at) -> str:
    """만료까지 남은 시간을 사람 말로."""
    delta = expires_at - timezone.now()
    seconds = int(delta.total_seconds())
    if seconds <= 0:
        return "만료됨"
    hours, rest = divmod(seconds, 3600)
    return f"{hours}시간 {rest // 60}분"
