from django.db import migrations, models
import django.db.models.deletion
import uuid


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ('auth', '0012_alter_user_first_name_max_length'),
    ]

    operations = [
        migrations.CreateModel(
            name='MahjongRule',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=100, verbose_name='ルール名')),
                ('detail', models.TextField(verbose_name='ルール詳細')),
                ('order', models.PositiveIntegerField(default=0, verbose_name='表示順')),
            ],
            options={'ordering': ['order'], 'verbose_name': '対局ルール', 'verbose_name_plural': '対局ルール'},
        ),
        migrations.CreateModel(
            name='GameRoom',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=200, verbose_name='対局名')),
                ('game_count', models.PositiveIntegerField(verbose_name='対局数')),
                ('invite_token', models.UUIDField(default=uuid.uuid4, editable=False, unique=True, verbose_name='招待トークン')),
                ('created_at', models.DateTimeField(auto_now_add=True, verbose_name='作成日時')),
                ('created_by', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='created_rooms', to='auth.user', verbose_name='作成者')),
                ('rule', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to='accounts.mahjongRule', verbose_name='ルール')),
            ],
            options={'ordering': ['-created_at'], 'verbose_name': '対局ルーム', 'verbose_name_plural': '対局ルーム'},
        ),
        migrations.CreateModel(
            name='GameRoomMember',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('joined_at', models.DateTimeField(auto_now_add=True, verbose_name='参加日時')),
                ('room', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='members', to='accounts.gameroom', verbose_name='対局ルーム')),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='joined_rooms', to='auth.user', verbose_name='ユーザ')),
            ],
            options={'ordering': ['joined_at'], 'unique_together': {('room', 'user')}, 'verbose_name': 'メンバー', 'verbose_name_plural': 'メンバー'},
        ),
        # マスタデータ投入
        migrations.RunPython(
            code=lambda apps, schema_editor: apps.get_model('accounts', 'MahjongRule').objects.bulk_create([
                apps.get_model('accounts', 'MahjongRule')(name='①Mルール', detail='25,000点持ち30,000返し、10-30', order=1),
                apps.get_model('accounts', 'MahjongRule')(name='②雀魂ルール', detail='段位に応じる', order=2),
            ]),
            reverse_code=lambda apps, schema_editor: apps.get_model('accounts', 'MahjongRule').objects.all().delete(),
        ),
    ]
