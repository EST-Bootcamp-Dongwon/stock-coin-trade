-- 참가 검증을 **읽기 전용 RPC** 로 옮긴다 — `verify` 가 쓰기 경로에 기대지 않게
--
-- 근거: ADR-SC-0011 ⑭ (⑨ 를 뒤집는다)
--
-- ## 🔴 ⑨ 는 왜 뒤집히는가
--
-- ⑨ 는 "이미 있는 성질로 되는 일에 DB 표면을 늘리지 않는다" 를 근거로 이 RPC 를
-- 택하지 않았다. 대신 `verify` 가 **그 조의 `team.created` 를 그대로 다시 보내서**
-- passcode 를 확인했다(`on conflict do nothing` → 0건 쓰기).
--
-- 그 방식은 `workspace_append` 가 `team.created` 를 **받아 준다는 것에 의존한다.**
-- 그런데 그것이 바로 닫으려는 위험이다(다음 마이그레이션) — 조원이 `at` 이 더 이른
-- 유효한 `team.created` 를 써서 조 이름·만든 사람 **기록**을 바꿀 수 있다.
-- 즉 ⑨ 의 우아함과 ② 의 차단이 **같은 한 줄을 두고 다툰다.** 둘 다 가질 수 없다.
--
-- 🔒 그런데 근거는 그것만이 아니다. 뒤집는 이유가 둘 더 있고, 이쪽이 더 무겁다 —
--
-- 1. **검증이 쓰기 스로틀에서 풀린다.** 지금 `verify` 는 `workspace_append` 의
--    분당 60행 제한을 그대로 상속한다. 조가 한창 쓰는 중이면 *참가*가 오류로
--    떨어진다 — 쓰기와 검증은 같은 한도를 나눠 쓸 이유가 없다.
-- 2. **롤아웃이 fail-closed 다.** 이 파일을 **먼저** 적용하면 옛 앱은 계속 probe 로
--    돌고(아무것도 깨지지 않는다), 앱을 배포한 뒤 다음 파일을 적용한다. 순서가
--    어긋나 앱이 먼저 떠도 없는 함수는 404 라 **참가가 막힐 뿐 열리지는 않는다.**
--    🔴 반대 설계(빈 배열 검사를 passcode 검사 뒤로 옮기기)는 순서가 어긋나면
--       옛 함수가 빈 배열을 passcode **검사 앞에서** 0 으로 돌려보내므로
--       `verify` 가 **아무 passcode 로나 true** 가 된다 — fail-open 이다. 그래서 택하지 않았다.
--
-- ## 🔴 `volatile` 이다 — "읽기 전용인데 왜" 의 답을 미리 적는다
--
-- PostgREST 는 **IMMUTABLE·STABLE 함수를 GET 으로도 노출한다.** 이 함수를 STABLE 로
-- 선언하는 순간 아래가 유효해진다 —
--
--     GET /rest/v1/rpc/workspace_verify_passcode?p_team_id=team_a&p_encoded=scrypt$...$<digest>
--
-- 🔴 `p_encoded` 는 저장된 해시 **그 자체**이고(`auth.recompute_passcode` 가
--    `params + digest` 를 통째로 돌려주고 `workspace_ct_eq` 가 통째로 비교한다),
--    그것이 곧 그 조의 **영구 쓰기 자격증명**이다. 쿼리스트링은 게이트웨이 로그 ·
--    프록시 · 브라우저 히스토리 · 화면 공유에 남는다. **passcode 를 바꾸는 RPC 가
--    없으므로 회수할 수도 없다.**
--
-- 그래서 plpgsql 의 기본값인 `volatile` 을 **명시해서** 둔다. 최적화를 잃는 대신
-- 자격증명이 URL 로 갈 길을 없앤다.
--
-- ## 🔒 노출이 늘지 않는다 — 오라클은 이미 있었다
--
-- 이 RPC 는 참·거짓 1비트를 준다. 그런데 오늘도 누구나 같은 1비트를 얻는다:
-- 원장 SELECT 가 anon 에 열려 있어 그 조의 `team.created` 를 읽고(1),
-- `workspace_passcode_params` 로 salt 를 받고(2), `workspace_append` 로 되보내면(3)
-- 200 이냐 거부냐로 갈린다. 이 함수는 **1번 단계를 없앨 뿐**이다.
--
-- 🔴 다만 오늘 그 오라클에 **어떤 상한도 없다**는 사실은 그대로 남는다 —
--    `workspace_append` 의 분당 60 제한은 passcode 검사 **뒤**에 있고 *삽입된 행*을
--    세므로 틀린 시도는 세지 않는다. 공격자는 scrypt 를 자기 기계에서 돌리고
--    결과만 보낸다. 48단어 4개(약 530만)는 수 시간이면 훑린다.
--    🔒 시도 횟수 제한은 **이번에 넣지 않는다** — 대회 기간 사용자는 7명이고,
--       카운터 테이블은 append-only 원장 옆에 *지울 수 있는* 상태를 하나 더
--       만든다. 필요해지면 그때 넣되, 그것은 이 파일이 아니라 별도 결정이다.
--       (`auth.py` 머리주석의 "횟수를 셀 수단이 생긴다" 는 **가능성이지 사실이 아니다**)

create or replace function public.workspace_verify_passcode(
  p_team_id text,
  p_encoded text
)
returns boolean
language plpgsql
volatile                                  -- 🔴 STABLE 로 바꾸지 마라 (머리주석)
security definer
set search_path = public, pg_temp
as $$
declare
  v_stored text;
begin
  select passcode_hash into v_stored
  from public.workspace_team_secret where team_id = p_team_id;
  -- 🔒 없는 조와 틀린 passcode 를 **같은 값으로** 답한다 (`workspace_append` 가
  --    둘을 같은 문장으로 거부하는 것과 같은 규율). `workspace_ct_eq` 는 null 을
  --    받으면 false 를 돌려주므로 분기가 필요 없지만, coalesce 로 못을 박는다 —
  --    null 이 새면 PostgREST 가 `null` 을 실어 보내고 앱의 `is False` 가 깨진다.
  return coalesce(public.workspace_ct_eq(v_stored, p_encoded), false);
end $$;

-- 🔒 먼저 전부 뺀 뒤 필요한 것만 준다. `20260912095328` 의 권한 절과 같은 규율이다 —
--    이 파일만 읽고 권한을 알 수 있어야 한다.
revoke all on function public.workspace_verify_passcode(text, text)
  from public, anon, authenticated;
grant execute on function public.workspace_verify_passcode(text, text)
  to anon, authenticated;

-- 🔴 `workspace_ct_eq` 에는 여전히 EXECUTE 를 주지 않는다. definer 안에서는
--    definer 권한으로 돌아가므로 필요가 없고, 밖에서 부를 수 있게 하면 비교 오라클이 된다.

-- 🔒 오버로드를 만들지 마라. PostgREST 는 인자 **이름**으로 함수를 고르므로
--    `workspace_verify_passcode(text)` 같은 것을 나중에 더하면 PGRST203 모호성으로
--    참가가 통째로 죽는다.
