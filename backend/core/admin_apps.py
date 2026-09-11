"""Django Admin 을 우리 사이트로 갈아끼우는 AppConfig (F-20 5장).

`config/settings.py` 의 `INSTALLED_APPS` 에서 **`"django.contrib.admin"` 자리에**
`"core.admin_apps.OpsAdminConfig"` 를 적는다.

★★ **왜 파일이 따로 있는가** — 두 가지 사고를 각각 피하기 위해서다. 둘 다 실제로
   겪었으므로 합치지 말 것.

① **`core/apps.py` 에 두면 앱 등록이 깨진다.**
   `INSTALLED_APPS` 의 `"core"` 항목을 풀 때 Django 는 `core.apps` 안의 AppConfig
   후보를 훑는다. `AdminConfig` 를 import 하는 순간 그것도 후보로 잡히고,
   `AdminConfig` 와 `OpsAdminConfig` 둘 다 `default = True` 라 이렇게 터진다::

       RuntimeError: 'core.apps' declares more than one default AppConfig:
                     'AdminConfig', 'OpsAdminConfig'.

② **`core/admin_site.py` 에 두면 모델이 너무 일찍 import 된다.**
   `INSTALLED_APPS` 항목은 **앱 레지스트리가 준비되기 전에** import 된다.
   `admin_site.py` 는 `job_health` → `core.models` 로 이어지므로, 거기 AppConfig 를
   두면 모델이 등록 전에 로드되어 `AppRegistryNotReady` 가 난다.

   반대로 `default_site` 는 **문자열**이다. Django 가 `admin.site` 에 처음 접근할 때
   `import_string` 으로 푼다 — 그 시점에는 모델이 다 올라와 있어 안전하다.
   문자열로 두는 이유가 이것이지, 단순한 취향이 아니다.

즉 이 파일은 **django.contrib.admin 말고는 아무것도 import 하지 않아야 한다.**
"""

from django.contrib.admin.apps import AdminConfig


class OpsAdminConfig(AdminConfig):
    """`admin.site` 를 `OpsAdminSite` 로 세운다.

    `AdminConfig` 를 상속하므로 `name` 은 여전히 `django.contrib.admin` 이고 앱
    라벨도 `admin` 그대로다 — 마이그레이션·권한·URL 이름이 전부 유지된다.

    ★ **`admin.site` 자체가 우리 인스턴스가 된다.** 그래서 각 앱의
    `@admin.register(...)` 를 한 줄도 고치지 않아도 된다. AdminSite 를 새로 만들어
    직접 인스턴스화하는 방식이었다면 모든 등록을 새 사이트로 옮겨야 하고, 옮기다
    빠뜨린 모델은 화면에서 조용히 사라진다.

    Django 관점 — FastAPI 에는 이런 자동 운영 화면이 없어 대시보드를 직접 만들었다.
    Django 는 반대로 **이미 있는 화면을 어떻게 갈아끼우느냐**가 문제이고, 그 공식
    경로가 `AdminConfig.default_site` 다. monkeypatch 하지 않는다.
    """

    default_site = "core.admin_site.OpsAdminSite"
