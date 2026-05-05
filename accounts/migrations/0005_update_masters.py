from django.db import migrations

NEW_YAKUS = [
    ('門前清自摸和', 1, 1),
    ('立直', 1, 2),
    ('搶槓', 1, 3),
    ('嶺上開花', 1, 4),
    ('海底摸月', 1, 5),
    ('河底撈魚', 1, 6),
    ('役牌 白', 1, 7),
    ('役牌 發', 1, 8),
    ('役牌 中', 1, 9),
    ('役牌 自風牌', 1, 10),
    ('役牌 場風牌', 1, 11),
    ('断么九', 1, 12),
    ('一盃口', 1, 13),
    ('平和', 1, 14),
    ('混全帯么九', 2, 15),
    ('一気通貫', 2, 16),
    ('三色同順', 2, 17),
    ('ダブル立直', 2, 18),
    ('三色同刻', 2, 19),
    ('三槓子', 2, 20),
    ('対々和', 2, 21),
    ('三暗刻', 2, 22),
    ('小三元', 2, 23),
    ('混老頭', 2, 24),
    ('七対子', 2, 25),
    ('純全帯么九', 3, 26),
    ('混一色', 3, 27),
    ('二盃口', 3, 28),
    ('清一色', 6, 29),
    ('一発', 1, 30),
    ('流し満貫', 5, 31),
    ('天和', 13, 32),
    ('地和', 13, 33),
    ('大三元', 13, 34),
    ('四暗刻', 13, 35),
    ('字一色', 13, 36),
    ('緑一色', 13, 37),
    ('清老頭', 13, 38),
    ('国士無双', 13, 39),
    ('小四喜', 13, 40),
    ('四槓子', 13, 41),
    ('九蓮宝燈', 13, 42),
    ('純正九蓮宝燈', 13, 43),
    ('四暗刻単騎', 13, 44),
    ('国士無双十三面待ち', 13, 45),
    ('大四喜', 13, 46),
]


def update_yakus(apps, schema_editor):
    YakuMaster = apps.get_model('accounts', 'YakuMaster')
    YakuMaster.objects.all().delete()
    YakuMaster.objects.bulk_create([
        YakuMaster(name=name, han=han, order=order)
        for name, han, order in NEW_YAKUS
    ])


def update_rules(apps, schema_editor):
    MahjongRule = apps.get_model('accounts', 'MahjongRule')
    MahjongRule.objects.filter(name='①Mルール').update(name='Mルール')
    MahjongRule.objects.filter(name='②雀魂ルール').update(name='雀魂ルール')


class Migration(migrations.Migration):
    dependencies = [
        ('accounts', '0004_fix_gameresult'),
    ]
    operations = [
        migrations.RunPython(update_yakus, migrations.RunPython.noop),
        migrations.RunPython(update_rules, migrations.RunPython.noop),
    ]
