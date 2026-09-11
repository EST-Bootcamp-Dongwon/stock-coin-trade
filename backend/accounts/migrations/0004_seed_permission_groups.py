"""운영자 권한 그룹 2종 시드 (05 문서 5.1 · F-19 4장).

    | 역할        | 판정                      | 범위                    |
    |-------------|---------------------------|-------------------------|
    | 슈퍼유저    | `is_superuser`            | 전체                    |
    | 대회 운영자 | `Group("contest_admin")`  | 대회·참가자·주문 조회   |
    | 콘텐츠 운영자| `Group("content_admin")` | 레슨·가이드·지식 문서   |

**이메일 하드코딩은 폐기한다** (F-19 4장). v1.0 은 `admin.py` 가 특정 이메일을
문자열로 비교해 관리자를 판정했다. 그 방식은 ① 사람이 바뀌면 배포가 필요하고
② 권한의 범위를 나눌 수 없다.

★★ **Django 의 함정 — 이 시점에 `Permission` 행은 아직 없다** ────────────────

Django 는 `Permission` 을 마이그레이션으로 만들지 않는다. `contenttypes` 앱이
**`post_migrate` 시그널**에서 모델을 훑어 만들어 준다. 그런데 시그널은 이름 그대로
**모든 마이그레이션이 끝난 뒤**에 발화한다.

    migrate 실행 ─┬─ 0001_initial ...
                  ├─ 0004_seed_permission_groups  ← 여기서 Permission 을 찾으면 0건
                  └─ (전부 끝) → post_migrate → create_permissions() 가 그제야 만든다

빈 DB 에 `migrate` 를 돌리면 그룹은 생기지만 **권한이 하나도 안 붙는다.**
게다가 조용히 성공해서 알아채기도 어렵다 — 운영자가 Admin 에 로그인해 아무것도
못 보고 나서야 발견된다.

그래서 이 마이그레이션은 **`create_permissions()` 를 직접 먼저 호출한다.**
멱등이라 두 번 불려도 안전하다.

> **FastAPI 였다면** — 권한 테이블도 그냥 우리가 만든 테이블이라 시드에 같이 넣으면
> 끝이었다. Django 는 권한을 프레임워크가 자동 생성해 주는 대신, **그 생성 시점이
> 마이그레이션 바깥**이라는 제약이 따라온다.
"""

from django.contrib.auth.management import create_permissions
from django.db import migrations

# ── 권한 배분 ────────────────────────────────────────────────────
# (app_label, model_name, [action...])
#
# **`delete` 를 아무에게도 주지 않는다.** 대회 데이터·콘텐츠 삭제는 슈퍼유저만 할 수
# 있게 남긴다. 실격은 삭제가 아니라 `is_ranked=False` 이고(F-02 4.3),
# 레슨 삭제는 `LearningProgress` 가 `PROTECT` 로 막는다. 지울 일 자체가 거의 없다.

CONTEST_ADMIN_PERMISSIONS = [
    # 대회 도메인 — 만들고 고칠 수 있어야 한다
    ("contests", "contest", ["add", "change", "view"]),
    ("contests", "participation", ["add", "change", "view"]),
    ("contests", "contestuniverse", ["view"]),
    ("contests", "contestsectorweight", ["view"]),
    ("contests", "dailysnapshot", ["view"]),
    ("contests", "snapshotholding", ["view"]),
    ("contests", "intradaysnapshot", ["view"]),
    ("contests", "contestranking", ["view"]),
    ("contests", "contestresult", ["change", "view"]),
    ("contests", "ruleviolation", ["change", "view"]),
    ("contests", "weeklyturnover", ["view"]),
    # 주문·체결·보유는 **조회만** — 운영자가 참가자의 거래를 고칠 수 있으면
    # 순위 조작이 가능해진다. 정정이 필요하면 슈퍼유저가 감사 로그를 남기고 한다
    ("trading", "order", ["view"]),
    ("trading", "execution", ["view"]),
    ("trading", "position", ["view"]),
    ("accounts", "account", ["view"]),
    # 배치 상태 확인 · 자기 조치 이력 확인
    ("core", "datasynclog", ["view"]),
    ("core", "adminauditlog", ["view"]),
]

CONTENT_ADMIN_PERMISSIONS = [
    ("learning", "lesson", ["add", "change", "view"]),
    ("learning", "guide", ["add", "change", "view"]),
    ("learning", "learningprogress", ["view"]),
    # 사용자가 추가한 지식 문서를 검토·승인한다 (결함 D-6 대응 경로)
    ("insight", "knowledgedocument", ["add", "change", "view"]),
]

GROUPS = {
    "contest_admin": CONTEST_ADMIN_PERMISSIONS,
    "content_admin": CONTENT_ADMIN_PERMISSIONS,
}


def _ensure_permissions_exist(apps, schema_editor):
    """`post_migrate` 를 기다리지 않고 `Permission` 행을 지금 만든다.

    `create_permissions()` 는 `app_config.models_module` 이 `None` 이면 조용히
    돌아간다. 마이그레이션 중의 앱 설정(StateApps)에는 그 값이 없으므로
    잠시 채웠다가 되돌린다. django-extensions 등이 쓰는 것과 같은 관용구다.

    멱등이다 — `create_permissions` 자체가 이미 있는 권한은 건너뛴다.
    """
    for app_config in apps.get_app_configs():
        app_config.models_module = True
        create_permissions(app_config, apps=apps, verbosity=0, using=schema_editor.connection.alias)
        app_config.models_module = None


def seed_groups(apps, schema_editor):
    """그룹 2종을 만들고 권한을 붙인다."""
    _ensure_permissions_exist(apps, schema_editor)

    Group = apps.get_model("auth", "Group")
    Permission = apps.get_model("auth", "Permission")
    db = schema_editor.connection.alias

    for group_name, spec in GROUPS.items():
        group, _ = Group.objects.using(db).get_or_create(name=group_name)

        codenames_by_app: dict[str, list[str]] = {}
        for app_label, model_name, actions in spec:
            codenames_by_app.setdefault(app_label, []).extend(
                f"{action}_{model_name}" for action in actions
            )

        permissions = []
        for app_label, codenames in codenames_by_app.items():
            found = list(
                Permission.objects.using(db).filter(
                    content_type__app_label=app_label, codename__in=codenames
                )
            )
            # 오타로 권한 하나가 조용히 빠지는 것을 막는다.
            # 모델명을 잘못 적으면 여기서 즉시 터진다 — 배포 후에 발견하는 것보다 낫다.
            if len(found) != len(codenames):
                missing = set(codenames) - {p.codename for p in found}
                raise RuntimeError(
                    f"{group_name}: {app_label} 권한을 찾지 못했습니다 → {sorted(missing)}"
                )
            permissions.extend(found)

        # `set()` 이라 재실행해도 중복이 쌓이지 않는다.
        # 운영자가 Admin 에서 권한을 더 붙여 뒀다면 **되돌아간다** — 그룹의 권한 구성은
        # 코드가 정본이라는 뜻이다. 개인에게만 주고 싶은 권한은 사용자 단위로 준다.
        group.permissions.set(permissions)


def unseed_groups(apps, schema_editor):
    """되돌리기 — 그룹 2종을 지운다.

    그룹에 속한 회원의 `is_staff` 는 건드리지 않는다. 그룹만 사라지므로
    Admin 에는 들어가되 아무것도 보이지 않는 상태가 된다. 되돌리기의
    목적(스키마를 이전 상태로)에는 그게 맞다.
    """
    Group = apps.get_model("auth", "Group")
    Group.objects.using(schema_editor.connection.alias).filter(name__in=GROUPS).delete()


class Migration(migrations.Migration):

    # 권한을 붙일 모델이 **전부 만들어진 뒤**여야 한다.
    # 하나라도 빠지면 `create_permissions` 가 그 앱의 권한을 만들지 못해
    # 위의 RuntimeError 로 떨어진다.
    dependencies = [
        ("accounts", "0003_remove_account_idx_contest"),
        ("auth", "0012_alter_user_first_name_max_length"),
        ("contenttypes", "0002_remove_content_type_name"),
        ("contests", "0001_initial"),
        ("core", "0002_seed_app_settings"),
        ("insight", "0002_seed_knowledge_documents"),
        ("learning", "0002_seed_lessons_and_guides"),
        ("trading", "0003_seed_alternative_products"),
    ]

    operations = [
        migrations.RunPython(seed_groups, unseed_groups),
    ]
