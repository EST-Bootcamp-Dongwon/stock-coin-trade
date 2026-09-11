"""Vercel 서버리스 함수 진입점 — Django WSGI 어댑터.

Vercel 의 파이썬 런타임은 **`api/` 디렉터리의 파일 하나 = 함수 하나** 로 본다.
`vercel.json` 이 모든 요청을 이 파일로 보내고, 여기서 Django 가 받는다.

    브라우저 → Vercel 엣지 → api/index.py (이 파일) → Django → Supabase

★★ **`app` 이라는 이름이어야 한다** ────────────────────────────────────────

Vercel 의 파이썬 빌더는 모듈에서 `handler` 또는 **`app`** 이라는 이름을 찾는다.
Django 가 만들어 주는 것은 `application` 이라 **이름을 바꿔 준다.**
이 한 줄을 빠뜨리면 배포는 성공하는데 모든 요청이 500 이 된다.

Django 관점 ─────────────────────────────────────────────────────────────────

로컬의 `manage.py runserver` 는 개발 전용 서버다. 운영에서는 gunicorn·uwsgi 같은
WSGI 서버가 `config/wsgi.py` 의 `application` 을 호출한다. **Vercel 에서는 그
WSGI 서버 자리를 플랫폼이 대신**하고, 우리는 호출 가능한 객체만 내놓으면 된다.

FastAPI 였다면 `app = FastAPI()` 를 그대로 내보내면 끝이었다(ASGI). Django 는
WSGI 라 요청마다 동기로 처리되고, 서버리스에서는 그 편이 오히려 단순하다 —
어차피 함수 인스턴스 하나가 요청 하나를 처리한다.

★ **왜 저장소 루트의 `api/` 인가** ───────────────────────────────────────────

Django 프로젝트는 `backend/` 안에 있는데, Vercel 은 프로젝트 루트에서 `api/` 를
찾는다. 루트 디렉터리 설정을 `backend` 로 바꾸는 방법도 있지만, 그러면 이
저장소의 v1.0 코드(`python-stock-backend/`·`frontend/`)가 배포 컨텍스트 밖으로
나가 나중에 함께 올리기 어려워진다. **어댑터 한 장으로 경로만 이어 준다.**
"""

import os
import sys
from pathlib import Path

# 저장소 루트/backend 를 파이썬 경로에 넣는다 — `import config.settings` 가 되게.
BACKEND_DIR = Path(__file__).resolve().parent.parent / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

# ★ import 순서가 중요하다 — sys.path 를 손본 **뒤에** 불러야 한다.
from config.wsgi import application as app       # noqa: E402
