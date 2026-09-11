# E-04. `market` — 종목 마스터 · 영업일 · 시세 캐시

> **문서 버전** v2.0 · **작성일** 2026-08-13 (KST) · **전제** [02-공통-설계규약](02-공통-설계규약.md)
> **기능 명세** [F-16 시장데이터 파이프라인](../../features/version2.0/F-16-시장데이터-파이프라인.md) ·
> [F-04 규칙 엔진](../../features/version2.0/F-04-대회-규칙엔진.md) ·
> [F-20 스케줄러](../../features/version2.0/F-20-스케줄러.md)

**모델 13종.** 이 앱이 v1.0 결함 D-9(프로세스 메모리 캐시)를 구조적으로 해소한다.

---

## 1. 모델 13종

| 구분 | 모델 | 테이블 | 갱신 |
|---|---|---|---|
| **마스터** | `StockMaster` | `stock_master` | pykrx 일배치 16:00 |
| | `TradingCalendar` | `trading_calendar` | pykrx 연 1회 + 수동 |
| | `UpbitMarket` | `upbit_market` | 매일 18:00 |
| | `AlternativeProduct` | (→ [E-03](E-03-trading.md)) | 수동 |
| **캐시** | `QuoteCache` | `quote_cache` | 장중 10초 |
| | `OrderbookCache` | `orderbook_cache` | 장중 5초 |
| | `ChartCache` | `chart_cache` | 300초 |
| | `IndexCache` | `index_cache` | 60초 |
| | `NewsCache` | `news_cache` | 300초 |
| **운영** | `ApiCallBudget` | `api_call_budget` | 호출 시마다 |
| | `SubscriptionRegistry` | `subscription_registry` | 화면 열람 시 |
| | `ExternalToken` | `external_token` | 24시간 |
| **집계** | `CryptoRank` | `crypto_rank` | 매시 |
| | `MarketIndexSnapshot` | `market_index_snapshot` | 매 영업일 |

---

## 2. 캐시 원칙 ★ (결함 D-9)

> **모든 캐시는 Postgres 테이블에 있다.
> 요청을 넘어서 사는 프로세스 메모리 캐시는 만들지 않는다.**

| v1.0 | v2.0 |
|---|---|
| 시세·차트·뉴스·랭킹 캐시가 전부 전역 `dict` | 테이블 6종 |
| 서버리스에서 인스턴스마다 따로 놈 → 히트율 0 | 전 인스턴스가 같은 값을 본다 |
| 재기동하면 사라짐 | 영속 |

**허용되는 유일한 메모리 캐시는 요청 단위**다. 한 HTTP 요청 안에서 같은 종목을
여러 번 조회하면 두 번째부터 파이썬 딕셔너리에서 꺼내고, 요청이 끝나면 버린다.

### 2.1 캐시 테이블의 공통 모양

```python
# market/models.py
class CacheBase(models.Model):
    """캐시 테이블 공통. TimeStampedModel 을 상속하지 않는다 —
    fetched_at / expires_at 이 그 역할을 하고, 매 갱신마다 updated_at 을
    함께 쓰는 것은 낭비다(규약 10장).
    """

    fetched_at = models.DateTimeField(verbose_name="수집 시각")
    expires_at = models.DateTimeField(db_index=True, verbose_name="만료 시각")
    source = models.CharField(max_length=10, blank=True, verbose_name="출처")

    class Meta:
        abstract = True

    @property
    def is_fresh(self) -> bool:
        return self.expires_at > timezone.now()
```

`expires_at` 에 인덱스를 거는 이유는 **`cleanup` 잡이 만료 행을 지울 때** 쓰기 때문이다.

---

## 3. `StockMaster` — 종목 마스터 ★

대회 규칙 판정에 필요한 모든 속성을 pykrx 일배치로 채운다.
**`symbol` 을 PK 로 둔다** — `Order.symbol` · `Position.symbol` · `ContestUniverse.symbol` 과
문자열로 맞물려야 하기 때문이다.

| 필드 | 타입 | 규칙 용도 |
|---|---|---|
| `symbol` | varchar(6) **PK** | |
| `name` | varchar(60) | |
| `market` | varchar(10) | KOSPI / KOSDAQ |
| `stock_type` | varchar(15) | **보통주만 허용** (우선주·ETF·ETN·리츠·스팩 배제) |
| `sector_code` / `sector_name` | varchar | **KRX 업종분류** — 섹터 한도 |
| `listing_date` | date | 신규·재상장 6영업일 |
| `market_cap` | bigint | 시총 1000억 / 1조 판정 |
| `shares_outstanding` | bigint | 상장주식수 |
| `avg_turnover_5d` | bigint | **5일 평균 거래대금** — 30억 이하 매수 불가 |
| `close_price` | numeric(20,8) | 전일 종가 |
| `is_supervised` | bool | 관리종목 |
| `alert_level` | varchar(15) | 투자주의/경고/위험/환기 |
| `is_delisted` | bool | 상장폐지 |
| **`is_featured`** | bool | **수업용 14종** — 시뮬레이션 폴백 대상 |
| `updated_at` | timestamptz | |

```python
class StockMaster(models.Model):
    """종목 마스터 — pykrx 일배치(매 영업일 16:00 KST)로 적재한다.

    v1.0 은 14종을 하드코딩하고, 그 밖의 종목은 KRX KIND 페이지를 EUC-KR HTML
    정규식으로 긁었다. 깨지기 쉬운 경로라 pykrx 로 교체한다.

    Django 관점 — symbol 을 primary_key 로 두면 Django 가 id 컬럼을 만들지 않는다.
    FastAPI + SQLAlchemy 에서 자연키를 쓰던 감각과 같지만, Django Admin 의 URL 이
    /admin/market/stockmaster/005930/ 처럼 종목코드로 나와 운영에도 편하다.
    """

    symbol = models.CharField(max_length=6, primary_key=True, verbose_name="종목코드")
    name = models.CharField(max_length=60, db_index=True, verbose_name="종목명")
    market = models.CharField(max_length=10, choices=Market.choices)
    stock_type = models.CharField(max_length=15, choices=StockType.choices, default=StockType.COMMON)

    sector_code = models.CharField(max_length=20, blank=True, db_index=True)
    sector_name = models.CharField(max_length=40, blank=True,
                                   help_text="KRX 업종분류. GICS 는 유료라 사용하지 않는다")

    listing_date = models.DateField(null=True, blank=True)
    market_cap = models.BigIntegerField(default=0, verbose_name="시가총액")
    shares_outstanding = models.BigIntegerField(default=0)
    avg_turnover_5d = models.BigIntegerField(default=0, verbose_name="5일 평균 거래대금")
    close_price = models.DecimalField(**PRICE, default=0)

    is_supervised = models.BooleanField(default=False, verbose_name="관리종목")
    alert_level = models.CharField(max_length=15, blank=True, verbose_name="시장경보")
    is_delisted = models.BooleanField(default=False)
    is_featured = models.BooleanField(default=False, verbose_name="수업용 14종")

    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "stock_master"
        indexes = [
            # 거래 가능 종목 검색 — 규칙 엔진이 매 주문마다 본다
            models.Index(fields=["market", "stock_type", "is_delisted"], name="stock_idx_tradable"),
            models.Index(fields=["-market_cap"], name="stock_idx_market_cap"),   # 시총 랭킹
        ]
```

### 3.1 GICS 대체 명시 ★

타임폴리오는 GICS 산업군으로 섹터를 나눈다. **GICS 는 MSCI·S&P 의 유료 데이터**라 쓸 수 없다.

- `sector_code` / `sector_name` 에는 **KRX 업종분류**가 들어간다
- 이 사실을 대회 규칙 안내문과 섹터 화면에 명시한다
- 대회 시작 시점의 업종은 `ContestUniverse` 로 고정한다 (→ [E-02](E-02-contests.md) 5장)

### 3.2 "거래 불가" 는 컬럼이 아니라 판정이다

`is_tradable` 같은 컬럼을 두지 **않는다.** 거래 불가 조건 5가지가
**대회 `rule_set` 의 임계값에 따라 달라지기** 때문이다 (시총 1000억은 기본값일 뿐이다).

```python
def is_buyable(stock: StockMaster, rule_set: dict, today: date) -> tuple[bool, str]:
    """매수 가능 여부와 그 이유. 매도는 언제나 허용한다(F-04 2.2).

    이미 보유한 종목이 대회 중 관리종목이 되면 매수는 막히지만 팔 길은 열어둬야 한다.
    막으면 참가자가 물린 채로 대회를 끝내야 한다.
    """
    if stock.stock_type != StockType.COMMON:
        return False, "보통주가 아닙니다"
    if stock.market_cap < rule_set["min_market_cap_krw"]:
        return False, f"시가총액 {stock.market_cap/1e8:.0f}억으로 기준 미달입니다"
    if stock.avg_turnover_5d <= rule_set["min_avg_turnover_krw"]:
        return False, f"5일 평균 거래대금 {stock.avg_turnover_5d/1e8:.1f}억으로 기준 30억 미만입니다"
    if stock.is_supervised or stock.alert_level:
        return False, f"{stock.alert_level or '관리종목'}으로 지정되어 있습니다"
    ...
```

**에러 메시지에 실제 수치를 넣는다.** "거래할 수 없습니다"만 말하면 참가자가 이유를 모른다.

---

## 4. `TradingCalendar` — 영업일

| 필드 | 타입 | 설명 |
|---|---|---|
| `date` | date **PK** | KST |
| `is_open` | bool | 개장 여부 |
| `note` | varchar(50) | "임시 휴장" 등 |
| `source` | varchar(10) | `PYKRX` / `MANUAL` |

pykrx 로 연간 영업일을 미리 채우고, **임시 휴장은 운영자가 Django Admin 에서 수정**한다.
`source` 를 남기는 이유는 배치가 수동 수정을 덮어쓰지 않게 하기 위해서다.

```python
# 배치는 MANUAL 행을 건드리지 않는다
TradingCalendar.objects.filter(source=Source.PYKRX).bulk_create(
    rows, update_conflicts=True, unique_fields=["date"], update_fields=["is_open"],
)
```

---

## 5. 캐시 5종

### 5.1 `QuoteCache` — 현재가

| 필드 | 타입 | 설명 |
|---|---|---|
| `asset_class` / `symbol` | varchar | **UNIQUE(asset_class, symbol)** |
| `price` | numeric(20,8) | |
| `change` / `change_pct` | | 전일 대비 |
| `volume` | bigint | |
| `open` / `high` / `low` / `prev_close` | numeric(20,8) | |
| `source` | varchar(10) | `KIS`/`YF`/`NAVER`/`UPBIT`/`SIM` |
| **`is_simulated`** | bool | **시뮬레이션 가격 여부** ★ |
| `fetched_at` / `expires_at` | timestamptz | TTL 10초(장중) |

**`asset_class` 를 키에 포함하는 이유**: 주식 `005930` 과 코인 `KRW-BTC` 가
한 테이블을 쓴다. 형식이 달라 충돌하지는 않지만, 자산군별 일괄 조회·정리가 필요하다.

#### `is_simulated` — 정직성 컬럼 ★

v1.0 의 3단 폴백에서 3순위(시뮬레이션)로 떨어진 값이다.
**v1.0 은 이 사실이 코드에만 있고 화면에는 없었다.**

| 규칙 | |
|---|---|
| **대회 체결** | `is_simulated=True` 인 가격으로는 **절대 체결하지 않는다.** 주문은 `ACCEPTED` 로 남기고 복구를 기다린다 |
| 연습 체결 | 허용. 단 화면에 **"시뮬레이션 가격" 배지** |

### 5.2 `OrderbookCache` — 호가 10단계

| 필드 | 타입 | 설명 |
|---|---|---|
| `symbol` | varchar(20) | **UNIQUE** |
| `levels` | jsonb | `[{"ask_price":…, "ask_qty":…, "bid_price":…, "bid_qty":…} × 10]` |
| `total_ask_qty` / `total_bid_qty` | numeric(28,8) | |
| `fetched_at` / `expires_at` | | TTL 5초(장중) |

**10단계를 JSONB 한 칸에 넣는 이유**: 호가는 항상 통째로 읽고 통째로 쓴다.
40개 컬럼으로 펼치거나 자식 테이블로 나누면 갱신마다 10행을 쓰게 되어 손해다.

**대회 모드에서만 채운다.** 연습 모드는 현재가에서 파생한 5단계를 화면에서 만들 뿐이라
KIS 를 호출하지 않는다. 이것이 유량 부담을 크게 줄인다.

### 5.3 나머지 3종

| 모델 | 키 | TTL | 내용 |
|---|---|---|---|
| `ChartCache` | UNIQUE(asset_class, symbol, interval) | 300초 | `payload` jsonb — 봉 배열 |
| `IndexCache` | UNIQUE(index_code) | 60초 | KOSPI·KOSDAQ 현재 지수 |
| `NewsCache` | UNIQUE(source) | 300초 | `payload` jsonb — KRX 뉴스 목록 |

**`NewsCache` 는 실패해도 마지막 성공분을 남긴다.** v1.0 은 실패 시 빈 배열을 반환해
화면이 조용히 비었다. v2.0 은 만료돼도 행을 지우지 않고, 수집 실패는
`DataSyncLog` 에 남겨 Admin 에서 보이게 한다.

---

## 6. `ApiCallBudget` — KIS 유량 관리 ★

KIS 모의투자 API 는 **초당 5건** 제한이다(실전 20건). 초과하면 `EGW00201` 이 난다.

**서버리스에서는 각 인스턴스가 자기 호출 수만 알기 때문에, 전역 예산을 DB 에서
관리하지 않으면 반드시 터진다.**

| 필드 | 타입 | 설명 |
|---|---|---|
| `api_name` | varchar(20) | `KIS` / `UPBIT` / … |
| `window_start` | timestamptz | **초 단위 절삭** |
| `count` | int | |
| — | **UNIQUE(api_name, window_start)** | |

```sql
-- 호출 직전. 원자적 증가와 검사를 한 번에 한다
INSERT INTO api_call_budget (api_name, window_start, count)
VALUES ('KIS', date_trunc('second', now()), 1)
ON CONFLICT (api_name, window_start)
DO UPDATE SET count = api_call_budget.count + 1
RETURNING count;
-- count > 5 이면 호출하지 않고 캐시값으로 응답한다
```

> **왜 잠금(`FOR UPDATE`)이 아니라 upsert 인가** — [F-16](../../features/version2.0/F-16-시장데이터-파이프라인.md) 2.4 는
> `SELECT … FOR UPDATE` 로 적혀 있지만, 카운터 증가에는 upsert 가 더 낫다.
> 잠금은 트랜잭션이 끝날 때까지 다른 요청을 세우는데, **초당 5건짜리 창에서는
> 그 대기 자체가 유량을 낭비**한다. upsert 는 대기 없이 즉시 결과를 준다.
> (동시 갱신 방지가 필요한 **캐시 행 갱신**에는 여전히 `FOR UPDATE SKIP LOCKED` 를 쓴다.)

---

## 7. `SubscriptionRegistry` — 폴링 우선순위

초당 5건 = 분당 300건이다. 전 종목 2,700여 개를 주기적으로 갱신하는 건 불가능하다.
**필요한 것만 갱신한다.**

| 필드 | 타입 | 설명 |
|---|---|---|
| `asset_class` / `symbol` | varchar | **UNIQUE(asset_class, symbol)** |
| `priority` | smallint | 1(미체결 주문) / 2(보유) / 3(열람 중) / 4(그 외) |
| `reason` | varchar(20) | 우선순위 근거 |
| `last_polled_at` | timestamptz | |
| `expires_at` | timestamptz | 화면 열람은 일정 시간 후 만료 |

| 우선순위 | 대상 | 주기 |
|---|---|---|
| 1 | 대회 참가자가 **미체결 주문을 걸어둔 종목** | 5초 |
| 2 | 대회 참가자가 **보유 중인 종목** | 30초 |
| 3 | 화면에서 **보고 있는 종목** | 요청 시 on-demand |
| 4 | 그 외 | 캐시 만료 시에만 |

인덱스: `(priority, last_polled_at)` — 잡이 "우선순위 높고 가장 오래된 것부터" 뽑는다.

---

## 8. `ExternalToken` — 외부 브로커 접근 토큰

> **2026-08-13 개정 (세션 5 · 변경노트 E-1 반영)** — KIS 단독 전제에서
> **브로커 다중화**로 넓혔다. 강사님 원본이 KIS 하나에서 KB증권·Alpaca 까지
> 확장됐다(upstream `cde723a` · `179b946` · `8798d1b`).

| 필드 | 타입 | 설명 |
|---|---|---|
| `provider` | varchar(20) | `BrokerProvider` — `KIS` / `KB` / `ALPACA` / `UPBIT` |
| **`environment`** | varchar(10) | **신설** — `MOCK`(모의/Paper) / `REAL`(실전/Live) |
| `access_token` | text | |
| `token_type` | varchar(20) | `Bearer` |
| `issued_at` / `expires_at` | timestamptz | 24시간 |
| — | **UNIQUE(provider, environment)** | 구 `UNIQUE(provider)` 에서 변경 |

### 8.1 `environment` 가 필요한 이유 ★

**같은 브로커에 자격증명이 둘이다.** KIS 는 모의투자/실전, Alpaca 는 Paper/Live 다.
`provider` 단독 유니크면 **둘 중 하나만 캐시할 수 있고**, 모의로 개발하다 실전 토큰이
덮어써지는 사고가 난다.

### 8.2 1차 범위는 KIS 만 실사용이다

| 브로커 | 상태 | 폴백 체인 |
|---|---|---|
| **KIS** | 실사용 (모의투자) | **포함** |
| KB | 포털 승인 전 — 시세 경로가 없다 | 제외 |
| Alpaca | 읽기 전용 계정 조회까지 | 제외 |
| UPBIT | 공개 시세는 인증 불필요 | 토큰 미사용 |

**모델 자리만 만들어 두고 폴백 체인에는 넣지 않는다.** 원본 `broker_test.py` 의
주석이 KB 의 미승인 상태를 정확히 적어 두었다.

발급 절차는 **[api_발급_가이드](../../api/version2.0/api_발급_가이드.md)** 참조.
같은 문서에 **AWS 는 이 프로젝트에서 사용하지 않는다**(강사님 `e97d82f` 의 Lambda 연동
배제)를 명시했다.

> **보안** — `KIS_APP_KEY` / `KIS_APP_SECRET` 은 **환경변수에만** 둔다. DB 에 넣지 않는다.
> DB 에 저장하는 것은 **발급된 액세스 토큰뿐**이고, 이것도 24시간 후 만료된다.
> (학습가이드 (b) 가 "키는 브라우저가 아닌 서버에만 둡니다"를 첫 원칙으로 말한다 →
> [F-17](../../features/version2.0/F-17-학습콘텐츠.md) 3.1. 그 문서 내용을 서비스가 실제로 실천하는 셈이다.)

---

## 9. 코인 — `UpbitMarket` · `CryptoRank`

### 9.1 `UpbitMarket`

| 필드 | 타입 | 설명 |
|---|---|---|
| `market` | varchar(20) **PK** | `KRW-BTC` |
| `korean_name` / `english_name` | varchar | |
| `is_warning` | bool | 유의 종목 |
| `is_active` | bool | |

매일 18:00 에 **없는 것만 추가**(upsert)한다. v1.0 동작 승계.

### 9.2 `CryptoRank` — KRW 기준으로 재정의 ★ (결함 D-3)

v1.0 은 CoinMarketCap Top 100(**USD 기준**)을 매시 `TRUNCATE` 후 재적재했는데
**어느 화면에서도 쓰지 않았다.** 거래는 KRW 기준이라 데이터가 연결되지도 않았다.

| 필드 | 타입 | 설명 |
|---|---|---|
| `market` | varchar(20) | **UNIQUE** — `KRW-BTC` |
| `rank` | int | |
| `trade_price` | numeric(20,8) | |
| `acc_trade_price_24h` | bigint | **24시간 거래대금 (KRW)** — 랭킹 기준 |
| `change_rate` | numeric(9,4) | |
| `updated_at` | timestamptz | |

| | v1.0 | v2.0 |
|---|---|---|
| 소스 | CoinMarketCap Pro (USD) | **업비트 KRW 마켓** |
| 갱신 | 매시 `TRUNCATE` 후 재적재 | 매시 **upsert** |
| 사용처 | **없음** | **코인 연습 화면에 표시** |

**`TRUNCATE` 를 버리는 이유**: 동기화 중에 조회하면 빈 테이블이 보이는 창이 생긴다.
CoinMarketCap API 키 의존도 함께 사라진다.

---

## 10. `MarketIndexSnapshot` — 벤치마크 지수

NAV 차트에 KOSPI·KOSDAQ 을 겹쳐 그리려면 지수 종가도 함께 쌓아야 한다.

| 필드 | 타입 | 설명 |
|---|---|---|
| `date` | date | |
| `index_code` | varchar(10) | `KOSPI` / `KOSDAQ` |
| `close` | numeric(14,4) | 종가 |
| `change_pct` | numeric(9,4) | |
| — | **UNIQUE(date, index_code)** | |

### 10.1 `nav` 컬럼을 두지 않는다 ★ (기능명세 조정)

[F-05](../../features/version2.0/F-05-랭킹-정산-스냅샷.md) 4.4 는
`MarketIndexSnapshot(date, index_code, close, nav)` 로 적혀 있다.
**`nav` 는 저장할 수 없다.**

NAV 는 "**대회 시작일**을 1000 으로 놓은 지수"인데, 이 테이블은 대회와 무관한 전역
시계열이다. **대회마다 기준일이 다르므로 한 행에 하나의 NAV 를 담을 수 없다.**
대회가 3개면 같은 날짜에 NAV 가 3개여야 한다.

**해결**: 종가만 저장하고 NAV 는 조회 시 환산한다.

```
지수 NAV(대회 c, 날짜 d) = 1000 × close(d) / close(대회 c 의 시작일)
```

기준일 종가 1건만 더 읽으면 되고, 대회가 몇 개든 같은 테이블을 공유한다.
→ 조정 근거는 [06-기능명세-정합성-변경노트](06-기능명세-정합성-변경노트.md) 1번.

---

## 11. 캐시 정리 (`cleanup` 잡)

매일 04:00 KST 에 도는 잡이 지운다.

| 대상 | 기준 |
|---|---|
| `QuoteCache` · `ChartCache` · `IndexCache` | `expires_at < now() - 1일` |
| `OrderbookCache` | `expires_at < now() - 1일` |
| `ApiCallBudget` | `window_start < now() - 1시간` |
| `RateLimitCounter` (accounts) | `window_start < now() - 1시간` |
| `IntradaySnapshot` (contests) | 전일분 전체 |
| `SubscriptionRegistry` | `priority=3` 이고 `expires_at` 경과 |

**`NewsCache` 는 지우지 않는다.** 만료돼도 마지막 성공분을 보여줘야 하기 때문이다.

---

## 12. v1.0 대조

| v1.0 | v2.0 | 결함 |
|---|---|---|
| 전역 `dict` 캐시 5종 | 캐시 테이블 5종 | **D-9** |
| `dict` rate limit | `ApiCallBudget` + `RateLimitCounter` | **D-9** |
| `STOCKS` 14종 하드코딩 + KRX HTML 정규식 파싱 | `StockMaster` (pykrx) | |
| `crypto_rank` USD·`TRUNCATE`·미사용 | KRW·upsert·화면 연결 | **D-3** |
| `upbit_market` | 승계 (upsert 유지) | |
| (호가 없음 — 현재가에서 파생) | `OrderbookCache` (KIS 실호가) | |
| (지수 없음) | `IndexCache` · `MarketIndexSnapshot` | |

---

## 13. 관련 문서

- 규칙 판정에서의 사용 → [E-02 contests](E-02-contests.md) 5장
- 체결에서의 호가 사용 → [E-03 trading](E-03-trading.md) 9장
- 잡 스케줄 → [F-20 스케줄러](../../features/version2.0/F-20-스케줄러.md)
- 명세 조정 → [06-기능명세-정합성-변경노트](06-기능명세-정합성-변경노트.md)
