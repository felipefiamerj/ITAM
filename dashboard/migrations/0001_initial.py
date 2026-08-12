import django.core.validators
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models

import dashboard.models


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='BackupConfiguration',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                (
                    'retention_days',
                    models.PositiveSmallIntegerField(
                        default=3,
                        validators=[
                            django.core.validators.MinValueValidator(1),
                            django.core.validators.MaxValueValidator(3650),
                        ],
                        verbose_name='Retencao em dias',
                    ),
                ),
                (
                    'schedule_times',
                    models.JSONField(default=dashboard.models.default_backup_schedule, verbose_name='Horarios diarios'),
                ),
                ('updated_at', models.DateTimeField(auto_now=True, verbose_name='Atualizado em')),
                (
                    'updated_by',
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name='backup_configurations_updated',
                        to=settings.AUTH_USER_MODEL,
                        verbose_name='Atualizado por',
                    ),
                ),
            ],
            options={
                'verbose_name': 'Configuracao de backup',
                'verbose_name_plural': 'Configuracoes de backup',
            },
        ),
    ]
