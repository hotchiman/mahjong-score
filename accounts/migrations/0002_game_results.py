from django.db import migrations, models
import django.db.models.deletion


ROUND_MASTERS = [
    ('東1', 1), ('東2', 2), ('東3', 3), ('東4', 4),
    ('南1', 5), ('南2', 6), ('南3', 7), ('南4', 8),
    ('西1', 9), ('西2', 10), ('西3', 11), ('西4', 12),
]

YAKU_MASTERS = [
    ('立直', 1, 1), ('一発', 1, 2), ('平和', 1, 3), ('三暗刻', 3, 4),
]


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0001_initial'),
        ('auth', '0012_alter_user_first_name_max_length'),
    ]

    operations = [
        # RoundMaster
        migrations.CreateModel(
            name='RoundMaster',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False)),
                ('name', models.CharField(max_length=20, verbose_name='局名')),
                ('order', models.PositiveIntegerField(default=0, verbose_name='表示順')),
            ],
            options={'ordering': ['order'], 'verbose_name': '局マスタ'},
        ),
        # YakuMaster
        migrations.CreateModel(
            name='YakuMaster',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False)),
                ('name', models.CharField(max_length=50, verbose_name='役名')),
                ('han', models.PositiveIntegerField(default=1, verbose_name='翻数')),
                ('order', models.PositiveIntegerField(default=0, verbose_name='表示順')),
            ],
            options={'ordering': ['order'], 'verbose_name': '役マスタ'},
        ),
        # GameSession
        migrations.CreateModel(
            name='GameSession',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False)),
                ('session_number', models.PositiveIntegerField(verbose_name='対局No')),
                ('game_date', models.DateField(verbose_name='対局日')),
                ('is_finalized', models.BooleanField(default=False, verbose_name='確定済み')),
                ('rank_east',  models.PositiveIntegerField(null=True, blank=True)),
                ('rank_south', models.PositiveIntegerField(null=True, blank=True)),
                ('rank_west',  models.PositiveIntegerField(null=True, blank=True)),
                ('rank_north', models.PositiveIntegerField(null=True, blank=True)),
                ('pts_east',  models.DecimalField(max_digits=8, decimal_places=1, null=True, blank=True)),
                ('pts_south', models.DecimalField(max_digits=8, decimal_places=1, null=True, blank=True)),
                ('pts_west',  models.DecimalField(max_digits=8, decimal_places=1, null=True, blank=True)),
                ('pts_north', models.DecimalField(max_digits=8, decimal_places=1, null=True, blank=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('room', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='sessions', to='accounts.gameroom', verbose_name='対局ルーム')),
                ('player_east',  models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='sessions_east',  to='auth.user', verbose_name='起家')),
                ('player_south', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='sessions_south', to='auth.user', verbose_name='南家')),
                ('player_west',  models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='sessions_west',  to='auth.user', verbose_name='西家')),
                ('player_north', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='sessions_north', to='auth.user', verbose_name='北家')),
            ],
            options={'ordering': ['session_number'], 'unique_together': {('room', 'session_number')}, 'verbose_name': '対局セッション'},
        ),
        # RoundResult
        migrations.CreateModel(
            name='RoundResult',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False)),
                ('round_number', models.PositiveIntegerField(verbose_name='局No')),
                ('honba', models.PositiveIntegerField(default=0, verbose_name='本場')),
                ('kyotaku', models.PositiveIntegerField(default=0, verbose_name='供託本数')),
                ('is_ryukyoku', models.BooleanField(default=False, verbose_name='流局')),
                ('session', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='rounds', to='accounts.gamesession', verbose_name='対局セッション')),
                ('round_master', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to='accounts.roundmaster', verbose_name='局')),
            ],
            options={'ordering': ['round_number'], 'verbose_name': '局結果'},
        ),
        # PlayerRoundResult
        migrations.CreateModel(
            name='PlayerRoundResult',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False)),
                ('seat', models.CharField(max_length=10, verbose_name='座席')),
                ('is_agari', models.BooleanField(default=False, verbose_name='和了')),
                ('agari_method', models.CharField(max_length=10, blank=True, default='', verbose_name='和了方法')),
                ('is_houjuu', models.BooleanField(default=False, verbose_name='放銃')),
                ('is_furo', models.BooleanField(default=False, verbose_name='副露')),
                ('furo_count', models.PositiveIntegerField(default=0, verbose_name='副露回数')),
                ('is_riichi', models.BooleanField(default=False, verbose_name='立直')),
                ('ryukyoku_state', models.CharField(max_length=10, blank=True, default='', verbose_name='流局時状態')),
                ('kyoku_balance', models.IntegerField(default=0, verbose_name='局収支')),
                ('kyotaku_points', models.IntegerField(default=0, verbose_name='供託点')),
                ('points_after', models.IntegerField(default=0, verbose_name='持ち点')),
                ('round_result', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='player_results', to='accounts.roundresult', verbose_name='局結果')),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='auth.user', verbose_name='ユーザ')),
            ],
            options={'unique_together': {('round_result', 'user')}, 'verbose_name': 'プレイヤー局結果'},
        ),
        # RoundYaku
        migrations.CreateModel(
            name='RoundYaku',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False)),
                ('han', models.PositiveIntegerField(default=0, verbose_name='翻数')),
                ('dora', models.PositiveIntegerField(default=0, verbose_name='表ドラ')),
                ('ura_dora', models.PositiveIntegerField(default=0, verbose_name='裏ドラ')),
                ('aka_dora', models.PositiveIntegerField(default=0, verbose_name='赤ドラ')),
                ('round_result', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='yakus', to='accounts.roundresult', verbose_name='局結果')),
                ('agari_user', models.ForeignKey(null=True, blank=True, on_delete=django.db.models.deletion.SET_NULL, related_name='agari_yakus', to='auth.user', verbose_name='和了者')),
                ('houjuu_user', models.ForeignKey(null=True, blank=True, on_delete=django.db.models.deletion.SET_NULL, related_name='houjuu_yakus', to='auth.user', verbose_name='放銃者')),
                ('yakus', models.ManyToManyField(blank=True, to='accounts.yakumaster', verbose_name='役')),
            ],
            options={'verbose_name': '役記録'},
        ),
        # GameResult
        migrations.CreateModel(
            name='GameResult',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False)),
                ('seat', models.CharField(max_length=10, verbose_name='座席')),
                ('rank', models.PositiveIntegerField(verbose_name='着順')),
                ('pts', models.DecimalField(max_digits=8, decimal_places=1, verbose_name='獲得pts')),
                ('final_points', models.IntegerField(verbose_name='最終持ち点')),
                ('session', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='results', to='accounts.gamesession', verbose_name='対局セッション')),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='game_results', to='auth.user', verbose_name='ユーザ')),
            ],
            options={'verbose_name': '対局結果サマリ', 'unique_together': {('session', 'user')}},
        ),
        # マスタデータ投入
        migrations.RunPython(
            code=lambda apps, schema_editor: (
                apps.get_model('accounts', 'RoundMaster').objects.bulk_create([
                    apps.get_model('accounts', 'RoundMaster')(name=name, order=order)
                    for name, order in ROUND_MASTERS
                ]),
                apps.get_model('accounts', 'YakuMaster').objects.bulk_create([
                    apps.get_model('accounts', 'YakuMaster')(name=name, han=han, order=order)
                    for name, han, order in YAKU_MASTERS
                ]),
            ),
            reverse_code=migrations.RunPython.noop,
        ),
    ]
