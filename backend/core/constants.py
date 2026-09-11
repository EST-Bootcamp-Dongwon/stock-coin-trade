"""앱 경계를 넘는 공통 선택지.

규약 8장 — 상태·구분값은 전부 `models.TextChoices` 로 정의한다.
문자열을 코드 여기저기 흩뿌리면 오타가 런타임까지 살아남는다.

여러 앱이 함께 쓰는 것만 여기 둔다. 한 앱에서만 쓰는 선택지
(`OrderStatus` · `ContestStatus` 등)는 그 앱의 models.py 상단에 둔다.

Django 관점 — FastAPI 에서는 `class AssetClass(str, Enum)` 을 쓰고 DB 컬럼에는
`String` 을 넣은 뒤, 화면 표시명은 별도 dict 로 관리했다. TextChoices 는
DB 값과 표시명을 한 줄에 묶고, `get_asset_class_display()` 와 Admin 드롭다운을
공짜로 준다.
"""

from django.db import models


class AssetClass(models.TextChoices):
    """자산군. `Order` · `Position` · `Watchlist` 가 공유한다."""

    STOCK = "STOCK", "주식"
    CRYPTO = "CRYPTO", "코인"
    ALT = "ALT", "대체자산"


class AccountMode(models.TextChoices):
    """계좌 모드. v2.0 데이터 모델의 중심 개념이다.

    v1.0 은 `member.asset` 한 칸으로 주식·코인·대체자산을 전부 사고팔았다.
    v2.0 은 모드마다 계좌를 분리한다 (→ E-01 4장).
    """

    CONTEST = "CONTEST", "대회"
    PRACTICE_STOCK = "PRACTICE_STOCK", "주식 연습"
    PRACTICE_CRYPTO = "PRACTICE_CRYPTO", "코인 연습"
    PRACTICE_ALT = "PRACTICE_ALT", "대체자산 연습"

    @property
    def asset_class(self) -> str:
        """모드 → 자산군 매핑. 계좌에 `asset_class` 컬럼을 따로 두지 않는 이유다.

        컬럼으로 두면 모드와 자산군이 어긋날 수 있다. 모드가 자산군을 결정하므로
        파생 값으로 계산한다 (규약 8.1).

        주의 — 열거형 멤버에서 다른 멤버를 `self.CONTEST` 로 꺼내는 표기는
        Python 3.12 에서 아직 동작하지만 폐기 예정이다. 클래스명으로 명시한다.
        """
        return {
            AccountMode.CONTEST: AssetClass.STOCK,  # 1차는 주식 대회만 (E-02 3.1)
            AccountMode.PRACTICE_STOCK: AssetClass.STOCK,
            AccountMode.PRACTICE_CRYPTO: AssetClass.CRYPTO,
            AccountMode.PRACTICE_ALT: AssetClass.ALT,
        }[self]


class OrderSide(models.TextChoices):
    """매매 구분."""

    BUY = "BUY", "매수"
    SELL = "SELL", "매도"


class SyncStatus(models.TextChoices):
    """배치 실행 상태 (`core.DataSyncLog`)."""

    RUNNING = "RUNNING", "실행중"
    SUCCESS = "SUCCESS", "성공"
    FAILED = "FAILED", "실패"


class TriggeredBy(models.TextChoices):
    """배치를 누가 돌렸는가."""

    CRON = "CRON", "스케줄러"
    ADMIN = "ADMIN", "운영자"
    CLI = "CLI", "커맨드"


class BrokerProvider(models.TextChoices):
    """외부 브로커·거래소 식별자.

    ★ 2026-08-13 추가 — 강사님 원본이 KIS 하나에서 KB증권·Alpaca 까지 넓어졌다
    (upstream `cde723a` · `179b946` · `8798d1b`). v2.0 ERD 는 KIS 단독을 전제로
    `ExternalToken.provider` 를 잡아 두었으므로 여기서 선택지를 넓힌다.

    각 브로커의 키 발급 절차는 `docs/v2-design/api/version2.0/api_발급_가이드.md` 참조.

    주의 — 이 값은 **우리 서버가 외부에 붙을 때 쓰는 자격증명의 종류**다.
    회원이 우리 서비스를 호출할 때 쓰는 키(`accounts.ApiKey`)와 혼동하지 않는다.
    """

    KIS = "KIS", "한국투자증권"
    KB = "KB", "KB증권"
    ALPACA = "ALPACA", "Alpaca (미국주식 Paper)"
    UPBIT = "UPBIT", "업비트"


class QuoteSource(models.TextChoices):
    """시세 출처. `market.QuoteCache.source` 등이 쓴다.

    `SIM` 은 외부 조회가 전부 실패했을 때의 시뮬레이션 값이다.
    이 값으로는 대회 체결을 하지 않는다 (→ E-04 5.1).
    """

    KIS = "KIS", "한국투자증권"
    KB = "KB", "KB증권"
    ALPACA = "ALPACA", "Alpaca"
    UPBIT = "UPBIT", "업비트"
    PYKRX = "PYKRX", "pykrx"
    NAVER = "NAVER", "네이버"
    YF = "YF", "yfinance"
    SIM = "SIM", "시뮬레이션"
