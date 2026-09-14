# AGENTS.md — `stock-coin-trade` 작업 규칙

> 이 저장소에서 AI 에이전트(Claude Code · Cursor · Windsurf · Cline)가 지켜야 할 규칙의 **정본**이다.
> Claude Code 는 이 파일을 직접 읽지 않는다 — [`CLAUDE.md`](CLAUDE.md) 가 `@AGENTS.md` 로 임포트한다.
>
> 상위 규칙: 모노레포 루트 `EST-Camp-AI-Quant/AGENTS.md` · 사용자 전역 `~/.claude/CLAUDE.md`
> 충돌하면 **상위가 이긴다.** 여기에는 이 저장소에만 해당하는 것을 적는다.
>
> **현재 어디까지 왔는지는 [`세션-시작-프롬프트.md`](세션-시작-프롬프트.md) 가 답한다.**

---

## 1. 이 저장소는 무엇인가

**키움증권 모의투자 대회용 — 섹터 ETF 레이더.**
"지금 어느 섹터가 유망한가"를 **근거와 함께** 판단하는 Streamlit 대시보드다.
개발은 소유자 1인, 사용은 팀 7명. 비용 0원.

| | 무엇 |
|---|---|
| **v3.0** ★ 현재 | `streamlit_app.py` · `sector/` · `batch/` · `dashboard/` — Streamlit Community Cloud |
| **v2.0** 동결 | `backend/` Django 5.2.17 + DRF — 모의투자 플랫폼. **개발하지 않는다** |

### 🔒 "동결" 의 뜻 — 방치가 아니다

`backend/`(26,597줄 · 테스트 384건)는 **개발하지 않으나 계속 돌아야 한다.**

```bash
cd backend && .venv/bin/pytest          # 골든 23건 · DB 불필요 ← 항상 통과해야 한다
```

이 한 줄을 M1·M7·M14 의 완료 조건에 넣었다. 넣지 않으면 이 규칙이 거짓말이 된다.
동결 해제 시점은 대회 종료 후에 다시 판단한다. → [ADR-SC-0005](docs/decisions/0005-산출물-재지정과-이력-재시작.md)

### 이력은 2026-09-11 에 재시작했다

강사님 원본(`edumgt/stock-coin-trade`)의 fork 로 출발했으나, 목적이 바뀌면서
**orphan 으로 이력을 다시 시작하고 강사님 코드를 새 이력에 담지 않았다.**
그 이유(시크릿·라이선스·타인 커밋)와 백업 위치는 ADR-SC-0005 에 있다.

- 옛 이력: `stock-coin-trade-fork-history-20260911.bundle` (모노레포 상위 폴더, gitignore)
- 강사님 원본: `upstream` 원격(fetch 전용) · 모노레포 `learning/th03-stock-coin-trade/lecture/`
- ⚠️ **ADR-SC-0001(fork 계보 유지)·0002(체결엔진 원본)는 폐기됐다.** 그 문서를 근거로 삼지 마라.

---

## 2. 원격과 계정 ★ 가장 조심할 곳

| 원격 | 대상 | 용도 |
|---|---|---|
| `origin` | `gitlab.com/dev-dongwon05253/stock-coin-trade` | **정본.** 평소 push 대상. ⚠️ `est-` 접두사 없음 |
| `github-est` | `github.com/EST-Bootcamp-Dongwon/stock-coin-trade` | 동시 push 유지 (⚠️ Flagged 계정) |
| `upstream` | `github.com/edumgt/stock-coin-trade` | fetch 전용. push URL 차단됨 |
| *(로컬 원격 없음)* | `github.com/devlee328288/stock-coin-trade` | 🔒 **GitLab push-mirror 로만 간다** |

```bash
git push origin main        # GitLab — 미러가 devlee328288 GitHub 을 따라온다
git push github-est main    # Flagged 계정이라 실패할 수 있다. 실패해도 무방
```

### 🔒 지켜야 할 것

1. 🔒 **`devlee328288` 원격을 로컬 git 에 절대 추가하지 않는다.**
   GitLab 이 자기 서버에서 devlee328288 토큰으로 push 하므로 **오push 경로가 존재하지 않는다.**
   규칙으로 막으면 언젠가 어기지만, 없는 경로는 밟을 수 없다.
2. 🔒 **모든 git·gh 작업 전에 `gh auth status` 로 활성 계정을 확인하고 보고한다.**
   사용자는 `dev-dongwon05253`(개인 · Flagged)과 `devlee328288`(팀 · 공개)을 **오간다.**
   세션 중간에도 바뀐다.
3. 🔒 **세션 마무리 복붙 프롬프트 끝에 항상 현재 `gh` 활성 계정을 적는다.**
4. ⚠️ **push mirror 는 force push 로 동작한다.** GitHub 미러에서 직접 커밋하면 사라진다.
   Issues·Projects·Wiki 를 끄고 README 최상단에 "읽기 전용 미러"를 명시한다.
5. 되돌리기는 `git revert`. **`reset --hard` · force push · 히스토리 재작성은 사용자 승인 후에만.**

→ [ADR-SC-0008](docs/decisions/0008-배포-streamlit-전환과-원격-재구성.md)

---

## 3. 절대 제약 — 이걸 어기는 제안은 하지 않는다

| # | 제약 | 근거 |
|---|---|---|
| 1 | **LLM 유료 API 비용 0원** | 강사님 방침 |
| 2 | **매매 신호 생성 경로에 LLM 금지** | 비결정성 · 재현 불가 · 사전학습 룩어헤드 오염 |
| 3 | RAG·Agent 는 신호 경로와 **물리적으로 분리된 별도 서비스** | 2번의 귀결 |
| 4 | AWS 등 클라우드 인프라 미사용 | 강사님 방침 |
| 5 | Docker 이미지 용량 최소화 | 로컬 디스크 제약 |
| 6 | **CI/CD 를 필수 경로에 두지 않음** | GitHub 계정 리스크. 검증은 로컬에서 동일하게 돌아야 한다 |
| 7 | **헤비 프론트 프레임워크 금지** (Next.js · React SPA · 번들러) | 강사님 방침 |
| **8** | 🔴 **값을 지어내지 않는다** — 취득 실패는 예외이거나 "없음" 표시 | [ADR-SC-0007](docs/decisions/0007-값을-지어내지-않는다.md) |
| **9** | 🔴 **국내 데이터만.** 미국 주식·해외 원천 미사용 | 대회가 국내장 |
| **10** | 🔴 **KRX 원천 데이터를 저장소·HF 에 올리지 않는다.** 파생·집계값만 | [ADR-SC-0006](docs/decisions/0006-krx-데이터-제3자-제공-금지.md) · 약관 제11조② |
| **11** | 🔴 **`gh` 활성 계정을 작업 전마다 확인한다** | 2장 |
| **12** | 🔴 **화면에 "한국거래소 통계정보" 출처를 표시한다** | 약관 제10조③ |

### 데이터 취득 경로 — 조사로 확정된 것 (2026-09-11)

| 경로 | 판정 |
|---|---|
| **KRX Open API** `data-dbg.krx.co.kr/svc/apis/` | ✅ **주 경로.** `AUTH_KEY` 헤더 · 인증키 + **개별 API 승인** · 키당 일 10,000회 |
| **KIS 한국투자증권** | ✅ **준실시간 시세.** `MARKET_DIV_CODE="J"` 가 ETF 포함 |
| DART OpenAPI · ECOS · 언론사 RSS | ✅ 공식 |
| 네이버 검색 API | ⚠️ 2026-09-07 특약 — **저장·가공·재정렬 금지**, 보관 21일. **조회 시점 링크만** |
| **`pykrx`** | 🔴 **쓰지 않는다.** KRX FAQ 가 이름을 지목해 IP 차단 경고 |
| `finance.naver.com` · `api.finance.naver.com` · `m.stock.naver.com` | 🔴 robots `Disallow: /` |
| `polling.finance.naver.com` | 🔴 robots 는 404지만 **네이버 약관이 봇 자동수집 금지** |
| yfinance · 해외 데이터 | 🔴 제약 9 |

### 보안 — Public 저장소다

- 🔒 **`.env` · KIS `appkey`/`appsecret` · KRX·HF·DART 키를 커밋하지 않는다.**
  `.gitignore` 가 `.env*` 를 막고 `!.env.example` 로 예시만 푼다. **이 순서를 뒤집지 마라.**
- 🔒 **`.gitignore` 에 `.streamlit/secrets.toml` 을 별도로 넣는다** — `.env*` 패턴이 못 막는다.
- 🔒 **HF 토큰은 이름이 역할을 말한다** (2026-09-11 · M6):
  `HF_TOKEN_WRITE`(배치 전용 · 루트 `.env` **만**) / `HF_TOKEN_READ`(앱 · Streamlit Secrets) /
  `HF_TOKEN_NOTES_WRITE`(M13 · `team-notes` 그 저장소 하나에만).
  🔴 옛 이름 `HUGGINGFACE_ACCESS_TOKEN` 을 버린 이유 — "ACCESS" 가 read 인지 write 인지
  이름으로 알 수 없어 **org 전체 쓰기 토큰이 앱용 칸에 들어가 있었다.** 그대로 배포했다면
  쓰기 권한이 Streamlit Secrets 로 넘어간다. 시크릿은 `sector/secret_access.py` 하나로만 읽는다.
- 🔒 **Supabase 키도 이름이 역할을 말한다** (2026-09-12 · M8): `SUPABASE_URL` ·
  `SUPABASE_ANON_KEY`(앱·배치 공용 · 공개 전제). 🔴 **`service_role`·`sb_secret_` 키를
  어느 칸에도 넣지 않는다** — RLS 를 통째로 우회하고 v2.0 유산 53개 테이블 전부에 닿는다.
  `sector/workspace/store.py` 가 그 두 형태를 생성 시점에 거부한다.
- `.env` 가 둘이다 — 루트 `.env`(v3.0·v1.0) / `backend/.env`(v2.0 Django 가 읽는 유일한 것).
- push 전 `git status --short` 로 PDF·ZIP·데이터 원본 혼입을 확인한다.

---

## 4. 코딩 규약

- Python 3.12 · 들여쓰기 **4 spaces**(파이썬) / 2 spaces(설정 파일) · ruff line-length 100
- 모든 대화·주석·문서는 **한국어**. 변수·함수명만 영어(`snake_case`)
- 날짜·시간은 **KST** 기준으로 사고하되 저장은 UTC. 영업일 키는 `bas_dd` (`"YYYYMMDD"` 문자열 —
  사전순 = 날짜순이라 `<=` 비교가 그대로 통한다)
- 금액은 **정수(원)**, 비율은 **bp 정수 + `Decimal`**. `float` 금지 — 누적 반올림이 샌다
  - 스코어링 중간 계산은 numpy float 를 쓰되 **저장·스냅샷 직전에 bp 정수로 양자화**한다
- 복잡한 비즈니스 로직에는 **왜** 를 적는 한국어 주석을 단다. 무엇을 하는지는 코드가 말한다
- 커밋 메시지: `<scope>: <동사원형 요약>` (예: `sector: add momentum axis`)
- 문서 문체: 개조식("-다"). 튜토리얼만 "-습니다"
- 다이어그램은 Mermaid `flowchart` + `subgraph`. **`C4Context`/`C4Container` 문법 금지**
  (GitHub 이 렌더링하지 못한다)
- ADR 은 `docs/decisions/NNNN-title.md`, 참조 키는 **`ADR-SC-NNNN`**

---

## 5. 테스트 — 러너가 셋이다. 섞지 마라

| 러너 | 대상 | 언제 |
|---|---|---|
| `pytest` (저장소 루트) | `sector/scoring_golden_test.py` — 순수 함수 · **DB 미사용** | v3.0 정본 |
| `cd backend && .venv/bin/pytest` | `trading/golden_test.py` **23건** | **동결 회귀.** 항상 통과해야 한다 |
| `cd backend && manage.py test` | `*/tests.py` (Django `TestCase` · DB 사용) | 동결 회귀 |

- 🔒 **루트 `pytest.ini` 에 `DJANGO_SETTINGS_MODULE` 을 넣지 않는다.** `testpaths = sector`.
- 🔒 **파일명이 `scoring_golden_test.py` 인 것은 우연이 아니다** — pytest 의 `*_test.py` 는 줍고
  Django 의 `test*.py` 는 안 줍는다. `test_scoring.py` 로 바꾸면 양쪽이 다 주워
  `snapshot` 픽스처가 없다며 깨진다.
- 🔒 **골든 테스트를 먼저, 튜닝을 나중에.** 값이 바뀐 이유를 설명할 수 있을 때만 `--snapshot-update`.
- 테스트는 **언제 돌려도 같은 답**을 내야 한다. 시계는 입력이지 환경이 아니다.
- 🔒 **`test_asof_monotone`** — `data[:T+30]` 으로 계산한 `score(T)` 와 `data[:T]` 로 계산한
  값이 **정확히 같아야 한다.** 이 한 줄이 룩어헤드를 구조적으로 잡는다.
- 🔒 **픽스처에 실제 KRX 데이터를 커밋하지 않는다.** 합성 데이터를 쓴다 (제약 10).

---

## 6. 이미 확정된 것 — 다시 논의하지 않는다

- **섹터는 2계층이다** — GICS 11 대분류(상위) + 한국 테마 ETF(하위). 각 섹터를
  **ETF 렌즈**와 **구성종목 렌즈** 둘로 본다. 🔒 **갈라지면 갈라진 채로 보여준다.** 평균 내지 않는다
- **스코어링은 4축이다** — 모멘텀 35 / 자금흐름 30 / 폭 20 / 밸류 15.
  🔒 **거래대금을 점수에 넣지 않는다** (Lee & Swaminathan 2000: 고회전은 오히려 미래 수익률이
  낮다). 거래대금은 **유동성 게이트**로만 쓴다 — 일평균 1억 미만이면 경고 배지
- **뉴스·공시로 관심 축을 만들지 않는다.** 네이버 특약이 저장·가공을 금지하고, DART 공시 건수는
  정기보고서 계절성이 지배하며 유상증자(악재)와 공급계약(호재)이 같은 점수가 된다.
  **부호가 섞인 지표는 축이 될 수 없다.** 뉴스·공시는 **타임라인 맥락**으로만 쓴다
- **자금흐름 축은 ETF 상장좌수(`LIST_SHRS`) 변화만 본다.** 순자산총액은 좌수 × NAV 라
  NAV 변화를 모멘텀 축과 이중 계산하게 된다
- **섹터 구성종목은 `sector/config/sectors.yaml` 에 사람이 손으로 적는다.**
  KRX Open API 에 지수 구성종목·ETF PDF 가 없다. 🔒 **모든 섹터에 `note`(왜 이렇게 묶었나)를
  1줄 이상 쓴다** — 근거 없는 묶음을 막는 게이트다
- **배포는 둘을 공존시킨다** ★ (2026-09-12 개정 · [ADR-SC-0010](docs/decisions/0010-두-배포-공존과-쓰기-상태-분리.md))
  — **Streamlit Cloud = 팀용 정본**(대회 기간 매일 쓴다) · **Django + Vercel = 확장·쇼케이스**.
  🔒 **Streamlit 을 먼저 끄지 않는다.** 대회가 도는 중이다
  - ★ **의존성은 이미 갈려 있다** (2026-09-12 실측 · ADR-SC-0010 ③ 개정) — Vercel 은
    **`api/requirements.txt`**(slim)를, Streamlit 은 루트 `requirements.txt` 를 읽는다.
    🔴 **Vercel Root Directory 를 비워 둔다** — `backend/` 로 바꾸면 `vercel.json` 의
    `builds` 가 `backend/api/index.py` 를 찾다 실패해 **함수 0개**로 배포되고,
    `.vercelignore` 가 따라가 `/data/`(KRX 원천)·`.streamlit/`(시크릿) 방어까지 사라진다
    (→ `VERCEL.md` 0.1). 🔒 엔트리포인트를 하위 폴더로 옮기지 않는다 — Streamlit 은
    스크립트 폴더만 `sys.path` 에 넣어서 `import sector` 가 깨진다
  - 🔒 **두 화면이 점수·서술을 각자 구현하지 않는다.** `sector/` 와 `dashboard/view.py`·
    `explain.py` 가 공유 코어다. 렌더러 B 가 코어를 안 쓰면 **검증되지 않은 화면**이다
  - ⚠️ `backend/` 26,597줄은 *다른 제품*(모의투자 플랫폼)이다. 물려받는 것은
    `templates/base.html`·`_partials/`·Tailwind·HTMX/Alpine **패턴뿐**이고 섹터 화면은 새로 쓴다
  - ⚠️ ~~ADR-SC-0008 ②③(Vercel 해제 · Supabase 미사용)~~ 은 **폐기됐다.** 근거로 삼지 마라
- **쓰기 상태는 Supabase, 파생값은 HF — 이중 보관** ★ (2026-09-12 · ADR-SC-0010)
  - **팀 원장**(조·참가·확정·코멘트) → Supabase `stock-coin-trade`(`sgbhrahtewojmicwmxxu`).
    동시 쓰기·실시간·RLS 가 여기서 값을 한다
  - **파생 집계·점수** → HF private dataset. 🔴 **Supabase Free 는 DB 500MB 다**
    (5GB 는 egress). `score_daily` 는 3개월이면 수십MB로 자라 여기 둘 물건이 아니다
  - 🔴 **제약 10 은 Supabase 에도 붙는다.** 올라가는 것은 **파생값뿐**이다
  - 7일 pause 는 **매일 도는 `batch.publish` 가 한 줄 써서** 푼다.
    🔒 GitHub Actions keep-alive 를 쓰지 않는다 (제약 6)
  - 🔴🔴 **RLS 없이 원장을 넣지 않는다.** 순서는 **스키마 → RLS 정책 → 마이그레이션 → 데이터**
- **원장 스키마와 RLS** ★ (2026-09-12 · [ADR-SC-0011](docs/decisions/0011-팀-원장-supabase-스키마와-rls.md))
  마이그레이션은 `supabase/migrations/` 에 **SQL 로 남는다** (Vercel 설정과 달리 코드로 남는다)
  - 🔴 **W12 는 과소평가였다.** "0행이라 무해" 는 *읽기* 관점이고, 실제로는 `anon` 에게
    53개 전부 **INSERT·UPDATE·DELETE·TRUNCATE** 가 있었다. `external_token`·`api_key`
    가 그 안에 있다 — **비어 있을 때 닫는다**
  - ⚠️ ~~"정책 없이 RLS 만 켜면 앱이 죽는다"~~ 는 **anon 경로에만** 해당한다.
    실측상 53개 소유자는 `postgres` 이고 **`rolbypassrls = true`** 다 — Django 는 우회한다.
    그래서 v2.0 유산은 **정책 0개로 전면 차단**한다. 정책을 안 두는 것이 곧 정책이다
  - 🔒 **원장은 테이블 하나**(`workspace_event`)다. 정규화하면 `fold.py` 를 버리게 된다
  - 🔒 **`at` 은 `timestamptz` 가 아니라 `text`** — `event_id` 가 `at` **문자열**의
    해시라 표기가 한 글자만 바뀌어도 `parse_event` 가 원장 전체를 거부한다
  - 🔴 **passcode 해시는 원장 밖**(`workspace_team_secret` · 정책 0개 · GRANT 0).
    원장 쪽에 `check (not (payload ? 'passcode_hash'))` 를 걸어 DB 가 규칙을 지킨다
  - 🔴 **쓰기는 RPC 하나뿐이다.** anon 키는 공개 전제이므로 테이블 INSERT 를 열면
    passcode 가 지키기로 한 것이 뚫린다. 🔒 **`service_role` 키를 앱에 두지 않는다**
    (HF 토큰을 `WRITE`/`READ` 로 가른 것과 같은 판단). `SupabaseStore` 가 `sb_secret_`
    와 `role≠anon` JWT 를 **생성 시점에 거부**한다
  - ⚠️ **원장 읽기는 공개다** — HF private 대비 실질 변화다. **코멘트에 비밀을 적지 않는다**
- **passcode 검증은 화면이 아니라 저장소가 한다** ★ (2026-09-12 · ADR-SC-0011 ⑨⑩⑪⑫)
  - 화면은 해시를 손에 들지 않는다. `passcode_params`(digest 없음) → `auth.recompute_passcode`
    → `store.verify` 순이고 **참·거짓만 받는다**. `fold.Team` 에 `passcode_hash` 가 없다
  - 🔒 **`append` 로는 조를 만들 수 없다.** `create_team` 만이 만든다 — 안 막으면
    passcode 없는 조가 목록에 보이면서 아무도 참가할 수 없다
  - 🔒 원장에 쓸 때마다 **자격증명**(`scrypt$…$digest`)이 필요하고 세션이 들고 있다.
    평문이 아니고, 그 조에서만 쓸 수 있다
  - ✅ **마스터(개발자)는 없다** ★ (2026-09-12 · ADR-SC-0011 ⑫ 로 닫혔다 · V42).
    보관은 **만든 사람**이, 복구는 «보관된 조» 칸에서 **그 조의 passcode** 가 한다.
    🔴 마스터 RPC 를 더하지 않은 이유 — 해시 하나가 **모든 조**에 대한 쓰기 권한이
    되어 `service_role` 키를 앱에 두는 것과 같은 모양이 된다.
    🔒 **권한 0 인 마스터를 남겨 두지 않았다** — `ADMIN_PASSCODE_HASH` 는 아무것도
    열지 않으므로 시크릿에서 지운다. 대가는 "잘못 만든 조를 개발자가 못 치운다"
  - 🔴 **원장은 Supabase 가 먼저다**(`data.workspace_store()` — Supabase → HF → 로컬).
    시크릿이 **하나라도** 있으면 폴백하지 않고 **그대로 던진다** — 조용히 내려가면
    팀이 서로 다른 원장에 쓰고, `service_role` 키 사고가 침묵에 묻힌다
  - 🔒 옛 원장(payload 에 해시가 있는 것)은 **읽히되** `fold.anomalies` 가 "옛 형식 ·
    참가할 수 없다" 고 말한다. 읽기를 막으면 한 줄이 팀 전체 화면을 죽인다
  - 🔒 append-only 는 **트리거**가 지킨다. 소유자도 못 지운다. 되돌리기는 반대 이벤트
  - 🔴 **원장 한 줄은 읽기를 멈추지 않는다** (2026-09-14 · ADR-SC-0011 ⑬). RPC 는 `event_id` 를
    재계산하지 않아 내용과 어긋난 줄이 들어올 수 있고 지울 수 없다. 🔒 `read_all()` 은
    `Ledger(events, rejected)` 를 돌려주고 **`fold.fold(ledger)` 로 넘긴다** — 목록만 꺼내 접으면
    읽지 못한 줄이 조용히 사라진다. 🔒 내용이 틀린 입력은 전부 `EventError` 로 올리고,
    **닿지 못한 것(파일 읽기 · 네트워크 · 상한)은 여전히 던진다**
  - 🔒 클라이언트는 **`requests` + PostgREST**. `supabase-py`·`psycopg` 를 넣지 않는다
    (루트 `requirements.txt` 는 4줄이고 Streamlit Cloud 메모리를 직접 깎는다)
- **근거 첨부 — 링크는 서버가 부르지 않는다** ★ (2026-09-14 · [ADR-SC-0012](docs/decisions/0012-근거-첨부와-확정-모달.md))
  저장하고 `<a>` 로 그릴 뿐이다 — **SSRF 표면 0.** 🔒 `links.normalize_link` 는 SSRF 가드가
  **아니다**(팀원 브라우저가 내부망 · 기만 링크로 가는 것을 막는다). 미리보기(fetch)를 붙이려면
  **연결 시점 IP 검사부터** 설계한다. 🔒 검사는 쓸 때와 **읽을 때(`fold`)** 두 번 — RPC 가 앱을 건너뛴다
- **사람이 쓴 글은 HTML 블록으로만 그린다** 🔴 (2026-09-14 · ADR-SC-0012 ④ · Y9)
  Streamlit 1.63 은 `unsafe_allow_html` 을 **정화하지 않고**, 마크다운 역슬래시 이스케이프는
  **GFM 자동 링크를 못 막는다.** 🔒 사람 글(이름 · 사유 · 근거 · 원장에서 온 섹터 id · 저장소
  오류 문장)은 `theme.esc`+`html_line` · `user_block` · `links_block` · `failure` 로만 내보낸다.
  🔒 위젯 라벨 · 모달 제목 · `st.success` · `st.error` 에 사람 글을 넣지 않는다
- **확정은 랭킹의 모달이다** (2026-09-14 · ADR-SC-0012 ②) — '섹터 확정' 페이지는 없다.
  핵심 섹터 상태 · 되돌리기 · 기록은 **조** 페이지. 🔒 모달은 `session_state` 플래그 +
  `on_dismiss` 로 연다 — 버튼으로 바로 열면 AppTest 가 못 밟는다
- **디자인 토큰은 한 벌이다** ★ (2026-09-12 · ADR-SC-0010 ⑦)
  F-4 팔레트를 **`.streamlit/config.toml` 에서 먼저 확정**하고 같은 값을 Django 쪽
  Tailwind 로 옮긴다. 🔴 **CSS 만으로 디자인하지 않는다** — `st.bar_chart` 는 canvas 라
  CSS 가 닿지 않고, 차트 색은 `chartCategoricalColors` 로만 바뀐다.
  `theme.py` 의 `_CSS` 에는 **`config.toml` 이 표현 못 하는 것만** 남긴다
- **팀원은 개발자가 아니라 사용자다.** 설치 0, URL 하나. 화면이 용어를 설명해야 하고
  **"과거 데이터의 요약이다. 투자 권유가 아니다"** 를 상시 표시한다
