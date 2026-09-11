# A-06. Open API v1 (외부 봇용)

> **문서 버전** v2.0 · **관련 기능** [F-13](../../features/version2.0/F-13-OpenAPI.md)
> **성격** 참가자가 **자기 봇으로 매매**하는 공개 인터페이스. 내부 `/api/*` 와 분리한다

---

## 1. 인증

```http
Authorization: Bearer eduapi_live_XXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX
```

| 항목 | 값 |
|---|---|
| 키 형식 | `eduapi_live_<token_urlsafe(32)>` |
| 저장 | DB 에는 `sha256(raw)` 만 |
| 폐기 | soft delete (`is_active=False`) |
| **계좌** | **키에 계좌가 고정된다.** 요청에서 계좌를 지정하지 않는다 ★ |

### 1.1 왜 키에 계좌를 고정하는가

v2.0 은 계좌가 4개 이상이다. 요청마다 계좌를 지정하게 하면
**실수로 대회 계좌에 연습용 봇이 주문하는 사고**가 난다.
키를 발급할 때 계좌를 정하고, 그 키는 그 계좌에만 주문할 수 있다.

**대회 종료 시 해당 대회 계좌의 키는 자동 비활성화된다.**

---

## 2. Rate Limit ★

| 항목 | 값 |
|---|---|
| 한도 | **키당 분당 60회** (v1.0 승계) |
| 저장소 | **Postgres `RateLimitCounter`** — v1.0 의 프로세스 메모리를 대체 |
| 윈도우 | 고정 1분 |

### 2.1 응답 헤더

```http
X-RateLimit-Limit: 60
X-RateLimit-Remaining: 42
X-RateLimit-Reset: 1786800000
```

초과 시 **429**:

```jsonc
{ "ok": false, "rule": "RATE_LIMITED",
  "message": "분당 60회 한도를 초과했습니다.",
  "detail": { "retry_after_sec": 18 } }
```

> **왜 Postgres 인가** — Vercel 서버리스에서 프로세스 메모리 카운터는
> 인스턴스마다 따로 세어 **한도가 사실상 무력화**된다(v1.0 결함 D-9).
> → [02-확정사항-8건](../../features/version2.0/02-확정사항-8건.md) 8번

---

## 3. 엔드포인트

### 3.1 조회

| 메서드 | 경로 | 설명 |
|---|---|---|
| GET | `/openapi/v1/stocks` | 종목 목록 (max 100) |
| GET | `/openapi/v1/quote/<symbol>` | 시세 |
| GET | `/openapi/v1/orderbook/<symbol>` | **호가 10단계** ★ 신규 (대회 계좌 키만) |
| GET | `/openapi/v1/account` | 계좌 스냅샷 |
| GET | `/openapi/v1/positions` | 보유 |
| GET | `/openapi/v1/orders` | 주문 이력 (max 200) |
| GET | `/openapi/v1/contests/<id>/ranking` | **순위** ★ 신규 |

### 3.2 주문

| 메서드 | 경로 |
|---|---|
| POST | `/openapi/v1/orders` |
| DELETE | `/openapi/v1/orders/<id>` ★ 신규 (취소) |

요청·응답은 [A-03](A-03-주문-체결.md)과 동일한 계약을 쓴다.
**`account_id` 는 보내지 않는다** (키에 고정).

### 3.3 대회 규칙은 우회할 수 없다 ★

대회 계좌 키로 낸 주문도 **[F-04](../../features/version2.0/F-04-대회-규칙엔진.md)
규칙 검증을 동일하게 거친다.**

```jsonc
// 400
{ "ok": false, "rule": "POSITION_LIMIT",
  "message": "삼성전자 편입 비중이 42.1% 가 되어 한도 40% 를 초과합니다.",
  "detail": { "max_additional_krw": 15230000 } }
```

**봇이 이 응답을 읽고 스스로 조정할 수 있도록** `detail` 을 충실히 준다.

---

## 4. 주문 경로 라벨

Open API 로 낸 주문은 `source="OPENAPI"` 로 기록된다.
거래이력·랭킹에서 **수동 주문과 구분**된다 (v1.0 설계 승계).

---

## 5. 자동매매 정책

| 경로 | 자동 실행 |
|---|---|
| **Open API** | ✅ 허용. 이것이 자동매매의 정식 경로다 |
| Pine 전략 화면 | ✕ 수동만 (→ [F-12](../../features/version2.0/F-12-Pine-전략거래.md) 4장) |

대회 안내문에 명시한다:
> "Pine 전략은 수동 실행만 가능합니다. 자동매매가 필요하면 Open API 를 이용하십시오."

---

## 6. 문서화

수기 HTML 대신 **drf-spectacular 자동 생성**.

| 산출물 | 경로 |
|---|---|
| Swagger UI | `/api/docs/` |
| ReDoc | `/api/redoc/` |
| 스키마 | `/api/schema/` |

**퀵스타트 예제**(파이썬·curl)는 `/learn/guides/` 에 별도 문서로 둔다.
자동 생성 문서는 레퍼런스, 가이드는 튜토리얼로 역할을 나눈다.

---

## 7. 관련 문서

- 키 관리 화면 → [ui/U-04](../../ui/version2.0/U-04-학습-도구화면군.md) 7장
- 주문 계약 → [A-03](A-03-주문-체결.md)
