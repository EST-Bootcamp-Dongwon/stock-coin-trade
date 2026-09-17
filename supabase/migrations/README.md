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
| `20260917053500` | **읽기 전용 `workspace_verify_passcode`** — `verify` 를 쓰기 경로에서 뗀다 |
| `20260917053600` | **조 생성은 `create_team` 만** — 부분 유니크 인덱스 · `append` 거절 · `create_team` 두 곳 수정 |

⚠️ **DB 에 적용된 SQL 은 주석이 축약돼 있다.** 설계 근거(왜 이렇게 했는지)는 여기
파일 쪽이 자세하다. 로직은 같다.

## 🔴 다시 밟기 쉬운 함정 여덟

1. **`at` 은 `text` 다.** `timestamptz` 로 바꾸면 `event_id`(= `at` 문자열을 포함한
   해시)와 어긋나 `parse_event` 가 원장 전체를 거부한다
2. **`pg_column_size` 는 CHECK 에 못 쓴다** (STABLE). `octet_length(payload::text)` 를 쓴다
3. **`revoke ... from anon` 만으로 함수가 닫히지 않는다.** `PUBLIC` 기본 EXECUTE 가
   남는다 → `from public, anon, authenticated`
4. **테이블마다 REVOKE 해도 `migrate` 한 번에 되돌아간다.**
   `alter default privileges` 를 함께 해야 한다
5. **`workspace_append` 는 빈 배열을 passcode 검사 *앞에서* 0 으로 돌려보낸다.**
   즉 **빈 append 는 검증에 쓸 수 없다**(2026-09-12 · V41).
   🔴 **그래서 `20260917053500` 이 `workspace_verify_passcode` 를 따로 만들었다.**
   ⚠️ 이 자리에는 2026-09-17 까지 *"참가 검증은 그 조의 `team.created` 를 그대로 다시
   보내서 한다"* 고 적혀 있었다. **그 방식은 끝났다** — `20260917053600` 이 `append` 에서
   `team.created` 를 무조건 거절하므로 옛 probe 는 튕긴다(ADR-SC-0011 ⑭).
   🔒 그 빈 배열 검사를 passcode 검사 **뒤로 옮기고 싶어지거든 멈춰라** — 그렇게 하면
   옛 함수가 남아 있는 동안 `verify` 가 **아무 passcode 로나 true** 가 된다(fail-open).
   🔒 `SupabaseStore.verify` 는 **원장을 먼저 읽고** 읽히는 `team.created` 가 없으면
   RPC 를 부르지 않는다(ADR-SC-0011 ⑬). "RPC 하나면 되는데" 로 줄이지 마라 —
   시크릿만 남은 조에서 참가가 열리고 `fold` 에는 그 조가 없다

6. 🔴 **쓰기는 passcode 뿐이라 마스터 경로가 없다** — 화면의 마스터 보관·복구가
   Supabase 에서는 통하지 않는다(V42 · ADR-SC-0011 ⑪). 앱을 이쪽으로 돌리기 전에
   결정이 필요하다
7. 🔴 **새 마이그레이션이 함수를 `create or replace` 하면, 그 함수를 검사하던 SQL 대조
   테스트가 죽은 파일을 읽는다.** 실제로 그럴 뻔했다 — `test_거부_문장이_마이그레이션과_같다`
   가 `20260912095328` 한 파일을 하드코딩하고 있었다. 지금은 `workspace_test._live_function`
   이 마이그레이션들을 **버전 순으로 훑어 마지막 정의**를 검사한다. 함수를 갈아끼울 때
   그 헬퍼를 쓰는지 확인한다

8. ⚠️ **`supabase_admin` 이 소유한 객체의 기본 ACL 은 아직 열려 있다**(2026-09-17 실측 —
   테이블 `anon=arwdDxtm` · 함수 `anon=X`). 함정 4 가 껐지만 `postgres` 소유분에 한해서다.
   마이그레이션을 `postgres` 로 돌리는 한 새 객체는 닫힌 채 태어난다

## 🔴 GitHub 연동(자동 적용)을 쓰지 않는다 — 2026-09-12

Supabase → Settings → Integrations → GitHub 는 **연결된 저장소의 `supabase/migrations/`
를 프로덕션 DB 에 자동 적용**한다(`Deploy to production`). 켜지 않는다. 이유가 넷이다 —

1. **절대 제약 6** — CI/CD 를 필수 경로에 두지 않는다. 검증은 로컬에서 동일하게 돌아야 한다
2. 🔴 **이 저장소는 main 직커밋이다**(AGENTS.md 3절 · PR 을 만들지 않는다). 그러면
   **커밋 = 즉시 프로덕션 DB 변경**이고 사이에 사람이 보는 자리가 없다. PR 게이트를
   전제한 기능을 PR 없는 흐름에 붙이는 것이다
3. 🔴 연동 후보로 뜨는 `devlee328288/stock-coin-trade` 는 **GitLab push-mirror**
   (force push 로 동작 · AGENTS.md 2장). 정본이 아니고, 언제 갱신될지를 우리가 정하지 않는다
4. 🔴 Free 플랜은 **preview branch 가 없다**(Pro 필요). 되돌릴 자리가 없는데,
   원장 테이블은 append-only 트리거라 **되돌리기가 특히 비싸다** — 소유자도 못 지운다

🔒 **지금 방식이 이 프로젝트에서는 기능이다** — 한 번에 하나 적용하고, `anon` 역할로
   트랜잭션 안에서 11항목을 돌리고, 롤백한다(아래 "검증"). 파일명을 DB 버전과 맞춰
   두었으므로(위) 나중에 Supabase CLI 를 붙여도 이미 적용된 것을 다시 돌리지 않는다.

⚠️ 실측(2026-09-12 `list_migrations`): DB 에 **5건**이 기록돼 있다 — 우리 4건과
   `20260813012735_enable_pgcron_and_vector`. **마지막 것은 저장소에 파일이 없다**
   (확장 활성화 · 이 세션 전부터 있었다). 자동 적용을 켠다면 이 불일치를 먼저 봐야 한다.

## 클라이언트는 하나다 — `sector/workspace/store.py::SupabaseStore`

`requests` + PostgREST 다(의존 증가 0 · ADR-SC-0011 ⑧). 🔒 **SQL 과 문자열로 묶여
있는 자리가 하나 있다** — `workspace_append` 가 passcode 를 거부할 때 내는 문장
(`store.REJECTED_MESSAGE`). 갈라지면 클라이언트가 **거부를 통신 오류로 오인**한다.
`workspace_test.test_거부_문장이_마이그레이션과_같다` 가 둘을 묶는다 — 🔒 파일 하나가
아니라 `_live_function` 으로 **살아 있는 정의**(마지막 `create or replace`)를 읽는다.

## 검증

`anon` 역할로 트랜잭션 안에서 11항목을 돌리고 **롤백**한다 (append-only 트리거
때문에 테스트 행은 나중에 지울 수 없다 — 애초에 커밋하지 않는다).
항목과 결과는 ADR-SC-0011 "적용" 절에 있다.

🔒 **advisor 의 `rls_enabled_no_policy`·`*_security_definer_function_executable` 은
의도다.** 정책 0개가 곧 전면 차단이고, RPC 는 쓰기 관문이라 anon 이 불러야 한다.
