-- 트리거 함수에 search_path 를 고정한다
--
-- advisor `function_search_path_mutable` 이 지적한 유일한 함수다. 나머지는
-- 처음부터 `set search_path = public, pg_temp` 를 달고 만들었는데 트리거 함수만
-- 빠져 있었다.
--
-- 🔒 여기서는 **빈 search_path 가 가장 정확하다.** 이 함수는 객체를 하나도
--    참조하지 않고 `raise` 만 한다 — "무엇도 찾을 필요가 없다" 를 그대로 적는 것이
--    `public, pg_temp` 를 형식적으로 붙이는 것보다 뜻에 맞는다.
create or replace function public.workspace_event_append_only()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
  raise exception
    '원장은 append-only 다 — % 를 할 수 없다. 되돌리려면 반대 이벤트를 더한다 '
    '(sector.unconfirmed · team.restored)', tg_op;
end $$;

-- `create or replace` 는 권한을 보존하지만, 이 파일만 읽고도 최종 상태를 알 수
-- 있도록 다시 적는다 (앞 마이그레이션과 같은 내용 — 멱등하다)
revoke all on function public.workspace_event_append_only() from public, anon, authenticated;
