from django.db import migrations, models
from decimal import Decimal

NEW_RULES = [
    # name, order, init_pts, return_pts, uma1, uma2, uma3, uma4, kyotaku, draw
    ('Mルール',        1, 25000, 30000,  30,  10, -10, -30, 'top_split',  'split_east'),
    ('WRCルール',      2, 30000, 30000,  15,   5,  -5, -15, 'carryover',  'split'),
    ('Sample01ルール', 3, 25000, 30000,  40,  20, -20, -40, 'top_east',   'split'),
    ('Sample02ルール', 4, 25000, 30000,  30,  10, -10, -30, 'carryover',  'east_priority'),
]

# 旧ルール名 → 新ルール名 のマッピング（既存データの名前を更新）
RENAME_MAP = {
    '①Mルール':    'Mルール',
    '②雀魂ルール': 'WRCルール',
    'Mルール':      'Mルール',      # すでに変換済みの場合
    '雀魂ルール':   'WRCルール',    # すでに変換済みの場合
}


def apply(apps, schema_editor):
    MahjongRule = apps.get_model('accounts', 'MahjongRule')

    # Step1: 既存ルールをすべてupsert（削除しない）
    for name, order, ip, rp, u1, u2, u3, u4, kh, dh in NEW_RULES:
        obj, created = MahjongRule.objects.get_or_create(name=name)
        obj.order            = order
        obj.init_points      = ip
        obj.return_points    = rp
        obj.uma1             = u1
        obj.uma2             = u2
        obj.uma3             = u3
        obj.uma4             = u4
        obj.kyotaku_handling = kh
        obj.draw_handling    = dh
        obj.save()

    # Step2: 旧名称のルールを新名称に置き換え（参照を付け替えてから旧レコードを削除）
    GameRoom = apps.get_model('accounts', 'GameRoom')
    for old_name, new_name in RENAME_MAP.items():
        if old_name == new_name:
            continue
        old_objs = MahjongRule.objects.filter(name=old_name)
        if not old_objs.exists():
            continue
        new_obj = MahjongRule.objects.filter(name=new_name).first()
        if new_obj is None:
            # 新名称がなければリネーム
            old_objs.first().name = new_name
            old_objs.first().save()
        else:
            # 旧名称を参照しているGameRoomを新名称に付け替えてから削除
            for old_obj in old_objs:
                GameRoom.objects.filter(rule=old_obj).update(rule=new_obj)
                old_obj.delete()


class Migration(migrations.Migration):
    dependencies = [('accounts', '0005_update_masters')]

    operations = [
        # 旧 detail フィールドを削除し、新フィールドを追加
        migrations.RemoveField(model_name='mahjongRule', name='detail'),
        migrations.AddField(model_name='mahjongRule', name='init_points',
            field=models.IntegerField(default=25000, verbose_name='初期持ち点')),
        migrations.AddField(model_name='mahjongRule', name='return_points',
            field=models.IntegerField(default=30000, verbose_name='終局時返し点')),
        migrations.AddField(model_name='mahjongRule', name='uma1',
            field=models.IntegerField(default=20, verbose_name='順位ウマ1（1着）')),
        migrations.AddField(model_name='mahjongRule', name='uma2',
            field=models.IntegerField(default=0, verbose_name='順位ウマ2（2着）')),
        migrations.AddField(model_name='mahjongRule', name='uma3',
            field=models.IntegerField(default=0, verbose_name='順位ウマ3（3着）')),
        migrations.AddField(model_name='mahjongRule', name='uma4',
            field=models.IntegerField(default=-20, verbose_name='順位ウマ4（4着）')),
        migrations.AddField(model_name='mahjongRule', name='kyotaku_handling',
            field=models.CharField(default='top_split', max_length=20, verbose_name='終局時供託の扱い')),
        migrations.AddField(model_name='mahjongRule', name='draw_handling',
            field=models.CharField(default='split_east', max_length=20, verbose_name='同点の扱い')),
        migrations.RunPython(apply, migrations.RunPython.noop),
    ]
