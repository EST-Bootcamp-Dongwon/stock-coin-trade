# F-13. Open API (외부 연동)

> **문서 버전** v2.0 · **분류** 기능=살림 · 구현=수정포팅
> **v1.0 대응** 1-7 OpenAPI (`openapi.py` · `api_keys.py`)

---

## 1. 성격

참가자가 **자기 봇으로 매매**할 수 있게 하는 기능이다.
대회 도메인과 궁합이 매우 좋다 — [F-12 Pine](F-12-Pine-전략거래.md)의 자동 실행을
막는 대신 **여기로 유도**한다.

---

## 2. 승계하는 것 (v1.0 설계가 좋다)

| 항목 | 처리 |
|---|---|
| 키 형식 `eduapi_live_<token_urlsafe(32)>` | 유지 |
| **평문은 발급 시 1회만 반환**, DB 에는 `sha256(raw)` 만 저장 | 유지 |
| 목록에는 `key_prefix`(앞 16자)만 표시 | 유지 |
| 인증 `Authorization: Bearer <key>` | 유지 |
| 폐기는 soft delete (`is_active = False`) | 유지 |
| `last_used_at` 갱신 | 유지 |
| 주문에 `source="OPENAPI"` 라벨 | 유지 |

---

## 3. 반드시 바꿔야 하는 것 — Rate Limit ★

### 3.1 v1.0 의 구조적 문제

```
dict[api_key_id] → [timestamp...]  슬라이딩 윈도우 + threading.Lock
```

**프로세스 메모리다.** 재기동·다중 워커에서 초기화된다.
Vercel 서버리스로 가면 **인스턴스마다 자기 카운터를 갖게 되어 사실상 무력화**된다.
분당 60회 제한이 인스턴스 5개면 분당 300회가 된다.

### 3.2 v2.0 — Postgres 기반

```
RateLimitCounter(api_key, window_start, count)
   UNIQUE(api_key, window_start)

호출 시:
  INSERT INTO rate_limit_counter (api_key, window_start, count)
  VALUES (:key, date_trunc('minute', now()), 1)
  ON CONFLICT (api_key, window_start)
  DO UPDATE SET count = rate_limit_counter.count + 1
  RETURNING count;
  -- count > 60 이면 429
```

- **원자적 upsert 한 방**으로 카운트와 검사를 동시에 한다. 잠금이 필요 없다
- 고정 윈도우(fixed window)라 슬라이딩보다 단순하다. 경계에서 최대 2배까지 허용될 수 있지만
  교육용 서비스에서는 충분하다
- 지난 윈도우 행은 `cleanup` 잡이 지운다 (→ [F-20](F-20-스케줄러.md))

> **캐시 저장소 결정과 같은 이유다.** → [02-확정사항-8건](02-확정사항-8건.md) 8번

---

## 4. v2.0 확장

### 4.1 계좌 지정 필수 ★

v1.0 은 회원당 계좌가 하나뿐이라 지정이 필요 없었다.
v2.0 은 계좌가 여러 개이므로 **어느 계좌로 주문할지 지정해야 한다.**

| 방식 | |
|---|---|
| 키 발급 시 계좌 고정 | `ApiKey.account` — **채택.** 실수로 다른 계좌에 주문하는 사고를 원천 차단 |
| 요청마다 계좌 지정 | 유연하지만 실수 위험 |

**대회 계좌용 키는 대회가 끝나면 자동 비활성화**한다.

### 4.2 자산군 확장

v1.0 은 **주식만** 주문 가능했다. v2.0 은 계좌의 자산군에 따라 결정된다.

| 계좌 | 주문 가능 |
|---|---|
| `PRACTICE_STOCK` · `CONTEST` | 주식 |
| `PRACTICE_CRYPTO` | 코인 |
| `PRACTICE_ALT` | 대체자산 |

### 4.3 대회 규칙 적용

대회 계좌 키로 낸 주문도 **[F-04](F-04-대회-규칙엔진.md) 규칙 검증을 동일하게 거친다.**
API 라고 규칙을 우회할 수 없다. 위반 시 4.2절의 상세 에러 형식을 그대로 반환한다.

---

## 5. 엔드포인트

| 메서드 | 경로 | v1.0 | v2.0 |
|---|---|---|---|
| GET | `/openapi/v1/stocks` | ✅ | 유지 (max 100) |
| GET | `/openapi/v1/quote/<symbol>` | ✅ | 유지 |
| GET | `/openapi/v1/account` | ✅ | 유지 (키에 묶인 계좌) |
| GET | `/openapi/v1/positions` | ✅ | 유지 |
| POST | `/openapi/v1/orders` | ✅ | 유지 + 대회 규칙 검증 |
| GET | `/openapi/v1/orders` | ✅ | 유지 (max 200) |
| GET | `/openapi/v1/contests/<id>/ranking` | — | **신규** — 봇이 순위를 참고할 수 있게 |
| DELETE | `/openapi/v1/orders/<id>` | — | **신규** — 미체결 주문 취소 |

---

## 6. 문서화 — drf-spectacular ★

v1.0 은 `/openapi.html` 을 **손으로 써서** 유지했다. 코드가 바뀌면 문서가 낡는다.

v2.0 은 **DRF + drf-spectacular** 로 OpenAPI 3 스키마를 자동 생성한다.

| 산출물 | 경로 |
|---|---|
| 스키마 (YAML/JSON) | `/api/schema/` |
| Swagger UI | `/api/docs/` |
| ReDoc | `/api/redoc/` |

> **이것이 Django + DRF 를 택한 실익 중 하나다.**
> 강사님 필수요소 "API 명세 HTML" 을 자동으로 충족한다.

> **Django 관점** — FastAPI 는 `/docs` 가 기본 제공이라 신경 쓸 게 없었다.
> DRF 는 기본으로는 스키마를 안 만든다. `drf-spectacular` 를 설치하고
> `SPECTACULAR_SETTINGS` 를 잡아야 같은 결과가 나온다.
> 대신 **serializer 를 기준으로 스키마가 나오므로** serializer 를 성실히 쓰면
> 문서 품질이 FastAPI 보다 오히려 좋아진다.

---

## 7. 키 관리 화면

`/account/api-keys/` — v1.0 화면 승계

| 기능 | 비고 |
|---|---|
| 키 목록 (prefix · 계좌 · 생성일 · 마지막 사용 · 상태) | |
| 키 발급 | **계좌 선택 필수** ← 신규 |
| **평문 1회 표시** | 복사 버튼 + "다시 볼 수 없습니다" 경고 |
| 키 폐기 | soft delete |
| **사용량 표시** | 최근 1시간 호출 수 ← 신규 |

---

## 8. 관련 문서

- 주문 처리 → [F-03](F-03-주문-체결엔진.md)
- 대회 규칙 → [F-04](F-04-대회-규칙엔진.md)
- API 상세 → [api/A-06-OpenAPI-v1](../../api/version2.0/A-06-OpenAPI-v1.md)
