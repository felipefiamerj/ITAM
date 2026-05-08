from django.conf import settings
from django.db import models
from django.utils import timezone


class PrioridadeChamado(models.TextChoices):
    BAIXA = 'baixa', 'Baixa'
    MEDIA = 'media', 'Média'
    ALTA = 'alta', 'Alta'
    CRITICA = 'critica', 'Crítica'


class StatusChamado(models.TextChoices):
    ABERTO = 'aberto', 'Aberto'
    EM_ANALISE = 'em_analise', 'Em análise'
    EM_ATENDIMENTO = 'em_atendimento', 'Em atendimento'
    AGUARDANDO_USUARIO = 'aguardando_usuario', 'Aguardando usuário'
    RESOLVIDO = 'resolvido', 'Resolvido'
    FECHADO = 'fechado', 'Fechado'


class Chamado(models.Model):
    titulo = models.CharField(max_length=150)
    descricao = models.TextField()
    equipamento = models.ForeignKey(
        'equipamentos.Equipamento',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='chamados',
    )
    solicitante = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='chamados_usuario',
    )
    responsavel = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='chamados_responsavel',
    )
    prioridade = models.CharField(
        max_length=20,
        choices=PrioridadeChamado.choices,
        default=PrioridadeChamado.MEDIA,
    )
    status = models.CharField(
        max_length=20,
        choices=StatusChamado.choices,
        default=StatusChamado.ABERTO,
    )
    solucao = models.TextField(blank=True)
    data_fechamento = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'#{self.pk or "novo"} - {self.titulo}'

    def save(self, *args, **kwargs):
        if self.status in {StatusChamado.RESOLVIDO, StatusChamado.FECHADO} and not self.data_fechamento:
            self.data_fechamento = timezone.now()
        super().save(*args, **kwargs)

    @property
    def tempo_aberto(self):
        referencia = self.data_fechamento or timezone.now()
        return referencia - self.created_at

    def fechar(self):
        self.status = StatusChamado.FECHADO
        self.data_fechamento = timezone.now()
        return self
