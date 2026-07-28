import io
import secrets
from datetime import timedelta

import qrcode
from auditlog.registry import auditlog
from django.conf import settings
from django.core.files.base import ContentFile
from django.db import models
from django.utils import timezone


class TipoEquipamento(models.TextChoices):
    ADAPTADOR = 'adaptador', 'Adaptador'
    CADEADO_TRAVA = 'cadeado_trava', 'Cadeado/Trava'
    CELULAR = 'celular', 'Celular'
    DESKTOP_AVANCADO = 'desktop_avancado', 'Desktop Avançado'
    DESKTOP_PADRAO = 'desktop_padrao', 'Desktop Padrão'
    DESKTOP_PLUS = 'desktop_plus', 'Desktop Plus'
    DOCKSTATION = 'dockstation', 'Dockstation'
    FONE = 'fone', 'Fone'
    IPAD_TABLET = 'ipad_tablet', 'iPad/Tablet'
    MACBOOK = 'macbook', 'MacBook'
    MOCHILA = 'mochila', 'Mochila'
    MONITOR = 'monitor', 'Monitor'
    MOUSE = 'mouse', 'Mouse'
    NOTEBOOK_AVANCADO = 'notebook_avancado', 'Notebook Avançado'
    NOTEBOOK_PADRAO = 'notebook_padrao', 'Notebook Padrão'
    TECLADO = 'teclado', 'Teclado'
    ULTRABOOK = 'ultrabook', 'Ultrabook'
    OUTRO = 'outro', 'Outro'


class StatusEquipamento(models.TextChoices):
    EM_ESTOQUE = 'em_estoque', 'Em estoque'
    EM_USO = 'em_uso', 'Em uso'
    EM_MANUTENCAO = 'em_manutencao', 'Em manutenção'
    RESERVADO = 'reservado', 'Reservado'
    DESCARTADO = 'descartado', 'Descartado'
    AGUARDANDO_APROVACAO = 'aguardando', 'Aguardando aprovação'


class CondicaoEquipamento(models.TextChoices):
    OTIMO = 'otimo', 'Ótimo'
    BOM = 'bom', 'Bom'
    REGULAR = 'regular', 'Regular'
    RUIM = 'ruim', 'Ruim'
    INUTIL = 'inutil', 'Inútil'


class StatusMonitoramento(models.TextChoices):
    DESCONHECIDO = 'desconhecido', 'Desconhecido'
    ONLINE = 'online', 'Online'
    ALERTA = 'alerta', 'Alerta'
    OFFLINE = 'offline', 'Offline'


class AgenteMonitoramento(models.Model):
    nome = models.CharField('Nome do agente', max_length=120)
    token = models.CharField('Token', max_length=64, unique=True, db_index=True, editable=False)
    host_name = models.CharField('Host principal', max_length=120, blank=True)
    ativo = models.BooleanField('Ativo', default=True)
    last_seen_at = models.DateTimeField('Último heartbeat', null=True, blank=True)
    last_ip = models.GenericIPAddressField('Último IP', null=True, blank=True)
    metadata = models.JSONField('Metadata', default=dict, blank=True)
    criado_por = models.ForeignKey(
        'accounts.Usuario',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='agentes_monitoramento',
        verbose_name='Criado por',
    )
    created_at = models.DateTimeField('Criado em', auto_now_add=True)
    updated_at = models.DateTimeField('Atualizado em', auto_now=True)

    class Meta:
        verbose_name = 'Agente de monitoramento'
        verbose_name_plural = 'Agentes de monitoramento'
        ordering = ['nome']

    def __str__(self):
        return self.nome or f'Agente {self.pk}'

    def save(self, *args, **kwargs):
        if not self.token:
            self.token = secrets.token_urlsafe(32)
        super().save(*args, **kwargs)


class Equipamento(models.Model):
    id_patrimonio = models.CharField('Patrimônio (Ativo)', max_length=50, unique=True, db_index=True)
    tipo = models.CharField('Tipo', max_length=30, choices=TipoEquipamento.choices)
    tipo_outro = models.CharField('Tipo (outro)', max_length=100, blank=True)
    marca = models.CharField('Marca', max_length=50, blank=True)
    modelo = models.CharField('Modelo', max_length=100, blank=True)
    service_tag = models.CharField('Service Tag', max_length=100, blank=True, db_index=True)
    imei = models.CharField('IMEI', max_length=20, blank=True)
    numero_serie = models.CharField('Número de série', max_length=100, blank=True)
    monitor_patrimonio = models.CharField('Monitor (patrimônio)', max_length=50, blank=True)

    status = models.CharField(
        'Status',
        max_length=20,
        choices=StatusEquipamento.choices,
        default=StatusEquipamento.EM_ESTOQUE,
        db_index=True,
    )
    condicao = models.CharField(
        'Condição',
        max_length=20,
        choices=CondicaoEquipamento.choices,
        default=CondicaoEquipamento.BOM,
    )

    responsavel = models.ForeignKey(
        'accounts.Usuario',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='patrimonios',
        verbose_name='Responsável atual',
    )
    data_atribuicao = models.DateTimeField('Data de atribuição', null=True, blank=True)

    site = models.CharField('Site', max_length=100, blank=True)
    setor = models.CharField('Setor', max_length=100, blank=True)
    andar_sala = models.CharField('Andar/Sala', max_length=50, blank=True)

    descricao = models.TextField('Descrição/Observações', blank=True)
    data_aquisicao = models.DateField('Data de aquisição', null=True, blank=True)
    valor_aquisicao = models.DecimalField(
        'Valor de aquisição (R$)',
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
    )
    garantia_ate = models.DateField('Garantia até', null=True, blank=True)
    vida_util_estimada_meses = models.PositiveIntegerField('Vida útil estimada (meses)', default=36)

    qr_code = models.ImageField('QR Code', upload_to='qrcodes/', blank=True, null=True)
    score_saude = models.FloatField('Score de saúde (IA)', default=100.0)

    monitoramento_ativo = models.BooleanField('Monitoramento ativo', default=False)
    monitoramento_status = models.CharField(
        'Status de monitoramento',
        max_length=20,
        choices=StatusMonitoramento.choices,
        default=StatusMonitoramento.DESCONHECIDO,
        db_index=True,
    )
    last_seen_at = models.DateTimeField('Último heartbeat', null=True, blank=True, db_index=True)
    last_telemetria_agente = models.ForeignKey(
        AgenteMonitoramento,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='equipamentos_vistos',
        verbose_name='Último agente',
    )
    last_telemetria_payload = models.JSONField('Último payload de telemetria', default=dict, blank=True)

    criado_por = models.ForeignKey(
        'accounts.Usuario',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='equipamentos_criados',
        verbose_name='Criado por',
    )
    created_at = models.DateTimeField('Criado em', auto_now_add=True)
    updated_at = models.DateTimeField('Atualizado em', auto_now=True)

    class Meta:
        verbose_name = 'Equipamento'
        verbose_name_plural = 'Equipamentos'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['monitoramento_status', 'last_seen_at']),
        ]

    def __str__(self):
        return f'{self.get_tipo_display()} - {self.id_patrimonio}'

    @property
    def tipo_display(self):
        if self.tipo == TipoEquipamento.OUTRO:
            return self.tipo_outro or 'Outro'
        return self.get_tipo_display()

    @property
    def em_garantia(self):
        return bool(self.garantia_ate and self.garantia_ate >= timezone.now().date())

    @property
    def total_manutencoes(self):
        return self.movimentacoes.filter(tipo='manutencao').count()

    @property
    def localizacao_resumida(self):
        partes = [parte.strip() for parte in [self.site, self.setor, self.andar_sala] if parte and str(parte).strip()]
        return ' · '.join(partes) if partes else 'Sem localização'

    @property
    def monitoramento_em_atraso(self):
        if not self.monitoramento_ativo or not self.last_seen_at:
            return False
        limite_minutos = getattr(settings, 'ITAM_HEARTBEAT_STALE_MINUTES', 10)
        return self.last_seen_at < timezone.now() - timedelta(minutes=limite_minutos)

    def save(self, *args, **kwargs):
        if self.id_patrimonio and not self.qr_code:
            self._gerar_qrcode()
        super().save(*args, **kwargs)

    def _gerar_qrcode(self):
        qr = qrcode.QRCode(version=1, box_size=10, border=4)
        qr.add_data(f'PATRIMONIO:{self.id_patrimonio}')
        qr.make(fit=True)
        image = qr.make_image(fill_color='black', back_color='white')
        buffer = io.BytesIO()
        image.save(buffer, format='PNG')
        self.qr_code.save(f'qr_{self.id_patrimonio}.png', ContentFile(buffer.getvalue()), save=False)

    def get_historico(self):
        return [
            {
                'tipo': movimento.get_tipo_display(),
                'descricao': movimento.descricao,
                'usuario': str(movimento.realizado_por) if movimento.realizado_por else '-',
                'data': movimento.created_at,
                'icon': movimento.get_icon(),
            }
            for movimento in self.movimentacoes.select_related('realizado_por').order_by('-created_at')
        ]


class MovimentacaoEquipamento(models.Model):
    TIPOS = [
        ('entrada', 'Entrada em estoque'),
        ('reserva', 'Reserva de estoque'),
        ('liberacao_reserva', 'Liberação de reserva'),
        ('saida', 'Saída para usuário'),
        ('devolucao', 'Devolução ao estoque'),
        ('manutencao', 'Envio para manutenção'),
        ('retorno_manutencao', 'Retorno da manutenção'),
        ('descarte', 'Descarte'),
        ('transferencia', 'Transferência'),
        ('troca', 'Troca de equipamento'),
    ]

    equipamento = models.ForeignKey(Equipamento, on_delete=models.CASCADE, related_name='movimentacoes')
    tipo = models.CharField('Tipo', max_length=30, choices=TIPOS)
    descricao = models.TextField('Descrição')
    usuario_anterior = models.ForeignKey(
        'accounts.Usuario',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='movimentacoes_saiu',
        verbose_name='Usuário anterior',
    )
    usuario_novo = models.ForeignKey(
        'accounts.Usuario',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='movimentacoes_entrou',
        verbose_name='Novo usuário',
    )
    realizado_por = models.ForeignKey(
        'accounts.Usuario',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='movimentacoes_realizadas',
        verbose_name='Realizado por (técnico)',
    )
    chamado = models.ForeignKey(
        'chamados.Chamado',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='movimentacoes',
        verbose_name='Chamado vinculado',
    )
    observacoes = models.TextField('Observações', blank=True)
    created_at = models.DateTimeField('Data/Hora', auto_now_add=True)

    class Meta:
        verbose_name = 'Movimentação'
        verbose_name_plural = 'Movimentações'
        ordering = ['-created_at']

    def __str__(self):
        timestamp = self.created_at.strftime('%d/%m/%Y %H:%M') if self.created_at else 'sem data'
        return f'{self.get_tipo_display()} - {self.equipamento.id_patrimonio} - {timestamp}'

    def get_icon(self):
        icons = {
            'entrada': 'fa-arrow-down text-success',
            'saida': 'fa-arrow-up text-primary',
            'devolucao': 'fa-undo text-warning',
            'manutencao': 'fa-wrench text-danger',
            'retorno_manutencao': 'fa-check-circle text-info',
            'descarte': 'fa-trash text-danger',
            'transferencia': 'fa-exchange-alt text-secondary',
            'troca': 'fa-sync text-warning',
        }
        return icons.get(self.tipo, 'fa-circle')


class TelemetriaEvento(models.Model):
    TIPOS = [
        ('heartbeat', 'Heartbeat'),
        ('conectado', 'Conectado'),
        ('desconectado', 'Desconectado'),
        ('bateria_baixa', 'Bateria baixa'),
        ('erro_driver', 'Erro de driver'),
        ('health_warning', 'Alerta de saude'),
    ]

    SEVERIDADES = [
        ('info', 'Info'),
        ('warning', 'Warning'),
        ('critical', 'Critical'),
    ]

    equipamento = models.ForeignKey(Equipamento, on_delete=models.CASCADE, related_name='telemetria_eventos')
    agente = models.ForeignKey(
        AgenteMonitoramento,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='eventos',
    )
    tipo = models.CharField('Tipo', max_length=30, choices=TIPOS)
    severidade = models.CharField('Severidade', max_length=20, choices=SEVERIDADES, default='info')
    mensagem = models.TextField('Mensagem', blank=True)
    payload = models.JSONField('Payload', default=dict, blank=True)
    created_at = models.DateTimeField('Criado em', auto_now_add=True)

    class Meta:
        verbose_name = 'Evento de telemetria'
        verbose_name_plural = 'Eventos de telemetria'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['equipamento', '-created_at']),
            models.Index(fields=['agente', '-created_at']),
        ]

    def __str__(self):
        return f'{self.get_tipo_display()} - {self.equipamento.id_patrimonio}'


class EntradaLote(models.Model):
    """Registro de entrada em lote de equipamentos via Excel."""

    arquivo = models.FileField('Arquivo Excel', upload_to='lotes/')
    descricao = models.CharField('Descrição do lote', max_length=200, blank=True)
    total_itens = models.PositiveIntegerField('Total de itens', default=0)
    itens_importados = models.PositiveIntegerField('Itens importados', default=0)
    itens_com_erro = models.PositiveIntegerField('Itens com erro', default=0)
    status = models.CharField(
        'Status',
        max_length=20,
        default='pendente',
        choices=[
            ('pendente', 'Pendente'),
            ('processando', 'Processando'),
            ('concluido', 'Concluído'),
            ('erro', 'Erro'),
        ],
    )
    log_erros = models.TextField('Log de erros', blank=True)
    criado_por = models.ForeignKey('accounts.Usuario', on_delete=models.SET_NULL, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Entrada em lote'
        verbose_name_plural = 'Entradas em lote'
        ordering = ['-created_at']

    def __str__(self):
        return self.descricao or self.arquivo.name or 'Entrada em lote'


auditlog.register(Equipamento, exclude_fields=['qr_code', 'score_saude'])
auditlog.register(MovimentacaoEquipamento)
auditlog.register(EntradaLote)
