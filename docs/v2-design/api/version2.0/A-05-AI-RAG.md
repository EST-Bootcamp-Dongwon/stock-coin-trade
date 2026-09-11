# A-05. AI · RAG · 도구 API

> **문서 버전** v2.0 · **관련 기능** [F-14](../../features/version2.0/F-14-AI분석.md) · [F-15](../../features/version2.0/F-15-지식검색-RAG.md) · [F-17](../../features/version2.0/F-17-학습콘텐츠.md) · [F-18](../../features/version2.0/F-18-웹데이터-수집기.md)

---

## 1. 인증 원칙 ★ (결함 D-6 해소)

> **v1.0 은 이 문서의 엔드포인트 대부분이 인증 없이 열려 있었다.**
> Claude 비용이 발생하는 `/api/ai/analyze`, 문서를 추가할 수 있는 Qdrant `/add`,
> 외부 URL 을 대신 호출하는 `/api/ai-sheet/crawl` 전부.

**v2.0 은 전부 로그인 필수 + 사용량 제한.**

---

## 2. AI 분석

| 메서드 | 경로 | 인증 |
|---|---|---|
| POST | `/api/ai/analyze` | ✅ + 쿼터 |
| GET | `/api/ai/usage` | ✅ |

### 2.1 요청

```jsonc
{
  "scope": "MY_PORTFOLIO",     // MARKET_STOCK | MARKET_CRYPTO | MARKET_ALL
                               // | MY_PORTFOLIO | CONTEST
  "account_id": 12,            // MY_PORTFOLIO · CONTEST 일 때
  "contest_id": 3,             // CONTEST 일 때
  "rag_top_k": 5,              // 3 | 5 | 8 (v1.0 승계)
  "model": "haiku"             // haiku | sonnet
}
```

**`context` 를 클라이언트가 보내지 않는다.** v1.0 은 DOM 을 긁어 보냈다.
v2.0 은 서버가 `scope` 를 보고 직접 조회해 조립한다.

### 2.2 응답 — SSE 스트리밍

```
event: sources
data: {"documents":[{"title":"RSI 지표","similarity":0.82,"category":"technical_analysis"}]}

event: delta
data: {"text":"현재 포트폴리오는 "}

event: delta
data: {"text":"IT 섹터에 47% 가 집중되어 "}

event: done
data: {"tokens_in":1420,"tokens_out":380,"usage":{"used":4,"limit":20},
       "notice":"모의투자 교육용 분석이며 개인별 투자 권유가 아닙니다."}
```

| 이벤트 | 내용 |
|---|---|
| `sources` | RAG 검색 근거 (유사도 포함) — 접이식 UI 에 먼저 표시 |
| `delta` | 본문 조각 |
| `done` | 사용량 · 고지 |
| `error` | 오류 (쿼터 초과·API 키 미설정·타임아웃) |

### 2.3 쿼터

| 상황 | 처리 |
|---|---|
| 한도 초과 | **429** + `{"used":20,"limit":20,"reset_at":"내일 00:00 KST"}` |
| API 키 미설정 | **200 + `delta` 로 안내 메시지 스트리밍** (v1.0 판단 승계 — 화면이 깨지지 않는다) |
| 타임아웃 근접 | 정상 종료 + "응답이 길어 여기서 마칩니다" |
| 실패 | **쿼터를 차감하지 않는다** |

---

## 3. 지식 검색 (pgvector)

| 메서드 | 경로 | 인증 |
|---|---|---|
| POST | `/api/knowledge/search` | ✅ |
| GET | `/api/knowledge/documents?category=&q=&page=` | ✅ |
| POST | `/api/knowledge/documents` | ✅ (승인 대기로 저장) |
| PATCH · DELETE | `/api/knowledge/documents/<id>` | ✅ 본인/운영자 |
| GET | `/api/knowledge/stats` | ✅ |

### 3.1 검색 응답

```jsonc
{ "results": [
    { "id": 12, "title": "RSI 지표 해석", "category": "technical_analysis",
      "content": "...", "similarity": 0.823, "source": "SEED" }
  ],
  "model": "paraphrase-multilingual-MiniLM-L12-v2", "dimension": 384 }
```

유사도 색상 기준 (v1.0 승계): ≥0.75 녹색 / ≥0.55 주황 / 그 외 회색

### 3.2 문서 추가 — 승인 대기 ★

```jsonc
// 201
{ "id": 88, "is_approved": false,
  "message": "운영자 검토 후 검색에 반영됩니다." }
```

**승인 전에는 검색 결과에 나오지 않는다.** 지식 베이스 오염 방지선이다.

---

## 4. 학습 진행률

| 메서드 | 경로 |
|---|---|
| GET | `/api/learn/progress` |
| POST | `/api/learn/progress/<lesson_key>` |

```jsonc
// GET 응답
{ "total_points": 32, "goal": 100,
  "groups": [ { "name": "매크로 분석", "completed": 2, "total": 2 } ],
  "last_lesson": "technical-trend" }
```

**HTMX 에서 디바운스 1초로 POST** 한다 (→ [ui/U-04](../../ui/version2.0/U-04-학습-도구화면군.md) 3.2).

---

## 5. 웹 데이터 수집기

| 메서드 | 경로 | 인증 |
|---|---|---|
| POST | `/api/tools/web-scraper/fetch` | ✅ + 쿼터(1일 50회) |
| GET · POST | `/api/tools/sheets` | ✅ |
| GET · DELETE | `/api/tools/sheets/<id>` | ✅ |

### 5.1 SSRF 차단 응답

```jsonc
// 400
{ "ok": false, "rule": "BLOCKED_URL",
  "message": "사설망·루프백 주소는 조회할 수 없습니다.",
  "detail": { "reason": "PRIVATE_ADDRESS" } }
```

| `reason` | 조건 |
|---|---|
| `INVALID_SCHEME` | http/https 아님 |
| `INVALID_PORT` | 80/443 아님 |
| `CREDENTIALS_IN_URL` | user/password 포함 |
| `PRIVATE_ADDRESS` | A/AAAA 레코드에 사설·루프백 포함 |
| `REDIRECT_BLOCKED` | **리디렉션 후 최종 URL 이 차단 대상** |
| `NOT_HTML` | Content-Type 에 html 없음 (415) |
| `TOO_LARGE` | 2MB 초과 (413) |

→ 방어 상세는 [F-18](../../features/version2.0/F-18-웹데이터-수집기.md) 3장

---

## 6. 관련 문서

- AI 컨텍스트 조립 → [F-14](../../features/version2.0/F-14-AI분석.md) 2.1
- 우측 패널 UI → [ui/U-01](../../ui/version2.0/U-01-공통레이아웃-GNB.md) 3장
