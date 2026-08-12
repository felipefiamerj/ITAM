from datetime import datetime

from auditlog.registry import auditlog
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models


def default_backup_schedule():
    return ['19:00']


class BackupConfiguration(models.Model):
    retention_days = models.PositiveSmallIntegerField(
        'Retencao em dias',
        default=3,
        validators=[MinValueValidator(1), MaxValueValidator(30)],
    )
    schedule_times = models.JSONField('Horarios diarios', default=default_backup_schedule)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name='backup_configurations_updated',
        verbose_name='Atualizado por',
        null=True,
        blank=True,
    )
    updated_at = models.DateTimeField('Atualizado em', auto_now=True)

    class Meta:
        verbose_name = 'Configuracao de backup'
        verbose_name_plural = 'Configuracoes de backup'

    def __str__(self):
        return f'Backups por {self.retention_days} dias em {", ".join(self.schedule_times)}'

    def clean(self):
        super().clean()
        if not isinstance(self.schedule_times, list) or not self.schedule_times:
            raise ValidationError({'schedule_times': 'Informe pelo menos um horario.'})

        normalized = []
        for value in self.schedule_times:
            try:
                normalized.append(datetime.strptime(str(value), '%H:%M').strftime('%H:%M'))
            except ValueError as exc:
                raise ValidationError({'schedule_times': f'Horario invalido: {value}.'}) from exc
        if len(normalized) != len(set(normalized)):
            raise ValidationError({'schedule_times': 'Nao repita o mesmo horario.'})

    @classmethod
    def load(cls):
        configuration, _ = cls.objects.get_or_create(
            pk=1,
            defaults={
                'retention_days': 3,
                'schedule_times': default_backup_schedule(),
            },
        )
        return configuration


auditlog.register(BackupConfiguration)
