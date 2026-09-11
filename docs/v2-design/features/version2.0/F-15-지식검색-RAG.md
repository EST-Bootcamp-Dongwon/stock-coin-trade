# F-15. 지식검색 (RAG · pgvector)

> **문서 버전** v2.0 · **분류** 기능=대체 · 구현=수정포팅
> **v1.0 대응** 1-9 RAG (`qdrant_service.py` 338줄)
> **확정** 저장소를 **Qdrant → Supabase `pgvector`** 로 대체

---

## 1. 왜 바꾸는가

| | v1.0 (Qdrant) | v2.0 (pgvector) |
|---|---|---|
| 저장소 | Qdrant, **기본값 `:memory:`** | Supabase Postgres |
| 영속성 | **재기동 시 사용자 추가 문서 소실** (결함 D-8) | 영속 |
| 인프라 | 벡터 DB 별도 운영 | 이미 쓰는 DB 안 |
| 비용 | 별도 | 0 |

이미 Supabase Postgres 를 쓰기로 확정했으므로 **벡터 DB 를 따로 운영할 이유가 없다.**

---

## 2. 임베딩

| 항목 | v1.0 | v2.0 |
|---|---|---|
| 모델 | `paraphrase-multilingual-MiniLM-L12-v2` (384차원, fastembed) | **동일 유지** |
| 실행 위치 | Flask 프로세스 | Django (서버리스 콜드스타트 이슈 → 아래) |

### 2.1 서버리스에서의 임베딩 모델 ★

fastembed 는 모델 파일(수십~수백 MB)을 로드한다.
**서버리스 함수에서 매 콜드스타트마다 로드하면 응답이 크게 느려진다.**

대응 옵션:

| 옵션 | 판단 |
|---|---|
| **문서 임베딩은 배치로만** | **채택.** 지식 추가는 pg_cron 잡이 처리 → 실시간 부담 없음 |
| 검색 쿼리 임베딩 | 짧은 문장이라 부담이 작다. 그래도 콜드스타트 시 지연이 생김 |
| 대안: Supabase Edge Function 에서 임베딩 | v2.1 검토 |

**1차는 이렇게 간다** — 문서 추가는 비동기(배치), 검색 쿼리 임베딩은 동기.
검색이 느리면 v2.1 에서 Edge Function 으로 옮긴다.

---

## 3. 시드 지식 21건 (v1.0 승계)

카테고리 6종. **코드 하드코딩 → DB 시드 마이그레이션**으로 옮긴다.

| 카테고리 | 건수 | 내용 |
|---|---|---|
| `technical_analysis` | 5 | RSI · MACD · 볼린저 · 이동평균 · 거래량 |
| `market_structure` | 4 | KOSPI · KOSDAQ 구조 · 수급 · 세금 |
| `sector_analysis` | 5 | 반도체 · 2차전지 · 바이오 · NAVER · 삼성전자 |
| `investment_strategy` | 4 | 분산투자 · 하락장 · 배당 · 투자심리 |
| `risk_management` | 1 | 손절 원칙 |
| `fundamental_analysis` | 2 | PER · PBR · 공시 |

---

## 4. 기능

### 4.1 지식 검색 탭 (v1.0 승계)

시맨틱 검색 결과를 **유사도 점수 색상**과 함께 표시.

| 유사도 | 색 |
|---|---|
| ≥ 75% | 녹색 |
| ≥ 55% | 주황 |
| 그 외 | 회색 |

### 4.2 데이터셋 탭 (v1.0 승계 + 확장)

| 기능 | v1.0 | v2.0 |
|---|---|---|
| 컬렉션 통계 (문서 수·모델명) | ✅ | ✅ |
| 문서 목록 | ✅ | ✅ + 검색·필터 |
| 지식 추가 (제목·카테고리·본문 2000자) | ✅ | ✅ |
| **지식 수정·삭제** | ✕ | **신규** — v1.0 은 추가만 가능했다 |
| 소유자 표시 | ✕ | **신규** — 누가 추가했는지 |

### 4.3 배치 스크립트 (v1.0 승계)

`data_root_to_jsonl.py` → `jsonl_to_qdrant.py` (1200자 청크 · 120자 오버랩)

**청크 로직은 그대로 옮기고** 적재 대상만 pgvector 로 바꾼다.
Django management command 로 만든다 (`python manage.py ingest_knowledge <path>`).

---

## 5. 인증 (결함 D-6 해소) ★

> v1.0 은 Qdrant 4종 API 가 **인증 없이 열려 있었다.**
> **문서 추가(`POST /add`)까지 인증 없이 가능**했다 — 누구나 지식 베이스를 오염시킬 수 있었다.

v2.0:

| 동작 | 권한 |
|---|---|
| 검색 | 로그인 필수 |
| 통계·목록 조회 | 로그인 필수 |
| **지식 추가** | 로그인 필수 + **운영자 검토 대기 상태로 저장** |
| 지식 수정·삭제 | 본인 것 또는 운영자 |
| 시드 지식 수정·삭제 | 운영자만 |

**검토 대기 상태**(`is_approved = False`)인 문서는 검색에 나오지 않는다.
운영자가 Admin 에서 승인하면 활성화된다. 오염 방지선이다.

---

## 6. 스키마

```sql
-- 개념
CREATE TABLE knowledge_document (
  id           bigserial PRIMARY KEY,
  title        text NOT NULL,
  category     text NOT NULL,
  content      text NOT NULL,
  embedding    vector(384),          -- pgvector
  source       text,                 -- SEED / USER / BATCH
  created_by   bigint REFERENCES member(id),
  is_approved  boolean DEFAULT false,
  created_at   timestamptz DEFAULT now()
);

CREATE INDEX ON knowledge_document
  USING hnsw (embedding vector_cosine_ops);
```

- **HNSW 인덱스**를 쓴다. 문서 수가 적어도 붙여두면 나중에 늘어나도 안전하다
- 코사인 유사도로 검색 (v1.0 과 동일한 기준)

---

## 7. 관련 문서

- AI 분석에서의 사용 → [F-14 AI 분석](F-14-AI분석.md)
- 학습 콘텐츠와의 관계 → [F-17 학습 콘텐츠](F-17-학습콘텐츠.md)
