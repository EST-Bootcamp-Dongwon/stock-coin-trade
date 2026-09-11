-- ============================================================================
--  pg_cron 잡 등록 — v2.0 스케줄러 (F-20 2·3장)
--  작성 2026-08-13 (KST) · 세션 8 · 대상 Supabase 프로젝트 sgbhrahtewojmicwmxxu
-- ============================================================================
--
--  ★★ 이 파일은 **아직 실행하지 않았다.** 아래 0장부터 순서대로 실행한다.
--
--     2026-08-16 현재 상태 (라이브 확인):
--       앱 주소   https://stock-coin-trade.vercel.app   ✓ 200 · 미들웨어 동작 확인
--       pg_cron   1.6.4 설치됨 · supabase_vault 0.3.1 설치됨
--       pg_net    **미설치** ← 0.1 절이 필요하다
--       cron.job  0건 · vault 비밀값 0건 · ops 스키마 없음
--
--     ★ 남은 것은 **Vault 에 토큰을 넣는 일**뿐이다. 그 값은 Vercel 운영
--       환경변수 `INTERNAL_JOB_TOKEN` 과 **글자까지 같아야** 한다 (2026-08-14 설정됨).
--
--  등록 대상 (2장 · 2.2장) — **전부 DB 안의 데이터만 다루는 잡이다**:
--    · 잡 4   snapshot_intraday     장중 10분         KST   ← 세션 11
--    · 잡 7   settle_daily          매 영업일 15:40   KST   ← 세션 11
--    · 잡 9   settle_weekly         매주 월   06:00   KST   ← 세션 11
--    · 잡 10  settle_contest        매일      06:10   KST   ← 세션 11
--    · 잡 11  sync_upbit_markets    매일      18:00   KST   ← 공개 API(requests)라 Vercel 에서 돈다
--
--  ★★★ **로컬 CLI 로 뺀 것 — pykrx 3종** (2026-08-16 결정 · 2장 머리의 ★★★ 참조)
--    · 잡 8   sync_stock_master     매 영업일 16:00   KST
--    · 잡 8b  sync_trading_calendar 매월 2일  06:00   KST
--    · (추가) sync_market_index     매 영업일 15:38   KST   ← F-05 4.4
--    이유: **루트 requirements.txt 에 pykrx 가 없다**(Vercel 번들) · KRX 자격증명 미설정 ·
--    sync_stock_master 는 실측 88초. 등록하면 매일 실패만 쌓이고 데이터는 안 채워진다.
--
--  보류 (2.1장) — 잡 1·2·3·5·6. 구현은 끝났으나 **실행처를 정하지 못했다.**
--  미구현     — 잡 12·13·14. 서비스 함수가 아직 없다.
--  해당 기능을 구현하는 세션에서 같은 방식으로 이 파일에 덧붙인다.
--
-- ============================================================================
--  ★★★ 시각 환산 — pg_cron 은 UTC 로 돈다 (KST = UTC + 9)
-- ============================================================================
--
--  잡 시각은 전부 KST 로 정의돼 있다(F-20 2장). 등록할 때 9시간을 뺀다.
--  **그런데 뺐더니 음수가 되면 날짜가 하루 밀린다.** 이게 이 파일에서 가장
--  틀리기 쉬운 지점이다.
--
--    KST 16:00 (월~금) → UTC 07:00 (월~금)   '0 7 * * 1-5'   요일 그대로 ✅
--    KST 18:00 (매일)  → UTC 09:00 (매일)    '0 9 * * *'     그대로     ✅
--    KST 09:00 (매일)  → UTC 00:00 (매일)    '0 0 * * *'     경계값     ⚠
--    KST 06:00 (월)    → UTC 21:00 (일)      '0 21 * * 0'    ★ 요일이 하루 앞으로
--    KST 04:00 (매일)  → UTC 19:00 (전날)    '0 19 * * *'    매일이라 무해
--
--  **KST 09:00 이전에 도는 잡은 요일·날짜를 하루 앞으로 당겨야 한다.**
--  잡 9(월 06:00)·잡 10(매일 06:10)이 여기 걸렸고, 앞으로 붙일 잡 14(매일 04:00)도 그렇다.
--  검산: `SELECT (timestamptz '2026-08-17 06:00+09') AT TIME ZONE 'UTC';`
--
-- ============================================================================


-- ────────────────────────────────────────────────────────────────────────────
--  0장. 사전 준비 — 확장 · 비밀값
-- ────────────────────────────────────────────────────────────────────────────

-- 0.1 확장. 2026-08-13 실측 상태:
--       pg_cron        1.6.4   설치됨    (초 단위 스케줄 지원 — 변경노트 E-2)
--       supabase_vault 0.3.1   설치됨
--       pg_net         0.20.4  **미설치** ← 아래 한 줄이 필요하다
create extension if not exists pg_net;

-- 0.2 비밀값을 Vault 에 넣는다.
--
--     ★★ **토큰을 cron 명령문에 직접 적지 않는다.** `cron.job.command` 는 평문
--        컬럼이라, 그 안에 토큰을 박으면 DB 를 조회할 수 있는 모든 사람에게
--        내부 엔드포인트 열쇠가 공개된다. Admin 화면(pg_cron 실행 이력)에도
--        명령문이 그대로 보인다. Vault 에 넣고 **이름으로 참조**한다.
--
--     ★ base_url 도 Vault 에 둔다. 비밀은 아니지만, 스테이징·운영이
--       **같은 SQL 을 그대로 쓰게** 되어 환경마다 파일을 고칠 일이 없다.
--       끝에 슬래시를 붙이지 않는다 (아래 함수가 '/internal/...' 를 이어 붙인다).
--
--     아래 두 줄의 <...> 를 실제 값으로 바꿔 **한 번만** 실행한다.
--     같은 이름으로 다시 넣으면 중복 행이 생기므로, 값을 바꿀 때는 6장을 참조한다.

-- select vault.create_secret('https://<앱주소>',  'internal_job_base_url', 'v2.0 앱 주소 (끝 슬래시 없이)');
-- select vault.create_secret('<backend/.env 의 INTERNAL_JOB_TOKEN 과 같은 값>',
--                            'internal_job_token', 'v2.0 내부 잡 엔드포인트 토큰');

--     ※ 운영 토큰은 로컬 개발용과 **다른 값**을 쓴다.
--       생성: python -c "import secrets; print(secrets.token_urlsafe(48))"
--     ※ 앱 쪽 환경변수(INTERNAL_JOB_TOKEN)와 **글자 하나까지 같아야** 한다.
--       다르면 앱이 404 를 주고, pg_cron 은 그것을 성공으로 기록한다(3장 참조).
--     ※ 앱 주소가 `DJANGO_ALLOWED_HOSTS` 에 들어 있어야 한다. 없으면 Django 가
--       400 Bad Request 로 끊는다 — 토큰과 무관하게 실패한다.


-- ────────────────────────────────────────────────────────────────────────────
--  1장. 호출 헬퍼 — ops.call_internal_job()
-- ────────────────────────────────────────────────────────────────────────────
--
--  cron 명령문마다 URL·헤더·타임아웃을 되풀이하지 않기 위한 함수다.
--  잡이 14개로 늘어나면 그 반복이 그대로 유지보수 부담이 된다.

create schema if not exists ops;
comment on schema ops is
  '운영 잡 호출 헬퍼. PostgREST 로 노출하지 않는다 (Exposed schemas 설정에 추가 금지).';

create or replace function ops.call_internal_job(
  job_slug   text,
  body       jsonb   default '{}'::jsonb,
  timeout_ms integer default 300000        -- 5분. 사유는 아래 ★ 참조
)
returns bigint
language plpgsql
-- security invoker(기본값)로 둔다. definer 로 만들면 권한이 낮은 롤도 이 함수를
-- 통해 잡을 실행할 수 있게 된다 — 내부 엔드포인트를 잠근 의미가 없어진다.
set search_path = ''                        -- 스키마를 전부 명시한다 (search_path 하이재킹 방지)
as $$
declare
  base_url   text;
  token      text;
  request_id bigint;
begin
  select decrypted_secret into base_url
    from vault.decrypted_secrets where name = 'internal_job_base_url';
  select decrypted_secret into token
    from vault.decrypted_secrets where name = 'internal_job_token';

  -- 막다른 길로 만들지 않는다 (규약 8.5) — 무엇을 해야 하는지까지 말한다.
  if base_url is null or token is null then
    raise exception
      'Vault 에 internal_job_base_url 또는 internal_job_token 이 없습니다.'
      using hint = 'pg_cron_jobs.sql 0.2 절의 vault.create_secret 두 줄을 먼저 실행하십시오.';
  end if;

  -- ★★ **끝 슬래시를 여기서 잘라낸다** (2026-08-16 추가) ─────────────────────
  --
  --   base_url 에 슬래시가 하나 남아 있으면 URL 이 `https://앱/​/internal/jobs/...`
  --   가 된다. 그러면 Django 가 보는 `path_info` 는 `//internal/jobs/...` 이고,
  --   미들웨어의 `startswith('/internal/')` 가 **거짓**이 된다.
  --
  --   결과가 고약하다 — 토큰 검사가 아예 돌지 않으므로 거부 로그도 남지 않고,
  --   URL 해석에서 404 가 난다. 운영자는 "토큰이 틀렸나?" 를 몇 시간 뒤진다.
  --   원인은 슬래시 한 글자다. **입력에서 잘라 그 상황 자체를 없앤다.**
  base_url := rtrim(base_url, '/');

  -- 스킴이 없으면 pg_net 이 요청을 만들지 못한다. 실패를 뒤로 미루지 않고 여기서 말한다.
  if base_url !~ '^https?://' then
    raise exception 'internal_job_base_url 이 http(s):// 로 시작하지 않습니다: %', base_url
      using hint = '스킴까지 넣어 다시 저장하십시오. 예) https://stock-coin-trade.vercel.app';
  end if;

  select net.http_post(
           url     := base_url || '/internal/jobs/' || job_slug,
           body    := body,
           headers := jsonb_build_object(
                        'Content-Type',    'application/json',
                        'X-Internal-Token', token
                      ),
           timeout_milliseconds := timeout_ms
         )
    into request_id;

  return request_id;
end;
$$;

-- ★ **타임아웃을 5분으로 잡은 이유** — pg_net 기본값은 5초인데, 실측
--   `sync_stock_master` 가 88초다(업종까지 받으면 15분). 기본값이면 응답을
--   **매번 버린다.** 잡 자체는 Django 에서 끝까지 돌지만, 상태코드와 본문
--   (notes·progress)이 사라져 `net._http_response` 에 타임아웃만 남는다.
--   `--with-sectors` 처럼 15분짜리를 부를 때는 호출부에서 timeout_ms 를 더 올린다.

-- 아무나 부르지 못하게 한다. 잡 실행은 cron(postgres)과 운영자만의 일이다.
revoke all on function ops.call_internal_job(text, jsonb, integer) from public;

-- ★ `anon` · `authenticated` 는 **Supabase 에만 있는 롤**이다. 로컬 Postgres 나
--   다른 호스팅에서 이 파일을 돌리면 `role "anon" does not exist` 로 **파일 실행이
--   통째로 멈춘다** — 그 뒤의 잡 등록이 하나도 안 된다.
--   있을 때만 회수한다. `revoke ... from public` 이 이미 기본 권한을 걷어냈으므로
--   이 블록이 건너뛰어져도 열려 있지 않다.
do $$
begin
  if exists (select 1 from pg_roles where rolname = 'anon') then
    execute 'revoke all on function ops.call_internal_job(text, jsonb, integer) from anon';
  end if;
  if exists (select 1 from pg_roles where rolname = 'authenticated') then
    execute 'revoke all on function ops.call_internal_job(text, jsonb, integer) from authenticated';
  end if;
end;
$$;


-- ────────────────────────────────────────────────────────────────────────────
--  1.5장. ★★ 배선 게이트 — **여기가 통과하기 전에는 2장으로 가지 않는다**
-- ────────────────────────────────────────────────────────────────────────────
--
--  ★★★ **왜 등록보다 먼저인가** ─────────────────────────────────────────────
--
--    pg_cron 은 `net.http_post` 가 request_id 를 돌려주면 **성공으로 기록한다.**
--    앱이 404 를 주든, 302 로 튕기든, 주소가 아예 틀렸든 `cron.job_run_details` 는
--    `succeeded` 다. 즉 **배선이 틀린 채로 등록하면 초록불만 보면서 아무 일도
--    일어나지 않는다.** 그 상태를 며칠 뒤에 발견하는 것이 최악이다.
--
--    그래서 **부수효과가 없는 요청 한 번**으로 왕복을 먼저 증명한다.
--    `GET /internal/jobs/` 는 등록된 잡 목록만 돌려준다 (config/internal_urls.py 의
--    `job_index`) — 종목 마스터를 실제로 적재해 보면서 배선을 점검할 수는 없다.
--
--  ★ 2026-08-16 실측 — Vercel 배포 URL 에는 **두 종류**가 있고 하나는 못 쓴다:
--
--      stock-coin-trade-<해시>-<팀>.vercel.app   → 302 vercel.com/sso-api  ✗ Django 에 도달 못 함
--      stock-coin-trade.vercel.app               → 200 / 404               ✓ 이것을 쓴다
--
--    `vercel ls` 가 알려주는 것은 **앞의 것**이다. Deployment Protection(SSO)이
--    걸려 있어 pg_net 이 302 만 받는다. base_url 에는 반드시 **안정 별칭**을 넣는다
--    (`vercel alias ls` 로 확인).

create or replace function ops.ping_internal()
returns bigint
language plpgsql
set search_path = ''
as $$
declare
  base_url   text;
  token      text;
  request_id bigint;
begin
  select decrypted_secret into base_url
    from vault.decrypted_secrets where name = 'internal_job_base_url';
  select decrypted_secret into token
    from vault.decrypted_secrets where name = 'internal_job_token';

  if base_url is null or token is null then
    raise exception 'Vault 에 internal_job_base_url 또는 internal_job_token 이 없습니다.'
      using hint = '0.2 절을 먼저 실행하십시오.';
  end if;
  base_url := rtrim(base_url, '/');

  -- ★ GET 이다. 잡을 돌리지 않는다 — 목록만 읽는다.
  select net.http_get(
           url     := base_url || '/internal/jobs/',
           headers := jsonb_build_object('X-Internal-Token', token),
           timeout_milliseconds := 20000
         )
    into request_id;
  return request_id;
end;
$$;

revoke all on function ops.ping_internal() from public;

--  실행 순서:
--
--      select ops.ping_internal();          -- ① 요청을 던진다
--      -- 10초쯤 기다린 뒤
--      select status_code, timed_out, error_msg, left(content, 300)   -- ② 결과를 본다
--        from net._http_response order by created desc limit 1;
--
--  ★ 읽는 법 — **200 이 아니면 2장으로 가지 않는다.**
--
--      200          ✓ 잡 목록 JSON 이 보인다. 진행해도 좋다
--      404          토큰 불일치. Vault 의 값과 Vercel 환경변수를 글자까지 대조한다
--                   (`vercel env ls production` 에 INTERNAL_JOB_TOKEN 이 있는지부터)
--      302 / 307    Deployment Protection 이 걸린 URL 이다. 안정 별칭으로 바꾼다 (위 ★)
--      308          base_url 끝 슬래시. 1장이 잘라 주지만, 그래도 나오면 Vault 값을 고친다
--      400          앱 주소가 DJANGO_ALLOWED_HOSTS 에 없다. 토큰과 무관하게 끊긴다
--      500          앱이 터졌다. Vercel 로그를 본다
--      행이 없다     pg_net 워커가 멎었다. `select net.worker_restart();`

-- ────────────────────────────────────────────────────────────────────────────
--  2장. 잡 등록
-- ────────────────────────────────────────────────────────────────────────────
--
--  `cron.schedule(잡이름, 스케줄, 명령)` 은 **같은 이름이면 덮어쓴다.**
--  그래서 이 파일 전체를 다시 실행해도 잡이 중복되지 않는다 (F-20 6장 멱등성).

-- ★★★ 잡 8 · 8b · sync_market_index — **등록하지 않는다. 로컬 CLI 로 돌린다** ★★★
--
--     2026-08-16 확인. 세 잡 모두 pykrx 를 쓰는데, **Vercel 번들에 pykrx 가 없다.**
--
--       루트 requirements.txt      Django · requests · … (pykrx **없음**)  ← Vercel 이 설치하는 것
--       backend/requirements.txt   pykrx==1.2.8                            ← 로컬 전용
--
--     `vercel.json` 의 `@vercel/python` 빌더는 **루트** requirements.txt 를 본다.
--     따라서 이 세 잡을 등록하면 `_load_pykrx()` 가 `ModuleNotFoundError` 로 죽는다.
--
--     ★ 설사 pykrx 를 번들에 넣어도 **두 번째 벽이 있다.** 2026-08-13 부터 KRX
--       데이터포털이 로그인을 요구해 `KRX_ID` · `KRX_PW` 가 필요한데, VERCEL.md 3.2 가
--       그 둘을 운영 환경변수에 넣지 않기로 정했다 (자격증명을 서버리스에 두지 않는다).
--
--     ★ 세 번째로 `sync_stock_master` 는 실측 88초다 (`--with-sectors` 는 15분).
--       Vercel 함수 실행 한도에 걸린다.
--
--     → **셋 다 로컬에서 돌린다.** 코드는 그대로다 — CLI 와 HTTP 가 같은 서비스
--       함수를 부르므로(F-16 5.3), 나중에 상시 기동 서버가 생기면 아래 주석만 풀면 된다.
--
--         대회 열기 전 1회   backend/.venv/bin/python manage.py sync_stock_master --with-sectors
--         매 영업일 장 마감 후  backend/.venv/bin/python manage.py sync_stock_master
--         매월 초             backend/.venv/bin/python manage.py sync_trading_calendar --year 2026
--         NAV 벤치마크        backend/.venv/bin/python manage.py sync_market_index
--
--     ★★ 연말 주의 — `sync_trading_calendar` 는 인자를 생략하면 **부르는 시점의
--        연도**만 채운다. 1월에 돌리면 전년 12월이 영영 빈 채로 남는다.
--        연초에는 `--year <전년도>` 로 한 번 더 돌릴 것.

-- 잡 8 — 종목 마스터 (F-20 표 8번) · KST 매 영업일 16:00 = UTC 월~금 07:00
-- select cron.schedule(
--   'sync_stock_master',
--   '0 7 * * 1-5',
--   $$ select ops.call_internal_job('sync-stock-master'); $$
-- );

-- 잡 8b — 영업일 달력 (F-20 표에 없는 추가분) · KST 매월 2일 06:00 = UTC 매월 1일 21:00
--   → 이 잡은 F-20 2장 표에 없다. 다음 문서 버전업 때 표에 추가할 것.
-- select cron.schedule(
--   'sync_trading_calendar',
--   '0 21 1 * *',
--   $$ select ops.call_internal_job('sync-trading-calendar'); $$
-- );

-- 잡 11 — 업비트 마켓 목록 (F-20 표 11번)
--   KST 매일 18:00 = UTC 매일 09:00
--   ★ 인증이 필요 없는 공개 API 라 자격증명 없이 돈다. 배선 점검용으로 가장 만만하다.
select cron.schedule(
  'sync_upbit_markets',
  '0 9 * * *',
  $$ select ops.call_internal_job('sync-upbit-markets'); $$
);


-- ────────────────────────────────────────────────────────────────────────────
--  2.1장. 체결 3종 — ★★ **아직 등록하지 않는다** (세션 9)
-- ────────────────────────────────────────────────────────────────────────────
--
--  잡 3(체결 추종) · 5(장 시작) · 6(장 마감)은 구현이 끝났고 엔드포인트도 열려 있다:
--
--      /internal/jobs/match-pending-orders
--      /internal/jobs/open-market
--      /internal/jobs/close-market
--
--  그런데 **아래 등록문은 주석 처리해 둔다.** 실행처가 정해지지 않았기 때문이다.
--
--  ★★ 등록 전에 반드시 읽을 것 — 비용 (변경노트 E-28 · E-36) ─────────────────
--
--    잡 3 은 장중 5초마다 돈다 = **하루 4,680회 · 월 93,600회.**
--    앱이 어디 있느냐에 따라 이 호출량의 값이 완전히 달라진다:
--
--      Vercel Hobby      Active CPU 월 4시간 · 함수 실행 10초 한도 · 비상업 개인용
--                        → 이 호출량은 한도에 닿을 수 있고, 넘으면 과금이 아니라 **중단**이다
--      상시 기동 서버     문제없다. 등록하면 된다
--      **로컬 PC**        `manage.py match_pending_orders --loop` — **비용 0 이고 확실하다**
--
--    대회 기간이 한 달이라면 **로컬 상시 기동이 가장 단순한 답**이다.
--    체결 로직은 셋 다 같은 함수를 쓰므로 나중에 옮겨도 코드는 그대로다 (F-16 5.3).
--
--  ★ **주기는 이 파일이 아니라 DB 설정이 정한다** — `AppSetting` 의
--    `trading.match_interval_seconds`(기본 5). `--loop` 가 그 값을 읽는다.
--    pg_cron 으로 돌릴 때는 아래 '5 seconds' 를 그 값과 맞춰 적는다.
--
--  ★ **pg_cron 은 '초 단위'와 '시간 제한'을 함께 표현할 수 없다.** `'5 seconds'`
--    스케줄에는 "장중에만" 을 붙일 수 없다. 그래서 **장 시작 잡이 켜고 장 마감 잡이
--    끈다.** 그러지 않으면 밤새 5초마다 헛호출이 나간다 (24시간이면 17,280회).
--    앱 쪽에도 방어가 한 겹 있다 — 잡이 스스로 `is_market_open()` 을 보고
--    장외에는 대회 주문을 건너뛴다 (`trading/jobs.py`).

/*  ── 실행처를 정한 뒤 이 블록의 주석을 푼다 ───────────────────────────────

-- 잡 5 — 장 시작 (F-20 표 5번). KST 09:00 = UTC 00:00, 월~금
--   PENDING_OPEN 주문을 접수로 올리고, **고빈도 잡 3종을 켠다.**
--   ★ 중첩 달러 인용($inner$)이 필요하다 — $$ 안에 또 $$ 를 쓸 수 없다.
--
--   ★★ **세 잡의 순서는 pg_cron 으로 보장되지 않는다** (세션 10 추가) ────────
--      각자 자기 주기로 돌 뿐이라, 시세 갱신 직전의 옛 값으로 체결되는 창이 생긴다.
--      주기를 호가 5초 · 시세 10초 · 체결 5초로 두면 **최악의 경우 5초 전 시세**로
--      체결되는데, 이는 낡은 캐시 허용치(5분 · F-16 3.4) 안이라 감수할 만하다.
--      순서까지 확실히 하려면 로컬 `manage.py run_trading_loop --loop` 를 쓴다.
select cron.schedule(
  'open_market',
  '0 0 * * 1-5',
  $$
    select ops.call_internal_job('open-market');
    select cron.schedule(
      'poll_orderbook',
      '5 seconds',
      $inner$ select ops.call_internal_job('poll-orderbook', '{}'::jsonb, 8000); $inner$
    );
    select cron.schedule(
      'poll_quotes',
      '10 seconds',
      $inner$ select ops.call_internal_job('poll-quotes', '{}'::jsonb, 8000); $inner$
    );
    select cron.schedule(
      'match_pending_orders',
      '5 seconds',
      $inner$ select ops.call_internal_job('match-pending-orders', '{}'::jsonb, 8000); $inner$
    );
  $$
);

-- 잡 6 — 장 마감 (F-20 표 6번). KST 15:35 = UTC 06:35, 월~금
--   **고빈도 잡을 먼저 끄고** 미체결 주문을 자동 취소한다.
--   순서가 중요하다 — 끄기 전에 취소하면 그 사이 5초 회차가 되살릴 수 있다.
--   ★ `cron.unschedule` 은 잡이 없으면 예외를 던진다. 존재를 확인하고 부른다.
select cron.schedule(
  'close_market',
  '35 6 * * 1-5',
  $$
    select cron.unschedule(jobname)
      from cron.job
     where jobname in ('match_pending_orders', 'poll_quotes', 'poll_orderbook');
    select ops.call_internal_job('close-market');
  $$
);

    ─────────────────────────────────────────────────────────────────────── */

-- 실행처가 정해지기 전이라도 **손으로 한 번 불러보는 것**은 언제든 된다:
--   select ops.call_internal_job('match-pending-orders');
-- 또는 로컬에서:
--   python manage.py match_pending_orders --dry-run


-- ────────────────────────────────────────────────────────────────────────────
--  2.2장. 정산 4종 + 벤치마크 — ★ **바로 등록해도 된다** (세션 11)
-- ────────────────────────────────────────────────────────────────────────────
--
--  잡 4(장중 스냅샷) · 7(일별 정산) · 9(주간 정산) · 10(대회 상태 전이)과
--  벤치마크 지수 적재다.
--
--  ★★ **2.1장의 체결 3종과 달리 실행처를 고민할 필요가 없다** ─────────────────
--
--    체결 잡은 5초마다 돌아 월 93,600회였다. 이쪽은 다르다:
--
--        잡 4   장중 10분 × 6.5시간 × 20영업일 =  월   780회
--        잡 7   영업일 1회                      =  월    20회
--        잡 9   주 1회                          =  월     4회
--        잡 10  매일 1회                        =  월    30회
--        지수   영업일 1회                      =  월    20회
--                                                  ─────────
--                                                  월 854회
--
--    Vercel Hobby 한도에 닿을 양이 아니다. **넷 다 DB 안의 데이터만 다루고**
--    (지수만 pykrx 호출 2건) 실행이 짧다.
--
--  ★★ **로컬 커맨드와 동시에 돌려도 안전하다** — 정산은 전부 멱등하다(F-05 4.3).
--    두 번 돌면 같은 행을 덮어쓸 뿐이다. 체결·시세 폴링과 결정적으로 다른 점이고,
--    그래서 4장의 "실행처는 하나만" 경고가 여기에는 적용되지 않는다.
--
--  ★ **잡 4·7 은 장중·영업일에만 의미가 있다.** pg_cron 에는 "영업일" 개념이 없으므로
--    스케줄은 요일로만 좁히고, **잡이 스스로 `TradingCalendar` 를 보고 조기 종료한다**
--    (contests/services.py 의 `is_intraday_window` · `contests_in_progress`).
--    공휴일에 헛호출이 나가지만 잡은 0건으로 끝나고 이력도 합쳐진다(coalesce_idle).

-- 잡 4 — 장중 스냅샷 (F-20 표 4번)
--   KST 장중 10분 = UTC 00:00~06:50 의 매 10분, 월~금
--   ★ UTC 0~6시가 KST 9~15시다. 시간 범위를 좁히지 않고 '*/10 * * * *' 로 두면
--     밤새 헛호출이 나간다(하루 144회 → 44회로 준다).
select cron.schedule(
  'snapshot_intraday',
  '*/10 0-6 * * 1-5',
  $$ select ops.call_internal_job('snapshot-intraday'); $$
);

-- 벤치마크 지수 — NAV 차트의 KOSPI·KOSDAQ 선 (F-05 4.4)
--   KST 매 영업일 15:38 = UTC 06:38, 월~금
--   ★ **일별 정산(15:40)보다 먼저 돌린다.** 순서가 뒤바뀌어도 차트가 하루 늦게
--     채워질 뿐이지만, 같은 날 것을 같은 날 갖추는 편이 읽기 쉽다.
--
--   ★★★ **등록하지 않는다 — pykrx 3종 중 하나다.** 위 2장 머리의 ★★★ 참조.
--        Vercel 번들에 pykrx 가 없고 KRX 자격증명도 없다. 등록하면 매 영업일
--        실패가 쌓여 Admin 배너가 상시 빨개지고, 정작 봐야 할 실패가 묻힌다.
--        → 로컬에서 `manage.py sync_market_index` 로 돌린다.
--        정산 자체는 이 잡과 무관하게 돈다 — 차트의 비교선만 비어 있을 뿐이다.
-- select cron.schedule(
--   'sync_market_index',
--   '38 6 * * 1-5',
--   $$ select ops.call_internal_job('sync-market-index'); $$
-- );

-- 잡 7 — 일별 정산 (F-20 표 7번)
--   KST 매 영업일 15:40 = UTC 06:40, 월~금
--   ★ 장 마감 잡(15:35)이 미체결 주문을 취소한 **뒤**에 돈다. 순서가 중요하다 —
--     취소 전에 정산하면 곧 사라질 주문이 그날 스냅샷에 잡힌다.
--   ★ 종가는 15:30 마지막 폴링값이다. 확정 종가로 다시 잡고 싶으면 16:00 의
--     `sync_stock_master` 뒤에 손으로 한 번 더 부른다 — 멱등하므로 덮어쓴다.
select cron.schedule(
  'settle_daily',
  '40 6 * * 1-5',
  $$ select ops.call_internal_job('settle-daily', '{}'::jsonb, 60000); $$
);

-- 잡 9 — 주간 정산 (F-20 표 9번)
--   KST 매주 월 06:00 = UTC **일요일** 21:00  ★★ 요일이 하루 앞으로 간다
--   검산: select (timestamptz '2026-08-17 06:00+09') at time zone 'UTC';  → 2026-08-16 21:00
--   ★ 지난 주를 확정하고 4회 위반자를 자동 정지한다. **유일한 자동 실격 경로다.**
select cron.schedule(
  'settle_weekly',
  '0 21 * * 0',
  $$ select ops.call_internal_job('settle-weekly', '{}'::jsonb, 60000); $$
);

-- 잡 10 — 대회 상태 전이 + 최종 정산 (F-20 표 10번)
--   KST 매일 06:10 = UTC 전날 21:10
--
--   ★★ **F-20 표는 06:00 인데 10분 늦춘다** (세션 11 · 변경노트) ─────────────
--
--     잡 9 도 월요일 06:00 이다. 둘이 같은 분에 걸리면 **pg_cron 은 순서를 보장하지
--     않는다.** 그런데 순서가 결과를 바꾼다:
--
--         회전율 확정(잡 9) → 4회 위반자 실격 → 최종 정산(잡 10)이 그를 랭킹에서 뺀다  ✅
--         최종 정산 먼저    → 실격 처리가 반영되지 않은 등급·순위가 확정된다         ✖
--
--     10분이면 DB 안에서만 도는 잡 9 가 끝나고도 남는다. **월요일에 종료되는 대회가
--     실제로 이 경로를 탄다** — 대회 종료일이 일요일이면 다음 날이 월요일이다.
select cron.schedule(
  'settle_contest',
  '10 21 * * *',
  $$ select ops.call_internal_job('settle-contest', '{}'::jsonb, 60000); $$
);

-- ★ 위 넷은 `timeout_ms` 를 60초로 올렸다. 참가자가 수백 명이면 기본 5초로는
--   응답을 못 받고, 그러면 notes·progress 가 통째로 사라진다(1장 주석 참조).
--   잡 자체는 계속 돌지만 **무엇을 했는지 알 수 없게 된다.**


-- ────────────────────────────────────────────────────────────────────────────
--  3장. 확인 — ★★ **어디를 봐야 진짜인가**
-- ────────────────────────────────────────────────────────────────────────────
--
--  `net.http_post()` 는 **요청 id 를 즉시 반환하는 비동기 함수**다. 실제 HTTP 는
--  pg_net 워커가 나중에 보낸다. 그래서:
--
--      cron.job_run_details.status = 'succeeded'
--        ↑ **잡이 성공했다는 뜻이 아니다.** "요청을 큐에 넣었다" 는 뜻이다.
--          앱이 꺼져 있어도, 토큰이 틀려 404 를 받아도 여기는 succeeded 다.
--
--  판정 순서는 셋이다:
--    ① cron.job_run_details  — SQL 이 제 시각에 돌았는가
--    ② net._http_response    — 응답이 몇 번으로 왔는가 (여기서 404/503 이 보인다)
--    ③ data_sync_log         — ★ 잡이 무엇을 했는가. **진실은 여기다**

-- 3.1 등록된 잡 (스케줄은 UTC)
select jobid, jobname, schedule, active, command
  from cron.job
 order by jobname;

-- 3.2 최근 실행 — ①
select d.runid, j.jobname, d.status, d.return_message,
       d.start_time at time zone 'Asia/Seoul' as 시작_KST
  from cron.job_run_details d
  left join cron.job j on j.jobid = d.jobid
 order by d.start_time desc
 limit 20;

-- 3.3 HTTP 응답 — ② ★ 실패 원인이 실제로 보이는 곳
--     status_code 404 → 토큰 불일치이거나 경로 오타 (앱은 둘을 구분해 주지 않는다.
--                       구분은 앱 로그에 있다 — core/middleware.py 가 사유를 찍는다)
--     status_code 400 → DJANGO_ALLOWED_HOSTS 에 앱 주소가 없다
--     status_code 409 → 3회 연속 실패로 자동 비활성화된 잡 (F-20 5장)
--     status_code 503 → 외부 데이터 소스 장애 (KRX·업비트). 우리 잘못이 아니다
--     timed out       → 잡이 timeout_ms 보다 오래 걸렸다. 잡 자체는 계속 돌고 있다
--     ※ pg_net 은 응답을 몇 시간만 보관한다. 오래된 것은 사라진다 — 그래서 ③ 이 필요하다.
select id, status_code, timed_out, error_msg,
       created at time zone 'Asia/Seoul' as 수신_KST,
       left(content, 400) as 본문
  from net._http_response
 order by created desc
 limit 20;

-- 3.4 잡이 실제로 한 일 — ③ **여기가 진실이다**
--     notes·progress 는 응답 본문(3.3)에 있고, 여기에는 결과 숫자와 스택트레이스가 남는다.
select job_name, status, rows_affected, triggered_by,
       started_at  at time zone 'Asia/Seoul' as 시작_KST,
       finished_at at time zone 'Asia/Seoul' as 종료_KST,
       left(error, 300) as 오류
  from public.data_sync_log
 order by started_at desc
 limit 20;

-- 3.5 손으로 한 번 돌려보기 (스케줄을 기다리지 않고)
--     ★ dry_run 으로 먼저 친다. DB 를 건드리지 않고 배선만 확인한다.
--       그다음 3.3 에서 status_code 200 을 확인한다.
-- select ops.call_internal_job('sync-upbit-markets', '{"dry_run": true}'::jsonb);


-- ────────────────────────────────────────────────────────────────────────────
--  4장. 해제 · 일시 중지
-- ────────────────────────────────────────────────────────────────────────────

-- 잠시 멈춘다 (등록은 남긴다)
-- update cron.job set active = false where jobname = 'sync_stock_master';

-- 완전히 지운다
-- select cron.unschedule('sync_stock_master');

-- ※ Admin 대시보드 배너가 `active = false` 인 잡 수를 세어 보여준다.
--   꺼놓고 잊는 사고를 막기 위한 것이다 (core/job_health.py).


-- ────────────────────────────────────────────────────────────────────────────
--  5장. F-20 잡 14종 — 등록 현황 한눈에
-- ────────────────────────────────────────────────────────────────────────────
--
--  | # | 잡                   | KST          | UTC 스케줄      | 상태                    |
--  |---|----------------------|--------------|-----------------|-------------------------|
--  | 4 | snapshot_intraday    | 장중 10분    | '*/10 0-6 * * 1-5' | ✅ 2.2장에 등록됨    |
--  | 5 | open_market          | 영업일 09:00 | '0 0 * * 1-5'   | 2.1장 — 실행처 미정     |
--  | 6 | close_market         | 영업일 15:35 | '35 6 * * 1-5'  | 2.1장 — 실행처 미정     |
--  | 7 | settle_daily         | 영업일 15:40 | '40 6 * * 1-5'  | ✅ 2.2장에 등록됨       |
--  | 9 | settle_weekly        | 월 06:00     | '0 21 * * 0'    | ✅ ★★ 일요일로 당겨진다 |
--  |10 | settle_contest       | 매일 06:10   | '10 21 * * *'   | ✅ ★ 잡 9 와 10분 벌림  |
--  |12 | sync_crypto_rank     | 매시 정각    | '0 * * * *'     | 서비스 함수 없음        |
--  |13 | sync_krx_news        | 5분          | '*/5 * * * *'   | 서비스 함수 없음        |
--  |14 | cleanup              | 매일 04:00   | '0 19 * * *'    | 서비스 함수 없음        |
--
--  ※ 표에 없는 추가분: 잡 8b `sync_trading_calendar`(월 1회) ·
--     `sync_market_index`(영업일 15:38 · F-05 4.4). 다음 문서 버전업 때 표에 넣을 것.
--
--  잡 4·7 은 **장중·영업일에만** 의미가 있다. pg_cron 에는 그 개념이 없으므로
--  잡 안에서 `TradingCalendar` + 현재 시각으로 판정해 조기 종료한다.


-- ────────────────────────────────────────────────────────────────────────────
--  5-1장. 잡 1·2·3 — **실행처는 하나만 고른다** ★★ (세션 10)
-- ────────────────────────────────────────────────────────────────────────────
--
--  세션 10 에서 잡 1·2 의 서비스 함수와 엔드포인트가 준비됐다. 이제 셋을 돌리는
--  길이 둘이고, **둘을 함께 켜면 안 된다.**
--
--    ① 로컬 상시 기동  python manage.py run_trading_loop --loop   ← 대회 기간의 기본
--    ② pg_cron         2장의 open_market 블록 주석을 푼다         ← 상시 서버가 생기면
--
--  ★ **둘 다 켜면 같은 종목을 두 배로 조회한다.** KIS 는 초당 몇 건뿐이라
--    (변경노트 E-43) 유량이 절반으로 줄고, 폴링 주기가 사실상 두 배로 늘어난다.
--    `ApiCallBudget` 이 전역이라 서로를 밀어내기까지 한다.
--
--  ★ ①이 나은 점은 **순서 보장**이다 — 호가 → 시세 → 체결을 한 프로세스에서
--    이어서 돌리므로(F-20 2.1) 옛 시세로 체결되는 창이 없다. 비용도 0 이다.
--  ★ ②가 나은 점은 **PC 를 꺼도 돈다**는 것이다.
--
--  ※ 이력 걱정은 하지 않아도 된다. 세 잡 모두 `job_run(coalesce_idle=True)` 라
--    아무 일도 없던 회차는 새 행을 만들지 않고 직전 무변화 행의 finished_at 만
--    민다 (변경노트 E-37). 하루 8,640행이 쌓이던 문제는 이미 해결돼 있다.
--
--  ※ 장중 판정도 잡이 스스로 한다 — `match_pending_orders` 는 장외에 대회 주문을
--    건너뛰고, `run_trading_loop --market-hours-only` 는 회차 자체를 건너뛴다.


-- ────────────────────────────────────────────────────────────────────────────
--  6장. 토큰 교체 (rotate)
-- ────────────────────────────────────────────────────────────────────────────
--
--  ★ 순서가 중요하다. 반대로 하면 그 사이에 도는 잡이 전부 404 로 실패한다.
--    ① 앱의 INTERNAL_JOB_TOKEN 을 새 값으로 바꾸고 **재배포**한다
--    ② 아래로 Vault 값을 갱신한다 (cron 명령문은 고칠 필요가 없다 —
--       이름으로 참조하므로 다음 실행부터 새 값을 읽는다)
--
-- select vault.update_secret(
--          (select id from vault.secrets where name = 'internal_job_token'),
--          '<새 토큰>'
--        );
--
--  ※ ①과 ② 사이에는 잡이 실패한다. 실패가 3회 쌓이면 자동 비활성화(F-20 5장)에
--    걸리므로, 교체는 짧게 끝내고 Admin 배너를 확인한다.
