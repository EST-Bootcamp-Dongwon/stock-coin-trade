# ADR-SC-0011 — 팀 원장을 Supabase 로 옮기고, 쓰기를 passcode 에 묶는다

- **상태**: 채택
- **날짜**: 2026-09-12
- **관련**: [ADR-SC-0010](0010-두-배포-공존과-쓰기-상태-분리.md) **④⑥ 을 실행한다** ·
  [ADR-SC-0006](0006-krx-데이터-제3자-제공-금지.md) · [ADR-SC-0007](0007-값을-지어내지-않는다.md)
- **개정하지 않는 것**: ADR-SC-0010 의 나머지 결정은 그대로다

## 맥락

ADR-SC-0010 ⑥ 이 순서를 고정했다 — **스키마 → RLS 정책 → 마이그레이션 → 데이터.**
그 첫 두 칸을 채우는 문서다.

설계에 앞서 DB 와 코어를 실측했다. **두 가지가 문서와 달랐다.**

### 재확인한 것 (2026-09-12 · 전부 1차 출처)

| # | 사실 | 어떻게 확인했나 |
|---|---|---|
| **X1** | 🔴🔴 **W12 는 과소평가였다.** ADR-SC-0010 은 *"53개 전부 RLS 가 꺼져 있다. 지금은 0행이라 샐 것이 없다"* 고 적었는데 그것은 **읽기 관점**이다. 실제 GRANT 는 `anon` 에게 53개 전부 **SELECT·INSERT·UPDATE·DELETE·TRUNCATE** 다. 0행이어도 **쓰기가 열려 있다** | `information_schema.role_table_grants` 집계 |
| **X2** | ✅ 53개의 소유자는 전부 `postgres` 이고 `postgres` 는 **`rolbypassrls = true`** 다. `anon` 은 `false` | `pg_tables` · `pg_roles` |
| **X3** | ✅ 루트 `requirements.txt` 는 **4줄**이다 — `streamlit` · `requests` · `PyYAML` · `huggingface_hub`. DB 드라이버도 `supabase-py` 도 없다 | 파일 직접 확인 |
| **X4** | ✅ 원장은 **이미 이벤트 소싱**이다 — `events.py`(이벤트 생성·검증) · `fold.py`(이벤트→상태 순수 함수) · `store.py`(`EventStore` Protocol · `LocalStore`/`HubStore` 두 구현) | 소스 |
| **X5** | 🔴 **`event_id` 는 `at` 문자열을 포함한 내용의 SHA-256 이다**(`events._event_id`). 그리고 `parse_event` 가 읽을 때마다 id 를 **재계산해 대조**한다 | 소스 |
| **X6** | 🔴 `team.created` 의 **payload 에 `passcode_hash` 가 들어 있다.** HF private dataset 이라 토큰 없이는 못 읽었기에 성립하던 구조다 | `events.team_created` |
| **X7** | 🔴 화면이 **앱에서** 검증한다 — `teams.py:114` 의 `auth.verify_passcode(passcode, team.passcode_hash)`. 즉 `fold` 결과에 해시가 실려 있어야 돌아간다 | 소스 |
| **X8** | ✅ 확장은 이미 있다(`pgcrypto` · `vector` · `pg_cron` …). 기존 마이그레이션은 1건 | `pg_extension` · `list_migrations` |
| **X9** | 🔴🔴 **`public` 스키마의 default ACL 이 `anon` 에게 테이블 `arwdDxtm`(ALL)·함수 `X`(EXECUTE) 를 준다** (주인이 `postgres`·`supabase_admin` **둘**). **X1 의 원인이 이것**이고, 테이블마다 REVOKE 만 하면 다음 `migrate` 한 번에 되돌아간다 | `pg_default_acl` (적용 중 발견) |
| **X10** | 🔴 **`pg_column_size` 는 STABLE**(`provolatile='s'`)이라 CHECK 제약이 거부한다. `octet_length` 와 `jsonb_out` 은 IMMUTABLE | `pg_proc` (적용 전 발견 — 그래서 실패하지 않았다) |

## 결정

### ① v2.0 유산 53개를 **먼저** 잠근다

X1 은 "원장을 넣기 전에 해결" 이 아니라 **지금 실재하는 위험**이다. 둘 때문이다 —

1. 아무나 행을 밀어 넣어 Free 티어 **DB 500MB** 를 소진시킬 수 있다
2. `external_token` · `api_key` 는 Django 가 Vercel 에서 실제로 쓰이는 순간
   **KIS `appkey` 가 들어갈 자리**다. 그때 닫는 것이 아니라 **비어 있는 지금** 닫는다

조치는 세 겹이다 — **RLS 켜기 + anon/authenticated REVOKE + default privileges REVOKE.**
세 번째가 없으면 **Django 가 `migrate` 를 한 번 돌리는 것만으로 전부 무의미해진다**
(새 테이블이 다시 anon ALL 로 태어난다).

🔒 **정책은 하나도 두지 않는다. 그리고 그래도 앱은 죽지 않는다.** ADR-SC-0010 ⑥ 의
경고(*"정책 없이 RLS 만 켜면 앱이 죽는다"*)는 **anon 으로 붙는 경로**에만 해당한다.
X2 대로 Django 는 `postgres`(BYPASSRLS)로 붙으므로 RLS 를 통째로 우회한다. 여기서
막히는 것은 PostgREST 의 `anon`·`authenticated` 뿐이고, 그 둘은 v2.0 스키마를 쓸
일이 없다. **정책을 두지 않는 것이 곧 정책이다** — "이 테이블들은 REST 로 열지 않는다".

### ② 원장은 **테이블 하나**다 — 정규화하지 않는다

조·참가·확정을 각각 테이블로 쪼개고 싶어진다. 그러면 `fold.py` 를 버리게 된다 —
그것이 이미 "이벤트 → 현재 상태" 를 하는 **검증된 순수 함수**이고 테스트가 거기 붙어 있다.

ADR-SC-0010 ② 가 렌더러 둘에 대해 말한 것 — *"점수·서술을 두 번 구현하지 않는다"* —
이 **원장에도 그대로 적용된다.** `workspace_event` 하나에 이벤트 1건 = 행 1개로 담고,
`EventStore` 계약(`append` · `read_all`)에 구현을 하나 더 붙인다. 화면은 바뀌지 않는다.

### ③ `at` 은 `timestamptz` 가 아니라 **`text`** 다

X5 가 강제한다. `timestamptz` 로 저장하면 왕복에서 표기가 바뀔 수 있고
(`+00` vs `+00:00` · 마이크로초 절삭), 그 순간 `parse_event` 의 id 대조가 어긋나
**원장 전체가 "손으로 고쳐졌다" 는 예외**를 낸다.

정렬은 잃지 않는다 — `_AT_RE` 가 형식을 한 가지로 고정하므로 **사전순 = 시간순**이다.
`bas_dd` 를 문자열로 둔 것과 같은 이유다(AGENTS.md 4장). 🔒 형식 검사는 DB 쪽
`CHECK` 로도 건다 — 두 곳이 같은 정규식을 갖는다.

### ④ 🔴 passcode 해시를 **원장 밖으로** 뺀다

X6 은 HF private 위에서만 성립하던 구조다. Supabase 는 원장 읽기가 공개이므로(⑥)
**그대로 옮기면 해시가 공개된다.**

- 해시는 `workspace_team_secret` 으로 옮긴다 — anon GRANT 없음 · RLS 켜짐 · **정책 0개**.
  REST 로 이 테이블은 존재하지 않는 것과 같다
- 원장 쪽에 `check (not (payload ? 'passcode_hash'))` 를 건다.
  🔒 **규칙을 사람의 기억이 아니라 DB 가 지킨다** — `events.team_created` 가 `scrypt$`
  아닌 값을 거부하는 것과 같은 정신이다

**분리는 설계적으로도 낫다.** passcode 는 *사건이 아니라 현재 상태*다. 이벤트에 박아
두었기 때문에 지금 구조는 **passcode 를 바꿀 방법이 없다**(`team.created` 에만 있으니까).

### ⑤ 쓰기 경로는 **RPC 하나뿐**이다

저장소가 Public 이므로 **anon 키는 공개된다고 가정한다**(ADR-SC-0010 ⑥). 테이블에
INSERT 를 열면 키를 가진 누구나 남의 조 이름으로 확정·코멘트를 쓸 수 있다 —
**passcode 가 지키기로 한 바로 그것이 뚫린다.**

`SECURITY DEFINER` 함수 안에서 passcode 를 검증하면 쓰기 권한이 **키가 아니라
passcode 에** 걸린다. 그것이 원래 의도였다.

| RPC | 무엇 |
|---|---|
| `workspace_passcode_params(team_id)` | scrypt 파라미터 + salt. 🔒 **digest 는 주지 않는다** |
| `workspace_create_team(event, passcode_hash)` | 조 생성. 이벤트와 해시가 **한 트랜잭션** |
| `workspace_append(team_id, encoded, events)` | passcode 검증 후 append. 멱등 |
| `workspace_heartbeat(bas_dd, note)` | 7일 pause 해제용 게시 로그 |

🔴 **`service_role` 키를 앱에 두는 길은 택하지 않았다.** 그 키는 RLS 를 통째로
우회하고 53개 테이블 전부에 닿는다 — **HF 토큰을 `WRITE`/`READ` 로 가른 것과 같은
판단**이다(org 전체 쓰기 토큰이 앱용 칸에 들어가 있던 사고, AGENTS.md 3장).

🔒 **salt 를 주는 것은 약화가 아니다.** salt 는 비밀이 아니라 레인보우 테이블을 막는
장치다. salt 만으로는 오프라인 대입이 성립하지 않는다 — 맞는지 확인하려면 매번 이
DB 를 불러야 하므로 대입이 **온라인으로 묶이고**, RPC 안에서 횟수를 셀 수 있다.
`auth.py` 머리주석이 *"앱에 시도 횟수 제한을 걸 수단이 없다"* 고 적은 그 수단이
**Supabase 로 오면서 생긴다.** HF 방식(해시 통째 노출)보다 오히려 강하다.

### ⑥ 원장 **읽기는 공개다** — 숨기지 않고 적는다

`workspace_event` 의 SELECT 를 anon 에 연다. 두 렌더러 모두 anon 키로 붙으므로
**"anon 이 할 수 있는 것 = 앱이 할 수 있는 것"** 이고, 읽기를 RPC 뒤에 숨겨도 실질
경계는 생기지 않는다. 그리고 조별 확정 현황을 팀 전체가 보는 것이 이 도구의 목적이다.

🔴 **이것은 HF private 대비 실질적 변화다.** 따라서 —

- **코멘트에 비밀을 적지 않는다.** `auth.py` 의 위협 모델 표에 *"지키지 않는 것:
  원장 읽기"* 를 더한다
- 실제로 지키는 것은 하나다 — **passcode 해시는 어떤 경로로도 나가지 않는다**(④)

### ⑦ append-only 를 **트리거로** 강제한다

RLS 로 UPDATE·DELETE 정책을 주지 않으면 anon 은 이미 막힌다. 트리거를 더 거는 것은
**소유자(`postgres` · BYPASSRLS)까지** 막기 위해서다. 원장이 원장이려면 "실수로
지웠다" 가 가능해서는 안 된다. **HF 보다 강해지는 지점**이다 — 거기서는 토큰이 있으면
파일을 지울 수 있었다.

되돌리기는 반대 이벤트를 더하는 것이다(`sector.unconfirmed` · `team.restored`).
정말로 행을 지워야 하는 사고가 나면 트리거를 명시적으로 disable 해야 하고,
🔒 **그 한 줄을 누군가 타이핑하는 순간이 곧 "이건 정상 경로가 아니다" 라는 신호다.**

### ⑧ 클라이언트는 `requests` + PostgREST — **의존 증가 0**

X3 이 좌우한다. 루트 `requirements.txt` 는 Streamlit Cloud 가 설치하는 목록이고
메모리 한도(690MB~2.7GB)를 직접 깎는다. `supabase-py` 도 `psycopg` 도 넣지 않는다.

**PostgREST 는 그냥 REST API 다.** 이미 있는 `requests` 로 부른다 —
`python-dotenv` 를 넣지 않고 `.env` 를 직접 파싱한 것과 같은 판단이다
(`secret_access` 머리주석).

## 근거

**X1 하나가 이 세션의 순서를 바꿨다.** 원래 계획은 "원장 스키마를 만들고, 그 김에
53개도 본다" 였다. 재 보니 53개 쪽이 *지금* 열려 있는 문이었고 원장은 아직 존재하지도
않았다. **비어 있을 때 닫는 것이 가장 싸다.**

**X2 가 ADR-SC-0010 ⑥ 의 경고를 해제한다.** "정책 없이 RLS 만 켜면 앱이 죽는다" 는
일반론으로는 옳지만, 이 DB 에서는 Django 가 BYPASSRLS 역할로 붙어서 해당하지 않는다.
**일반론을 실측으로 대체한 자리**이고, 그래서 53개를 한 번에 잠글 수 있었다.

**X4~X7 은 "얼마나 옮길 것인가" 를 정했다.** 원장이 이미 이벤트 소싱이라 테이블
하나로 충분하고(②), 그러면 `fold` 와 골든 테스트가 그대로 산다. 반대로 X5·X6 은
**그냥 옮기면 깨지는 두 곳**을 짚었다 — 시각 표기와 passcode 위치다. 둘 다 원래
구조의 흠이 아니라 **HF 라는 저장소의 성질이 남긴 자국**이고, DB 로 오면서 풀린다.

**⑤ 는 "키가 새면 끝" 을 "passcode 가 새면 그 조만" 으로 바꾼다.** 공개 저장소에서
키의 기밀성에 기대는 설계는 언젠가 무너진다. 권한을 DB 안쪽 함수에 두면 키는
*어디에 붙을지*만 정하고 *무엇을 할지*는 정하지 못한다.

## 적용 — 2026-09-12

마이그레이션 **4건**(프로젝트 `sgbhrahtewojmicwmxxu`). SQL 정본은 `supabase/migrations/`.

| 순서 | 이름 | 무엇 |
|---|---|---|
| 1 | `lock_legacy_django_tables` | 53개 RLS on · anon REVOKE · **default privileges REVOKE**(X9) |
| 2 | `workspace_ledger` | 원장 3테이블 · RLS · 트리거 · RPC 4 |
| 3 | `workspace_revoke_trigger_function` | 트리거 함수의 **PUBLIC 기본 EXECUTE** 회수(X9 후반) |
| 4 | `workspace_trigger_fix_search_path` | 트리거 함수 `search_path = ''` |

🔒 3·4 가 따로 있는 이유를 적어 둔다 — **`from anon` 만으로는 함수가 닫히지 않는다.**
Postgres 는 함수 생성 시 `PUBLIC` 에 EXECUTE 를 기본 부여하고 `anon` 은 PUBLIC 의
일원이다. RPC 넷은 `from public, anon, authenticated` 로 적어 정확히 닫혔는데
**트리거 함수만 그 목록에서 빠져 있었다.** 실질 위험은 없었지만(`returns trigger` 는
일반 SQL 로 호출 불가) 권한 목록에 설명되지 않는 구멍을 남기지 않는다.

### 실동작 검증 — `anon` 역할로, 트랜잭션 안에서, 11항목

🔒 **롤백했다.** append-only 트리거 때문에 테스트 행을 나중에 지울 수 없어서,
지우는 대신 **애초에 커밋하지 않는** 길을 택했다. 검증 후 세 테이블 전부 0행이다.

| # | 시도 | 결과 |
|---|---|---|
| ① | anon 이 RPC 로 조 생성 | ✅ 성공 (의도) |
| ② | anon 이 원장에 **직접 INSERT** | ✅ 거부 — `permission denied` |
| ③ | anon 이 **passcode 해시 SELECT** | ✅ 거부 — `permission denied` |
| ④ | `passcode_params` 반환값 | ✅ `scrypt$16384$8$1$<salt>$` — **digest 없음** |
| ⑤ | 맞는 passcode 로 append | ✅ 1건 |
| ⑥ | 같은 이벤트 재전송 | ✅ **0건** (멱등) |
| ⑦ | 틀린 passcode | ✅ 거부 — 이유를 말하지 않는다 |
| ⑧ | 다른 조 이벤트 섞기 | ✅ 거부 |
| ⑨ | payload 에 `passcode_hash` | ✅ CHECK 거부 |
| ⑩ | **`postgres`(BYPASSRLS)가 DELETE** | ✅ **거부** — 트리거 |
| ⑪ | `at` 형식 위반(`공백`·`+00`) | ✅ CHECK 거부 |

⑩ 이 ⑦ 의 설계를 완성한다 — 쓰기는 passcode 가 가르고, **지우기는 아무도 못 한다.**

### advisor — `ERROR` 0건. 남는 것은 전부 의도다

⚠️ 🔒 **아래 둘을 "고쳐야 할 것" 으로 보고하지 않는다.**

| 남은 것 | 왜 의도인가 |
|---|---|
| `rls_enabled_no_policy` (INFO · 54) | **정책 0개가 곧 전면 차단**이다(①·④). advisor 는 "실수 아닌가" 를 묻는 것이고, 우리는 의도했다 |
| `anon/authenticated_security_definer_function_executable` (WARN · 각 4) | RPC 가 **쓰기 관문**이다(⑤). anon 이 못 부르면 앱이 아무것도 못 쓴다 |

## 결과

- ✅ `anon` 이 v2.0 53개 테이블에 **쓸 수 없다.** `migrate` 재실행에도 되돌아가지 않는다
- ✅ 원장 상태 계산이 한 곳(`fold.py`)에 남는다 — 두 렌더러가 같은 말을 한다
- ✅ append-only 가 **DB 수준에서** 지켜진다. 소유자도 못 지운다
- ✅ passcode 대입이 온라인으로 묶이고 횟수를 셀 수 있다
- ✅ 루트 의존성 증가 **0**
- ✅ **코어 변경 완료** (2026-09-12 · 같은 날 후속 세션 · 테스트 375 → 415건):
  - `events.team_created` 가 passcode 를 **받지 않는다.** payload 에 `passcode`
    를 품은 키가 오면 `make_event` 가 거부한다 — DB CHECK 와 같은 규칙이고
    **이쪽이 더 넓다**(넓은 쪽이 먼저 거부하므로 "앱이 만든 이벤트를 DB 가 거부"
    가 생기지 않는다)
  - `fold.Team.passcode_hash` 를 **없앴다.** 검증은 `EventStore.verify` 가 한다 —
    화면은 참·거짓만 받는다
  - `auth` 에 `params_of`(digest 를 뗀다) · `recompute_passcode`(저장된 salt 로
    재계산) · `credential_matches`(상수시간 · SQL `workspace_ct_eq` 와 같은 것)
    를 더했다. `hash_passcode(salt=)` 주석도 함께 고쳤다
  - `store` 에 `SupabaseStore` — `requests` + PostgREST. **루트 의존성 증가 0**
  - `auth` 위협 모델 표에 *"지키지 않는 것: 원장 읽기"* 를 더했다(⑥)
- ⚠️ **원장 읽기가 공개다.** `auth` 머리주석에 적었고 화면 문구는 M9 이후에 붙는다(⑥)
- ⚠️ 마이그레이션은 `supabase/migrations/` 에 **SQL 로 남는다.** Vercel 설정과 달리
  코드로 남길 수 있으므로 `deploy.md` 가 아니라 저장소에 둔다
- ⚠️ rate limit 수치(조 200개 · 시간당 20조 · 분당 60이벤트)는 **추정이다.**
  팀 7명 기준으로 넉넉하나, 실제로 걸리면 그때 근거를 갖고 올린다

## 코어를 맞추면서 드러난 것 — 2026-09-12 (같은 날 후속)

스키마를 코드에 실제로 물려 보니 **세 가지가 위 결정에서 안 보였다.** 둘은 설계로
풀렸고 하나는 **남는 결정**이다.

### ⑨ 읽기 전용 검증 RPC 가 없다 — 멱등성으로 푼다

`workspace_append` 는 **빈 배열을 passcode 검사 *앞에서*** 0건으로 돌려보낸다
(`if jsonb_array_length(p_events) = 0 then return 0`). 즉 빈 append 로는 passcode 를
확인할 수 없다. 그런데 참가 시점에는 *쓰지 않고* 확인해야 한다 — "참가했다" 고
말한 뒤 첫 확정에서 거부당하면 화면이 거짓말한 것이 된다.

**그 조의 `team.created` 를 그대로 다시 보낸다.** passcode 는 삽입 전에 검사되고,
`on conflict (event_id) do nothing` 이라 **0건 쓰인다**. 원장의 멱등성(이벤트 id 가
내용에서 나온다)을 **우회가 아니라 그대로** 쓴 것이다.

🔒 대안은 5번째 마이그레이션(`workspace_verify` RPC)이었다. 택하지 않은 이유 —
   이미 있는 성질로 되는 일에 DB 표면을 늘리지 않는다. 대신 `verify` 를
   **저장소 계약**으로 올려 세 구현이 각자 자연스럽게 답하게 했다(로컬·HF 는
   저장된 해시와 상수시간 비교, Supabase 는 위 방식).

### ⑩ `append` 로 조를 만들 수 없게 했다 — Supabase 가 이미 그렇다

`workspace_append` 는 그 조의 해시가 **이미 있어야** 통과한다. 따라서 새 조의
`team.created` 는 어떤 passcode 로도 들어가지 않는다 — Supabase 에서는 구조적으로
`create_team` 만이 조를 만든다. 로컬·HF 도 같이 막았다.

🔴 안 막으면 **passcode 없는 조**가 생긴다. 목록에는 보이면서 아무도 참가할 수
   없고, 같은 id 로 다시 만들 수도 없다 — 조용히 망가진 상태다.

### ⑪ 🔴 **마스터(개발자) 쓰기 경로가 DB 에 없다** — 남는 결정이다

⑤ 가 "쓰기는 passcode 를 통과한 RPC 하나뿐" 으로 정했는데, 화면에는 **마스터가
남의 조를 보관·복구하는 기능**이 있다(`teams.py` · `ADMIN_PASSCODE_HASH`). 마스터는
그 조의 passcode 를 갖고 있지 않고 **가질 수도 없다**(해시만 저장한다).

지금까지 이것이 되던 이유는 원장에 쓰기 관문이 아예 없었기 때문이고, 그 사실은
`_can_archive` 주석이 이미 적어 뒀다 — *"권한은 원장이 아니라 화면이 건다"*.

🔒 **이번 세션에서 우회하지 않았다.** `SupabaseStore.append` 는 자격증명이 없으면
   *무엇을 해야 하는지* 말하고 멈춘다. 로컬·HF 는 종전대로 쓴다(거기서는 실제
   경계가 파일·토큰이다). **앱을 Supabase 로 돌리기 전에** 셋 중 하나를 골라야 한다 —

| 선택 | 값 | 대가 |
|---|---|---|
| 마스터 RPC 를 더한다 | 기능 유지 | 🔴 `ADMIN_PASSCODE_HASH` 가 DB 로 가고, 그 해시 하나가 **모든 조**에 대한 쓰기 권한이 된다 |
| 보관·복구를 조 안으로 되돌린다 | DB 표면 0 · ⑤ 가 깨지지 않는다 | 잘못 만든 조를 개발자가 치울 수 없다 |
| 마스터가 그 조 passcode 를 입력한다 | 표면 0 | 개발자가 팀의 passcode 를 물어야 한다 — passcode 의 뜻이 흐려진다 |

⚠️ 세 번째는 "지키지 않는 것" 을 늘린다. 🔒 **두 번째가 기본값**이다 — 대회 기간에
   조는 7개뿐이고, 잘못 만든 조는 그 조원이 보관하면 된다.

✅ **닫혔다 — 2026-09-12. 두 번째를 택했다. → 아래 [⑫](#⑫-결정--⑪-을-닫는다-보관복구를-조-안으로-되돌린다-2026-09-12--m8)**
   🔒 이 절을 "남은 결정" 으로 읽지 마라.

---

## ⑫ 결정 — ⑪ 을 닫는다: **보관·복구를 조 안으로 되돌린다** (2026-09-12 · M8)

⑪ 이 남겨 둔 선택 셋 중 **두 번째**를 택했다. 앱을 Supabase 로 돌리기 전에
정해야 했던 마지막 하나다(V42).

### 왜 마스터 RPC 가 아닌가

| 선택 | 판정 |
|---|---|
| 마스터 RPC 를 더한다 | 🔴 **기각.** `ADMIN_PASSCODE_HASH` 가 DB 로 가고 **해시 하나가 모든 조에 대한 쓰기 권한**이 된다. 그것은 `service_role` 키를 앱에 두는 것과 같은 모양이다 — HF 토큰을 `WRITE`/`READ` 로 가르고(V29) `SupabaseStore` 가 `sb_secret_` 를 생성 시점에 거부하게 만든 바로 그 판단을, 이름만 바꿔 되돌리는 셈이다 |
| **보관·복구를 조 안으로** | ✅ **채택.** DB 표면 0 · ⑤("쓰기는 passcode 를 통과한 RPC 하나뿐")가 깨지지 않는다 |
| 마스터가 그 조 passcode 를 입력한다 | 🔴 기각. 개발자가 팀에게 passcode 를 물어야 하고, 그러면 passcode 의 뜻이 흐려진다 — "지키지 않는 것" 을 늘린다 |

🔒 **대가를 숨기지 않는다** — 잘못 만든 조를 개발자가 치울 수 없다. 대회 기간에
   조는 7개뿐이고, 그런 조는 **만든 사람이 보관**하면 된다. 값을 지불하는 쪽이
   "개발자의 편의" 이고 지키는 쪽이 "쓰기 권한의 단일 관문" 이라 맞바꿀 만하다.

### 무엇이 바뀌었나

- **마스터 개념을 v3.0 에서 걷어냈다.** `_render_master_gate` · `session.is_master` ·
  `auth.master_hash`/`is_master` 를 지웠다. 🔒 **권한 0 인 마스터를 남겨 두지
  않는다** — 남겨 두면 "여기 뭔가 있나" 만 남고, 언젠가 누가 왜 끊었는지 모른 채
  다시 잇는다. `ADMIN_PASSCODE_HASH` 는 이제 아무것도 열지 않으므로 시크릿에서
  지운다(`.env` · `.env.example`)
- **보관**은 만든 사람이 한다(`_can_archive`). 옛 주석 *"권한은 원장이 아니라
  화면이 건다"* 가 이제 거짓이다 — 원장이 passcode 로 가른다. 화면이 거는 것은
  **권한이 아니라 실수 방지**이고, 그렇게 고쳐 적었다
- **복구**는 «보관된 조» 칸에서 **그 조의 passcode** 로 한다. 🔒 목록은 누구에게나
  보인다 — 원장 읽기는 이미 공개고(⑥) 감추면 자기 조를 아무도 못 찾는다
- 🔒 **보관보다 복구를 쉽게 뒀다.** 보관은 만든 사람만, 복구는 passcode 만 묻는다.
  되돌린 조는 원장에 그대로 있던 것이고, 잘못 되돌려도 다시 보관하면 된다
- `_write_as_master` 를 지우고 `_write(store, event, credential)` 로 **자격증명을
  인자로** 받게 했다. 안에서 `session.credential()` 을 읽으면 *지금 참가 중인 조*
  의 것밖에 못 쓰는데, 보관된 조를 되돌릴 때는 **그 조의** 자격증명이 필요하다
- 참가와 복구가 `_enter_team` 이라는 **같은 꼬리**를 쓴다. 둘 다 passcode 를 방금
  증명한 직후다 — 갈라 두면 한쪽에서만 조원 기록이 빠진다

### 앱을 Supabase 로 돌렸다

`data.workspace_store()` 가 **Supabase → HF → 로컬** 순으로 고른다.

🔴 **Supabase 시크릿이 하나라도 있으면 폴백하지 않는다.** 형식이 틀렸거나 반쪽만
   채워졌으면 **그대로 던진다.** 조용히 HF 로 내려가면 ① 팀이 서로 다른 원장에
   쓰면서 같은 것을 본다고 믿게 되고 ② `service_role` 키를 앱 칸에 붙여넣은 사고가
   **침묵에 묻힌다**(`_require_anon_key` 가 모처럼 소리를 질러도 아무도 못 듣는다).
   **못 붙는 것과 잘못 붙는 것은 다르게 다뤄야 한다.**

🔴 옛 HF 원장을 **옮기지 않았다**(V43) — 3건 전부 같은 날 스모크 테스트다.

### 결과

- ✅ 테스트 **415 → 422건**. 마스터 테스트 5건을 지우고 되돌리기 4건 · 원장 배선 3건을 더했다
- ✅ 루트 의존성 증가 **0** (`requirements.txt` 4줄 그대로)
- ✅ `cd backend && .venv/bin/pytest` 골든 **23건** 통과 (동결 회귀)
- ✅ Supabase 원장 실접속 확인 — `sgbhrahtewojmicwmxxu.supabase.co` · **이벤트 0건**
  (스키마·RLS 는 서 있고 데이터는 아직 없다)
- ⚠️ 🔒 **`ADMIN_PASSCODE_HASH` 를 `.env` 에서 지우는 일이 남았다** — 코드가 읽지
  않으므로 해가 되지는 않으나, 아무것도 열지 않는 시크릿은 다음 사람을 헷갈리게 한다

---

## ⑬ 원장 한 줄이 읽기를 멈추지 않는다 (2026-09-14 · M8 #8.1)

### 무엇이 틀렸나

⑤ 는 *누가* 쓰는지를 passcode 로 막지만 *무엇을* 쓰는지는 막지 않는다(ADR-SC-0012 Y5).
`workspace_append` 는 `event_id` 를 다시 계산하지 않고, DB CHECK 는 `parse_event` 보다 좁다 —

| DB 는 받고 `parse_event` 는 거부하는 줄 | 왜 들어오나 |
|---|---|
| `event_id` 가 내용과 어긋난다 | RPC 가 id 를 재계산하지 않는다 |
| `payload` 가 객체가 아니다(`"문자열"`) | `jsonb_typeof` 검사가 없다 |
| `actor` 가 공백뿐이다 | CHECK 는 `length` 만 본다. `_make` 는 앞뒤 공백을 지운 뒤 id 를 계산한다 |

예전 `read_all` 은 그런 줄에서 **던졌다.** 조 페이지와 랭킹의 "우리 조" 가 **모든 조**에서 멈추고,
⑦ 의 트리거 때문에 **소유자도 그 줄을 못 지운다.** `verify` 도 그 조의 `team.created` 첫 행을
`parse_event` 에 넣었으므로, `at` 이 더 이른 가짜 한 줄이 그 조의 참가를 전부 막았다.

### 결정 — 읽는 쪽이 줄 단위로 건너뛰고 말한다 (사용자 결정 · 후보 ②)

- `EventStore.read_all()` 은 **`Ledger(events, rejected)`** 를 돌려준다. 세 구현이 같다.
- **내용이 틀린 줄**(`EventError` — JSON 이 아니다 · 칸이 틀렸다 · id 가 어긋난다)은 건너뛰고
  `RejectedRow(where, reason, team_id)` 로 담는다. 위치 80자 · 사유 300자로 자른다 —
  `event_id` 에는 DB 길이 제한이 없다.
- 🔒 **`fold.fold` 가 `Ledger` 를 그대로 받아** `anomalies` 맨 앞에 올린다. 목록만 돌려주면
  건너뛴 사실이 호출부에서 사라진다 — 둘을 한 값으로 묶은 이유다.
- 🔒 **닿지 못한 것은 여전히 던진다**(파일 읽기 · 네트워크 · 권한 · 목록 · 5만 건 상한).
  일부만 읽은 원장을 온전한 것처럼 보여주면 무엇이 빠졌는지 모른다.
- `verify` 는 **읽히는 `team.created` 행**을 probe 로 쓴다. probe 는 `on conflict` 에 걸리기만
  하면 되므로(⑨) 아무 행이나 된다. 읽히는 행이 없으면 `fold` 에도 그 조가 없다.
- 🔒 내용이 틀린 입력은 전부 `EventError` 다. 파일 원장에만 들어오는 두 경로(짝 없는 서로게이트 →
  `UnicodeEncodeError`, 2만 단 이상 중첩 → `RecursionError`)를 `EventError` 로 모았다. Supabase 는
  jsonb 입력이 서로게이트를 거부하고 16KB 상한이 중첩을 8000단으로 묶어 들어오지 않는다(V55 실측).

### 기각한 대안

| 대안 | 기각 이유 |
|---|---|
| ① RPC 에서 id 재계산 | plpgsql 로 `canonical_json`+sha256 을 재현해야 한다. jsonb 키 순서가 Python `sort_keys` 와 달라 직렬화를 손으로 짜야 하고, **두 구현이 영원히 같아야** 한다. id 선점(아래)까지 닫히지만 이번 범위를 넘는다 |
| ② + DB 형태 검사(`jsonb_typeof` · `btrim`) | 해시 불일치는 여전히 통과한다 — 읽는 쪽 방어가 어차피 필요하다 |
| `read_all()` 유지 + `read_ledger()` 추가 | 호출부가 엄격한 쪽을 고르면 다시 멈춘다 |

### 결과

- 테스트 **490 → 497건**(세 구현 대조 · 파일 전용 경로 · 알림 길이 · 못 읽은 파일은 던짐 ·
  Supabase GET 실패는 던짐 · 가짜 조 생성 줄 · 조 화면과 랭킹이 AppTest 로 선다) · backend 골든 23건
- 마이그레이션 0 · `requirements.txt` 증가 0

### 대가 — ② 가 닫지 않은 것

`세션-시작-프롬프트.md` 4.5 "남은 원장 위험" 에 적었다 — ① **id 선점**(RPC 가 `event_id`·`team_id`
짝을 안 본다) ② **`workspace_append` 가 `team.created` 를 받는다**(그 조의 이름 · 만든 사람을 바꿀 수
있다) ③ **양으로 멈추기**(조 생성 게이트 없음 · 조마다 분당 60건 → 약 42분에 5만 건 상한)
④ 랭킹 칸에는 알림이 없다.

---

## ⑭ 조 생성은 `create_team` 만 한다 — ⑨ 를 뒤집고 ⑩ 을 정정한다 (2026-09-17 · M8)

### 무엇이 틀렸나

⑩ 은 *"Supabase 에서는 구조적으로 `create_team` 만이 조를 만든다"* 고 적었다. **반만 맞았다.**
`workspace_append` 는 그 조의 해시가 이미 있어야 통과하므로 **새 조**는 못 만든다 — 그러나
**이미 있는 조**에 `team.created` 를 하나 더 쓰는 것은 막지 않았다. 막는 곳은 Python 저장소
(`store._reject_team_created`)뿐이었고, **RPC 는 앱을 건너뛴다.**

`fold` 는 조마다 *먼저 것*을 쓰고 뒤엣것은 이상으로만 알린다. 그러므로 그 조 passcode 를 가진
조원이 `at` 이 더 이른 **유효한** `team.created` 를 쓰면 조 이름 · `created_by` · `created_at`
기록이 통째로 바뀐다. 원장은 append-only 라 **지울 수도 없다.**

🔴 **피해 범위를 "그 조 하나" 로 적었던 것도 과소평가였다.** `workspace_create_team` 의 시간당
게이트는 `team.created` 를 **조 구분 없이 전역으로** 센다(`where kind = 'team.created' and
recorded_at > now() - interval '1 hour'`). 조원 하나가 자기 조에 20건을 밀어 넣으면 **한 시간
동안 아무도 새 조를 만들 수 없고**, 한 시간마다 반복하면 조 생성이 영구 차단된다.

🔒 반대로 이것은 **권한 상승이 아니다.** passcode 를 가진 사람은 이미 임의의 `actor` 로 확정 ·
코멘트 · 보관을 쓸 수 있고, 보관 버튼(`teams._can_archive`)은 스스로 *"권한이 아니라 실수 방지"*
라고 적고 있다. 여기서 막는 것은 **역사 다시쓰기**(원장 1행의 불변성)와 위 전역 차단이다.

### 결정 — 세 겹으로 막는다

| 겹 | 무엇 | 왜 이것만으로는 부족한가 |
|---|---|---|
| ① 구조 | 부분 유니크 인덱스 `(team_id) where kind = 'team.created'` | 위반 시 영문 제약 이름이 화면에 그대로 뜬다 |
| ② 규칙 | `workspace_append` 가 `team.created` 를 **무조건** 거절 | 함수는 갈아끼울 수 있다. 다음 RPC 가 또 연다 |
| ③ 탈결합 | 읽기 전용 `workspace_verify_passcode` RPC | ② 없이는 닫을 것이 안 닫힌다 |

- **①** 은 `fold` 가 이미 하던 가정을 코드 밖으로 꺼내 DB 가 들게 한다. 🔒 **지금이 비용 없이 걸
  수 있는 유일한 시점이다** — 2026-09-17 실측으로 `workspace_event` 0행 · `workspace_team_secret`
  0행이다. 조가 생긴 뒤에는 중복 확인이 형식이 아니게 된다.
- **②** 는 passcode 검사 **앞**에 둔다. 종류 오류가 "틀린 passcode" 와 같은 문장으로 묻히면
  원인을 찾을 수 없다.
- **③** 은 ② 의 부수 효과가 아니라 **그 자체로 더 낫다.** 옛 `verify` 는 `workspace_append` 의
  분당 60행 제한을 상속해서, 조가 한창 쓰는 중이면 *참가*가 오류로 떨어졌다. 쓰기와 검증이
  같은 한도를 나눠 쓸 이유가 없다.

곁들여 `workspace_create_team` 두 곳을 고쳤다 — **orphan 사전 검사**(원장에 생성 기록이 있는데
시크릿이 없는 조에서 ① 이 영문 오류를 내는 것을 막는다)와 **0건 삽입 시 실패**(옛 정의는
`on conflict do nothing` 뒤에 무조건 `return 1` 이라, id 선점 시 화면이 "조를 만들었다" 고
말하면서 `fold` 에는 그 조가 없었다).

### ⑨ 를 왜 뒤집나 — 우아함과 차단이 같은 한 줄을 두고 다툰다

⑨ 는 *"이미 있는 성질로 되는 일에 DB 표면을 늘리지 않는다"* 를 근거로 검증 RPC 를 택하지 않고,
`verify` 가 그 조의 `team.created` 를 되보내게 했다. 그 방식은 **append 가 `team.created` 를
받아 준다는 것에 의존한다** — 즉 ⑨ 의 우아함이 성립하려면 위 구멍이 열려 있어야 한다.
둘 다 가질 수 없다. 🔒 **⑨ 는 지우지 않는다.** 그때의 판단은 그때의 정보로 옳았고, 무엇이
달라져서 뒤집혔는지가 기록으로 남아야 한다.

### 🔴 `volatile` 이다 — 읽기 전용인데 왜

PostgREST 는 **IMMUTABLE·STABLE 함수를 GET 으로도 노출한다.** 검증 RPC 를 STABLE 로 선언하면
`GET /rest/v1/rpc/workspace_verify_passcode?p_team_id=…&p_encoded=…` 가 유효해지는데,
`p_encoded` 는 저장된 해시 **그 자체**이고 그것이 곧 그 조의 **영구 쓰기 자격증명**이다.
쿼리스트링은 게이트웨이 로그 · 프록시 · 브라우저 히스토리 · 화면 공유에 남고, **passcode 를
바꾸는 RPC 가 없으므로 회수할 수 없다.** 그래서 기본값인 `volatile` 을 명시해서 둔다.

### 기각한 대안

| 대안 | 기각 이유 |
|---|---|
| append 가 **아직 없는** `team.created` 만 거절(probe 는 통과) | 보안은 같지만 **DB 규칙이 Python 규칙과 달라진다.** "세 구현이 같은 답을 낸다" 는 규율에 따라 다음 사람이 Python 쪽을 완화할 길이 열리고, 파일·HF 원장에는 인덱스가 **없으므로** 거기서 실제로 두 번째 `team.created` 가 쓰인다 |
| 빈 배열 조기 반환을 passcode 검사 **뒤**로 한 줄 이동(③ 불필요) | 🔴 **fail-open 이다.** 앱을 먼저 배포하면 옛 함수가 빈 배열을 passcode 검사 *앞*에서 0 으로 돌려보내 `verify` 가 **아무 passcode 로나 true** 가 된다. 그 창 동안 모든 조에 참가·확정·보관이 열린다 |
| ①(RPC 에서 id 재계산)로 id 선점까지 함께 | ⑬ 에서 이미 기각했다. 이번 범위를 넘는다 |
| 틀린 passcode 시도 횟수 제한 | 🔴 **오늘 상한이 없다는 사실은 그대로 남는다**(passcode 검사가 rate limit 앞이고, 그 limit 은 *삽입된 행*을 센다). 다만 카운터 테이블은 append-only 원장 옆에 *지울 수 있는* 상태를 하나 더 만든다. 사용자 7명이라 이번에는 넣지 않는다 — **별도 결정으로 남긴다** |

### 🔒 적용 순서 — 어긋나도 **열리지는** 않는다

마이그레이션을 **둘로 나눈 이유가 이것이다.** 셋 다 수동이다.

1. `20260917053500_workspace_verify_passcode.sql` 적용 — 옛 앱은 계속 probe 로 돈다
2. 앱 배포 (`verify` 가 새 RPC 로) — 🔒 적용 직후 REST 로 한 번 찔러 본다.
   PostgREST 스키마 캐시가 늦으면 `PGRST202` + 404 다
3. `20260917053600_workspace_append_rejects_team_created.sql` 적용 —
   적용 전 **중복 확인 쿼리를 먼저** 돌린다(파일 머리주석)

🔴 **"어느 시점에도 참가가 깨지지 않는다" 고 쓰면 거짓이다.** 어긋남은 두 방향이고
피해가 다르다 — 🔒 **어느 쪽도 인증을 열지는 않는다**(그것이 이 설계를 고른 이유다).

| 어긋남 | 무슨 일이 나나 |
|---|---|
| **2 가 1 보다 먼저** (새 앱 + 옛 DB) | 없는 함수는 404 → `StoreError` → 화면이 "원장에 닿지 못해 참가를 확인할 수 없다". **참가만 막힌다** |
| 🔴 **3 이 2 보다 먼저** (옛 앱 + 새 DB) | **전 조 참가 차단.** 옛 `verify` 의 probe 가 `'조 생성은 append 로 하지 않는다'` 로 거절되는데 그 문장은 `REJECTED_MESSAGE` 가 **아니라서** `PasscodeRejected` 가 아니라 `StoreError` 로 오른다. 옛 `verify` 는 `PasscodeRejected` 만 잡는다 |
| 캐시 지연 (둘 다 새것) | 일시 전면 차단. 1단계 뒤 REST 로 확인하면 피한다 |

🔒 **둘을 한 자리에서 이어 붙이지 마라.** 3 은 2 를 배포해 확인한 **뒤**다.
`workspace_test.test_Supabase_검증이_실패에_열리지_않는다` 가 fail-closed 쪽을 못 박는다 —
주장만 적어 두면 다음 사람이 `except` 로 감싸 "확인할 수 없으니 통과" 로 바꾼다.

### 🔴 대가 — 조 복구 절차가 하나 닫힌다

orphan 검사(② ')는 여태 유일하게 **동작하던** 복구 절차를 막는다. 옛 동작은 이랬다 —
소유자가 `workspace_team_secret` 행을 지우고 사용자가 **같은 id** 로 다시 만들면,
내용이 같아 `event_id` 도 같으므로 `on conflict` 로 0건 쓰이고 시크릿만 새로 들어가
조가 되살아났다.

🔒 **대신 되는 길이 있고 그쪽이 원래 맞다** — 행을 *지우지 말고* `passcode_hash` 를
**UPDATE** 한다. append-only 트리거는 `workspace_event` 에만 걸려 있고
`workspace_team_secret` 에는 `updated_at` 칸이 이미 있다. 구문은 `20260917053600` 의
②' 주석에 적었고, 테이블 주석(`comment on table`)에도 같은 말을 박아 넣었다 —
🔴 그 주석이 *"검증은 `workspace_append` RPC 안에서만 일어난다"* 고 **거짓을** 적고
있었으므로 어차피 고쳐야 했다.

🔒 **이것은 소유자 콘솔 작업이고 앱에는 경로를 만들지 않는다.** 만들면 해시 하나가
모든 조를 여는 마스터가 되는 모양이 된다(⑫).

### ⚠️ 세 구현이 여기서 갈린다 — 알고 둔다

`create_team` 의 orphan 거절은 **Supabase 에만** 있다. `LocalStore`·`HubStore` 는
시크릿 파일만 없으면 통과하고 0 을 돌려준다. 🔒 파일·HF 원장에는 부분 유니크 인덱스가
없어서 막을 **구조**가 없고, 그 둘은 개발·옛 경로다. 팀이 쓰는 원장은 Supabase 다(⑬).

⚠️ `verify` 의 "읽히는 `team.created` 가 없으면 False" 도 Supabase 에만 있다 —
**이번에 생긴 격차가 아니라 ⑬ 부터 그랬다.** `test_두_구현이_자격증명에_같은_답을_낸다`
가 Supabase 를 넣지 않아 드러나지 않는다. 고칠 자리로 남긴다.

### 결과

- 테스트 **734 → 745건** · backend 골든 23건 · 마이그레이션 **2건** · `requirements.txt` 증가 0
- 적대적 리뷰 2회차(설계 · 구현). 구현 리뷰가 잡은 것 — `create_team` 의 새 성질에
  **SQL 앵커가 없어** 옛 정의로 되돌려도 테스트가 전부 통과했다 · `_live_function` 이
  `language sql` 함수에서 **다음 함수를 통째로 삼켰다** · 위 fail-closed 문장이 한 방향만
  다뤘다 · 테이블 주석과 `auth.py` 머리주석이 **거짓이 됐다** · `verify` 가 GET 과 RPC 에
  **다른 `team_id`** 를 보냈다. 전부 이 커밋에서 고쳤다
- 🔴 SQL 대조 테스트 둘이 `20260912095328` **한 파일을 하드코딩**하고 있었다. 새 파일이
  `create or replace` 하는 순간 **죽은 파일을 검사하며 통과**한다 — README 가 경고한 바로 그
  사고("갈라지면 거부를 통신 오류로 오인한다")가 그것을 막으라고 만든 테스트를 통과한 채로
  일어난다. `_live_function` 이 마이그레이션들을 버전 순으로 훑어 **마지막 정의**를 검사하게 고쳤다.
- `FakePostgrest` 가 세 규칙을 진짜와 같이 막는다. 🔒 **가짜가 진짜보다 관대하면**
  `_reject_team_created` 를 지워도 CI 가 통과한다.
