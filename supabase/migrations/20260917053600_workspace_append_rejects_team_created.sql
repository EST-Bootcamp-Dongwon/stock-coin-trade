-- 조 생성은 `create_team` 만 한다 — DB 가 지키게 한다
--
-- 근거: ADR-SC-0011 ⑭ · `세션-시작-프롬프트.md` 4.5 "남은 원장 위험" ②
--
-- 🔒 **이 파일은 `20260917053500_workspace_verify_passcode.sql` 다음이다.**
--    순서를 지키는 이유는 그 파일 머리주석에 있다(옛 앱의 probe 가 여기서 막힌다).
--
-- ## 무엇이 뚫려 있었나
--
-- `workspace_append` 가 `team.created` 종류를 받았다. 막는 곳이 Python 저장소
-- (`store._reject_team_created`)뿐이었고, **RPC 는 앱을 건너뛴다.**
--
-- 그 조 passcode 를 가진 조원이 `at` 이 더 이른 **유효한** `team.created` 를 쓰면 —
-- `fold` 는 조마다 **먼저 것**을 쓰고 뒤엣것은 이상으로만 알린다(`fold.py`) —
-- 조 이름 · `created_by` · `created_at` 기록이 통째로 바뀐다. 원장은 append-only 라
-- **지울 수도 없다.**
--
-- 🔴 피해 범위를 "그 조 하나" 로 적었던 것은 **과소평가였다.** 아래 `create_team` 의
--    시간당 게이트는 `team.created` 를 **조 구분 없이 전역으로** 센다. 조원 하나가
--    자기 조에 20건을 밀어 넣으면 **한 시간 동안 아무도 새 조를 만들 수 없다.**
--    한 시간마다 반복하면 조 생성이 영구 차단된다.
--
-- 🔒 반대로, 이것은 **권한 상승이 아니다.** passcode 를 가진 사람은 이미 임의의
--    `actor` 로 확정 · 코멘트 · 보관을 쓸 수 있고, 보관 버튼(`teams._can_archive`)은
--    스스로 "권한이 아니라 실수 방지" 라고 적고 있다. 여기서 막는 것은
--    **역사 다시쓰기**(원장 1행의 불변성)와 위 전역 차단이다.
--
-- ## 세 겹으로 막는다 — 하나로는 다음 사람이 다시 연다
--
-- ① **구조** — 부분 유니크 인덱스. 어느 함수가 쓰든, 앞으로 RPC 가 늘어도
--    한 조에 `team.created` 는 하나다. `fold` 가 이미 그렇게 가정하고 있었다 —
--    가정을 코드 밖으로 꺼내 DB 가 들게 한다.
-- ② **규칙** — `workspace_append` 가 `team.created` 를 **무조건** 거절한다.
--    인덱스가 내는 `unique_violation` 은 영문 제약 이름이라 화면에 그대로 뜬다.
--    사람이 읽을 수 있는 문장은 함수가 준다.
-- ③ **탈결합** — 앞 파일의 `workspace_verify_passcode`. ② 가 probe 를 막으므로 필요하다.
--
-- 🔒 "아직 없는 `team.created` 만 거절" 이라는 더 작은 대안은 택하지 않았다.
--    보안상으로는 같지만 **DB 의 규칙이 Python 의 규칙(`_reject_team_created` — 무조건)
--    과 달라진다.** 그러면 "세 구현이 같은 답을 낸다" 는 이 저장소의 규율에 따라
--    다음 사람이 Python 쪽을 완화할 길이 열리고, 파일 원장에는 유니크 인덱스가
--    **없으므로** 거기서 실제로 두 번째 `team.created` 가 쓰인다.
--
-- ## 🔴 적용 전에 확인한다 — 중복이 있으면 인덱스 생성이 실패한다
--
--     select team_id, count(*) from public.workspace_event
--     where kind = 'team.created' group by team_id having count(*) > 1;
--
-- 0행이어야 한다. 아니면 **멈추고** 어느 줄을 남길지 사람이 정한다 —
-- 원장은 지울 수 없으므로 이 판단은 되돌릴 수 없다.
-- (2026-09-17 실측: `workspace_event` 0행 · `workspace_team_secret` 0행 —
--  비용 없이 걸 수 있는 시점이다. 조가 생긴 뒤에는 이 확인이 형식이 아니게 된다.)
--
-- 🔒 `concurrently` 를 쓰지 마라 — 트랜잭션 안에서 돌지 않는다. 이 저장소의 검증
--    관례가 "anon 역할로 트랜잭션 안에서 돌리고 롤백" 이라 정면으로 충돌한다.

-- ── ① 구조 ──────────────────────────────────────────────────────────────────

create unique index if not exists workspace_event_one_created_per_team
  on public.workspace_event (team_id)
  where kind = 'team.created';

comment on index public.workspace_event_one_created_per_team is
  '한 조에 team.created 는 하나. fold 가 먼저 것만 쓰므로 둘째 줄은 조 이름·만든 사람 기록을 바꾼다';

-- ── ② 규칙 — append 는 조를 만들지 않는다 ───────────────────────────────────
--
-- 🔒 아래는 `20260912095328_workspace_ledger.sql` 의 함수에 검사 한 덩이를 더한 것이다.
--    나머지 로직은 **한 글자도 바뀌지 않았다.**
-- 🔴 거부 문장(`쓰지 못했다. 조와 passcode 를 확인한다`)은 `store.REJECTED_MESSAGE`
--    와 글자 그대로 묶여 있다. 갈라지면 클라이언트가 **거부를 통신 오류로 오인한다.**
--    `workspace_test.test_거부_문장이_마이그레이션과_같다` 가 둘을 묶는다.

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

  -- 🔴 ② — 조 생성은 `workspace_create_team` 만 한다 (머리주석).
  --    🔒 passcode 검사 **앞**이다. 이 거절은 자격증명과 무관한 종류 오류이고,
  --       뒤에 두면 "틀린 passcode" 와 같은 문장으로 묻혀 원인을 알 수 없다.
  if exists (
    select 1 from jsonb_array_elements(p_events) as e
    where e->>'kind' = 'team.created'
  ) then
    raise exception '조 생성은 append 로 하지 않는다 — workspace_create_team 을 쓴다';
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

-- ── ②' 조 생성이 조용히 성공을 보고하지 않게 ────────────────────────────────
--
-- 🔴 옛 정의는 `on conflict (event_id) do nothing;` 바로 뒤에 무조건 `return 1` 이었다.
--    **0건 쓰여도 1 이라고 답한다.** 도달 경로가 하나 있다 — 남은 위험 ①(id 선점):
--    공격자가 자기 조에 `kind` 만 다른 행으로 그 `event_id` 를 먼저 차지하면
--    (부분 유니크 인덱스에도 걸리지 않는다) 진짜 조 생성이 0건으로 삼켜지고,
--    화면은 "조를 만들었다" 를 띄우고 세션까지 묶는다. 시크릿은 들어갔으므로
--    **같은 id 로 다시 만들 수도 없다** — 조용히 망가진 상태다.
--    이제 0건이면 던지고, 트랜잭션이 통째로 롤백되어 시크릿도 남지 않는다.
--
-- 🔴 인덱스(①)가 생기면 orphan 상태(원장에 `team.created` 는 있는데 시크릿에는
--    없는 조 — 옛 원장 backfill · 소유자의 시크릿 삭제로 만들어진다)에서
--    `unique_violation` 이 난다. 먼저 검사해 읽을 수 있는 문장을 준다.
--
--    🔴 **그런데 이 검사는 여태 유일하게 남아 있던 조 복구 절차를 닫는다.** 옛 동작은
--       이랬다 — 소유자가 `workspace_team_secret` 행을 지우고 사용자가 **같은 id** 로
--       다시 만들면, 내용이 같아 `event_id` 도 같으므로 `on conflict` 로 0건 쓰이고
--       시크릿만 새로 들어가 조가 되살아났다. 이제는 여기서 막힌다.
--    🔒 **대신 되는 길이 있고, 그쪽이 원래 맞다** — 시크릿 행을 *지우지 말고*
--       `passcode_hash` 를 **UPDATE** 한다. append-only 트리거는 `workspace_event`
--       에만 걸려 있고 `workspace_team_secret` 에는 `updated_at` 칸이 이미 있다.
--
--           update public.workspace_team_secret
--              set passcode_hash = '<auth.hash_passcode 의 출력>', updated_at = now()
--            where team_id = '<조 id>';
--
--       🔒 이것은 **소유자 콘솔 작업이다.** 앱에는 경로가 없고, 만들지도 않는다 —
--          해시 하나가 모든 조를 여는 마스터가 되는 모양이기 때문이다(ADR-SC-0011 ⑫).

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
  v_written int;
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
  -- 🔴 orphan — 시크릿은 없는데 원장에 생성 기록이 있다 (머리주석 ②')
  if exists (
    select 1 from public.workspace_event
    where team_id = v_team_id and kind = 'team.created'
  ) then
    raise exception
      '조 % 의 생성 기록이 이미 원장에 있다. 개발자에게 알린다 — 그 조의 passcode 를 다시 걸 수 있다',
      v_team_id;
  end if;

  -- 🔴 Free 티어 500MB 방어. 앱이 public 이라 조 생성에는 passcode 게이트가 없다 —
  --    없을 수밖에 없다(아직 passcode 가 없으니까). 그래서 여기서 민다.
  select count(*) into v_total from public.workspace_team_secret;
  if v_total >= 200 then
    raise exception '조가 너무 많다 (%개). 개발자에게 알린다', v_total;
  end if;
  -- 🔴 이 게이트는 조 구분 없이 **전역**으로 센다. append 가 `team.created` 를
  --    받던 동안에는 조원 하나가 20건을 밀어 넣어 조 생성을 통째로 막을 수 있었다.
  --    위 ② 가 그 경로를 닫았다 — 이제 여기 세어지는 것은 진짜 조 생성뿐이다.
  select count(*) into v_recent
  from public.workspace_event
  where kind = 'team.created' and recorded_at > now() - interval '1 hour';
  if v_recent >= 20 then
    raise exception '한 시간에 만들 수 있는 조 수를 넘었다. 잠시 뒤 다시 시도한다';
  end if;

  insert into public.workspace_team_secret (team_id, passcode_hash)
  values (v_team_id, p_passcode_hash);

  with ins as (
    insert into public.workspace_event (event_id, kind, team_id, actor, at, payload)
    values (
      p_event->>'event_id', p_event->>'kind', v_team_id, p_event->>'actor',
      p_event->>'at', coalesce(p_event->'payload', '{}'::jsonb)
    )
    on conflict (event_id) do nothing
    returning 1
  )
  select count(*) into v_written from ins;

  if v_written = 0 then
    raise exception
      '조 % 의 생성 기록을 쓰지 못했다 — 같은 event_id 가 이미 원장에 있다. 다른 이름으로 만든다',
      v_team_id;
  end if;

  return v_written;
end $$;

-- ── ③ DB 에 박힌 자기서술을 고친다 ─────────────────────────────────────────
--
-- 🔴 `20260912095328` 의 `comment on table` 이 *"검증은 workspace_append RPC 안에서만
--    일어난다"* 고 적고 있다. **이제 거짓이다** — `workspace_verify_passcode` 도 이
--    테이블을 읽는다. 테이블 주석은 psql `\d+` 와 Supabase UI 에 그대로 뜨므로,
--    고치지 않으면 "이 테이블을 만지는 함수는 하나뿐" 이라는 결론을 다음 사람에게 준다.

comment on table public.workspace_team_secret is
  '🔴 조 passcode 해시. anon 에 GRANT 없음 + RLS 켜짐 + 정책 0개 — 어떤 REST 경로로도 나가지 않는다. 읽는 것은 SECURITY DEFINER 함수 셋뿐이다: workspace_passcode_params(salt 만) · workspace_append(쓰기 관문) · workspace_verify_passcode(참거짓만). passcode 를 다시 걸려면 이 테이블의 passcode_hash 를 UPDATE 한다 — 행을 지우면 그 조는 되살릴 수 없다';

-- 🔒 권한은 바뀌지 않는다. `create or replace` 는 기존 ACL 을 보존하지만,
--    이 파일만 읽고 권한을 알 수 있어야 한다는 규율에 따라 다시 적는다.
revoke all on function public.workspace_create_team(jsonb, text)  from public, anon, authenticated;
revoke all on function public.workspace_append(text, text, jsonb) from public, anon, authenticated;
grant execute on function public.workspace_create_team(jsonb, text)  to anon, authenticated;
grant execute on function public.workspace_append(text, text, jsonb) to anon, authenticated;
