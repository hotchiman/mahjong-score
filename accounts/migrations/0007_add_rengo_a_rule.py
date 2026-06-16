from django.db import migrations, models


def add_rengo_a_rule(apps, schema_editor):
    MahjongRule = apps.get_model('accounts', 'MahjongRule')

    # 連盟Aルールを order=2 で挿入するため、
    # WRCルール以降の order を +1 ずらす
    MahjongRule.objects.filter(order__gte=2).update(order=models.F('order') + 1)

    MahjongRule.objects.create(
        name='連盟Aルール',
        order=2,
        init_points=30000,
        return_points=30000,
        # uma1〜uma4 は連盟Aルールでは使用しない（uma_type='rengo_a' で分岐）
        uma1=0,
        uma2=0,
        uma3=0,
        uma4=0,
        uma_type='rengo_a',
        kyotaku_handling='carryover',
        draw_handling='split',
    )


def remove_rengo_a_rule(apps, schema_editor):
    MahjongRule = apps.get_model('accounts', 'MahjongRule')
    MahjongRule.objects.filter(name='連盟Aルール').delete()
    # order を元に戻す
    MahjongRule.objects.filter(order__gte=3).update(order=models.F('order') - 1)


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0006_rule_detail_fields'),
    ]

    operations = [
        migrations.AddField(
            model_name='mahjongRule',
            name='uma_type',
            field=models.CharField(
                verbose_name='ウマ種別',
                max_length=20,
                choices=[
                    ('fixed',   '固定ウマ'),
                    ('rengo_a', '連盟Aルール（浮き人数連動）'),
                ],
                default='fixed',
            ),
        ),
        migrations.RunPython(add_rengo_a_rule, remove_rengo_a_rule),
    ]
