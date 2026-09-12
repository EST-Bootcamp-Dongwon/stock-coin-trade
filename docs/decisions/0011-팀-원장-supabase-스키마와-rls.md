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
- ⚠️ 🔴 **코어 변경이 남는다**(다음 세션 · #7.6 이전):
  - `events.team_created` 가 payload 에 `passcode_hash` 를 넣지 않게 한다
  - `fold.Team.passcode_hash` 를 화면이 더는 쓰지 않게 한다(X7) — 검증이 DB 로 갔다
  - `auth` 에 저장된 파라미터·salt 로 재계산하는 함수를 더한다.
    🔒 지금 `hash_passcode(salt=)` 주석은 *"운영 경로에서는 절대 주지 않는다"* 인데,
    새 용도는 정당하다(저장된 salt 로 재계산). **주석을 함께 고친다** — 안 고치면
    그 주석이 거짓이 된다
  - `store` 에 `SupabaseStore` 를 더한다(`requests` + PostgREST)
- ⚠️ **원장 읽기가 공개다.** 화면과 `auth` 위협 모델에 명시해야 한다(⑥)
- ⚠️ 마이그레이션은 `supabase/migrations/` 에 **SQL 로 남는다.** Vercel 설정과 달리
  코드로 남길 수 있으므로 `deploy.md` 가 아니라 저장소에 둔다
- ⚠️ rate limit 수치(조 200개 · 시간당 20조 · 분당 60이벤트)는 **추정이다.**
  팀 7명 기준으로 넉넉하나, 실제로 걸리면 그때 근거를 갖고 올린다
