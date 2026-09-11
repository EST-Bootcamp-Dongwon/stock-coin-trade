#!/usr/bin/env python
"""Django 관리 명령 진입점.

FastAPI 에서는 `uvicorn app:app` 이 유일한 진입점이었고 배치는 별도 스크립트였다.
Django 는 이 파일 하나가 서버 기동(`runserver`)·마이그레이션(`migrate`)·
커스텀 커맨드(`sync_stock_master` 등)를 전부 받는다.
"""

import os
import sys


def main():
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:
        raise ImportError(
            "Django 를 불러올 수 없습니다. 가상환경이 활성화되어 있는지 확인하십시오.\n"
            "  backend/.venv/bin/python manage.py <명령>"
        ) from exc
    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
