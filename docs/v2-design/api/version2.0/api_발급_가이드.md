# API 키 발급 가이드

> **문서 버전** v2.0 · **작성일** 2026-08-13 (KST) · **상위** [00-API-전체목록](00-API-전체목록.md)
> **용도** v2.0 이 쓰는 외부 API 자격증명의 **발급 절차와 보관 규칙**
> **최종 확인일** 2026-08-13 (세션 10 — KIS 2장 전면 개정) — 외부 서비스의 화면은 바뀐다.
> 6개월이 지나면 다시 확인한다

이 문서는 **우리 서버가 외부에 붙을 때 쓰는 키**를 다룬다.
회원이 우리 서비스를 호출할 때 쓰는 키(`accounts.ApiKey`)는
[A-06 OpenAPI-v1](A-06-OpenAPI-v1.md) 이 다룬다. **둘을 혼동하지 않는다.**

---

## 0. 먼저 — 이 프로젝트가 쓰지 않는 것 ★

| 대상 | 판정 |
|---|---|
| **AWS (Lambda · S3 · 그 밖 전부)** | **사용하지 않는다.** 강사님 원본 `e97d82f` 의 README 가 AWS Lambda 연동을 다루지만 **v2.0 은 채택하지 않는다.** 배치는 Supabase `pg_cron` 이 돌린다 (→ [F-20](../../features/version2.0/F-20-스케줄러.md)) |
| **CoinMarketCap (`CMC_API_KEY`)** | **더 이상 필요 없다.** 코인 랭킹을 USD 기준 CMC 에서 **업비트 KRW 마켓**으로 바꿨다 (결함 D-3 → [E-04](../../erd/version2.0/E-04-market.md) 9.2) |
| **Qdrant** | pgvector 로 대체 (결함 D-8) |

**AWS 키를 이 프로젝트의 `.env` 에 넣지 않는다.**

---

## 1. 보관 3원칙 ★

이 세 줄이 이 문서에서 가장 중요하다.

| # | 원칙 | 근거 |
|---|---|---|
| 1 | **앱키·시크릿은 환경변수에만 둔다. DB 에 넣지 않는다** | DB 가 유출되면 증권사 계정까지 넘어간다 |
| 2 | **DB 에 저장하는 것은 발급된 액세스 토큰뿐**이고 24시간 후 만료된다 | `market.ExternalToken` (→ [E-04](../../erd/version2.0/E-04-market.md) 8장) |
| 3 | **키는 브라우저가 아닌 서버에만 둔다** | 학습가이드 (b) 의 첫 원칙이기도 하다 (→ [F-17](../../features/version2.0/F-17-학습콘텐츠.md) 3.1) |

3번은 강사님 원본이 이미 실천하고 있다. `broker_test.py` 는 키를
`/run/secrets` 또는 환경변수에서 읽고, **브라우저에는 정규화된 시세나 안전한
에러 메시지만** 내려보낸다. 이 구조를 v2.0 도 그대로 따른다.

```python
# 강사님 원본 broker_test.py — BrokerApiError 는 자격증명을 절대 담지 않는다
class BrokerApiError(RuntimeError):
    """A user-safe error that never includes credentials or access tokens."""
```

### 1.1 커밋 사고 방지

이 저장소는 **GitHub·GitLab 양쪽 모두 Public** 이다.

```bash
# .gitignore 가 이미 막고 있다 — 확인만 한다
git check-ignore -v backend/.env      # → .gitignore:22:.env  backend/.env
git status --short                    # push 전 습관
```

`.gitignore` 는 `*.key` 도 막는다 (`kis.key` · `kb.key` · `al.key` 형식 대비).

---

## 2. 한국투자증권 KIS Developers — 주식 실호가 ★

**v2.0 이 실제로 의존하는 유일한 증권사 API 다.** 대회 모드의 호가 10단계가 여기서 온다.

> **2026-08-13 개정 (세션 10)** — 구현하면서 절차·유량·오류 코드를 다시 확인했다.
> 유량 수치는 **공개 자료가 서로 어긋난다.** 아래 2.5 를 반드시 읽는다.

| 항목 | 값 |
|---|---|
| 포털 | <https://apiportal.koreainvestment.com> |
| 모의투자 도메인 | `https://openapivts.koreainvestment.com:29443` |
| 실전 도메인 | `https://openapi.koreainvestment.com:9443` |
| **유량 제한** | 실전 초당 20건 · **모의는 자료마다 다르다 (2.5)** — 초과 시 `EGW00201` |
| 토큰 수명 | 24시간. **발급은 1분에 1회**로 제한된다 (`EGW00133`) |

### 2.1 절차 — 처음부터 끝까지

**포털에서 바로 시작할 수 없다.** 증권 계좌가 먼저다. 순서를 건너뛰면 3번에서 막힌다.

| # | 단계 | 어디서 | 걸리는 시간 |
|---|---|---|---|
| 1 | **한국투자증권 계좌 개설** (비대면 가능) | 한국투자 앱 / 영업점 | 신분증·본인확인 필요 |
| 2 | **HTS/MTS ID 등록 및 계좌 연결** | 한국투자 홈페이지 | 즉시 |
| 3 | **모의투자 신청** → 모의계좌번호 발급 | 홈페이지 / MTS 의 모의투자 메뉴 | 즉시 |
| 4 | **오픈API 서비스 신청** | 홈페이지 `서비스신청 > Open API > KIS Developers` | 즉시 |
| 5 | KIS Developers ID 생성 → **임시 비밀번호를 알림톡으로** 받는다 | 자동 | 즉시 |
| 6 | 포털 로그인 → **앱키 발급** (앱 이름·설명 입력) | apiportal 우상단 `API 신청` | 즉시 |

**3번을 건너뛰지 않는다.** 모의투자 앱키는 **모의계좌번호로 신청**해야 나온다.
실전 계좌만 있는 상태로 신청하면 실전 키가 나오고, 그 키로 모의 도메인을 부르면
인증이 거부된다.

> ★★ **모의와 실전은 앱키가 서로 다르다.** 한 쌍으로 양쪽을 쓸 수 없다.
> `KIS_MODE` 와 발급 환경이 어긋나면 `EGW00121` 계열 인증 오류가 나는데,
> 메시지만 봐서는 "키가 틀렸다" 로 읽혀 원인을 한참 찾게 된다.

> ★★ **APP SECRET 은 발급 직후 한 번만 보인다.** 포털이 평문으로 다시 보여주지
> 않는다. 그 자리에서 `backend/.env` 에 붙여 넣는다. 놓치면 재발급이다.
> (우리 `accounts.ApiKey` 설계와 같은 방식이다 — [A-06](A-06-OpenAPI-v1.md))

> 강사님이 이 과정을 SVG 4장으로 정리해 두었다 (upstream `8798d1b`):
> `frontend/images/kis-mock-investment-application.svg` ·
> `kis-mock-account-confirmation.svg` · `kis-account-requirements.svg` ·
> `kis-app-key-security.svg`. **화면 캡처가 필요하면 이걸 먼저 본다.**
> 학습용 화면은 `frontend/learning/kis-developers.html` 에도 있다.

### 2.2 `.env`

```bash
KIS_MODE=mock                 # mock | real — 모의와 실전은 앱키가 다르다
KIS_APP_KEY=
KIS_APP_SECRET=
KIS_ACCOUNT_NO=               # 계좌번호 앞 8자리 (시세 조회에는 쓰지 않는다)
KIS_ACCOUNT_PRODUCT_CODE=01   # 뒤 2자리
KIS_RATE_LIMIT=               # 초당 한도. 비우면 모의 2 · 실전 15 (2.5 참조)
```

**계좌번호는 시세 조회에 필요 없다.** v2.0 은 KIS 로 **주문을 내지 않는다** —
주문은 전부 우리 DB 안의 모의 거래다. 계좌번호 칸을 비워 두어도 호가·현재가는 온다.

### 2.3 넣은 뒤 — 확인 한 줄 ★

```bash
cd backend
python manage.py kis_probe
```

세 단계를 순서대로 확인하고, 막힌 지점에서 **무엇을 해야 하는지** 알려준다.

```
① 설정 (backend/.env)
  · 모드      MOCK (KIS_MODE)
  · 도메인    https://openapivts.koreainvestment.com:29443
  · 앱키      길이 36자 (값은 표시하지 않습니다)
② 접근토큰
  ✔ 토큰 발급 — 만료 2026-08-14 16:30:00 KST (남은 23시간 58분)
③ 시세 — 005930
  ✔ 현재가 74,300원 (전일 73,000 · +1,300 · +1.78%) · 거래량 12,345,678
  ✔ 호가 10단계 — 매도1 74,300(10) / 매수1 74,200(20)
```

**자격증명은 화면에 찍지 않는다.** 앞 4글자조차 보여주지 않고 **길이만** 알려준다 —
터미널 기록·스크린샷·화면 공유가 가장 흔한 유출 경로다.

| 옵션 | 용도 |
|---|---|
| `--symbol 000660` | 다른 종목으로 확인 |
| `--token-only` | 토큰까지만 (시세를 부르지 않는다) |
| `--refresh` | 캐시된 토큰을 버리고 새로 발급 — **1분에 1회 제한을 기억한다** |

> **장외에 `③ 호가 없음` 이 뜨는 것은 정상이다.** 호가는 장중(09:00~15:30 KST)에만
> 의미가 있다. 현재가(`✔`)까지 나왔으면 연결은 성공한 것이다.

### 2.4 v2.0 에서의 취급

- 토큰은 `market.ExternalToken(provider="KIS", environment="MOCK")` 에 캐시한다.
  **서버리스라 메모리에 두면 콜드스타트마다 재발급하게 되는데, 발급이 1분에 1회라
  메모리 캐시는 느린 게 아니라 아예 동작하지 않는다.**
- 만료 **30분 전**에 미리 갈아치운다. 만료 직전 토큰으로 호출하면 요청이 날아가는
  사이에 만료돼 `EGW00123` 이 나고, 재발급은 1분 뒤로 밀린다.
- 호출 직전에 `market.ApiCallBudget` 으로 초당 한도를 **전역 관리**한다.
  인스턴스마다 세면 반드시 터진다 (→ [E-04](../../erd/version2.0/E-04-market.md) 6장).
- **호가는 대회 모드에서만 채운다.** 연습 모드는 현재가에서 파생한 5단계를 화면에서
  만들 뿐이라 KIS 를 호출하지 않는다. 이것이 유량 부담을 크게 줄인다.
  구분은 `SubscriptionRegistry.needs_orderbook` 이 들고 있다.
- 구현 위치는 `backend/market/kis.py`, 폴링 잡은 `backend/market/jobs.py` 다.

### 2.5 ★★ 유량 — 자료가 어긋난다 (2026-08-13 확인)

**모의투자의 초당 한도에 대해 공개 자료가 서로 다른 값을 말한다.**

| 출처 | 모의 | 실전 |
|---|---|---|
| 이 문서의 기존 기술 · [F-16](../../features/version2.0/F-16-시장데이터-파이프라인.md) 2.4 | 초당 5건 | 초당 20건 |
| 2026-08 시점 가이드 글 (복수) | **초당 1건** | 초당 20건 |
| 커뮤니티 질의응답 | 초당 2건 | 초당 20건 |

실계정이 없어 **아직 실측하지 못했다.** 그래서 코드는 이렇게 처리한다.

```
기본값   모의 2건 · 실전 15건        ← 보수적으로 잡는다
조절     .env 의 KIS_RATE_LIMIT      ← 실측하면 그 값을 넣는다
```

**틀리는 방향을 고른 것이다.** 낮게 잡으면 폴링이 느려질 뿐이지만, 높게 잡으면
`EGW00201` 이 연발해 **대회 체결이 멈춘다.**

> **F-16 2.4 의 "초당 5건 = 분당 300건" 계산은 재검토가 필요하다.**
> 실제가 초당 1건이면 분당 60건이고, 우선순위 폴링 설계의 전제가 달라진다
> (→ 변경노트 E-43). **키가 생기면 가장 먼저 실측할 항목이다.**

### 2.6 오류 코드 — 무엇을 뜻하고 무엇을 해야 하는가

| 코드 | 뜻 | 대응 |
|---|---|---|
| `EGW00133` | **토큰 발급 과다** (1분에 1회 초과) | 기다린다. 코드가 70초 쿨다운을 걸어 스스로 참는다 |
| `EGW00121` | 앱키·시크릿 오류 | 키를 확인한다. **`KIS_MODE` 와 발급 환경이 같은지도 본다** |
| `EGW00123` | 토큰 만료·무효 | 자동 재발급된다. 계속 나면 앱키를 새로 발급한 뒤 `ExternalToken` 행을 지운다 |
| `EGW00201` | **유량 초과** | 2.5 의 `KIS_RATE_LIMIT` 을 낮춘다 |

> ★★ **KIS 는 HTTP 200 으로 실패를 알린다.** 본문의 `rt_cd` 가 `"0"` 이 아니면
> 실패다. `raise_for_status()` 만 믿으면 **유량 초과가 성공으로 통과**하고
> 빈 응답을 파싱해 **0원짜리 호가**가 캐시에 들어간다. 그 값으로 대회가 체결된다.

---

## 3. KB증권 Open API — 인증까지만 확인됨 ⚠️

| 항목 | 값 |
|---|---|
| 포털 | <https://developer.kbsec.com> |
| API 도메인 | `https://developer.kbsec.com:32484` |
| 토큰 | `POST /oauth2/token` (`grant_type=client_credentials`) |

### 3.1 현재 상태 — 시세 조회는 아직 안 된다

강사님 원본 `broker_test.py` 의 주석이 상태를 정확히 적어 두었다:

> KB's quote URI and required API group are issued per approved service.
> The current project key is rejected by KB at OAuth, so no unverified quote URI is guessed.

즉 **OAuth 인증은 성공해도 시세 API 경로·API 그룹이 포털에서 승인되어야** 쓸 수 있다.
승인 전에는 추측한 URI 를 호출하지 않고 설정 누락을 명확히 알린다 — 옳은 처리다.

### 3.2 `.env`

```bash
KB_APP_KEY=
KB_APP_SECRET=
```

**v2.0 1차 범위에서는 KB 를 시세 원천으로 쓰지 않는다.**
`market.BrokerProvider.KB` 선택지와 `ExternalToken` 자리는 만들어 두되,
[F-16](../../features/version2.0/F-16-시장데이터-파이프라인.md) 의 폴백 체인에는 넣지 않는다.
포털 승인이 떨어지면 그때 연결한다.

> 강사님 SVG: `frontend/images/` 의 KB증권 Open API · PC 로그인 안내 (upstream `179b946`)

---

## 4. Alpaca Paper Trading — 미국 주식 모의투자

| 항목 | 값 |
|---|---|
| 포털 | <https://alpaca.markets> |
| Paper 도메인 | `https://paper-api.alpaca.markets` |
| 인증 헤더 | `APCA-API-KEY-ID` · `APCA-API-SECRET-KEY` |

### 4.1 절차

1. Alpaca 가입 (Paper Trading 은 **실계좌 개설 없이** 쓸 수 있다)
2. 대시보드에서 **Paper Trading** 환경 선택
3. **API Key 생성** → `Key ID` 와 `Secret Key` 를 받는다
   (**Secret 은 생성 직후 1회만 보인다** — 우리 `ApiKey` 설계와 같은 방식이다)

### 4.2 `.env`

```bash
ALPACA_API_KEY=
ALPACA_SECRET_KEY=
```

### 4.3 v2.0 에서의 취급

강사님 원본 `alpaca_test.py` 는 **읽기 전용 계정 조회**만 한다.
반환값에도 계좌 식별자를 담지 않는다:

```python
"""Call the read-only Paper account endpoint and return no account identifier."""
```

**v2.0 1차 범위는 여기까지다** — 연결 확인용이고, 미국 주식 거래는 도입하지 않는다.
모델에는 `BrokerProvider.ALPACA` · `ExternalToken(environment="MOCK")` 자리만 만들어 둔다.

> 미국 주식을 실제로 거래하려면 자산군(`AssetClass`)에 `US_STOCK` 을 추가하고
> 환율·거래시간·소수점 주식까지 설계해야 한다. **v2.2 이후의 별도 과제다.**

---

## 5. 업비트 — 코인 시세 (키 불필요)

**시세·마켓 목록 조회는 인증이 필요 없다.** v2.0 이 쓰는 범위가 여기까지다.

| 용도 | 엔드포인트 | 인증 |
|---|---|---|
| 마켓 목록 (`UpbitMarket`) | `GET /v1/market/all` | 불필요 |
| 현재가 (`QuoteCache`) | `GET /v1/ticker` | 불필요 |
| 24시간 거래대금 (`CryptoRank`) | `GET /v1/ticker` | 불필요 |

**코인 주문은 우리 DB 안의 모의 거래**라 업비트 주문 API 를 쓰지 않는다.
따라서 **Access Key / Secret Key 를 발급받을 이유가 없다.**

---

## 6. Anthropic Claude API — AI 분석

| 항목 | 값 |
|---|---|
| 콘솔 | <https://console.anthropic.com> |
| `.env` | `ANTHROPIC_API_KEY=sk-ant-…` |

### 6.1 비용이 드는 키다 — 반드시 로그인 + 쿼터 뒤에 둔다 ★

> v1.0 은 `/api/ai/analyze` 가 **인증 없이 열려 있었다** (결함 D-6).
> Claude API 비용이 발생하는 엔드포인트가 누구에게나 열려 있던 것이다.

v2.0 의 방어:

| 장치 | 위치 |
|---|---|
| 로그인 필수 (DRF 기본값을 `IsAuthenticated` 로 뒤집음) | `config/settings.py` |
| 회원당 1일 쿼터 (기본 20회) | `insight.AiUsageLog` |
| **실패는 쿼터를 차감하지 않는다** | `AiUsageLog.is_error` |
| **모델명 하드코딩 금지** | `core.AppSetting["ai.model_basic"]` |

---

## 7. 공공 데이터 — 선택 (분석 기능용)

거래·대회에는 필요 없다. 학습 콘텐츠와 분석 화면에서 쓴다.

| 키 | 발급처 | 용도 |
|---|---|---|
| `DART_API_KEY` | <https://opendart.fss.or.kr> | 공시·재무제표 |
| `ECOS_API_KEY` | <https://ecos.bok.or.kr> | 한국은행 거시지표 |
| `KOSIS_API_KEY` | <https://kosis.kr> | 통계청 |
| `FRED_API_KEY` | <https://fred.stlouisfed.org> | 미국 거시지표 |
| `KRX_API_KEY` | <https://data.krx.co.kr> | KRX 통계 |

**종목 마스터는 이 중 어느 것도 쓰지 않는다** — `pykrx` 로 받는다
(→ [E-04](../../erd/version2.0/E-04-market.md) 3장). v1.0 이 KRX KIND 페이지를
EUC-KR HTML 정규식으로 긁던 경로를 없애기 위해서다.

---

## 8. 전체 `.env` 배치

`backend/.env` 하나에 모은다. **저장소 루트의 `.env`(v1.0 MariaDB 용)와 분리한다** —
같은 파일을 공유하면 v1.0/v2.0 의 DB 설정이 서로를 덮어쓴다.

```bash
cp backend/.env.example backend/.env    # 그 뒤 실제 값을 채운다
```

| 구분 | 키 | 필수 |
|---|---|---|
| Django | `DJANGO_SECRET_KEY` · `DJANGO_DEBUG` · `DJANGO_ALLOWED_HOSTS` | ✅ |
| DB | `POSTGRES_*` | ✅ |
| 증권사 | `KIS_*` | 대회 모드에 필요 |
| 증권사 | `KB_*` · `ALPACA_*` | 선택 (연결 확인용) |
| AI | `ANTHROPIC_API_KEY` | AI 분석에 필요 |
| 공공 | `DART_*` · `ECOS_*` 등 | 선택 |

---

## 9. 키가 새면 — 순서 ★

1. **먼저 폐기(revoke)한다.** 커밋을 지우는 것보다 이게 먼저다.
   히스토리에서 지워도 이미 복제됐을 수 있다
2. 새 키를 발급받아 `.env` 를 교체
3. `market.ExternalToken` 의 해당 행을 삭제 — 옛 앱키로 받은 토큰이 남아 있다
4. 히스토리 정리가 필요하면 **사용자가 직접** 한다
   (`AGENTS.md` 7.1 — force push · 히스토리 재작성은 사용자 승인 후에만)

---

## 10. 관련 문서

- 데이터 모델 → [E-04 market](../../erd/version2.0/E-04-market.md) 8장 (`ExternalToken`)
- 회원용 Open API 키 → [A-06 OpenAPI-v1](A-06-OpenAPI-v1.md) · [E-01](../../erd/version2.0/E-01-accounts.md) 5장
- 시장데이터 파이프라인 → [F-16](../../features/version2.0/F-16-시장데이터-파이프라인.md)
- 학습가이드(화면용) → [F-17](../../features/version2.0/F-17-학습콘텐츠.md) · `learning.Guide`
