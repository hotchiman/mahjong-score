from django.db import migrations


def create_superuser(apps, schema_editor):
    from django.contrib.auth.models import User
    if not User.objects.filter(username='sa').exists():
        User.objects.create_superuser(username='sa', password='123qwe', email='')


class Migration(migrations.Migration):
    dependencies = [
        ('accounts', '0002_game_results'),
    ]
    operations = [
        migrations.RunPython(create_superuser, migrations.RunPython.noop),
    ]
