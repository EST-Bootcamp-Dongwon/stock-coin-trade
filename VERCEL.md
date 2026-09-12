# Vercel 배포 메모 (v2.0)

> ✅ **살아 있다 (2026-09-12 실측).** `https://stock-coin-trade.vercel.app` 가 200 이고
> Git 연동은 **`gitlab.com/dev-dongwon05253/stock-coin-trade` · `main`**(정본)이다.
> Streamlit Community Cloud(v3.0 · 팀용 정본)와 **공존**한다.
> → [ADR-SC-0010](docs/decisions/0010-두-배포-공존과-쓰기-상태-분리.md) ①③
>
> ⚠️ ~~"연동 해제됨(2026-09-11)"~~ 은 **사실이 아니었다.** 같은 날 14:38 빌드 로그가
> `Cloning gitlab.com/dev-dongwon05253/stock-coin-trade (Branch: main)` 이라고 적는다.
> 대시보드를 보지 않고 적은 문장이었다. **ADR-SC-0008 ② 는 폐기됐다.**

---

## 0. 이 장만 읽으면 된다 — 🔴 건드리면 깨지는 것 하나

### 0.1 🔴 **Root Directory 를 비워 둔다 (저장소 루트)**

Vercel → Settings → General → Root Directory 는 **빈칸이어야 한다**(`vercel project
inspect` 가 `Root Directory .` 로 보여 준다 — 2026-09-12 확인). `backend/` 로 바꾸면
**배포가 조용히 죽는다** — 빌드는 `Ready` 로 뜨는데 **함수가 0개**가 되고 모든 경로가
404 다. 2026-09-12 실측:

| Root Directory | Builds | `/` |
|---|---|---|
| (빈칸) | `λ api/index.py (33.71MB)` | **200** |
| `backend/` | **`. [0ms]`** | **404** |

왜 — `vercel.json` 의 `builds` 가 `src: "api/index.py"` 를 **Root Directory 기준으로**
푼다. `backend/api/index.py` 는 없으므로 빌드가 **6ms 에 산출물 없이** 끝난다. 로그는
`WARNING! Build output contains no "functions", "static", or "services" directory` 다.

🔒 **그 로그의 다른 한 줄도 같은 뜻이다** — `WARNING! Due to 'builds' existing in your
configuration file, the Build and Development Settings defined in your Project Settings
will not apply`. `builds` 가 있으면 Project Settings(프레임워크 감지·빌드 커맨드)가
**통째로 무시**된다. 그래서 "Django 를 자동 감지한다"(ADR-SC-0010 W7)에 기대면 안 된다 —
`builds` 를 지우지 않는 한 그 경로는 열리지 않는다.

🔒 **`.vercelignore` 도 Root Directory 를 따라간다.** 루트 `.vercelignore` 가
`/data/`(KRX 원천 · 제약 10)와 `.streamlit/`(시크릿)을 막고 있다 — Root Directory 를
옮기면 **그 방어가 같이 사라진다.** 이것이 0.1 을 🔴 로 적은 진짜 이유다.

### 0.2 ✅ 의존성은 이미 갈려 있다 — `api/requirements.txt` 를 옮기지 않는다

2026-09-11 프로덕션 빌드 로그:

```
Found .vercelignore
Removed 112 ignored files defined in .vercelignore
Installing required dependencies from api/requirements.txt...
```

**Vercel 이 `api/` 안의 `requirements.txt` 를 읽는다.** 옛 메모는 *"`api/` 안을 자동으로
읽는다는 보장을 확인하지 못했다 → 그 자리는 보관본이다"* 였으나 **로그가 반증한다.**
따라서:

- 🔒 **`api/requirements.txt` 를 저장소 루트로 되돌리지 않는다.** 루트는 Streamlit Cloud
  가 독점한다(의존성 파일을 처음 만난 하나만 쓴다) — 되돌리면 **그쪽이 깨진다.**
- 🔒 **`.vercelignore` 의 `/requirements.txt` 줄을 지우지 않는다.** 그 한 줄이 Streamlit
  목록을 Vercel 번들에서 빼 준다. **앞의 슬래시가 핵심이다**(그 파일 주석 참조).
- ✅ 두 배포가 **의존성 파일을 공유하지 않는다.** ADR-SC-0008 ② 의 *"둘 중 하나만 쓴다"*
  는 전제가 여기서 무너졌다 — `pyproject.toml` 우선순위를 검증할 필요도 없었다.

### 0.3 Supabase 가 잠들면 `/` 만 500 이 된다

프로젝트가 `INACTIVE` 면 빌드는 멀쩡하고 **`/` 만 500** 이다(`/static/…` 200 ·
`/admin/` 302 — 2026-09-11 V17 의 그 증상). 2026-09-12 현재 `stock-coin-trade`
(`sgbhrahtewojmicwmxxu`)는 `ACTIVE_HEALTHY` 고, 7일 pause 는 **매일 도는
`batch.publish` 가 한 줄 써서** 푼다(ADR-SC-0010 ④).

### 0.4 ⚠️ `sector/`·`dashboard/` 는 아직 번들에 없다

`.vercelignore` 가 `sector/`·`dashboard/`·`batch/`·`streamlit_app.py` 를 뺀다. v2.0
화면이 import 하지 않으므로 **지금은 맞다.** 렌더러 B(섹터 화면)를 쓰는 날
`sector/`·`dashboard/` 두 줄을 풀고 `vercel.json` 의 `includeFiles` 를 넓힌다.
🔒 `batch/` 는 **풀지 않는다** — 수집·집계는 로컬이다(ADR-SC-0010 ①).

---

## 1. 무엇이 어디로 가는가

```
브라우저
  ↓  https://<프로젝트>.vercel.app
Vercel 엣지
  ↓  vercel.json 의 routes — 모든 경로를 함수 하나로
api/index.py                    ← WSGI 어댑터 (sys.path 에 backend/ 를 넣는다)
  ↓
backend/config/wsgi.py → Django
  ↓                        ↓
Supabase Postgres      WhiteNoise → backend/static/  (CSS·JS)
```

| 파일 | 역할 |
|---|---|
| `vercel.json` | 모든 요청을 `api/index.py` 로 보낸다. `includeFiles` 로 `backend/**` 를 번들에 넣는다 |
| `api/index.py` | Django `application` 을 Vercel 이 찾는 이름(`app`)으로 내보낸다 |
| `requirements.txt` (루트) | **배포 전용 slim 의존성.** 개발용은 `backend/requirements.txt` |
| `.vercelignore` | 번들에서 뺄 것 (가상환경·문서·v1.0 코드·node_modules) |

---

## 2. 알아 둘 제약 세 가지

### 2.1 무거운 배치는 Vercel 에서 돌지 않는다

> ⚠️ **2026-08-17 정정 — "10초 한도" 는 낡은 정보였다.**
> Fluid compute 가 기본이 된 뒤로 함수 실행은 **전 플랜 기본 300초**이고
> Pro 는 800초까지 올릴 수 있다. `sync_stock_master` 의 실측 **88초**는
> **시간만 놓고 보면 지금 Vercel 에서 돈다.** 아래 결론은 유지되지만
> **근거가 바뀌었다** — 이제 시간이 아니라 번들과 CPU 예산이 이유다.

`/internal/jobs/*` 를 Vercel 에서 부르지 않는 이유는 두 가지다.

| | |
|---|---|
| **번들에 배치 의존성이 없다** ★ | 루트 `requirements.txt`(slim)에는 `pykrx`·`fastembed` 가 **아예 빠져 있다**. 이 둘이 끌고 오는 pandas·numpy·onnxruntime·matplotlib 만 260MB+ 라 번들 한도를 위협한다. 넣지 않았으므로 배치 잡은 Vercel 에서 **import 부터 실패한다** — 시간 문제가 아니다 |
| **Active CPU 월 4시간** | Hobby 예산이다. 88초짜리를 하루 몇 번만 돌려도 축난다. 시세 폴링(5초 주기)은 애초에 서버리스에 맞지 않는다 |

**그래서 `/internal/jobs/*` 를 Vercel 에서 부르지 않는다.**
시세·정산 잡은 로컬 또는 상시 기동 서버에서 `backend/requirements.txt`(full) 로 돌린다.
배포된 앱은 **DB 에 이미 쌓인 것을 읽어 보여주기만** 한다 — 홈 화면이 외부 API 를
부르지 않는다는 F-21 4장 원칙이 여기서 그대로 이득이 된다.

### 2.2 정적 파일에 `collectstatic` 단계를 두지 않았다

`WHITENOISE_USE_FINDERS = True` 라서 WhiteNoise 가 `backend/static/` 을 직접 읽는다.
빌드 단계가 하나 없어지는 대신 **파일명에 해시가 없어** 배포 직후 브라우저가 옛 CSS 를
잠시 쓸 수 있다. 그래서 캐시 수명을 1시간으로 짧게 뒀다(`WHITENOISE_MAX_AGE`).

**Tailwind 산출물 `backend/static/css/app.css` 는 git 에 커밋돼 있어야 한다.**
Vercel 에는 Node 빌드 단계가 없다. CSS 를 고쳤다면 push 전에:

```bash
cd backend && npm run css
```

### 2.3 DB 커넥션 — ★ 실측으로 바로잡은 부분

Supabase 는 접속 경로를 셋 준다. **셋의 성질이 전부 다르다.**

| 경로 | 주소 | 성질 |
|---|---|---|
| 다이렉트 | `db.<ref>.supabase.co:5432` | **IPv6 전용** (무료 플랜). IPv4 레코드가 아예 없다 |
| **세션 풀러** | `aws-0-<region>.pooler.supabase.com:5432` | IPv4. 연결당 백엔드 고정 |
| **트랜잭션 풀러** | `aws-0-<region>.pooler.supabase.com:6543` | IPv4. 트랜잭션마다 백엔드가 바뀐다 |

> ★ **다이렉트는 WSL2·대부분의 CI 에서 닿지 않는다.** IPv6 경로가 없어
> `Network is unreachable` 로 끝난다(2026-08-14 실측). 그래서
> **마이그레이션은 세션 풀러(5432)** 로 돌린다 — DDL·데이터 마이그레이션 모두 정상이다.
>
> 풀러는 **사용자명에 프로젝트 ref 가 붙는다**: `postgres.sgbhrahtewojmicwmxxu`
>
> 지역 접두는 `aws-0` 이다. `aws-1` 은 같은 이름으로 해석되지만 이 프로젝트가
> 없어 `(ENOTFOUND)` 로 거절한다 — 붙는 쪽을 확인하고 쓴다.

**앱(런타임)은 트랜잭션 풀러(6543)** 를 쓴다. 함수 인스턴스가 수십 개 떠도 실제
Postgres 연결은 풀러가 소수로 묶어 준다. 대신 `POSTGRES_PGBOUNCER=1` 을 **반드시**
켠다 — `settings.py` 가 이 값을 보고 둘을 끈다.

```python
DISABLE_SERVER_SIDE_CURSORS = True      # 커서가 남의 백엔드로 넘어가면 깨진다
OPTIONS["prepare_threshold"] = None     # psycopg3 의 자동 PREPARE 를 막는다
```

★ **안 켜면 배포 직후에는 멀쩡하다.** psycopg3 는 같은 쿼리를 5번 만나야 PREPARE 를
걸기 때문이다. 트래픽이 붙고 나서 `prepared statement "_pg3_0" already exists` 로
간헐적 500 이 나기 시작한다 — 원인을 찾기 가장 어려운 종류다.

`DJANGO_CONN_MAX_AGE=0` 도 함께 둔다. 연결을 붙들면 인스턴스 수만큼 커넥션이 물린다.

---

## 3. 최초 배포 절차

### 3.1 Supabase DB 준비 (한 번만 · 2026-08-14 완료)

`ALTER` 가 섞인 DDL 과 데이터 마이그레이션(`accounts/0004_seed_permission_groups`,
`insight/0002_seed_knowledge_documents` 등)이 있어 **Django 로 실행해야 한다.**
SQL 만 밀어 넣으면 `django_migrations` 장부가 어긋난다.

**세션 풀러(5432)** 로 접속한다 — 다이렉트는 IPv6 전용이라 닿지 않는다(2.3).

```bash
cd backend
export SUPA="POSTGRES_HOST=aws-0-ap-northeast-2.pooler.supabase.com \
POSTGRES_PORT=5432 \
POSTGRES_DB=postgres \
POSTGRES_USER=postgres.sgbhrahtewojmicwmxxu \
POSTGRES_SSLMODE=require \
DJANGO_CONN_MAX_AGE=0"

env $SUPA POSTGRES_PASSWORD='<Supabase → Settings → Database 비밀번호>' \
  python manage.py migrate
```

시연용 데이터 (선택):

```bash
env $SUPA POSTGRES_PASSWORD='...' ALLOW_DEMO_SEED=1 DJANGO_DEBUG=False \
  python manage.py seed_demo --users --contest --market
```

| 플래그 | 넣는 것 |
|---|---|
| `--users` | 샘플 투자자 30명 |
| `--contest` | 진행 중 데모 대회 1개 + 참가 32명 + 20영업일 스냅샷·랭킹 |
| `--market` | 홈의 시세·지수·코인 캐시 — **전부 `is_simulated=True`** |

> ★ `seed_demo` 는 `DEBUG=False` 에서 스스로 멈춘다. `ALLOW_DEMO_SEED=1` 이 그
> 탈출구다 — **실제 회원이 생긴 뒤에는 돌리지 않는다.**
>
> ★ `--market` 이 넣는 시세는 **가짜다.** 화면에 "시뮬레이션 가격" 배지가 뜨고,
> 대회 체결은 이 값을 거부한다(F-16 4.1). API 키를 발급받아 `poll_quotes` 가
> 돌기 시작하면 같은 행이 진짜 값으로 덮인다.

### 3.2 Vercel 환경변수

프로젝트 링크 후 대시보드(Settings → Environment Variables) 또는 CLI 로 넣는다.

#### 🔴 Supabase 의 "Install Vercel integration" 을 쓰지 않는다 — 2026-09-12

Supabase → Settings → Integrations 의 Vercel 카드는 *"Supabase keeps environment
variables up to date in each connected Vercel project"* 다. 즉 **이 표를 자동으로
덮어쓰겠다는 기능**이다. 누르지 않는다 —

1. 🔴 **얻는 것이 0 이다.** `backend/` 는 `SUPABASE_*` 변수를 **한 줄도 읽지 않는다**
   (2026-09-12 실측 · `grep -rn SUPABASE backend --include=*.py` → 0건). Django 는
   아래 `POSTGRES_*` 만 읽고, 연동이 주는 `SUPABASE_URL`·`SUPABASE_ANON_KEY` 를 쓰는
   것은 **Streamlit 쪽(v3.0)이고 거기는 Vercel 이 아니다.** 쓸모 있는 값이 엉뚱한 곳에 꽂힌다
2. 🔴 **이름이 겹쳐서 이 표를 깨뜨린다.** 연동은 `POSTGRES_HOST`·`POSTGRES_USER`·
   `POSTGRES_PASSWORD` 를 **다이렉트 연결 형태로** 넣고 `POSTGRES_DATABASE`(우리는
   `POSTGRES_DB`)를 쓰며 `POSTGRES_PORT`·`POSTGRES_PGBOUNCER` 는 주지 않는다.
   → 풀러 유저명(`postgres.<ref>`)이 `postgres` 로 덮이면 **`Tenant or user not found`**,
     운 좋게 붙으면 `POSTGRES_PGBOUNCER` 가 빠진 채로 돌아 **2.3 의 그 간헐적 500**
     (`prepared statement … already exists`)이 트래픽 붙은 뒤에 시작된다.
   🔒 **부분 덮어쓰기가 전부 덮어쓰기보다 나쁘다** — 절반은 우리 값, 절반은 기본값이라
      어느 DB 에 어떤 모드로 붙었는지 아무도 말할 수 없게 된다
3. 🔴 **`SUPABASE_SERVICE_ROLE_KEY`·`SUPABASE_JWT_SECRET` 을 Vercel 환경에 심는다.**
   ADR-SC-0011 ⑤ 가 금지한 바로 그것이다 — 그 키는 RLS 를 통째로 우회하고 v2.0 유산
   53개 테이블 전부에 닿는다. 쓰지도 않을 키를 환경에 두는 것은 노출면만 넓히는 일이다
4. **절대 제약 6** — 배포 설정을 자동으로 바꾸는 것을 필수 경로에 두지 않는다

⚠️ 정확한 주입 변수 목록은 연동 버전마다 달라졌다. **install 화면에 나오는 목록을 먼저
   읽는다** — 위 ①③ 은 목록과 무관하게 성립하지만 ② 의 이름은 확인이 낫다.
🔒 환경변수는 **손으로 넣는다.** 아래 표가 그 정본이고, 열세 줄이라 자동화할 값이 없다.

| 변수 | 값 | 비고 |
|---|---|---|
| `DJANGO_SECRET_KEY` | 랜덤 50자 | `python -c "import secrets;print(secrets.token_urlsafe(50))"` |
| `DJANGO_DEBUG` | `False` | ★ 반드시 False |
| `DJANGO_ALLOWED_HOSTS` | `.vercel.app` | 앞의 점이 서브도메인 전체를 허용한다 |
| `POSTGRES_HOST` | `aws-0-ap-northeast-2.pooler.supabase.com` | |
| `POSTGRES_PORT` | `6543` | **트랜잭션 풀러** (앱은 이쪽, 마이그레이션은 5432) |
| `POSTGRES_DB` | `postgres` | |
| `POSTGRES_USER` | `postgres.sgbhrahtewojmicwmxxu` | 풀러는 유저명에 ref 가 붙는다 |
| `POSTGRES_PASSWORD` | (Supabase DB 비밀번호) | |
| `POSTGRES_SSLMODE` | `require` | |
| `POSTGRES_PGBOUNCER` | `1` | ★ **필수.** 빠뜨리면 트래픽이 붙고 나서 500 이 난다 (2.3) |
| `DJANGO_CONN_MAX_AGE` | `0` | |
| `INTERNAL_JOB_TOKEN` | 랜덤 48자 | 비우면 `/internal/*` 가 전부 404 (fail closed) |

CLI 로 넣으려면:

```bash
printf '%s' "값" | vercel env add <이름> production
vercel env ls production          # 확인
```

> KIS·KRX·업비트 키는 **넣지 않는다.** 배포된 앱은 외부 API 를 부르지 않는다(2.1).

### 3.3 배포 — ★ 평소에는 **push 가 배포다**

`git push origin main`(GitLab 정본) 하면 **약 1분 뒤** 프로덕션이 바뀐다. 2026-09-12 실측:

```
20:45 KST  git push origin main            (ab8117e)
20:47 KST  Cloning gitlab.com/dev-dongwon05253/stock-coin-trade (Branch: main, Commit: ab8117e)
           Found .vercelignore → Removed 165 ignored files
           Installing required dependencies from api/requirements.txt...
           Build Completed in /vercel/output [8s]
           λ api/index.py (34.76MB) · target production
           별칭 stock-coin-trade.vercel.app 가 새 배포로 이동
```

🔒 **CLI 로 프로덕션에 올리지 않는다.** `vercel --prod` 는 **로컬 디렉터리를 업로드**하므로
`.gitignore` 를 보지 않는다 — gitignore 된 `data/`(KRX 원천 · 제약 10)와 `.env` 를
`.vercelignore` 가 **다시** 막아야 하는 이유가 그것이다. Git 경로는 clone 이라 커밋 안 된
것이 애초에 존재하지 않는다. **같은 것을 두 번 막는 대신, 한 번도 노출되지 않는 경로를 쓴다.**

검증용 preview 는 CLI 로 올려도 된다. 🔒 단 **저장소를 clone 한 깨끗한 폴더에서** 한다:

```bash
git clone --depth 1 --no-local file://$PWD /tmp/vtest
mkdir -p /tmp/vtest/.vercel && cp .vercel/project.json /tmp/vtest/.vercel/   # .vercel/ 은 gitignore
cd /tmp/vtest && vercel deploy --yes        # preview — 프로덕션 별칭을 건드리지 않는다
```

⚠️ preview 는 Deployment Protection 때문에 `curl` 이 302(SSO)로 돈다. **응답 대신
`vercel inspect` 의 `Builds` 줄을 본다** — `λ api/index.py (…MB)` 가 있으면 성공,
`. [0ms]` 만 있으면 0.1 의 그 실패다.

최초 1회 링크만 CLI 로 한다:

```bash
cd /path/to/stock-coin-trade
vercel link
```

---

## 4. 배포 후 점검

```bash
curl -s -o /dev/null -w "%{http_code}\n" https://<주소>/                    # 200
curl -s -o /dev/null -w "%{http_code}\n" https://<주소>/static/css/app.css  # 200
curl -s -o /dev/null -w "%{http_code}\n" https://<주소>/account/login/       # 200
curl -s -o /dev/null -w "%{http_code}\n" https://<주소>/internal/jobs/       # 404 (토큰 없음 — 정상)
```

| 증상 | 원인 |
|---|---|
| 전 페이지 500 | `api/index.py` 의 `app` 이름 · `DJANGO_SECRET_KEY` 누락 · DB 접속 실패 |
| 로그인 폼만 403 | `CSRF_TRUSTED_ORIGINS` — 커스텀 도메인을 쓰면 `DJANGO_CSRF_TRUSTED_ORIGINS` 에 `https://` 까지 적는다 |
| CSS 없이 뜸 | `backend/static/css/app.css` 가 커밋되지 않았다 (2.2) |
| `DisallowedHost` | `DJANGO_ALLOWED_HOSTS` 에 도메인 추가 |
| 시세가 전부 빔 | 정상이다 — 잡을 Vercel 에서 돌리지 않는다(2.1). `seed_demo --market` 으로 채운다 |
| `Network is unreachable` (마이그레이션) | 다이렉트 주소를 썼다. 세션 풀러로 바꾼다 (2.3) |
| 간헐적 500 · `prepared statement … already exists` | `POSTGRES_PGBOUNCER=1` 이 빠졌다 (2.3) |

**2026-08-14 최초 배포 실측 결과 — 🔁 2026-09-12 재실측에서 일곱 줄 전부 동일**

```
/                          200      <title>모의투자 대회 플랫폼</title> 렌더
/account/login/            200      로그인 → 302 → 홈에 대회 현황·계좌 요약 렌더
/account/register/         200
/fragments/market/         200      지수·상승/하락 TOP·코인 TOP + 시뮬레이션 배지
/static/css/app.css        200      Tailwind v4.3.3 빌드본 (본문까지 확인)
/internal/jobs/            404      토큰 없음 — fail closed 정상
/admin/                    302      로그인으로 리다이렉트 — 정상
```

★ **`/` 가 200 이면 Supabase 도 살아 있다는 뜻이다** — 홈이 DB 를 읽는다(0.3).
이 일곱 줄이 배포 점검의 전부다. 하나라도 어긋나면 아래 표로 간다.

---

## 5. 관련 문서

- 비용·실행처 판단 → `docs/v2-design/features/version2.0/F-20-스케줄러.md`
- 잡 등록 SQL → `backend/sql/pg_cron_jobs.sql`
- 화면 라우팅 → `docs/v2-design/ui/version2.0/00-화면목록-라우팅.md`
