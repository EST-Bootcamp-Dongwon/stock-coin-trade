# `supabase/migrations/` — 팀 원장 DB 스키마

Supabase 프로젝트 **`stock-coin-trade`** (`sgbhrahtewojmicwmxxu` · ap-northeast-2 · PG 17.6).

설계 근거는 [ADR-SC-0011](../../docs/decisions/0011-팀-원장-supabase-스키마와-rls.md) ·
[ADR-SC-0010](../../docs/decisions/0010-두-배포-공존과-쓰기-상태-분리.md) ④⑥ 이다.

## 🔒 이 파일들이 정본이다

파일명의 타임스탬프는 **DB 에 기록된 마이그레이션 버전과 같다.** 어긋나면 나중에
Supabase CLI 를 붙였을 때 이미 적용된 것을 다시 돌린다.

| 버전 | 무엇 |
|---|---|
| `20260912095105` | v2.0 Django 유산 53개 잠금 — RLS on · anon REVOKE · **default privileges REVOKE** |
| `20260912095328` | 팀 원장 3테이블 · RLS · append-only 트리거 · RPC 4 |
| `20260912095552` | 트리거 함수의 **PUBLIC 기본 EXECUTE** 회수 |
| `20260912095631` | 트리거 함수 `search_path = ''` |

⚠️ **DB 에 적용된 SQL 은 주석이 축약돼 있다.** 설계 근거(왜 이렇게 했는지)는 여기
파일 쪽이 자세하다. 로직은 같다.

## 🔴 다시 밟기 쉬운 함정 넷

1. **`at` 은 `text` 다.** `timestamptz` 로 바꾸면 `event_id`(= `at` 문자열을 포함한
   해시)와 어긋나 `parse_event` 가 원장 전체를 거부한다
2. **`pg_column_size` 는 CHECK 에 못 쓴다** (STABLE). `octet_length(payload::text)` 를 쓴다
3. **`revoke ... from anon` 만으로 함수가 닫히지 않는다.** `PUBLIC` 기본 EXECUTE 가
   남는다 → `from public, anon, authenticated`
4. **테이블마다 REVOKE 해도 `migrate` 한 번에 되돌아간다.**
   `alter default privileges` 를 함께 해야 한다

## 검증

`anon` 역할로 트랜잭션 안에서 11항목을 돌리고 **롤백**한다 (append-only 트리거
때문에 테스트 행은 나중에 지울 수 없다 — 애초에 커밋하지 않는다).
항목과 결과는 ADR-SC-0011 "적용" 절에 있다.

🔒 **advisor 의 `rls_enabled_no_policy`·`*_security_definer_function_executable` 은
의도다.** 정책 0개가 곧 전면 차단이고, RPC 는 쓰기 관문이라 anon 이 불러야 한다.
