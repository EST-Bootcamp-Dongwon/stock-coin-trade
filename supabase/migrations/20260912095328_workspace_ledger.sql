-- 팀 원장(조 · 참가 · 확정 · 코멘트)을 Supabase 로 — 스키마와 RLS 를 한 벌로
--
-- 근거: ADR-SC-0010 ④⑥ · ADR-SC-0011
--
-- ## 🔒 테이블이 왜 하나인가 — 정규화하지 않는다
--
-- 조·참가·확정을 각각 테이블로 쪼개고 싶어진다. 그러면 `sector/workspace/fold.py`
-- 를 버리게 된다 — 그것이 이미 "이벤트 → 현재 상태" 를 하는 **검증된 순수 함수**이고,
-- 골든 테스트가 거기 붙어 있다. ADR-SC-0010 ② 가 렌더러 둘에 대해 말한 것
-- ("점수·서술을 두 번 구현하지 않는다")이 원장에도 그대로 적용된다.
--
-- 그래서 `workspace_event` 하나다. 이벤트 1건 = 행 1개. `EventStore` 계약
-- (append · read_all)에 Supabase 구현을 하나 더 붙이면 화면은 바뀌지 않는다.
--
-- 부수 효과가 오히려 크다 — **append-only 가 DB 수준에서 강제된다.** HF 는 토큰이
-- 있으면 파일을 지울 수 있었지만, 여기서는 트리거가 UPDATE·DELETE 를 거부한다.
--
-- ## 🔴 `at` 이 timestamptz 가 아니라 text 인 이유
--
-- 함정이다. `event_id` 는 **`at` 문자열을 포함한 내용의 해시**다
-- (`events._event_id`). timestamptz 로 저장하면 왕복에서 표기가 바뀔 수 있고
-- (`+00` vs `+00:00`, 마이크로초 절삭), 그 순간 `parse_event` 의 id 대조가
-- 어긋나 원장 전체가 "손으로 고쳐졌다" 는 예외를 낸다.
--
-- 정렬은 잃지 않는다 — `_AT_RE` 가 형식을 한 가지로 고정하므로 **사전순 = 시간순**
-- 이다. `bas_dd` 를 문자열로 둔 것과 똑같은 이유다 (AGENTS.md 4장).
--
-- ## 🔴 passcode 해시는 원장에 들어오지 않는다
--
-- 지금 코어는 `team.created` 의 payload 에 `passcode_hash` 를 담는다. HF private
-- dataset 이라 토큰 없이는 못 읽었기 때문에 성립하던 구조다. Supabase 에서는
-- 원장 읽기가 공개이므로(아래) **그대로 옮기면 해시가 공개된다.**
--
-- 그래서 해시를 `workspace_team_secret` 으로 분리하고, 원장 쪽에는
-- `check (not (payload ? 'passcode_hash'))` 를 건다. 🔒 규칙을 사람의 기억이
-- 아니라 DB 가 지킨다 — `events.team_created` 가 `scrypt$` 아닌 값을 거부하는
-- 것과 같은 정신이다.
--
-- 분리는 설계적으로도 낫다. passcode 는 **사건이 아니라 현재 상태**다. 이벤트에
-- 박아 두면 지금처럼 **passcode 를 바꿀 방법이 없다** (`team.created` 에만 있으니까).
--
-- ## 🔒 읽기는 공개다 — 숨기지 않고 적는다
--
-- `workspace_event` 의 SELECT 를 anon 에 연다. 두 렌더러 모두 anon 키로 붙으므로
-- "anon 이 할 수 있는 것 = 앱이 할 수 있는 것" 이고, 읽기를 RPC 뒤에 숨겨도
-- 실질 경계는 생기지 않는다. 그리고 조별 확정 현황을 팀 전체가 보는 것이 이 도구의
-- 목적이다.
--
-- 🔴 따라서 **코멘트에 비밀을 적지 않는다.** `auth.py` 의 위협 모델 표에 이 줄을
--    더해야 한다 (passcode 가 지키지 않는 것: 원장 읽기).
--    실제로 지키는 것은 하나 — passcode 해시는 어떤 경로로도 나가지 않는다.

-- ── ① 원장 ──────────────────────────────────────────────────────────────────

create table if not exists public.workspace_event (
  event_id    text primary key,
  kind        text        not null,
  team_id     text        not null,
  actor       text        not null,
  at          text        not null,          -- 🔒 timestamptz 가 아니다 (머리주석)
  payload     jsonb       not null default '{}'::jsonb,
  -- 🔒 `at`(사건이 일어난 시각)과 다르다. 이것은 **DB 가 받은 시각**이고 감사용이다.
  --    둘이 크게 벌어져 있으면 뒤늦게 밀어 넣은 이벤트라는 뜻이다.
  recorded_at timestamptz not null default now(),

  constraint workspace_event_kind_known check (kind in (
    'team.created', 'member.joined', 'sector.confirmed', 'sector.unconfirmed',
    'comment.posted', 'team.archived', 'team.restored'
  )),
  -- `events._ID_RE` 와 같은 규칙. 두 곳이 갈라지면 앱이 만든 이벤트를 DB 가 거부한다
  constraint workspace_event_team_id_shape check (team_id ~ '^[a-z][a-z0-9_]{1,39}$'),
  -- `events._AT_RE` 와 같은 규칙. 이것이 곧 "사전순 = 시간순" 의 근거다
  constraint workspace_event_at_shape check (
    at ~ '^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?\+00:00$'
  ),
  constraint workspace_event_actor_len check (
    length(actor) between 1 and 40
  ),
  -- 🔴 passcode 는 원장에 들어오지 않는다 (머리주석)
  constraint workspace_event_no_passcode check (not (payload ? 'passcode_hash')),
  -- 🔒 Free 티어는 DB 500MB 다. 한 건이 커지는 길을 막는다.
  --    `events._MAX_TEXT` 는 4000자 — 한글 UTF-8 최대 12KB 에 구조를 더해 16KB.
  -- 🔒 `pg_column_size` 를 쓰지 않는다 — STABLE 이라 CHECK 제약이 거부한다
  --    (실측 2026-09-12: provolatile='s'). `octet_length(jsonb::text)` 는 둘 다
  --    IMMUTABLE 이고, 재 보려는 것이 "저장량" 이라 오히려 이쪽이 뜻에 맞는다
  constraint workspace_event_payload_size check (
    octet_length(payload::text) <= 16384
  )
);

-- 조회는 둘뿐이다 — 전체를 시간순으로 접거나(`fold`), 한 조의 이력을 보거나.
create index if not exists workspace_event_at_idx on public.workspace_event (at);
create index if not exists workspace_event_team_at_idx on public.workspace_event (team_id, at);

comment on table public.workspace_event is
  '팀 원장 — append-only 이벤트. 상태는 sector/workspace/fold.py 가 접어서 만든다. 🔒 파생값·사람의 결정만 (ADR-SC-0006 제약 10)';

-- ## 🔒 append-only 를 DB 가 지킨다
--
-- RLS 로 UPDATE·DELETE 정책을 주지 않으면 anon 은 이미 막힌다. 트리거를 더 거는
-- 것은 **소유자(postgres · BYPASSRLS)까지** 막기 위해서다. 원장이 원장이려면
-- "실수로 지웠다" 가 가능해서는 안 된다.
--
-- 되돌리기는 반대 이벤트를 더하는 것이다 (`sector.unconfirmed` · `team.restored`).
-- 정말로 행을 지워야 하는 사고가 나면 트리거를 명시적으로 disable 해야 하고,
-- 🔒 그 한 줄을 누군가 타이핑하는 순간이 곧 "이건 정상 경로가 아니다" 라는 신호다.
create or replace function public.workspace_event_append_only()
returns trigger
language plpgsql
as $$
begin
  raise exception
    '원장은 append-only 다 — % 를 할 수 없다. 되돌리려면 반대 이벤트를 더한다 '
    '(sector.unconfirmed · team.restored)', tg_op;
end $$;

drop trigger if exists workspace_event_no_change on public.workspace_event;
create trigger workspace_event_no_change
  before update or delete on public.workspace_event
  for each row execute function public.workspace_event_append_only();

-- ── ② passcode — 원장 밖 ────────────────────────────────────────────────────

create table if not exists public.workspace_team_secret (
  team_id       text primary key,
  -- `auth.hash_passcode` 의 인코딩: scrypt$n$r$p$salt$digest
  passcode_hash text        not null check (passcode_hash like 'scrypt$%'),
  created_at    timestamptz not null default now(),
  updated_at    timestamptz not null default now()
);

comment on table public.workspace_team_secret is
  '🔴 조 passcode 해시. anon 에 GRANT 없음 + RLS 켜짐 + 정책 0개 — 어떤 REST 경로로도 나가지 않는다. 검증은 workspace_append RPC 안에서만 일어난다';

-- ── ③ 게시 하트비트 — 7일 pause 를 여기서 푼다 ──────────────────────────────
--
-- ADR-SC-0010 ④: 매일 도는 `batch.publish` 가 한 줄 쓰면 무활동 타이머가 리셋된다.
-- 🔒 GitHub Actions keep-alive 를 쓰지 않는다 (절대 제약 6 — CI/CD 를 필수 경로에
--    두지 않는다). 살아 있게 하는 일이 **이미 매일 하는 일** 안에 있어야 잊히지 않는다.
--
-- 🔒 이것은 원장이 아니라 운영 로그다. 그래서 upsert 를 허용한다(같은 날 두 번
--    게시하면 덮어쓴다). append-only 규율은 사람의 결정에만 적용된다.
create table if not exists public.workspace_publish_log (
  bas_dd       text primary key check (bas_dd ~ '^\d{8}$'),
  published_at timestamptz not null default now(),
  note         text check (note is null or length(note) <= 200)
);

comment on table public.workspace_publish_log is
  '배치 게시 하트비트. 🔴 KRX 원천도 파생 집계도 넣지 않는다 — "언제 게시했다" 는 사실뿐이다';

-- ── ④ RLS ───────────────────────────────────────────────────────────────────

alter table public.workspace_event       enable row level security;
alter table public.workspace_team_secret enable row level security;
alter table public.workspace_publish_log enable row level security;

revoke all on public.workspace_event       from anon, authenticated;
revoke all on public.workspace_team_secret from anon, authenticated;
revoke all on public.workspace_publish_log from anon, authenticated;

-- 🔒 읽기만 연다. 쓰기 GRANT 를 주지 않으므로 **정책을 실수로 써도 쓸 수 없다** —
--    RLS 정책은 GRANT 를 넘어서지 못한다. 두 겹인 이유다.
grant select on public.workspace_event       to anon, authenticated;
grant select on public.workspace_publish_log to anon, authenticated;

drop policy if exists workspace_event_public_read on public.workspace_event;
create policy workspace_event_public_read
  on public.workspace_event for select
  to anon, authenticated
  using (true);

drop policy if exists workspace_publish_log_public_read on public.workspace_publish_log;
create policy workspace_publish_log_public_read
  on public.workspace_publish_log for select
  to anon, authenticated
  using (true);

-- 🔴 `workspace_team_secret` 에는 정책이 **하나도 없다.** GRANT 도 없다.
--    REST 로 이 테이블은 존재하지 않는 것과 같다.

-- ── ⑤ 쓰기 경로 — RPC 하나뿐이다 ────────────────────────────────────────────
--
-- ## 🔒 왜 테이블 INSERT 가 아니라 RPC 인가
--
-- 앱은 anon 키로 붙고, 저장소는 Public 이므로 **anon 키는 공개된다고 가정한다**
-- (ADR-SC-0010 ⑥). 테이블에 INSERT 를 열면 키를 가진 누구나 남의 조 이름으로
-- 확정·코멘트를 쓸 수 있다 — passcode 가 지키기로 한 바로 그것이 뚫린다.
--
-- SECURITY DEFINER 함수 안에서 passcode 를 검증하면, 쓰기 권한이 **키가 아니라
-- passcode** 에 걸린다. 그것이 원래 의도였다.
--
-- 🔴 service_role 키를 앱에 두는 길은 택하지 않았다. 그 키는 RLS 를 통째로
--    우회하고 53개 테이블 전부에 닿는다 — HF 토큰을 `HF_TOKEN_WRITE`/`READ` 로
--    가른 것과 같은 판단이다 (org 전체 쓰기 토큰이 앱용 칸에 있던 사고).

-- 상수시간 비교. 🔒 `=` 은 첫 다른 바이트에서 반환해 길이 정보를 흘린다.
create or replace function public.workspace_ct_eq(a text, b text)
returns boolean
language plpgsql
immutable
set search_path = public, pg_temp
as $$
declare
  ba bytea; bb bytea; la int; lb int; diff int; i int;
begin
  if a is null or b is null then return false; end if;
  ba := convert_to(a, 'UTF8'); bb := convert_to(b, 'UTF8');
  la := octet_length(ba);      lb := octet_length(bb);
  if la = 0 or lb = 0 then return false; end if;
  diff := la # lb;                      -- 길이 차이도 결과에 섞는다
  for i in 0 .. greatest(la, lb) - 1 loop
    diff := diff | (get_byte(ba, least(i, la - 1)) # get_byte(bb, least(i, lb - 1)));
  end loop;
  return diff = 0;
end $$;

-- 앱이 passcode 를 재계산하려면 저장된 scrypt 파라미터와 salt 가 필요하다.
-- 🔒 **digest 는 주지 않는다.** salt 는 비밀이 아니라 레인보우 테이블을 막는 장치다.
--    salt 만으로는 오프라인 대입이 불가능하다 — 맞는지 확인하려면 매번 이 DB 를
--    불러야 하고, 그래서 대입이 **온라인**으로 묶인다. HF 방식(해시 통째 노출)보다
--    오히려 강하다.
create or replace function public.workspace_passcode_params(p_team_id text)
returns text
language sql
stable
security definer
set search_path = public, pg_temp
as $$
  -- 'scrypt$n$r$p$salt$digest' 에서 마지막 칸(digest)만 떼어 낸다
  select left(passcode_hash, length(passcode_hash) - length(split_part(passcode_hash, '$', 6)))
  from public.workspace_team_secret
  where team_id = p_team_id;
$$;

-- 조를 만든다. 🔒 원장 이벤트와 passcode 해시가 **한 트랜잭션**에 들어간다 —
--    둘이 갈라지면 "passcode 없는 조" 나 "조 없는 passcode" 가 남는다.
create or replace function public.workspace_create_team(
  p_event         jsonb,
  p_passcode_hash text
)
returns int
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  v_team_id text := p_event->>'team_id';
  v_recent  int;
  v_total   int;
begin
  if p_event->>'kind' is distinct from 'team.created' then
    raise exception '조 생성 RPC 는 team.created 만 받는다 (받은 것: %)', p_event->>'kind';
  end if;
  if p_passcode_hash is null or p_passcode_hash not like 'scrypt$%' then
    raise exception 'passcode 는 해시로만 받는다 — auth.hash_passcode 를 쓴다';
  end if;
  if exists (select 1 from public.workspace_team_secret where team_id = v_team_id) then
    raise exception '조 % 는 이미 있다. 다른 id 로 만든다', v_team_id;
  end if;

  -- 🔴 Free 티어 500MB 방어. 앱이 public 이라 조 생성에는 passcode 게이트가 없다 —
  --    없을 수밖에 없다(아직 passcode 가 없으니까). 그래서 여기서 민다.
  select count(*) into v_total from public.workspace_team_secret;
  if v_total >= 200 then
    raise exception '조가 너무 많다 (%개). 개발자에게 알린다', v_total;
  end if;
  select count(*) into v_recent
  from public.workspace_event
  where kind = 'team.created' and recorded_at > now() - interval '1 hour';
  if v_recent >= 20 then
    raise exception '한 시간에 만들 수 있는 조 수를 넘었다. 잠시 뒤 다시 시도한다';
  end if;

  insert into public.workspace_team_secret (team_id, passcode_hash)
  values (v_team_id, p_passcode_hash);

  insert into public.workspace_event (event_id, kind, team_id, actor, at, payload)
  values (
    p_event->>'event_id', p_event->>'kind', v_team_id, p_event->>'actor',
    p_event->>'at', coalesce(p_event->'payload', '{}'::jsonb)
  )
  on conflict (event_id) do nothing;

  return 1;
end $$;

-- 이벤트를 더한다. 🔒 passcode 를 통과해야만 쓴다.
--
-- `p_encoded` 는 앱이 `workspace_passcode_params` 로 받은 파라미터·salt 로
-- 재계산한 **전체 인코딩**(`scrypt$n$r$p$salt$digest`)이다. 평문은 이 DB 에
-- 어떤 형태로도 도달하지 않는다.
create or replace function public.workspace_append(
  p_team_id text,
  p_encoded text,
  p_events  jsonb
)
returns int
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  v_stored text;
  v_recent int;
  v_bad    text;
  v_written int;
begin
  if jsonb_typeof(p_events) is distinct from 'array' then
    raise exception '이벤트는 배열로 준다';
  end if;
  if jsonb_array_length(p_events) = 0 then
    return 0;
  end if;
  if jsonb_array_length(p_events) > 50 then
    raise exception '한 번에 보낼 수 있는 이벤트는 50건까지다';
  end if;

  select passcode_hash into v_stored
  from public.workspace_team_secret where team_id = p_team_id;
  -- 🔒 없는 조와 틀린 passcode 를 **같은 말로** 답한다 (`auth.verify_passcode` 와
  --    같은 규율 — 틀린 이유를 말해 주면 그것이 곧 정보다)
  if v_stored is null or not public.workspace_ct_eq(v_stored, p_encoded) then
    raise exception '쓰지 못했다. 조와 passcode 를 확인한다';
  end if;

  -- 🔒 passcode 는 그 조의 것이다. 다른 조 이벤트를 끼워 넣을 수 없다
  select string_agg(distinct e->>'team_id', ', ') into v_bad
  from jsonb_array_elements(p_events) as e
  where e->>'team_id' is distinct from p_team_id;
  if v_bad is not null then
    raise exception '다른 조의 이벤트가 섞였다: %', v_bad;
  end if;

  -- 🔴 500MB 방어. 사람이 손으로 누르는 속도를 훨씬 넘는 선으로 잡는다
  select count(*) into v_recent
  from public.workspace_event
  where team_id = p_team_id and recorded_at > now() - interval '1 minute';
  if v_recent >= 60 then
    raise exception '너무 빠르게 쓰고 있다. 잠시 뒤 다시 시도한다';
  end if;

  -- 🔒 멱등 — 같은 이벤트를 두 번 보내도 하나다. event_id 가 내용에서 나오므로
  --    성립한다(`events._event_id`). `store.HubStore` 와 같은 계약이다:
  --    **실제로 쓴 건수**를 돌려준다 — 0건 쓰고 N건이라 답하면 화면이 거짓말한다
  with ins as (
    insert into public.workspace_event (event_id, kind, team_id, actor, at, payload)
    select e->>'event_id', e->>'kind', e->>'team_id', e->>'actor', e->>'at',
           coalesce(e->'payload', '{}'::jsonb)
    from jsonb_array_elements(p_events) as e
    on conflict (event_id) do nothing
    returning 1
  )
  select count(*) into v_written from ins;

  return v_written;
end $$;

-- 게시 하트비트. 🔒 원장이 아니므로 upsert 다.
create or replace function public.workspace_heartbeat(p_bas_dd text, p_note text default null)
returns void
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  v_rows int;
begin
  if p_bas_dd !~ '^\d{8}$' then
    raise exception 'bas_dd 는 YYYYMMDD 여야 한다 (받은 것: %)', p_bas_dd;
  end if;

  -- 🔴 이 RPC 는 anon 이 부를 수 있고 passcode 게이트가 없다(배치가 부르는데
  --    배치에는 조가 없다). bas_dd 는 8자리이기만 하면 통과하므로 상한이 없으면
  --    10^8 행까지 밀어 넣을 수 있다 — Free 티어 500MB 를 그것만으로 채운다.
  --    대회 기간은 몇 달이고 영업일은 연 250일이라 2000 이면 8년치다.
  select count(*) into v_rows from public.workspace_publish_log;
  if v_rows >= 2000 and not exists (
    select 1 from public.workspace_publish_log where bas_dd = p_bas_dd
  ) then
    raise exception '게시 로그가 상한에 닿았다 (%행). 오래된 것을 정리한다', v_rows;
  end if;

  insert into public.workspace_publish_log (bas_dd, published_at, note)
  values (p_bas_dd, now(), left(p_note, 200))
  on conflict (bas_dd) do update
    set published_at = now(), note = excluded.note;
end $$;

-- ── ⑥ 실행 권한 ─────────────────────────────────────────────────────────────
--
-- 🔒 먼저 전부 뺀 뒤 필요한 것만 준다.
--
-- 🔴 `from public` 만으로는 부족하다. `PUBLIC` 과 `anon` 은 **다른 역할**이고,
--    실측(2026-09-12)상 `public` 스키마의 default ACL 이 함수에 `anon=X` 를 준다 —
--    즉 아무것도 안 하면 anon 이 **모든 함수를** 부를 수 있다. 앞 마이그레이션이
--    그 default 를 껐지만, 여기서도 명시한다: 마이그레이션이 재적용되거나 순서가
--    바뀌어도 이 파일만 읽고 권한을 알 수 있어야 한다.

revoke all on function public.workspace_ct_eq(text, text)         from public, anon, authenticated;
revoke all on function public.workspace_passcode_params(text)     from public, anon, authenticated;
revoke all on function public.workspace_create_team(jsonb, text)  from public, anon, authenticated;
revoke all on function public.workspace_append(text, text, jsonb) from public, anon, authenticated;
revoke all on function public.workspace_heartbeat(text, text)     from public, anon, authenticated;

-- 🔴 `workspace_ct_eq` 에는 EXECUTE 를 주지 않는다. SECURITY DEFINER 함수 안에서는
--    definer 권한으로 돌아가므로 필요가 없고, 밖에서 부를 수 있게 하면 비교
--    오라클이 된다.
grant execute on function public.workspace_passcode_params(text)     to anon, authenticated;
grant execute on function public.workspace_create_team(jsonb, text)  to anon, authenticated;
grant execute on function public.workspace_append(text, text, jsonb) to anon, authenticated;
grant execute on function public.workspace_heartbeat(text, text)     to anon, authenticated;
