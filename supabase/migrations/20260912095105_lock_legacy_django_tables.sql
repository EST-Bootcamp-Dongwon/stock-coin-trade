-- v2.0 Django 스키마 53개 테이블을 잠근다 — anon 쓰기를 막는 것이 요점이다
--
-- ## 🔴 왜 지금인가
--
-- ADR-SC-0010 W12 는 "53개 전부 RLS 가 꺼져 있다. 지금은 0행이라 샐 것이 없다" 고
-- 적었다. 그것은 **읽기 관점**이다. 실제 GRANT 를 재 보니 `anon` 에게 53개 전부
-- SELECT·INSERT·UPDATE·DELETE·TRUNCATE 가 있었다. 0행이어도 **쓰기가 열려 있다** —
--   ① 아무나 행을 밀어 넣어 Free 티어 500MB 를 소진시킬 수 있고
--   ② `external_token`·`api_key` 는 Django 가 Vercel 에서 실제로 쓰이는 순간
--      KIS appkey 가 들어갈 자리다. 그때 여는 것이 아니라 **비어 있는 지금** 닫는다
--
-- ## 🔒 왜 정책을 하나도 두지 않는가 — 그리고 왜 그래도 앱이 죽지 않는가
--
-- ADR-SC-0010 ⑥ 이 *"정책 없이 ENABLE ROW LEVEL SECURITY 만 켜면 전 접근이 막혀
-- 앱이 죽는다"* 고 경고했다. 그 경고는 **anon 으로 붙는 경로**에만 해당한다.
-- 실측(2026-09-12): 53개의 소유자는 전부 `postgres` 이고 `postgres` 는
-- `rolbypassrls = true` 다. Django 는 그 역할로 붙으므로(VERCEL.md 3.1) RLS 를
-- 통째로 우회한다. 즉 여기서 막히는 것은 **PostgREST 의 anon·authenticated 뿐**이고,
-- 그 둘은 v2.0 스키마를 쓸 일이 애초에 없다.
--
-- 🔒 그래서 정책을 두지 않는 것이 곧 정책이다 — "이 테이블들은 REST 로 열지 않는다".
--    나중에 어느 하나를 열고 싶어지면 그때 그 테이블에만 정책을 쓴다.
--
-- ## 🔒 REVOKE 와 RLS 를 둘 다 한다
--
-- 한 겹이면 충분해 보이지만 성질이 다르다 — RLS 는 행을 가리고, REVOKE 는 테이블에
-- 닿는 것 자체를 막는다. 나중에 누군가 정책을 하나 잘못 쓰면 RLS 는 뚫리지만
-- GRANT 가 없으면 여전히 막힌다. 반대로 누가 GRANT 를 되돌려도 RLS 가 남는다.

do $$
declare
  t record;
  n int := 0;
begin
  for t in
    select tablename
    from pg_tables
    where schemaname = 'public'
      -- 🔒 이 마이그레이션은 **v2.0 유산만** 건드린다. 팀 원장(workspace_*)은
      --    다음 마이그레이션이 자기 정책과 한 벌로 만든다 — 여기서 미리 잠그면
      --    거기서 무엇이 열려 있는지 읽을 수 없게 된다.
      and tablename not like 'workspace\_%'
  loop
    execute format('alter table public.%I enable row level security', t.tablename);
    execute format('revoke all on public.%I from anon, authenticated', t.tablename);
    n := n + 1;
  end loop;
  raise notice 'v2.0 유산 테이블 %개를 잠갔다 (RLS on · anon/authenticated REVOKE)', n;
end $$;

-- 앞으로 `public` 에 새로 만들어지는 것이 자동으로 anon 권한을 받지 않게 한다.
--
-- 🔴 이것이 없으면 다음에 Django 가 migrate 를 한 번 돌리는 것만으로 위 작업이
--    통째로 무의미해진다 — 새 테이블은 다시 anon ALL 로 태어난다. 실측(2026-09-12)
--    `pg_default_acl` 이 테이블에 `anon=arwdDxtm` 를 준다. **53개가 anon ALL 을
--    갖게 된 원인이 바로 이것**이고, 테이블마다 REVOKE 하는 것만으로는 재발한다.
--
-- 🔴 **함수(`f`)도 반드시 포함한다.** 같은 실측에서 `anon=X`(EXECUTE)가 나왔다 —
--    즉 새로 만드는 함수는 **아무 조치를 안 하면 anon 이 부를 수 있다.** 다음
--    마이그레이션의 RPC 가 그 위에 서므로, 내부용 함수(`workspace_ct_eq`)까지
--    조용히 열리는 길이 여기서 닫힌다.
alter default privileges in schema public revoke all on tables    from anon, authenticated;
alter default privileges in schema public revoke all on sequences from anon, authenticated;
alter default privileges in schema public revoke all on functions from anon, authenticated;

-- ⚠️ 이 문장은 **실행 역할(`postgres`)이 앞으로 만드는 것**에만 적용된다.
--    `pg_default_acl` 에는 `supabase_admin` 이 주인인 같은 규칙이 따로 있고 그것은
--    남는다. 실무적으로는 문제되지 않는다 — Django 도 우리도 `postgres` 로 붙고,
--    `supabase_admin` 은 Supabase 내부 작업용이다. 🔒 다만 **남아 있다는 사실을
--    적어 둔다.** 언젠가 대시보드로 만든 테이블이 anon 에 열려 있다면 원인은 여기다.
