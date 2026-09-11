"""Django 설정.

★ 이 파일에서 가장 비싼 두 줄은 `AUTH_USER_MODEL` 과 `DEFAULT_AUTO_FIELD` 다.
   **첫 `migrate` 이후에는 사실상 되돌릴 수 없다** (→ 05-마이그레이션-순서와-시드 1장).

Django 관점 — FastAPI 였다면 사용자 모델도 그냥 테이블 하나라 언제든 바꿀 수 있었다.
Django 는 `auth` · `admin` · `sessions` 가 전부 `AUTH_USER_MODEL` 을 참조하므로
첫 마이그레이션 시점에 고정된다.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent

# backend/.env 를 읽는다. 저장소 루트의 .env(v1.0 MariaDB 용)와 분리한다 —
# 같은 파일을 공유하면 v1.0/v2.0 의 DB 설정이 서로를 덮어쓴다.
load_dotenv(BASE_DIR / ".env")


def env_bool(key: str, default: bool = False) -> bool:
    """환경변수를 불리언으로 읽는다. "False"·"0"·"" 을 전부 거짓으로 본다."""
    return os.getenv(key, str(default)).strip().lower() in ("1", "true", "yes", "on")


# ── 보안 ────────────────────────────────────────────────────────
SECRET_KEY = os.getenv("DJANGO_SECRET_KEY", "dev-only-insecure-key-change-me")
DEBUG = env_bool("DJANGO_DEBUG", True)
ALLOWED_HOSTS = [h.strip() for h in os.getenv("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",") if h.strip()]

# ★ Vercel 은 배포마다 `<프로젝트>-<해시>-<계정>.vercel.app` 주소를 새로 만든다.
#   전부 환경변수에 적을 수 없으므로 플랫폼이 주는 호스트를 자동으로 허용한다.
#   `VERCEL_URL` 은 Vercel 런타임이 스킴 없이(`foo.vercel.app`) 넣어 준다.
_vercel_host = os.getenv("VERCEL_URL", "").strip()
if _vercel_host:
    ALLOWED_HOSTS.append(_vercel_host)
    # 고정 주소(프로덕션 별칭)도 함께 허용한다.
    _vercel_alias = os.getenv("VERCEL_PROJECT_PRODUCTION_URL", "").strip()
    if _vercel_alias:
        ALLOWED_HOSTS.append(_vercel_alias)

# ★★ CSRF_TRUSTED_ORIGINS 를 왜 따로 적는가 ─────────────────────────────
#   Django 4 부터 POST 요청의 `Origin` 헤더를 이 목록과 대조한다. HTTPS 뒤에
#   있는 앱에서 이 목록이 비면 **로그인 폼이 403 으로 거부된다.**
#   ALLOWED_HOSTS 와 달리 **스킴(https://)까지 적어야 한다.**
CSRF_TRUSTED_ORIGINS = [
    o.strip() for o in os.getenv("DJANGO_CSRF_TRUSTED_ORIGINS", "").split(",") if o.strip()
]
CSRF_TRUSTED_ORIGINS += [f"https://{host}" for host in ALLOWED_HOSTS if ".vercel.app" in host]

# ★★ 내부 잡 엔드포인트 토큰 (F-20 3.1) ─────────────────────────
#   pg_cron 이 `X-Internal-Token` 헤더로 보내는 값과 대조한다.
#   **비어 있으면 `/internal/*` 이 전부 404 로 막힌다**(fail closed).
#   운영에서는 Supabase Vault 에 같은 값을 넣어 cron 잡이 읽게 한다
#   (backend/sql/pg_cron_jobs.sql 참조).
#     생성: python -c "import secrets; print(secrets.token_urlsafe(48))"
INTERNAL_JOB_TOKEN = os.getenv("INTERNAL_JOB_TOKEN", "")


# ── 앱 ──────────────────────────────────────────────────────────
INSTALLED_APPS = [
    # ★ "django.contrib.admin" 대신 우리 AdminConfig 를 적는다.
    #   `default_site` 로 AdminSite 를 갈아끼우는 **공식 경로**다 (F-20 5장 경고 배너).
    #   이렇게 하면 `admin.site` 자체가 우리 것이 되어, 기존 `@admin.register` 는
    #   한 줄도 고치지 않아도 된다.
    "core.admin_apps.OpsAdminConfig",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    # 숫자 서식 — `{{ 100000000|intcomma }}` → `100,000,000` (U-01 4장 숫자 표시 규칙).
    # Django 에 들어 있는 앱이라 설치할 것이 없다.
    "django.contrib.humanize",
    # 서드파티
    "rest_framework",
    # 도메인 앱 7개 — 의존 순서대로 적는다 (규약 1.1)
    #   core  ← 추상 모델·상수만. 다른 앱을 참조하지 않는다
    #   market ← 다른 앱을 참조하지 않는다
    #   accounts ⇄ contests ← 유일한 양방향 (문자열 참조로 푼다)
    "core",
    "market",
    "accounts",
    "contests",
    "trading",
    "learning",
    "insight",
    # ★ 화면 조립 층 — 도메인 앱 전부를 참조한다. 맨 아래에 둔다 (web/apps.py 참조)
    "web",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    # ★★ WhiteNoise 는 SecurityMiddleware **바로 아래**에 둔다 (공식 권장).
    #   Django 는 원래 정적 파일을 서빙하지 않는다 — 개발 서버가 DEBUG=True 일 때만
    #   특별히 해 준다. 운영에서는 Nginx 가 맡는 것이 정석인데, 서버리스(Vercel)에는
    #   우리가 세울 Nginx 가 없다. WhiteNoise 가 그 자리를 파이썬 프로세스 안에서 대신한다.
    #
    #   Django 관점 — Next.js 는 `public/` 을 프레임워크가 알아서 서빙했다.
    #   Django 는 "정적 파일 서빙은 웹서버의 일" 이라는 입장이라 이 한 줄이 필요하다.
    "whitenoise.middleware.WhiteNoiseMiddleware",
    # ★ 세션·인증보다 **위**에 둔다 (F-20 3.1). 토큰이 틀린 요청은 여기서 끝나고
    #   URL 해석·세션 로드·DB 조회가 아예 일어나지 않는다. 인증 뒤에 두면 남의
    #   요청에도 세션 조회 비용을 다 치르게 된다.
    "core.middleware.InternalEndpointMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                # GNB 메뉴·대회 배지를 전 화면에 공급한다 (U-01 2장).
                # 모든 뷰가 같은 값을 컨텍스트에 넣는 대신 여기 한 줄로 끝낸다.
                #
                # Django 관점 — Next.js 의 `layout.tsx` 가 서버에서 데이터를 읽어
                # children 에 내려주던 것에 해당한다. Django 는 "레이아웃 전용 데이터"를
                # 컨텍스트 프로세서로 주입한다.
                "web.context_processors.navigation",
            ],
        },
    },
]


# ── 데이터베이스 ────────────────────────────────────────────────
# 로컬도 Postgres 를 쓴다. SQLite 로 개발하면 부분 유니크 인덱스·vector 타입·
# ON CONFLICT 동작이 달라 **로컬에서 통과한 것이 운영에서 깨진다** (05 문서 8장).
#   로컬: docker compose --profile v2 up -d postgres  (pgvector/pgvector:pg16)
#   운영: Supabase
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.getenv("POSTGRES_DB", "stockcoin"),
        "USER": os.getenv("POSTGRES_USER", "stockcoin"),
        "PASSWORD": os.getenv("POSTGRES_PASSWORD", "stockcoin-dev"),
        "HOST": os.getenv("POSTGRES_HOST", "127.0.0.1"),
        "PORT": os.getenv("POSTGRES_PORT", "5432"),
        # Supabase 는 커넥션 수가 빠듯하다. 서버리스에서는 0(매 요청 새 연결)이 안전하고,
        # 상시 기동 서버에서는 60 초 정도 재사용이 유리하다. 환경변수로 가른다.
        "CONN_MAX_AGE": int(os.getenv("DJANGO_CONN_MAX_AGE", "0")),
        "OPTIONS": {
            "sslmode": os.getenv("POSTGRES_SSLMODE", "prefer"),
        },
    }
}

# ★★ **pgbouncer 트랜잭션 모드에서 반드시 켜야 하는 두 가지** ────────────────
#
# Supabase 는 연결 경로를 세 갈래로 준다.
#
#   ① 다이렉트   db.<ref>.supabase.co:5432          — **IPv6 전용** (무료 플랜)
#   ② 세션 풀러  ...pooler.supabase.com:5432        — IPv4. 연결당 백엔드 고정
#   ③ 트랜잭션 풀러 ...pooler.supabase.com:6543     — IPv4. **트랜잭션마다 백엔드가 바뀐다**
#
# 서버리스에서는 ③ 을 쓴다. 함수 인스턴스가 수십 개 떠도 실제 Postgres 연결은
# 풀러가 소수로 묶어 주기 때문이다 (Supabase Free 의 좁은 커넥션 한도 대응).
#
# 그런데 ③ 은 **트랜잭션이 끝날 때마다 다른 백엔드**로 넘어간다. 그래서
#
#   · 서버사이드 커서 — 다음 fetch 가 커서를 모르는 백엔드로 가서 깨진다
#   · prepared statement — psycopg3 는 같은 쿼리를 5번 만나면 자동으로 PREPARE 를
#     건다. 그 이름이 남의 백엔드에 없거나, 반대로 이미 있어서
#     `prepared statement "_pg3_0" already exists` 로 터진다
#
# ★ 무서운 점은 **바로 안 터진다**는 것이다. 같은 쿼리가 5번 넘게 돌아야 시작되므로
#   배포 직후에는 멀쩡하다가 트래픽이 붙으면 간헐적으로 500 이 난다.
#
# 로컬(다이렉트 연결)에서는 켤 이유가 없다 — 성능만 손해다. 환경변수로 가른다.
if env_bool("POSTGRES_PGBOUNCER", False):
    DATABASES["default"]["DISABLE_SERVER_SIDE_CURSORS"] = True
    # `None` 이면 psycopg3 가 PREPARE 를 아예 걸지 않는다.
    DATABASES["default"]["OPTIONS"]["prepare_threshold"] = None

# ★ 되돌릴 수 없는 두 줄 ─────────────────────────────────────────
AUTH_USER_MODEL = "accounts.Member"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
# ────────────────────────────────────────────────────────────────

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]


# ── 국제화·시간 (규약 3장) ──────────────────────────────────────
LANGUAGE_CODE = "ko-kr"
TIME_ZONE = "Asia/Seoul"   # 템플릿·Admin 표시는 KST
USE_I18N = True
USE_TZ = True              # DB 에는 timestamptz 로, 내부적으로는 UTC 로 저장
# pg_cron 은 UTC 로 동작한다. 잡 등록 시 KST 를 환산해 적는다 (F-20 2장).


# ── 인증·세션 (F-01 4장) ───────────────────────────────────────
#
# ★ 세션 저장소는 **DB** 다. 서버리스에서는 파일·메모리 세션을 쓸 수 없다 —
#   요청마다 다른 인스턴스가 뜨므로 방금 만든 세션을 다음 요청이 못 찾는다.
#   `django.contrib.sessions.backends.db` 가 기본값이라 따로 적지 않아도 되지만,
#   **의도적으로 고른 값**임을 남기려고 명시한다.
#
# Django 관점 — FastAPI 에서는 서명 쿠키를 직접 만들고 만료를 손으로 검사했다.
# Django 는 `django_session` 테이블 + `SessionMiddleware` 가 그 일을 다 한다.
# 쿠키에는 세션 키만 담기고 값은 DB 에 있어, **서버가 세션을 강제로 끊을 수 있다.**
# 대회 실격·정지 처리가 있는 서비스에서는 이 성질이 중요하다.
SESSION_ENGINE = "django.contrib.sessions.backends.db"
SESSION_COOKIE_AGE = 60 * 60 * 24 * 7          # 7일 — v1.0 승계
SESSION_COOKIE_HTTPONLY = True                 # JS 가 세션 쿠키를 읽지 못하게 한다
SESSION_COOKIE_SAMESITE = "Lax"
# ★ False 로 둔다. True 면 요청마다 세션 행을 UPDATE 해서 만료가 계속 밀린다(슬라이딩).
#   편해 보이지만 요청마다 쓰기가 한 번 더 생기고, Supabase Free 의 좁은 커넥션에
#   불필요한 부하다. "마지막 로그인/저장 시점부터 7일" 로 충분하다.
SESSION_SAVE_EVERY_REQUEST = False

# `@login_required` 가 비로그인 사용자를 보낼 곳. URL 이 아니라 **URL name** 을 적는다 —
# 경로가 바뀌어도 여기를 고칠 필요가 없다.
LOGIN_URL = "account:login"
LOGIN_REDIRECT_URL = "home"
LOGOUT_REDIRECT_URL = "home"

# 운영(HTTPS)에서만 켠다. 로컬은 http 라 켜면 쿠키가 아예 안 붙어 로그인이 안 된다.
if not DEBUG:
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    # Vercel 은 앞단에서 TLS 를 끝내고 뒤로는 http 로 넘긴다. 이 헤더를 봐야
    # Django 가 "이 요청은 원래 https 였다"를 안다 — 없으면 무한 리다이렉트가 난다.
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")


# ── 정적·미디어 ────────────────────────────────────────────────
STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
# 우리가 작성한 정적 파일의 원본 위치. `collectstatic` 이 여기서 STATIC_ROOT 로 모은다.
STATICFILES_DIRS = [BASE_DIR / "static"]

# ★★ **collectstatic 없이 굴린다** ────────────────────────────────────────
#   `WHITENOISE_USE_FINDERS = True` 면 WhiteNoise 가 STATIC_ROOT 가 아니라
#   **STATICFILES_DIRS 를 직접** 뒤져 서빙한다. Vercel 배포에 빌드 단계를 하나
#   덜 넣기 위한 선택이다 — 파이썬 런타임만 올라가면 화면이 뜬다.
#
#   대가: 파일명에 해시가 붙지 않아 **배포 직후 브라우저가 옛 CSS 를 잠시 쓸 수 있다.**
#   그래서 캐시 수명을 1시간으로 짧게 잡는다. 트래픽이 늘어 이 비용이 아까워지면
#   `collectstatic` + `ManifestStaticFilesStorage` 로 바꾼다 (그때는 해시가 붙어
#   1년 캐시가 안전해진다).
WHITENOISE_USE_FINDERS = True
WHITENOISE_AUTOREFRESH = DEBUG      # 개발 중에는 파일이 바뀌면 즉시 반영
WHITENOISE_MAX_AGE = 0 if DEBUG else 60 * 60

MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "media"    # Member.avatar 업로드 위치


# ── DRF ────────────────────────────────────────────────────────
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework.authentication.SessionAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        # 기본을 "로그인 필수" 로 둔다. v1.0 은 /api/ai/analyze 와
        # Qdrant 4종이 인증 없이 열려 있었다(결함 D-6). 기본값을 뒤집어
        # **공개하려면 명시적으로 열게** 만든다.
        "rest_framework.permissions.IsAuthenticated",
    ],
}


# ── 로깅 ────────────────────────────────────────────────────────
# ★★ **왜 로깅 설정을 명시하는가** ───────────────────────────────────────────
#
# Django 기본값은 `django` 로거만 콘솔로 보내고, 그것도 `DEBUG=True` 일 때다.
# 우리가 만든 로거(`core.middleware` 등)는 루트로 전파되는데 루트에 핸들러가 없어
# **운영에서 메시지가 그냥 사라진다.**
#
# 하필 사라지는 게 "내부 엔드포인트 거부 — 토큰 불일치" 같은 것들이다. 응답은
# 일부러 빈 404 로 만들어 놨으므로, 로그마저 없으면 운영자에게 남는 단서가 0 이다.
# 규약 8.5("막다른 길로 만들지 않는다")가 성립하려면 이 설정이 있어야 한다.
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        # 시각은 KST 로 찍힌다 (TIME_ZONE 을 따른다).
        "standard": {"format": "[{asctime}] {levelname} {name}: {message}", "style": "{"},
    },
    "handlers": {
        "console": {"class": "logging.StreamHandler", "formatter": "standard"},
    },
    "root": {"handlers": ["console"], "level": "WARNING"},
    "loggers": {
        # 우리 앱의 진행 상황(잡 완료·소요 시간)은 INFO 로 본다.
        # 잡 진행 로그 한 줄 한 줄은 DEBUG 라 평소엔 안 나온다 —
        # 필요하면 DJANGO_LOG_LEVEL=DEBUG 로 켠다.
        "core": {"level": os.getenv("DJANGO_LOG_LEVEL", "INFO"), "propagate": True},
        "market": {"level": os.getenv("DJANGO_LOG_LEVEL", "INFO"), "propagate": True},
        "insight": {"level": os.getenv("DJANGO_LOG_LEVEL", "INFO"), "propagate": True},
        "django": {"level": "INFO", "propagate": True},
    },
}
