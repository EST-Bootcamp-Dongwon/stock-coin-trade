"""SubscriptionRegistry 에 `needs_orderbook` 추가 (세션 10 · 변경노트 E-44).

호가는 대회 모드에서만 쓴다(F-16 2.5). 우선순위만으로는 대회/연습이 구분되지 않아
연습 종목까지 KIS 호가를 받게 되고, **초당 몇 건뿐인 유량이 쓰이지도 않는 곳에**
소모된다. `market` 은 계좌 모드를 알 수 없으므로(규약 1.1) 등록하는 쪽이 넘긴다.

기본값이 `False` 라 기존 행은 그대로 두어도 안전하다 — 호가를 안 받게 될 뿐이고,
다음 주문 접수 때 `mark_priority()` 가 참으로 올린다.
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('market', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='subscriptionregistry',
            name='needs_orderbook',
            field=models.BooleanField(default=False, help_text='실호가로 체결하는 종목(대회)만 참. 잡 2 가 KIS 유량을 여기에만 쓴다', verbose_name='호가 필요'),
        ),
    ]
