"""WSGI 진입점 (gunicorn·uwsgi 가 이걸 부른다)."""

import os

from django.core.wsgi import get_wsgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

application = get_wsgi_application()
