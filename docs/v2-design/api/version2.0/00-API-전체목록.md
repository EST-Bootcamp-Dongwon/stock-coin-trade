# v2.0 API 전체 목록 · 설계 규약

> **문서 버전** v2.0 · **작성일** 2026-08-11 (KST)
> **스택** Django REST Framework + drf-spectacular
> **주의** 이 문서는 **엔드포인트 목록과 규약**을 확정한다. 요청·응답 스키마 상세는
> ERD 세션(`erd/version2.0`) 이후 각 `A-xx` 문서에서 확정한다.

---

## 1. 3계층 구조 ★

v2.0 은 서버가 **세 종류의 응답**을 낸다. 이걸 구분하지 않으면 설계가 엉킨다.

| 계층 | 경로 | 응답 | 인증 | 소비자 |
|---|---|---|---|---|
| **페이지** | `/...` | 완성된 HTML 문서 | 세션 | 브라우저 (첫 로드) |
| **프래그먼트** | `/.../fragments/...` | **HTML 조각** | 세션 | HTMX |
| **JSON API** | `/api/...` | JSON | 세션 | JS(차트·계산) |
| **Open API** | `/openapi/v1/...` | JSON | **Bearer 키** | 외부 봇 |
| **내부 잡** | `/internal/jobs/...` | JSON | **내부 토큰** | pg_cron |

### 1.1 왜 프래그먼트를 JSON API 와 분리하는가

> **Django 관점** — FastAPI + React 였다면 모든 게 JSON API 였다.
> HTMX 는 **HTML 을 받아 DOM 을 갈아끼우므로**, 화면 갱신용 응답은 JSON 이 아니라
> HTML 조각이어야 한다. JSON 으로 주고 JS 로 그리면 HTMX 를 쓸 이유가 없어진다.
>
> **원칙: 화면을 갱신하면 프래그먼트, 데이터가 필요하면 JSON API.**
> 차트 라이브러리(Lightweight Charts·Highcharts)처럼 JS 가 데이터를 먹어야 하는
> 경우에만 JSON 을 쓴다.

---

## 2. 설계 규약

### 2.1 명명

| 항목 | 규칙 |
|---|---|
| 경로 | 복수형 명사 (`/api/contests/`, `/api/orders/`) |
| 계층 | 소유 관계를 경로로 (`/api/contests/<id>/rankings/`) |
| 동사 | HTTP 메서드로 표현. 경로에 동사를 쓰지 않음 |
| 예외 | 상태 전이는 하위 자원으로 (`POST /api/orders/<id>/cancel`) |

### 2.2 응답 형식

**성공**

```jsonc
// 단건
{ "id": 1, "name": "..." }

// 목록 (페이지네이션)
{ "count": 142, "next": "...", "previous": null, "results": [...] }
```

**실패** — v1.0 의 필드 단위 에러 패턴을 승계·확장한다.

```jsonc
{
  "ok": false,
  "rule": "POSITION_LIMIT",       // 규칙 위반 시 (→ F-04)
  "field": "weight_pct",
  "message": "사람이 읽을 문장",
  "detail": { }                    // 화면 보조 정보
}
```

### 2.3 상태 코드

| 코드 | 사용 |
|---|---|
| 200 | 조회·수정 성공 |
| 201 | 생성 성공 |
| 400 | 검증 실패 · **규칙 위반** |
| 401 | 미인증 |
| 403 | 권한 없음 |
| 404 | 없음 (내부 잡 토큰 불일치도 404) |
| 409 | 상태 충돌 (이미 참가함 · 이미 체결됨) |
| 429 | 사용량 초과 (rate limit · AI 쿼터) |
| 503 | **외부 시세 불가** (v1.0 의 판단 승계 — 가짜 가격으로 주문시키지 않음) |

### 2.4 시각 표기

- 저장은 UTC, **응답은 KST ISO 8601** (`2026-08-11T14:23:11+09:00`)
- 날짜만 필요한 곳은 `YYYY-MM-DD` (KST 기준일)

### 2.5 금액·수량

| 항목 | 형식 |
|---|---|
| 원화 | 정수 (원 단위) |
| 주식 수량 | 정수 |
| **코인 수량** | **문자열** (소수점 8자리 — 부동소수 오차 방지) |
| 비율 | 소수 2자리 |

---

## 3. 엔드포인트 전체 목록

### 3.1 계정 · 인증 → [A-01](A-01-계정-인증.md)

| 메서드 | 경로 | 인증 |
|---|---|---|
| POST | `/api/auth/register` | — |
| POST | `/api/auth/login` | — |
| POST | `/api/auth/logout` | ✅ |
| GET | `/api/auth/me` | — |
| GET/PATCH | `/api/account/profile` | ✅ |
| GET | `/api/accounts` | ✅ | 내 계좌 4종 |
| GET | `/api/accounts/<id>/snapshot` | ✅ | **계좌 스냅샷 통합** |
| POST | `/api/accounts/<id>/reset` | ✅ | 연습 계좌만 |
| GET/POST | `/api/account/api-keys` | ✅ |
| DELETE | `/api/account/api-keys/<id>` | ✅ |

### 3.2 대회 → [A-02](A-02-대회.md)

| 메서드 | 경로 | 인증 |
|---|---|---|
| GET | `/api/contests` | — |
| GET | `/api/contests/<id>` | — |
| POST | `/api/contests/<id>/join` | ✅ |
| DELETE | `/api/contests/<id>/join` | ✅ | 참가 포기 |
| GET | `/api/contests/<id>/me` | ✅ | 내 현황 |
| GET | `/api/contests/<id>/guidelines` | ✅ | 한도·회전율 현황 |
| GET | `/api/contests/<id>/rankings` | — |
| GET | `/api/contests/<id>/participants/<pid>` | — | 포트폴리오 공개 |
| GET | `/api/contests/<id>/top-picks` | — |
| GET | `/api/contests/<id>/analysis` | — | 산점도 데이터 |
| GET | `/api/contests/<id>/result` | — | 종료 후 |
| GET | `/api/contests/<id>/universe` | ✅ | 거래 가능 종목 |
| GET | `/api/contests/<id>/sectors` | ✅ | 섹터 비중 |

### 3.3 주문 · 체결 → [A-03](A-03-주문-체결.md)

| 메서드 | 경로 | 인증 |
|---|---|---|
| GET | `/api/orders` | ✅ | 통합 이력 (필터·페이지네이션) |
| POST | `/api/orders` | ✅ | 주문 |
| GET | `/api/orders/<id>` | ✅ |
| POST | `/api/orders/<id>/cancel` | ✅ |
| POST | `/api/orders/preview` | ✅ | **주문 미리보기** (비용·한도 검증) ★ |
| GET | `/api/orders/export` | ✅ | CSV 스트리밍 |
| GET | `/api/positions` | ✅ |

### 3.4 시장 데이터 → [A-04](A-04-시장데이터.md)

| 메서드 | 경로 | 인증 |
|---|---|---|
| GET | `/api/market/stocks` | ✅ | 종목 목록·검색 |
| GET | `/api/market/stocks/<symbol>` | ✅ | 종목 상세 (규칙 판정 속성 포함) |
| GET | `/api/market/quotes` | ✅ | 배치 시세 |
| GET | `/api/market/orderbook/<symbol>` | ✅ | **호가 10단계** (대회) |
| GET | `/api/market/chart/<symbol>` | ✅ |
| GET | `/api/market/indices` | ✅ | KOSPI·KOSDAQ |
| GET | `/api/market/movers` | ✅ | 상승·하락 TOP |
| GET | `/api/market/rankings/market-cap` | ✅ | 시총 랭킹 |
| GET | `/api/market/crypto/markets` | ✅ |
| GET | `/api/market/crypto/<code>` | ✅ |
| GET | `/api/market/crypto/<code>/domestic` | ✅ | 국내 4사 비교 |
| GET | `/api/market/crypto/rankings` | ✅ | **KRW 거래대금 기준** |
| GET | `/api/market/alternatives` | ✅ |
| GET | `/api/market/alternatives/<symbol>/chart` | ✅ |
| GET | `/api/market/news/krx` | ✅ |
| GET | `/api/market/calendar` | ✅ | 영업일 |

### 3.5 AI · RAG → [A-05](A-05-AI-RAG.md)

| 메서드 | 경로 | 인증 |
|---|---|---|
| POST | `/api/ai/analyze` | ✅ **+ 쿼터** | SSE 스트리밍 |
| GET | `/api/ai/usage` | ✅ | 남은 횟수 |
| POST | `/api/knowledge/search` | ✅ |
| GET | `/api/knowledge/documents` | ✅ |
| POST | `/api/knowledge/documents` | ✅ | 승인 대기로 저장 |
| PATCH/DELETE | `/api/knowledge/documents/<id>` | ✅ | 본인/운영자 |
| GET | `/api/knowledge/stats` | ✅ |

### 3.6 학습 · 도구

| 메서드 | 경로 | 인증 |
|---|---|---|
| GET | `/api/learn/progress` | ✅ |
| POST | `/api/learn/progress/<lesson_key>` | ✅ |
| POST | `/api/tools/web-scraper/fetch` | ✅ **+ 쿼터** |
| GET/POST | `/api/tools/sheets` | ✅ |
| GET | `/api/watchlist` · POST · DELETE | ✅ |

### 3.7 Open API (외부) → [A-06](A-06-OpenAPI-v1.md)

| 메서드 | 경로 |
|---|---|
| GET | `/openapi/v1/stocks` |
| GET | `/openapi/v1/quote/<symbol>` |
| GET | `/openapi/v1/account` |
| GET | `/openapi/v1/positions` |
| GET/POST | `/openapi/v1/orders` |
| DELETE | `/openapi/v1/orders/<id>` |
| GET | `/openapi/v1/contests/<id>/ranking` |

### 3.8 내부 잡 (pg_cron 전용)

| 메서드 | 경로 |
|---|---|
| POST | `/internal/jobs/poll-quotes` |
| POST | `/internal/jobs/poll-orderbook` |
| POST | `/internal/jobs/match-pending-orders` |
| POST | `/internal/jobs/snapshot-intraday` |
| POST | `/internal/jobs/open-market` |
| POST | `/internal/jobs/close-market` |
| POST | `/internal/jobs/settle-daily` |
| POST | `/internal/jobs/settle-weekly` |
| POST | `/internal/jobs/settle-contest` |
| POST | `/internal/jobs/sync-stock-master` |
| POST | `/internal/jobs/sync-upbit-markets` |
| POST | `/internal/jobs/sync-crypto-rank` |
| POST | `/internal/jobs/sync-krx-news` |
| POST | `/internal/jobs/cleanup` |

**`X-Internal-Token` 불일치 시 404.** 403 을 주면 경로 존재가 드러난다.

---

## 4. 인증 요약 (결함 D-6 해소)

> v1.0 은 **AI 분석 · RAG 4종 · AI Sheet 가 인증 없이 열려 있었다.**
> Claude 비용이 발생하는 엔드포인트와 지식 추가가 누구에게나 열려 있었다.

**v2.0 원칙: 기본은 인증 필수. 공개는 예외로 명시한다.**

| 공개 (인증 불필요) | 이유 |
|---|---|
| `POST /api/auth/register` · `login` | 가입·로그인 |
| `GET /api/auth/me` | 로그인 상태 확인 |
| `GET /api/contests` · `<id>` · `rankings` · `top-picks` · `result` | 대회 홍보·관전 |

**그 외 전부 인증 필수.** 특히 AI·RAG·웹수집기는 **인증 + 사용량 제한** 이중 통제.

---

## 5. drf-spectacular

| 산출물 | 경로 |
|---|---|
| OpenAPI 3 스키마 | `/api/schema/` |
| Swagger UI | `/api/docs/` |
| ReDoc | `/api/redoc/` |

**v1.0 의 수기 `/openapi.html` 을 대체한다.** 코드와 문서가 어긋나지 않는다.

> 강사님 필수요소 "API 명세 HTML" 을 자동으로 충족한다.

---

## 6. 관련 문서

- HTMX 프래그먼트 목록 → [ui/01-HTMX-부분갱신-규약](../../ui/version2.0/01-HTMX-부분갱신-규약.md) 3장
- 에러 형식 상세 → [F-04](../../features/version2.0/F-04-대회-규칙엔진.md) 4.2
