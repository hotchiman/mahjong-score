from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    """
    GameResult.session を OneToOneField → ForeignKey に変更し、
    related_name を 'result' → 'results' に統一する。
    すでに migrate 済みの環境向けパッチ。
    """

    dependencies = [
        ('accounts', '0003_superuser'),
    ]

    operations = [
        migrations.AlterField(
            model_name='gameresult',
            name='session',
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name='results',
                to='accounts.gamesession',
                verbose_name='対局セッション',
            ),
        ),
    ]
