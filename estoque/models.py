"""Consultas e resumos do módulo de estoque."""

from auditlog.registry import auditlog
from django.conf import settings
from django.db import models
from django.db.models import Count, Q
from django.utils import timezone

from equipamentos.models import EntradaLote, Equipamento, StatusEquipamento


class StatusReservaEstoque(models.TextChoices):
    RESERVADA = 'reservada', 'Reservada'
    SEPARADA = 'separada', 'Separada'
    ENTREGUE = 'entregue', 'Entregue'
    CANCELADA = 'cancelada', 'Cancelada'


class ReservaEstoque(models.Model):
    chamado = models.ForeignKey(
        'chamados.Chamado',
        on_delete=models.CASCADE,
        related_name='reservas_estoque',
    )
    item_solicitado = models.ForeignKey(
        'chamados.ChamadoItemSolicitado',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='reservas_estoque',
    )
    equipamento = models.ForeignKey(
        Equipamento,
        on_delete=models.CASCADE,
        related_name='reservas_estoque',
    )
    solicitante = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='reservas_estoque_solicitadas',
    )
    separado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='reservas_estoque_separadas',
    )
    status = models.CharField(
        max_length=20,
        choices=StatusReservaEstoque.choices,
        default=StatusReservaEstoque.RESERVADA,
        db_index=True,
    )
    observacoes = models.TextField(blank=True)
    reserved_at = models.DateTimeField(auto_now_add=True)
    separated_at = models.DateTimeField(null=True, blank=True)
    delivered_at = models.DateTimeField(null=True, blank=True)
    canceled_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Reserva de estoque'
        verbose_name_plural = 'Reservas de estoque'
        ordering = ['-created_at']
        constraints = [
            models.UniqueConstraint(
                fields=['equipamento'],
                condition=Q(status__in=[StatusReservaEstoque.RESERVADA, StatusReservaEstoque.SEPARADA]),
                name='estoque_reserva_ativa_por_equipamento',
            )
        ]

    def __str__(self):
        return f'#{self.chamado_id} · {self.equipamento.id_patrimonio} · {self.get_status_display()}'

    @property
    def is_ativa(self):
        return self.status in {StatusReservaEstoque.RESERVADA, StatusReservaEstoque.SEPARADA}

    def marcar_separada(self, usuario=None):
        agora = timezone.now()
        self.status = StatusReservaEstoque.SEPARADA
        self.separated_at = agora
        if usuario is not None:
            self.separado_por = usuario
        self.updated_at = agora
        campos = {
            'status': self.status,
            'separated_at': self.separated_at,
            'updated_at': self.updated_at,
        }
        if usuario is not None:
            campos['separado_por'] = self.separado_por
        self.__class__.objects.filter(pk=self.pk).update(**campos)
        return self

    def marcar_entregue(self, usuario=None):
        agora = timezone.now()
        self.status = StatusReservaEstoque.ENTREGUE
        self.delivered_at = agora
        if usuario is not None and self.separado_por_id is None:
            self.separado_por = usuario
        self.updated_at = agora
        campos = {
            'status': self.status,
            'delivered_at': self.delivered_at,
            'updated_at': self.updated_at,
        }
        if usuario is not None and self.separado_por_id is None:
            campos['separado_por'] = self.separado_por
        self.__class__.objects.filter(pk=self.pk).update(**campos)
        return self

    def cancelar(self, usuario=None, motivo=''):
        agora = timezone.now()
        self.status = StatusReservaEstoque.CANCELADA
        self.canceled_at = agora
        if motivo:
            self.observacoes = f'{self.observacoes}\n{motivo}'.strip() if self.observacoes else motivo
        self.updated_at = agora
        self.__class__.objects.filter(pk=self.pk).update(
            status=self.status,
            canceled_at=self.canceled_at,
            observacoes=self.observacoes,
            updated_at=self.updated_at,
        )
        return self


def equipamentos_em_estoque():
    return Equipamento.objects.filter(status=StatusEquipamento.EM_ESTOQUE)


def equipamentos_em_manutencao():
    return Equipamento.objects.filter(status=StatusEquipamento.EM_MANUTENCAO)


def lotes_recentes(limit=10):
    return EntradaLote.objects.select_related('criado_por').order_by('-created_at')[:limit]


def resumo_por_tipo():
    return (
        equipamentos_em_estoque()
        .values('tipo')
        .annotate(total=Count('id'))
        .order_by('-total', 'tipo')
    )


def resumo_por_status():
    return (
        Equipamento.objects.values('status')
        .annotate(total=Count('id'))
        .order_by('-total', 'status')
    )


def resumo_por_site(limit=8):
    return (
        Equipamento.objects.exclude(site='')
        .values('site')
        .annotate(
            total=Count('id'),
            em_uso=Count('id', filter=Q(status=StatusEquipamento.EM_USO)),
            em_estoque=Count('id', filter=Q(status=StatusEquipamento.EM_ESTOQUE)),
            em_manutencao=Count('id', filter=Q(status=StatusEquipamento.EM_MANUTENCAO)),
            descartado=Count('id', filter=Q(status=StatusEquipamento.DESCARTADO)),
        )
        .order_by('-total', 'site')[:limit]
    )


def resumo_por_localizacao(limit=12):
    return (
        Equipamento.objects.exclude(site='').exclude(setor='').exclude(andar_sala='')
        .values('site', 'setor', 'andar_sala')
        .annotate(
            total=Count('id'),
            em_uso=Count('id', filter=Q(status=StatusEquipamento.EM_USO)),
            em_estoque=Count('id', filter=Q(status=StatusEquipamento.EM_ESTOQUE)),
            em_manutencao=Count('id', filter=Q(status=StatusEquipamento.EM_MANUTENCAO)),
            descartado=Count('id', filter=Q(status=StatusEquipamento.DESCARTADO)),
        )
        .order_by('-total', 'site', 'setor', 'andar_sala')[:limit]
    )


def reservas_ativas_queryset():
    return (
        ReservaEstoque.objects.select_related(
            'chamado',
            'item_solicitado',
            'equipamento',
            'solicitante',
            'separado_por',
        )
        .filter(status__in=[StatusReservaEstoque.RESERVADA, StatusReservaEstoque.SEPARADA])
        .order_by('-created_at')
    )


def reservas_ativas_por_chamado(chamado):
    if chamado is None:
        return ReservaEstoque.objects.none()
    return reservas_ativas_queryset().filter(chamado=chamado)


def equipamentos_reservados_para_chamado(chamado):
    return Equipamento.objects.filter(reservas_estoque__in=reservas_ativas_por_chamado(chamado)).distinct()


auditlog.register(ReservaEstoque)
