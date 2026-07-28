from datetime import timedelta

from django.conf import settings
from django.db import models
from django.utils import timezone

from auditlog.registry import auditlog
from equipamentos.models import TipoEquipamento


class PrioridadeChamado(models.TextChoices):
    BAIXA = 'baixa', 'Baixa'
    MEDIA = 'media', 'Média'
    ALTA = 'alta', 'Alta'
    CRITICA = 'critica', 'Crítica'


class StatusChamado(models.TextChoices):
    FILA = 'fila', 'Fila'
    EM_ATENDIMENTO = 'em_atendimento', 'Em atendimento'
    AGUARDANDO_ATENDIMENTO = 'aguardando_atendimento', 'Aguardando atendimento'
    ENCERRADO = 'encerrado', 'Encerrado'


STATUS_CHAMADO_EM_FLUXO = {
    StatusChamado.FILA,
    StatusChamado.EM_ATENDIMENTO,
    StatusChamado.AGUARDANDO_ATENDIMENTO,
}


class EtapaFluxoChamado(models.TextChoices):
    SOLICITADO = 'solicitado', 'Solicitado'
    TRIAGEM = 'triagem', 'Em triagem'
    AGUARDANDO_ESTOQUE = 'aguardando_estoque', 'Aguardando estoque'
    AGUARDANDO_APROVACAO = 'aguardando_aprovacao', 'Aguardando aprovação'
    APROVADO_PARA_RETIRADA = 'aprovado_para_retirada', 'Aprovado para retirada'
    EM_SEPARACAO = 'em_separacao', 'Em separação'
    PRONTO_PARA_ENTREGA = 'pronto_para_entrega', 'Pronto para entrega'
    ENCERRADO = 'encerrado', 'Encerrado'


FLUXO_CHAMADO_ETAPAS = [
    {
        'key': EtapaFluxoChamado.SOLICITADO,
        'label': 'Solicitado',
        'description': 'O chamado foi aberto pelo solicitante e aguarda triagem do time.',
        'icon': 'fa-inbox',
    },
    {
        'key': EtapaFluxoChamado.TRIAGEM,
        'label': 'Em triagem',
        'description': 'O analista ou estoquista assumiu o chamado e está conferindo a demanda.',
        'icon': 'fa-user-check',
    },
    {
        'key': EtapaFluxoChamado.AGUARDANDO_ESTOQUE,
        'label': 'Aguardando estoque',
        'description': 'O time está aguardando reposição ou confirmação de disponibilidade.',
        'icon': 'fa-warehouse',
    },
    {
        'key': EtapaFluxoChamado.AGUARDANDO_APROVACAO,
        'label': 'Aguardando aprovação',
        'description': 'O colaborador precisa aprovar a retirada antes da separação final.',
        'icon': 'fa-user-shield',
    },
    {
        'key': EtapaFluxoChamado.APROVADO_PARA_RETIRADA,
        'label': 'Aprovado para retirada',
        'description': 'A solicitação foi liberada e pode seguir para a separação do equipamento.',
        'icon': 'fa-circle-check',
    },
    {
        'key': EtapaFluxoChamado.EM_SEPARACAO,
        'label': 'Em separação',
        'description': 'O técnico está preparando os equipamentos para entrega.',
        'icon': 'fa-screwdriver-wrench',
    },
    {
        'key': EtapaFluxoChamado.PRONTO_PARA_ENTREGA,
        'label': 'Pronto para entrega',
        'description': 'Os equipamentos estão separados e prontos para o registro da entrega.',
        'icon': 'fa-box-open',
    },
    {
        'key': EtapaFluxoChamado.ENCERRADO,
        'label': 'Encerrado',
        'description': 'O chamado foi concluído e arquivado com a entrega registrada.',
        'icon': 'fa-circle-check',
    },
]

FLUXO_CHAMADO_ETAPAS_MAP = {etapa['key']: etapa for etapa in FLUXO_CHAMADO_ETAPAS}


class ServicoChamado(models.TextChoices):
    ENTREGA = 'entrega', 'Entrega de equipamento'
    RECOLHIMENTO = 'recolhimento', 'Recolhimento de equipamento'
    TROCA = 'troca', 'Troca de equipamento'
    MANUTENCAO = 'manutencao', 'Manutenção'
    INSTALACAO = 'instalacao', 'Instalação / configuração'
    ORIENTACAO = 'orientacao', 'Orientação'
    OUTRO = 'outro', 'Outro'


class SLANivel(models.TextChoices):
    NORMAL = 'normal', 'Dentro do prazo'
    ALERTA = 'alerta', 'Em alerta'
    ESCALADO = 'escalado', 'Escalonado'


SLA_PRAZOS_MINUTOS = {
    PrioridadeChamado.BAIXA: 48 * 60,
    PrioridadeChamado.MEDIA: 24 * 60,
    PrioridadeChamado.ALTA: 8 * 60,
    PrioridadeChamado.CRITICA: 4 * 60,
}


class Chamado(models.Model):
    titulo = models.CharField(max_length=150)
    descricao = models.TextField()
    servico_realizado = models.CharField(
        max_length=30,
        choices=ServicoChamado.choices,
        blank=True,
        default='',
        db_index=True,
    )
    tipo_equipamento_solicitado = models.CharField(
        max_length=30,
        choices=TipoEquipamento.choices,
        blank=True,
        default='',
        db_index=True,
    )
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
    destinatario = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='chamados_destinatario',
        verbose_name='Colaborador',
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
        max_length=30,
        choices=StatusChamado.choices,
        default=StatusChamado.FILA,
    )
    fluxo_etapa = models.CharField(
        max_length=40,
        choices=EtapaFluxoChamado.choices,
        default=EtapaFluxoChamado.SOLICITADO,
        db_index=True,
    )
    aprovado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='chamados_aprovados',
    )
    aprovado_em = models.DateTimeField(null=True, blank=True)
    solucao = models.TextField(blank=True)
    data_fechamento = models.DateTimeField(null=True, blank=True)
    sla_nivel = models.CharField(max_length=20, choices=SLANivel.choices, default=SLANivel.NORMAL, db_index=True)
    sla_alertado_em = models.DateTimeField(null=True, blank=True)
    sla_escalado_em = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'#{self.pk or "novo"} - {self.titulo}'

    def save(self, *args, **kwargs):
        if self.status == StatusChamado.ENCERRADO:
            self.fluxo_etapa = EtapaFluxoChamado.ENCERRADO
            if not self.data_fechamento:
                self.data_fechamento = timezone.now()
        else:
            if self.fluxo_etapa == EtapaFluxoChamado.ENCERRADO:
                self.fluxo_etapa = EtapaFluxoChamado.SOLICITADO
            self.data_fechamento = None
        super().save(*args, **kwargs)

    @property
    def tempo_aberto(self):
        referencia = self.data_fechamento or timezone.now()
        return referencia - self.created_at

    @property
    def sla_duracao_minutos(self):
        return SLA_PRAZOS_MINUTOS.get(self.prioridade, 24 * 60)

    @property
    def sla_prazo_em(self):
        base = self.created_at or timezone.now()
        return base + timedelta(minutes=self.sla_duracao_minutos)

    @property
    def sla_momento_alerta(self):
        janela = max(30, int(self.sla_duracao_minutos * 0.25))
        return self.sla_prazo_em - timedelta(minutes=janela)

    @property
    def sla_estado_atual(self):
        if self.status == StatusChamado.ENCERRADO:
            return 'encerrado'

        agora = timezone.now()
        if self.sla_nivel == SLANivel.ESCALADO or agora >= self.sla_prazo_em:
            return SLANivel.ESCALADO
        if self.sla_nivel == SLANivel.ALERTA or agora >= self.sla_momento_alerta:
            return SLANivel.ALERTA
        return SLANivel.NORMAL

    @property
    def sla_status_label(self):
        mapa = {
            'encerrado': 'Encerrado',
            SLANivel.NORMAL: 'Dentro do prazo',
            SLANivel.ALERTA: 'Em alerta',
            SLANivel.ESCALADO: 'Escalonado',
        }
        return mapa.get(self.sla_estado_atual, 'Dentro do prazo')

    @property
    def sla_status_tone(self):
        mapa = {
            'encerrado': 'success',
            SLANivel.NORMAL: 'success',
            SLANivel.ALERTA: 'warning',
            SLANivel.ESCALADO: 'danger',
        }
        return mapa.get(self.sla_estado_atual, 'secondary')

    @property
    def sla_restante_label(self):
        if self.status == StatusChamado.ENCERRADO:
            return 'Chamado encerrado'

        delta = self.sla_prazo_em - timezone.now()
        total_minutos = int(delta.total_seconds() // 60)
        horas = abs(total_minutos) // 60
        minutos = abs(total_minutos) % 60

        if total_minutos >= 0:
            if horas:
                return f'{horas}h {minutos:02d}m restantes'
            return f'{minutos}m restantes'

        if horas:
            return f'Atrasado há {horas}h {minutos:02d}m'
        return f'Atrasado há {minutos}m'

    @property
    def sla_em_atraso(self):
        return self.status != StatusChamado.ENCERRADO and timezone.now() >= self.sla_prazo_em

    @property
    def itens_solicitados_resumo(self):
        itens = list(self.itens_solicitados.all().order_by('id'))
        if not itens:
            if self.tipo_equipamento_solicitado:
                return self.get_tipo_equipamento_solicitado_display()
            return '-'

        labels = [item.tipo_display for item in itens]
        if len(labels) <= 3:
            return ', '.join(labels)
        return f'{", ".join(labels[:3])} +{len(labels) - 3}'

    def fechar(self):
        return self.encerrar()

    def encerrar(self):
        self.status = StatusChamado.ENCERRADO
        self.fluxo_etapa = EtapaFluxoChamado.ENCERRADO
        self.data_fechamento = timezone.now()
        return self

    def marcar_fluxo(self, etapa, *, aprovado_por=None, aprovado_em=None):
        self.fluxo_etapa = etapa
        if etapa == EtapaFluxoChamado.ENCERRADO:
            self.status = StatusChamado.ENCERRADO
            self.data_fechamento = aprovado_em or timezone.now()
        else:
            if self.status == StatusChamado.ENCERRADO:
                self.status = StatusChamado.FILA
            self.data_fechamento = None

        if aprovado_por is not None:
            self.aprovado_por = aprovado_por
            self.aprovado_em = aprovado_em or timezone.now()

        return self

    def itens_solicitados_texto_formatado(self):
        linhas = []
        for item in self.itens_solicitados.all().order_by('id'):
            linha = f'{item.tipo_display}; {item.quantidade}'
            if item.observacao:
                linha = f'{linha}; {item.observacao}'
            linhas.append(linha)
        return '\n'.join(linhas)

    @property
    def usuario_destinatario(self):
        return self.destinatario or self.solicitante

    @property
    def destinatario_nome_completo(self):
        pessoa = self.usuario_destinatario
        return pessoa.nome_completo if pessoa else '-'

    @property
    def destinatario_matricula(self):
        pessoa = self.usuario_destinatario
        return pessoa.matricula if pessoa else '-'

    @property
    def solicitacao_origem_label(self):
        if self.solicitante_id and self.destinatario_id and self.solicitante_id == self.destinatario_id:
            return 'Solicitacao feita pelo proprio solicitante'
        if self.solicitante:
            if self.destinatario:
                return f"Solicitacao feita pelo gestor {self.solicitante.nome_completo} em nome de {self.destinatario.nome_completo}"
            return f"Solicitacao registrada por {self.solicitante.nome_completo}"
        return '-'

    @property
    def fluxo_etapa_atual(self):
        return self.fluxo_etapa or EtapaFluxoChamado.SOLICITADO

    @property
    def fluxo_etapa_label(self):
        etapa = FLUXO_CHAMADO_ETAPAS_MAP.get(self.fluxo_etapa_atual)
        return etapa['label'] if etapa else self.get_fluxo_etapa_display()

    @property
    def fluxo_etapa_descricao(self):
        etapa = FLUXO_CHAMADO_ETAPAS_MAP.get(self.fluxo_etapa_atual)
        return etapa['description'] if etapa else ''

    @property
    def fluxo_etapas(self):
        chave_atual = self.fluxo_etapa_atual
        ordem_atual = next((index for index, etapa in enumerate(FLUXO_CHAMADO_ETAPAS) if etapa['key'] == chave_atual), 0)

        etapas = []
        for index, etapa in enumerate(FLUXO_CHAMADO_ETAPAS):
            etapas.append(
                {
                    **etapa,
                    'done': index < ordem_atual,
                    'active': index == ordem_atual,
                }
            )
        return etapas

    @property
    def aprovado_por_label(self):
        if not self.aprovado_por:
            return '-'
        if self.aprovado_em:
            return f'{self.aprovado_por.nome_completo} em {timezone.localtime(self.aprovado_em).strftime("%d/%m/%Y %H:%M")}'
        return self.aprovado_por.nome_completo


class ChamadoItemSolicitado(models.Model):
    chamado = models.ForeignKey(Chamado, on_delete=models.CASCADE, related_name='itens_solicitados')
    tipo_equipamento = models.CharField(max_length=30, choices=TipoEquipamento.choices)
    tipo_outro = models.CharField(max_length=100, blank=True)
    quantidade = models.PositiveIntegerField(default=1)
    observacao = models.CharField(max_length=200, blank=True)
    equipamento_entregue = models.ForeignKey(
        'equipamentos.Equipamento',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='itens_chamado_entregues',
        verbose_name='Equipamento entregue',
    )
    entregue_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='itens_chamado_entregues',
        verbose_name='Entregue por',
    )
    entregue_em = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Item solicitado'
        verbose_name_plural = 'Itens solicitados'
        ordering = ['id']

    def __str__(self):
        return f'{self.tipo_display} x{self.quantidade}'

    @property
    def tipo_display(self):
        if self.tipo_equipamento == TipoEquipamento.OUTRO:
            return self.tipo_outro or 'Outro'
        return self.get_tipo_equipamento_display()

    @property
    def foi_entregue(self):
        return self.equipamento_entregue_id is not None

    @property
    def equipamento_entregue_label(self):
        if not self.equipamento_entregue:
            return '-'
        return self.equipamento_entregue.id_patrimonio

auditlog.register(Chamado, exclude_fields=['solucao'])
auditlog.register(ChamadoItemSolicitado)
