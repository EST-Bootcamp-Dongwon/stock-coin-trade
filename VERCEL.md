# Vercel 배포 메모 (v2.0)

> 🔴 **연동 해제됨 (2026-09-11).** v3.0 배포는 Streamlit Community Cloud 다.
> 코드(`api/index.py` · `vercel.json` · `backend/`)는 **지우지 않고 보존한다** —
> 되살리려면 연동만 다시 걸면 된다. (→ [ADR-SC-0008](docs/decisions/0008-배포-streamlit-전환과-원격-재구성.md) ②)
>
> 아래 1~장은 **당시 구성의 기록**이다. 배포 구성의 **왜**를 적어 둔다.

---

## 0. 되살리는 법 — 이 장만 읽으면 된다

해제한 이유는 Vercel 이 나빠서가 아니라 **워크로드가 달라져서**다. v3.0 은 매일 데이터를
모으고 차트를 그리는 앱이라 서버리스와 맞지 않는다.

되살릴 때 **순서대로** 해야 하는 것:

| # | 무엇 | 왜 |
|---|---|---|
| 1 | **Supabase 프로젝트 restore** | 프로젝트 4개가 전부 `INACTIVE` 였다. 배포가 깨진 게 아니라 DB 가 잠들어 `/` 만 500 이었다. ⚠️ **되살려도 7일 무활동이면 또 멈춘다** |
| 2 | 🔴 **`api/requirements.txt` 를 저장소 루트로 되돌린다** | 2026-09-11 에 루트에서 `api/` 로 옮겼다. 루트는 **Streamlit Cloud 가 독점**한다(의존성 파일을 처음 만난 하나만 쓴다). Vercel 문서는 의존성 파일을 프로젝트 루트 기준으로만 설명하고 **`api/` 안을 자동으로 읽는다는 보장을 확인하지 못했다** — 그래서 그 자리는 동작하는 설정이 아니라 **보관본**이다 |
| 3 | `.vercelignore` 의 `/requirements.txt` 줄을 지운다 | 2번으로 루트에 돌아온 파일이 번들에서 빠지지 않게. 🔒 **앞의 슬래시가 있는 줄만** 지운다 |
| 4 | Vercel 대시보드에서 Git 연동을 다시 건다 | 해제는 대시보드에서 했다. 저장소에는 연동 흔적이 없다(`.vercel/` 은 gitignore) |
| 5 | 첫 빌드 로그에서 `requirements.txt` 가 설치됐는지 확인한다 | 2·3번을 빠뜨리면 의존성 0개로 빌드가 통과했다가 런타임에 죽는다 |

⚠️ **Streamlit 과 Vercel 을 동시에 유지하려면** 루트 의존성 파일을 나눠야 한다.
`pyproject.toml` 이 Streamlit 우선순위에서 `requirements.txt` **뒤**라 여지가 있으나
**검증하지 않았다.** 둘 중 하나만 쓰는 것이 지금의 결정이다.

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

### 3.3 배포

```bash
cd /path/to/stock-coin-trade
vercel link          # 최초 1회 — 프로젝트 이름을 정한다
vercel --prod
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

**2026-08-14 최초 배포 실측 결과**

```
/                          200
/account/login/            200      로그인 → 302 → 홈에 대회 현황·계좌 요약 렌더
/account/register/         200
/fragments/market/         200      지수·상승/하락 TOP·코인 TOP + 시뮬레이션 배지
/static/css/app.css        200      Tailwind v4.3.3 빌드본
/internal/jobs/            404      토큰 없음 — fail closed 정상
/admin/                    302      로그인으로 리다이렉트 — 정상
```

---

## 5. 관련 문서

- 비용·실행처 판단 → `docs/v2-design/features/version2.0/F-20-스케줄러.md`
- 잡 등록 SQL → `backend/sql/pg_cron_jobs.sql`
- 화면 라우팅 → `docs/v2-design/ui/version2.0/00-화면목록-라우팅.md`
