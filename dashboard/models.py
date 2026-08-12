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


class SystemHealthStatus(models.TextChoices):
    HEALTHY = 'healthy', 'Saudavel'
    WARNING = 'warning', 'Atencao'
    CRITICAL = 'critical', 'Critico'
    UNKNOWN = 'unknown', 'Sem dados'


class SystemHealthComponent(models.Model):
    component_key = models.CharField('Componente', max_length=50, unique=True)
    name = models.CharField('Nome', max_length=100)
    status = models.CharField(
        'Status',
        max_length=20,
        choices=SystemHealthStatus.choices,
        default=SystemHealthStatus.UNKNOWN,
        db_index=True,
    )
    summary = models.CharField('Resumo', max_length=255, blank=True)
    details = models.JSONField('Detalhes', default=dict, blank=True)
    source = models.CharField('Origem', max_length=20, default='scheduled')
    checked_at = models.DateTimeField('Verificado em')
    status_changed_at = models.DateTimeField('Status alterado em')
    last_notified_status = models.CharField(max_length=20, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Componente de saude'
        verbose_name_plural = 'Componentes de saude'
        ordering = ['name']

    def __str__(self):
        return f'{self.name}: {self.get_status_display()}'


class SystemHealthEvent(models.Model):
    component_key = models.CharField('Componente', max_length=50, db_index=True)
    component_name = models.CharField('Nome', max_length=100)
    previous_status = models.CharField('Status anterior', max_length=20, blank=True)
    status = models.CharField('Status', max_length=20, choices=SystemHealthStatus.choices, db_index=True)
    summary = models.CharField('Resumo', max_length=255)
    details = models.JSONField('Detalhes', default=dict, blank=True)
    occurred_at = models.DateTimeField('Ocorrido em', auto_now_add=True, db_index=True)

    class Meta:
        verbose_name = 'Evento de saude'
        verbose_name_plural = 'Eventos de saude'
        ordering = ['-occurred_at']

    def __str__(self):
        return f'{self.component_name}: {self.get_status_display()}'


class RestoreTestResult(models.TextChoices):
    SUCCESS = 'success', 'Aprovado'
    FAILED = 'failed', 'Falhou'


class RestoreValidation(models.Model):
    tested_at = models.DateTimeField('Testado em')
    result = models.CharField('Resultado', max_length=20, choices=RestoreTestResult.choices)
    backup_manifest = models.CharField('Ponto utilizado', max_length=255)
    notes = models.TextField('Observacoes', blank=True)
    recorded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name='restore_validations_recorded',
        verbose_name='Registrado por',
        null=True,
        blank=True,
    )
    created_at = models.DateTimeField('Registrado em', auto_now_add=True)

    class Meta:
        verbose_name = 'Validacao de restauracao'
        verbose_name_plural = 'Validacoes de restauracao'
        ordering = ['-tested_at', '-created_at']

    def __str__(self):
        return f'{self.get_result_display()} em {self.tested_at:%d/%m/%Y %H:%M}'


auditlog.register(RestoreValidation)
