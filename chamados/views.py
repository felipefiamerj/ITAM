import base64
import binascii
import csv
from datetime import timedelta
from html import escape
from io import BytesIO

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.views.decorators.http import require_POST
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Image as RLImage
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from accounts.models import NivelAcesso, Usuario
from notifications.services import notificar_time_operacional, notificar_usuarios

from .forms import (
    REQUEST_TEMPLATE_CARDS,
    AssinaturaTermoForm,
    ChamadoCreateForm,
    ChamadoUpdateForm,
    EntregaEquipamentoChamadoForm,
    get_request_template,
)
from .models import (
    STATUS_CHAMADO_EM_FLUXO,
    Chamado,
    ChamadoFluxoEvento,
    EtapaFluxoChamado,
    PrioridadeChamado,
    SLANivel,
    StatusChamado,
    StatusTermoAceite,
    TermoAceiteDigital,
)
from .policies import (
    PAINEL_OPERACIONAL_LANES,
)
from .policies import (
    acoes_fluxo_chamado as _fluxo_acoes_chamado,
)
from .policies import (
    pode_aprovar_retirada as _pode_aprovar_retirada,
)
from .policies import (
    pode_editar_chamado as _pode_editar_chamado,
)
from .policies import (
    pode_excluir_chamado as _pode_excluir_chamado,
)
from .policies import (
    pode_gerenciar_chamado as _pode_gerenciar_chamado,
)
from .policies import (
    pode_visualizar_chamado as _pode_visualizar_chamado,
)
from .services import (
    avaliar_sla_etapa_chamado,
    cobrar_assinaturas_termos,
    enviar_link_assinatura_termo,
    excluir_chamado_administrativo,
    gerar_playbook_chamado,
    obter_ou_criar_aceite_termo,
    preparar_evento_fluxo_chamado,
    registrar_assinatura_termo,
    registrar_entregas_chamado,
    renovar_link_assinatura_termo,
    sincronizar_itens_solicitados,
)


def _sla_queryset_filter(qs, sla):
    if sla == 'alerta':
        return qs.filter(sla_nivel=SLANivel.ALERTA)
    if sla == 'escalado':
        return qs.filter(sla_nivel=SLANivel.ESCALADO)
    return qs


TERMO_ACEITE_SITUACOES = (
    ('pendentes', 'Pendentes'),
    ('expirados', 'Expirados'),
    ('sem_envio', 'Sem envio'),
    ('enviados', 'Enviados'),
    ('assinados', 'Assinados'),
    ('todos', 'Todos'),
)


PRODUTIVIDADE_PERIODOS = (
    ('7', '7 dias'),
    ('30', '30 dias'),
    ('90', '90 dias'),
    ('todos', 'Todo historico'),
)


def _base_termos_aceite_queryset():
    return TermoAceiteDigital.objects.select_related(
        'chamado',
        'chamado__solicitante',
        'chamado__destinatario',
        'chamado__responsavel',
        'enviado_por',
    ).filter(chamado__status=StatusChamado.ENCERRADO)


def _filtrar_termos_aceite(qs, params, user, agora=None):
    agora = agora or timezone.now()
    q = (params.get('q') or '').strip()
    situacao = (params.get('situacao') or 'pendentes').strip()
    situacoes_validas = {value for value, _ in TERMO_ACEITE_SITUACOES}
    if situacao not in situacoes_validas:
        situacao = 'pendentes'

    responsavel = (params.get('responsavel') or '').strip()
    if q:
        filtro_busca = (
            Q(chamado__titulo__icontains=q)
            | Q(chamado__destinatario__matricula__icontains=q)
            | Q(chamado__destinatario__first_name__icontains=q)
            | Q(chamado__destinatario__last_name__icontains=q)
            | Q(chamado__solicitante__matricula__icontains=q)
            | Q(chamado__solicitante__first_name__icontains=q)
            | Q(chamado__solicitante__last_name__icontains=q)
        )
        if q.isdigit():
            filtro_busca |= Q(chamado__pk=int(q))
        qs = qs.filter(filtro_busca).distinct()

    if situacao == 'pendentes':
        qs = qs.filter(status=StatusTermoAceite.PENDENTE)
    elif situacao == 'expirados':
        qs = qs.filter(status=StatusTermoAceite.PENDENTE, expires_at__lt=agora)
    elif situacao == 'sem_envio':
        qs = qs.filter(status=StatusTermoAceite.PENDENTE, enviado_em__isnull=True, expires_at__gte=agora)
    elif situacao == 'enviados':
        qs = qs.filter(status=StatusTermoAceite.PENDENTE, enviado_em__isnull=False, expires_at__gte=agora)
    elif situacao == 'assinados':
        qs = qs.filter(status=StatusTermoAceite.ASSINADO)

    if responsavel == 'meus':
        qs = qs.filter(chamado__responsavel=user)
    elif responsavel.isdigit():
        qs = qs.filter(chamado__responsavel_id=int(responsavel))

    return qs, {
        'q': q,
        'situacao': situacao,
        'responsavel': responsavel,
    }


def _url_painel_termos_com_filtros(params):
    query = params.copy()
    for key in list(query.keys()):
        if key not in {'q', 'situacao', 'responsavel'}:
            query.pop(key, None)
    query.pop('page', None)
    query_string = query.urlencode()
    url = reverse('painel_termos_chamados')
    return f'{url}?{query_string}' if query_string else url


def _filtrar_compliance_termos(params, user, agora=None):
    agora = agora or timezone.now()
    filtros_base = params.copy()
    if not filtros_base.get('situacao'):
        filtros_base['situacao'] = 'todos'

    qs, filtros = _filtrar_termos_aceite(_base_termos_aceite_queryset(), filtros_base, user, agora=agora)
    fechado_de = (params.get('fechado_de') or '').strip()
    fechado_ate = (params.get('fechado_ate') or '').strip()
    fechado_de_data = parse_date(fechado_de) if fechado_de else None
    fechado_ate_data = parse_date(fechado_ate) if fechado_ate else None

    if fechado_de_data:
        qs = qs.filter(chamado__data_fechamento__date__gte=fechado_de_data)
    if fechado_ate_data:
        qs = qs.filter(chamado__data_fechamento__date__lte=fechado_ate_data)

    filtros.update(
        {
            'fechado_de': fechado_de,
            'fechado_ate': fechado_ate,
        }
    )
    return qs, filtros


def _horas_ate_aceite(aceite):
    fechado_em = aceite.chamado.data_fechamento
    if not fechado_em or not aceite.assinado_em:
        return None
    return round((aceite.assinado_em - fechado_em).total_seconds() / 3600, 1)


def _termo_compliance_row(aceite):
    chamado = aceite.chamado
    horas_ate_aceite = _horas_ate_aceite(aceite)
    return {
        'aceite': aceite,
        'chamado': chamado,
        'chamado_id': chamado.pk,
        'titulo': chamado.titulo,
        'colaborador': chamado.destinatario_nome_completo,
        'matricula': chamado.destinatario_matricula,
        'responsavel': chamado.responsavel.nome_completo if chamado.responsavel else '-',
        'fechado_em': timezone.localtime(chamado.data_fechamento).strftime('%d/%m/%Y %H:%M')
        if chamado.data_fechamento
        else '-',
        'status': aceite.status_operacional_label,
        'expires_at': aceite.expires_at_label,
        'enviado_em': aceite.enviado_em_label,
        'envios': aceite.envio_total,
        'assinado_em': aceite.assinado_em_label,
        'horas_ate_aceite': horas_ate_aceite if horas_ate_aceite is not None else '-',
        'hash_curto': aceite.documento_hash_curto,
        'hash': aceite.documento_hash or '-',
        'evidencia_ok': bool(aceite.is_assinado and aceite.documento_hash),
    }


def _resumo_compliance_termos(qs, agora=None):
    agora = agora or timezone.now()
    total = qs.count()
    assinados = qs.filter(status=StatusTermoAceite.ASSINADO).count()
    pendentes = qs.filter(status=StatusTermoAceite.PENDENTE).count()
    expirados = qs.filter(status=StatusTermoAceite.PENDENTE, expires_at__lt=agora).count()
    sem_envio = qs.filter(status=StatusTermoAceite.PENDENTE, enviado_em__isnull=True).count()
    evidencias_invalidas = qs.filter(status=StatusTermoAceite.ASSINADO, documento_hash='').count()
    linhas_assinadas = [
        aceite
        for aceite in qs.filter(status=StatusTermoAceite.ASSINADO, assinado_em__isnull=False)
        if aceite.chamado.data_fechamento
    ]
    tempos = [_horas_ate_aceite(aceite) for aceite in linhas_assinadas]
    tempos = [tempo for tempo in tempos if tempo is not None]
    assinados_no_prazo = sum(1 for aceite in linhas_assinadas if aceite.expires_at and aceite.assinado_em <= aceite.expires_at)

    return {
        'total': total,
        'assinados': assinados,
        'pendentes': pendentes,
        'expirados': expirados,
        'sem_envio': sem_envio,
        'evidencias_invalidas': evidencias_invalidas,
        'assinados_no_prazo': assinados_no_prazo,
        'percentual_assinado': round((assinados / total) * 100, 1) if total else 0,
        'tempo_medio_aceite': round(sum(tempos) / len(tempos), 1) if tempos else 0,
    }


def _horas_entre(inicio, fim):
    if not inicio or not fim:
        return None
    return round((fim - inicio).total_seconds() / 3600, 1)


def _media_horas(valores):
    valores_validos = [valor for valor in valores if valor is not None]
    if not valores_validos:
        return 0
    return round(sum(valores_validos) / len(valores_validos), 1)


def _produtividade_periodo(params, agora=None):
    agora = agora or timezone.now()
    periodo = (params.get('periodo') or '30').strip()
    validos = {value for value, _ in PRODUTIVIDADE_PERIODOS}
    if periodo not in validos:
        periodo = '30'
    if periodo == 'todos':
        return periodo, None
    return periodo, agora - timedelta(days=int(periodo))


def _produtividade_label_usuario(usuario):
    return usuario.nome_completo if usuario else 'Sem responsavel'


def _produtividade_responsavel_bucket(buckets, usuario):
    key = usuario.pk if usuario else 'sem'
    if key not in buckets:
        buckets[key] = {
            'key': key,
            'nome': _produtividade_label_usuario(usuario),
            'abertos': 0,
            'criticos': 0,
            'atrasados': 0,
            'fechados': 0,
            'horas_aberto': [],
            'horas_fechamento': [],
        }
    return buckets[key]


def _eventos_fluxo_por_chamado(chamados):
    chamado_ids = [chamado.pk for chamado in chamados if chamado.pk]
    eventos = {chamado_id: [] for chamado_id in chamado_ids}
    if not chamado_ids:
        return eventos
    for evento in ChamadoFluxoEvento.objects.filter(chamado_id__in=chamado_ids).order_by('chamado_id', '-criado_em'):
        eventos.setdefault(evento.chamado_id, []).append(evento)
    return eventos


def _evento_estado_atual_chamado(chamado, eventos_por_chamado):
    for evento in eventos_por_chamado.get(chamado.pk, []):
        if evento.etapa_nova == chamado.fluxo_etapa and evento.status_novo == chamado.status:
            return evento
    return None


def _inicio_estado_atual_chamado(chamado, eventos_por_chamado):
    evento = _evento_estado_atual_chamado(chamado, eventos_por_chamado)
    if evento:
        return evento.criado_em
    return chamado.updated_at or chamado.created_at


def _sla_etapa_label(estado):
    if estado == SLANivel.ESCALADO:
        return 'Etapa escalada'
    if estado == SLANivel.ALERTA:
        return 'Etapa em alerta'
    return 'Etapa no prazo'


def _produtividade_contexto(params):
    agora = timezone.now()
    periodo, inicio_periodo = _produtividade_periodo(params, agora=agora)
    chamados = Chamado.objects.select_related('solicitante', 'destinatario', 'responsavel')
    abertos = list(chamados.exclude(status=StatusChamado.ENCERRADO).order_by('updated_at', 'created_at'))
    encerrados_qs = chamados.filter(status=StatusChamado.ENCERRADO, data_fechamento__isnull=False)
    if inicio_periodo:
        encerrados_qs = encerrados_qs.filter(data_fechamento__gte=inicio_periodo)
    encerrados = list(encerrados_qs.order_by('-data_fechamento', '-updated_at'))

    tempos_fechamento = [_horas_entre(chamado.created_at, chamado.data_fechamento) for chamado in encerrados]
    fechados_no_sla = sum(
        1 for chamado in encerrados if chamado.data_fechamento and chamado.data_fechamento <= chamado.sla_prazo_em
    )
    abertos_atrasados = [chamado for chamado in abertos if chamado.sla_em_atraso]
    abertos_sem_responsavel = [chamado for chamado in abertos if not chamado.responsavel_id]

    etapas_labels = dict(EtapaFluxoChamado.choices)
    etapas_ordem = [
        EtapaFluxoChamado.SOLICITADO,
        EtapaFluxoChamado.TRIAGEM,
        EtapaFluxoChamado.AGUARDANDO_ESTOQUE,
        EtapaFluxoChamado.AGUARDANDO_APROVACAO,
        EtapaFluxoChamado.APROVADO_PARA_RETIRADA,
        EtapaFluxoChamado.EM_SEPARACAO,
        EtapaFluxoChamado.PRONTO_PARA_ENTREGA,
    ]
    etapa_tones = {
        EtapaFluxoChamado.SOLICITADO: 'blue',
        EtapaFluxoChamado.TRIAGEM: 'sky',
        EtapaFluxoChamado.AGUARDANDO_ESTOQUE: 'amber',
        EtapaFluxoChamado.AGUARDANDO_APROVACAO: 'violet',
        EtapaFluxoChamado.APROVADO_PARA_RETIRADA: 'green',
        EtapaFluxoChamado.EM_SEPARACAO: 'sky',
        EtapaFluxoChamado.PRONTO_PARA_ENTREGA: 'green',
    }
    eventos_abertos = _eventos_fluxo_por_chamado(abertos)
    etapas = []
    for etapa in etapas_ordem:
        itens = [chamado for chamado in abertos if chamado.fluxo_etapa == etapa]
        horas_parado = [_horas_entre(_inicio_estado_atual_chamado(chamado, eventos_abertos), agora) for chamado in itens]
        avaliacoes_etapa = [
            avaliar_sla_etapa_chamado(
                chamado,
                now=agora,
                evento=_evento_estado_atual_chamado(chamado, eventos_abertos),
            )
            for chamado in itens
        ]
        etapas.append(
            {
                'key': etapa,
                'label': etapas_labels.get(etapa, etapa),
                'count': len(itens),
                'tempo_medio_parado': _media_horas(horas_parado),
                'maior_parada': max([valor for valor in horas_parado if valor is not None], default=0),
                'sla_alerta': sum(1 for avaliacao in avaliacoes_etapa if avaliacao['estado'] == SLANivel.ALERTA),
                'sla_escalado': sum(1 for avaliacao in avaliacoes_etapa if avaliacao['estado'] == SLANivel.ESCALADO),
                'tone': etapa_tones.get(etapa, 'blue'),
            }
        )

    gargalos = []
    for chamado in abertos:
        evento_atual = _evento_estado_atual_chamado(chamado, eventos_abertos)
        inicio_estado = evento_atual.criado_em if evento_atual else chamado.updated_at or chamado.created_at
        sla_etapa = avaliar_sla_etapa_chamado(chamado, now=agora, evento=evento_atual)
        gargalos.append(
            {
                'chamado': chamado,
                'tempo_parado': _horas_entre(inicio_estado, agora) or 0,
                'tempo_aberto': _horas_entre(chamado.created_at, agora) or 0,
                'responsavel': _produtividade_label_usuario(chamado.responsavel),
                'sla_label': chamado.sla_status_label,
                'sla_tone': chamado.sla_status_tone,
                'sla_etapa_estado': sla_etapa['estado'],
                'sla_etapa_label': _sla_etapa_label(sla_etapa['estado']),
                'sla_etapa_restante': sla_etapa['minutos_restantes'],
            }
        )
    gargalos.sort(key=lambda item: (-item['tempo_parado'], -item['tempo_aberto']))

    buckets = {}
    for chamado in abertos:
        bucket = _produtividade_responsavel_bucket(buckets, chamado.responsavel)
        bucket['abertos'] += 1
        bucket['criticos'] += 1 if chamado.prioridade == PrioridadeChamado.CRITICA else 0
        bucket['atrasados'] += 1 if chamado.sla_em_atraso else 0
        bucket['horas_aberto'].append(_horas_entre(chamado.created_at, agora))
    for chamado in encerrados:
        bucket = _produtividade_responsavel_bucket(buckets, chamado.responsavel)
        bucket['fechados'] += 1
        bucket['horas_fechamento'].append(_horas_entre(chamado.created_at, chamado.data_fechamento))

    responsaveis = []
    for bucket in buckets.values():
        responsaveis.append(
            {
                **bucket,
                'tempo_medio_aberto': _media_horas(bucket['horas_aberto']),
                'tempo_medio_fechamento': _media_horas(bucket['horas_fechamento']),
            }
        )
    responsaveis.sort(key=lambda item: (-item['abertos'], -item['atrasados'], item['nome']))
    produtividade = sorted(responsaveis, key=lambda item: (-item['fechados'], item['tempo_medio_fechamento'], item['nome']))

    return {
        'periodo': periodo,
        'periodos_produtividade': PRODUTIVIDADE_PERIODOS,
        'inicio_periodo': inicio_periodo,
        'resumo_produtividade': {
            'abertos': len(abertos),
            'fechados_periodo': len(encerrados),
            'tempo_medio_fechamento': _media_horas(tempos_fechamento),
            'percentual_sla': round((fechados_no_sla / len(encerrados)) * 100, 1) if encerrados else 0,
            'atrasados': len(abertos_atrasados),
            'sem_responsavel': len(abertos_sem_responsavel),
            'etapas_alerta': sum(item['sla_alerta'] for item in etapas),
            'etapas_escaladas': sum(item['sla_escalado'] for item in etapas),
        },
        'produtividade_etapas': etapas,
        'produtividade_gargalos': gargalos[:10],
        'produtividade_responsaveis': responsaveis[:10],
        'produtividade_ranking': produtividade[:10],
        'produtividade_fechados_recentes': encerrados[:8],
    }


def _contexto_detalhe_chamado(request, chamado, entrega_form=None):
    acoes_fluxo = _fluxo_acoes_chamado(request.user, chamado)
    fluxo_etapas = chamado.fluxo_etapas
    aceite_digital = obter_ou_criar_aceite_termo(chamado)
    assinatura_pendente = bool(
        aceite_digital and not aceite_digital.is_assinado and chamado.status == StatusChamado.ENCERRADO
    )
    etapa_atual_indice = next((index for index, etapa in enumerate(fluxo_etapas) if etapa['active']), 0)
    total_etapas = len(fluxo_etapas)
    progresso = 100 if total_etapas <= 1 else round(((etapa_atual_indice + 1) / total_etapas) * 100)
    proxima_etapa = fluxo_etapas[etapa_atual_indice + 1] if etapa_atual_indice + 1 < total_etapas else None
    contexto = {
        'chamado': chamado,
        'pode_gerenciar_chamado': _pode_gerenciar_chamado(request.user),
        'pode_editar_chamado': _pode_editar_chamado(request.user, chamado),
        'pode_excluir_chamado': _pode_excluir_chamado(request.user),
        'acoes_fluxo': acoes_fluxo,
        'fluxo_etapas': fluxo_etapas,
        'fluxo_etapa_indice': etapa_atual_indice + 1,
        'fluxo_etapas_total': total_etapas,
        'fluxo_progresso_percent': progresso,
        'fluxo_proxima_etapa': proxima_etapa,
        'itens_solicitados': chamado.itens_solicitados.select_related('equipamento_entregue', 'entregue_por').all(),
        'aceite_digital': aceite_digital,
        'assinatura_pendente': assinatura_pendente,
        'playbook_chamado': gerar_playbook_chamado(chamado, aceite_digital=aceite_digital),
        'fluxo_eventos': chamado.fluxo_eventos.select_related('usuario').order_by('-criado_em')[:8],
    }
    if contexto['pode_gerenciar_chamado'] and chamado.status != StatusChamado.ENCERRADO and chamado.fluxo_etapa in {
        EtapaFluxoChamado.APROVADO_PARA_RETIRADA,
        EtapaFluxoChamado.EM_SEPARACAO,
        EtapaFluxoChamado.PRONTO_PARA_ENTREGA,
    }:
        if entrega_form is None:
            entrega_form = EntregaEquipamentoChamadoForm(chamado=chamado)
        contexto['entrega_form'] = entrega_form
    return contexto


@login_required
def lista_chamados(request):
    qs = Chamado.objects.select_related('equipamento', 'solicitante', 'destinatario', 'responsavel', 'aprovado_por').prefetch_related('itens_solicitados').order_by('-created_at')

    is_operacional = _pode_gerenciar_chamado(request.user)
    if not is_operacional:
        qs = qs.filter(Q(solicitante=request.user) | Q(destinatario=request.user))

    q = request.GET.get('q', '')
    status = request.GET.get('status', '')
    sla = request.GET.get('sla', '').strip()
    if q:
        qs = qs.filter(
            Q(titulo__icontains=q)
            | Q(descricao__icontains=q)
            | Q(tipo_equipamento_solicitado__icontains=q)
            | Q(servico_realizado__icontains=q)
            | Q(itens_solicitados__tipo_equipamento__icontains=q)
            | Q(itens_solicitados__tipo_outro__icontains=q)
            | Q(equipamento__id_patrimonio__icontains=q)
            | Q(solicitante__matricula__icontains=q)
            | Q(solicitante__first_name__icontains=q)
            | Q(solicitante__last_name__icontains=q)
            | Q(destinatario__matricula__icontains=q)
            | Q(destinatario__first_name__icontains=q)
            | Q(destinatario__last_name__icontains=q)
        ).distinct()
    if status:
        qs = qs.filter(status=status)
    if sla:
        qs = _sla_queryset_filter(qs, sla)

    status_breakdown = {
        item['status']: item['total']
        for item in qs.values('status').annotate(total=Count('id')).order_by('status')
    }
    sla_breakdown = {
        item['sla_nivel']: item['total']
        for item in qs.values('sla_nivel').annotate(total=Count('id')).order_by('sla_nivel')
    }

    resumo_chamados = qs.aggregate(
        total=Count('id'),
        em_andamento=Count('id', filter=Q(status__in=STATUS_CHAMADO_EM_FLUXO)),
        aguardando_aprovacao=Count('id', filter=Q(fluxo_etapa=EtapaFluxoChamado.AGUARDANDO_APROVACAO)),
        pronto_para_entrega=Count('id', filter=Q(fluxo_etapa=EtapaFluxoChamado.PRONTO_PARA_ENTREGA)),
        encerrados=Count('id', filter=Q(status=StatusChamado.ENCERRADO)),
        sla_alerta=Count('id', filter=Q(sla_nivel=SLANivel.ALERTA)),
        sla_escalado=Count('id', filter=Q(sla_nivel=SLANivel.ESCALADO)),
    )
    sla_em_risco = int(resumo_chamados['sla_alerta'] or 0) + int(resumo_chamados['sla_escalado'] or 0)

    def _status_url(status_value):
        params = request.GET.copy()
        params['status'] = status_value
        params.pop('page', None)
        return f'?{params.urlencode()}'

    def _sla_url(sla_value):
        params = request.GET.copy()
        params['sla'] = sla_value
        params.pop('page', None)
        return f'?{params.urlencode()}'

    status_cards = [
        {
            'key': StatusChamado.FILA,
            'label': 'Fila',
            'description': 'Pedidos aguardando triagem ou entrada no fluxo.',
            'count': status_breakdown.get(StatusChamado.FILA, 0),
            'url': _status_url(StatusChamado.FILA),
            'icon': 'fa-inbox',
            'tone': 'blue',
        },
        {
            'key': StatusChamado.EM_ATENDIMENTO,
            'label': 'Em atendimento',
            'description': 'Chamados já assumidos e em execução.',
            'count': status_breakdown.get(StatusChamado.EM_ATENDIMENTO, 0),
            'url': _status_url(StatusChamado.EM_ATENDIMENTO),
            'icon': 'fa-headset',
            'tone': 'teal',
        },
        {
            'key': StatusChamado.AGUARDANDO_ATENDIMENTO,
            'label': 'Aguardando atendimento',
            'description': 'Chamados pausados e esperando a próxima ação.',
            'count': status_breakdown.get(StatusChamado.AGUARDANDO_ATENDIMENTO, 0),
            'url': _status_url(StatusChamado.AGUARDANDO_ATENDIMENTO),
            'icon': 'fa-hourglass-half',
            'tone': 'amber',
        },
        {
            'key': StatusChamado.ENCERRADO,
            'label': 'Encerrados',
            'description': 'Histórico finalizado e pronto para consulta.',
            'count': status_breakdown.get(StatusChamado.ENCERRADO, 0),
            'url': _status_url(StatusChamado.ENCERRADO),
            'icon': 'fa-circle-check',
            'tone': 'violet',
        },
        {
            'key': 'sla',
            'label': 'SLA em risco',
            'description': 'Chamados que já entraram na janela de atenção ou venceram.',
            'count': sla_em_risco,
            'url': _sla_url('alerta'),
            'icon': 'fa-stopwatch',
            'tone': 'red',
        },
    ]

    params = request.GET.copy()
    params.pop('page', None)
    query_string = params.urlencode()

    paginator = Paginator(qs, 20)
    page = paginator.get_page(request.GET.get('page'))
    page_chamados = [
        {
            'chamado': chamado,
            'acoes': _fluxo_acoes_chamado(request.user, chamado),
        }
        for chamado in page.object_list
    ]
    return render(
        request,
        'chamados/lista.html',
        {
            'page_obj': page,
            'page_chamados': page_chamados,
            'q': q,
            'status': status,
            'status_choices': StatusChamado.choices,
            'query_string': query_string,
            'is_operacional': is_operacional,
            'resumo_chamados': {key: int(value or 0) for key, value in resumo_chamados.items()},
            'status_cards': status_cards,
            'sla': sla,
            'sla_breakdown': sla_breakdown,
        },
    )


@login_required
def painel_termos_chamados(request):
    if not _pode_gerenciar_chamado(request.user):
        messages.error(request, 'Acesso negado.')
        return redirect('chamados')

    agora = timezone.now()
    base_qs = _base_termos_aceite_queryset()

    if request.method == 'POST':
        acao = (request.POST.get('acao') or 'selecionados').strip()
        if acao == 'filtrados':
            cobranca_qs, _ = _filtrar_termos_aceite(base_qs, request.POST, request.user, agora=agora)
        else:
            selecionados = request.POST.getlist('aceites')
            cobranca_qs = base_qs.filter(pk__in=selecionados)

        if not cobranca_qs.exists():
            messages.warning(request, 'Nenhum termo elegivel foi selecionado para cobranca.')
            return redirect(_url_painel_termos_com_filtros(request.POST))

        resumo_cobranca = cobrar_assinaturas_termos(
            aceites=cobranca_qs,
            request=request,
            enviado_por=request.user,
        )
        if resumo_cobranca['enviados']:
            messages.success(
                request,
                (
                    f'Cobranca enviada para {resumo_cobranca["enviados"]} termo(s). '
                    f'{resumo_cobranca["renovados"]} link(s) expirado(s) foram renovados.'
                ),
            )
        if resumo_cobranca['sem_email']:
            messages.warning(
                request,
                f'{resumo_cobranca["sem_email"]} colaborador(es) receberam apenas notificacao interna por falta de e-mail.',
            )
        if resumo_cobranca['ignorados'] or resumo_cobranca['falhas']:
            messages.warning(
                request,
                (
                    f'{resumo_cobranca["ignorados"]} termo(s) assinado(s) foram ignorados; '
                    f'{resumo_cobranca["falhas"]} falharam.'
                ),
            )
        if not resumo_cobranca['enviados']:
            messages.warning(request, 'Nenhum novo lembrete foi enviado.')

        return redirect(_url_painel_termos_com_filtros(request.POST))

    qs, filtros = _filtrar_termos_aceite(base_qs, request.GET, request.user, agora=agora)
    qs = qs.order_by('status', 'expires_at', '-updated_at')

    resumo_termos = base_qs.aggregate(
        total=Count('id'),
        pendentes=Count('id', filter=Q(status=StatusTermoAceite.PENDENTE)),
        expirados=Count('id', filter=Q(status=StatusTermoAceite.PENDENTE, expires_at__lt=agora)),
        sem_envio=Count(
            'id',
            filter=Q(status=StatusTermoAceite.PENDENTE, enviado_em__isnull=True, expires_at__gte=agora),
        ),
        enviados=Count(
            'id',
            filter=Q(status=StatusTermoAceite.PENDENTE, enviado_em__isnull=False, expires_at__gte=agora),
        ),
        assinados=Count('id', filter=Q(status=StatusTermoAceite.ASSINADO)),
    )

    responsaveis = Usuario.objects.filter(
        Q(is_superuser=True) | Q(nivel_acesso__in=[NivelAcesso.ADMIN, NivelAcesso.ANALISTA, NivelAcesso.TECNICO])
    ).filter(ativo=True).order_by('first_name', 'last_name', 'matricula')

    params = request.GET.copy()
    params.pop('page', None)
    query_string = params.urlencode()
    paginator = Paginator(qs, 20)
    page = paginator.get_page(request.GET.get('page'))

    return render(
        request,
        'chamados/painel_termos.html',
        {
            'page_obj': page,
            'termos': page.object_list,
            'resumo_termos': {key: int(value or 0) for key, value in resumo_termos.items()},
            'situacoes_termo': TERMO_ACEITE_SITUACOES,
            'responsaveis': responsaveis,
            'query_string': query_string,
            **filtros,
        },
    )


@login_required
def compliance_termos_chamados(request):
    if not _pode_gerenciar_chamado(request.user):
        messages.error(request, 'Acesso negado.')
        return redirect('chamados')

    qs, filtros = _filtrar_compliance_termos(request.GET, request.user)
    qs = qs.order_by('-chamado__data_fechamento', 'status', 'expires_at', '-updated_at')
    resumo = _resumo_compliance_termos(qs)

    params = request.GET.copy()
    params.pop('page', None)
    query_string = params.urlencode()
    paginator = Paginator(qs, 25)
    page = paginator.get_page(request.GET.get('page'))
    linhas = [_termo_compliance_row(aceite) for aceite in page.object_list]
    responsaveis = Usuario.objects.filter(
        Q(is_superuser=True) | Q(nivel_acesso__in=[NivelAcesso.ADMIN, NivelAcesso.ANALISTA, NivelAcesso.TECNICO])
    ).filter(ativo=True).order_by('first_name', 'last_name', 'matricula')

    export_query = f'?{query_string}' if query_string else ''
    return render(
        request,
        'chamados/compliance_termos.html',
        {
            'linhas': linhas,
            'page_obj': page,
            'resumo_compliance': resumo,
            'situacoes_termo': TERMO_ACEITE_SITUACOES,
            'responsaveis': responsaveis,
            'query_string': query_string,
            'export_query': export_query,
            **filtros,
        },
    )


@login_required
def compliance_termos_csv(request):
    if not _pode_gerenciar_chamado(request.user):
        messages.error(request, 'Acesso negado.')
        return redirect('chamados')

    qs, _ = _filtrar_compliance_termos(request.GET, request.user)
    qs = qs.order_by('-chamado__data_fechamento', 'status', 'expires_at', '-updated_at')
    response = HttpResponse(content_type='text/csv; charset=utf-8')
    response['Content-Disposition'] = 'attachment; filename="compliance-termos-digitais.csv"'
    writer = csv.writer(response)
    writer.writerow(
        [
            'Chamado',
            'Titulo',
            'Colaborador',
            'Matricula',
            'Responsavel',
            'Fechado em',
            'Status termo',
            'Expira em',
            'Ultimo envio',
            'Envios',
            'Assinado em',
            'Horas ate aceite',
            'Evidencia OK',
            'Hash SHA-256',
        ]
    )
    for aceite in qs.iterator():
        row = _termo_compliance_row(aceite)
        writer.writerow(
            [
                row['chamado_id'],
                row['titulo'],
                row['colaborador'],
                row['matricula'],
                row['responsavel'],
                row['fechado_em'],
                row['status'],
                row['expires_at'],
                row['enviado_em'],
                row['envios'],
                row['assinado_em'],
                row['horas_ate_aceite'],
                'Sim' if row['evidencia_ok'] else 'Nao',
                row['hash'],
            ]
        )
    return response


@login_required
def compliance_termos_pdf(request):
    if not _pode_gerenciar_chamado(request.user):
        messages.error(request, 'Acesso negado.')
        return redirect('chamados')

    qs, _ = _filtrar_compliance_termos(request.GET, request.user)
    qs = qs.order_by('-chamado__data_fechamento', 'status', 'expires_at', '-updated_at')
    resumo = _resumo_compliance_termos(qs)
    linhas = [_termo_compliance_row(aceite) for aceite in qs[:200]]

    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=landscape(A4),
        rightMargin=10 * mm,
        leftMargin=10 * mm,
        topMargin=10 * mm,
        bottomMargin=10 * mm,
        title='Compliance de termos digitais',
    )
    styles = getSampleStyleSheet()
    styles.add(
        ParagraphStyle(
            name='ComplianceTitle',
            parent=styles['Title'],
            fontSize=16,
            leading=20,
            textColor=colors.HexColor('#102033'),
        )
    )
    styles.add(
        ParagraphStyle(
            name='ComplianceSmall',
            parent=styles['BodyText'],
            fontSize=7,
            leading=9,
        )
    )
    story = [
        Paragraph('Compliance de termos digitais', styles['ComplianceTitle']),
        Paragraph(
            (
                f'Total: {resumo["total"]} | Assinados: {resumo["assinados"]} '
                f'({resumo["percentual_assinado"]}%) | Pendentes: {resumo["pendentes"]} | '
                f'Expirados: {resumo["expirados"]} | Tempo medio ate aceite: {resumo["tempo_medio_aceite"]}h'
            ),
            styles['ComplianceSmall'],
        ),
        Spacer(1, 5 * mm),
    ]

    data = [
        [
            Paragraph('Chamado', styles['ComplianceSmall']),
            Paragraph('Colaborador', styles['ComplianceSmall']),
            Paragraph('Status', styles['ComplianceSmall']),
            Paragraph('Fechado em', styles['ComplianceSmall']),
            Paragraph('Assinado em', styles['ComplianceSmall']),
            Paragraph('Horas', styles['ComplianceSmall']),
            Paragraph('Hash', styles['ComplianceSmall']),
        ]
    ]
    for row in linhas:
        data.append(
            [
                Paragraph(f'#{row["chamado_id"]}<br/>{escape(row["titulo"])}', styles['ComplianceSmall']),
                Paragraph(f'{escape(row["colaborador"])}<br/>{escape(row["matricula"])}', styles['ComplianceSmall']),
                Paragraph(row['status'], styles['ComplianceSmall']),
                Paragraph(row['fechado_em'], styles['ComplianceSmall']),
                Paragraph(row['assinado_em'], styles['ComplianceSmall']),
                Paragraph(str(row['horas_ate_aceite']), styles['ComplianceSmall']),
                Paragraph(row['hash_curto'], styles['ComplianceSmall']),
            ]
        )
    if not linhas:
        data.append([Paragraph('Nenhum termo encontrado.', styles['ComplianceSmall']), '', '', '', '', '', ''])

    table = Table(data, colWidths=[48 * mm, 52 * mm, 28 * mm, 34 * mm, 34 * mm, 18 * mm, 32 * mm], repeatRows=1)
    table.setStyle(
        TableStyle(
            [
                ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#102033')),
                ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
                ('GRID', (0, 0), (-1, -1), 0.25, colors.HexColor('#ccd5df')),
                ('VALIGN', (0, 0), (-1, -1), 'TOP'),
                ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#f5f8fb')]),
            ]
        )
    )
    story.append(table)
    if qs.count() > len(linhas):
        story.extend(
            [
                Spacer(1, 4 * mm),
                Paragraph('PDF limitado aos primeiros 200 registros da busca. Use CSV para a base completa.', styles['ComplianceSmall']),
            ]
        )

    doc.build(story)
    pdf_bytes = buffer.getvalue()
    buffer.close()
    response = HttpResponse(pdf_bytes, content_type='application/pdf')
    response['Content-Disposition'] = 'inline; filename="compliance-termos-digitais.pdf"'
    return response


@login_required
def produtividade_operacional(request):
    if not _pode_gerenciar_chamado(request.user):
        messages.error(request, 'Acesso negado.')
        return redirect('chamados')

    return render(request, 'chamados/produtividade.html', _produtividade_contexto(request.GET))


@login_required
def criar_chamado(request):
    template_key_raw = (request.GET.get('template') or request.POST.get('template') or '').strip()
    template_info = get_request_template(template_key_raw) if template_key_raw else None
    template_key = template_info['key'] if template_info else ''

    if request.method == 'GET' and not template_key:
        return render(
            request,
            'chamados/solicitacoes.html',
            {
                'cards': REQUEST_TEMPLATE_CARDS,
                'title': 'Nova solicitacao',
            },
        )

    form = ChamadoCreateForm(request.POST or None, usuario_padrao=request.user, template=template_key)
    page_title = 'Novo chamado'
    if template_info:
        page_title = f"{template_info['title']} {template_info['highlight']}".strip()

    if form.is_valid():
        try:
            with transaction.atomic():
                chamado = form.save(commit=False)
                chamado.solicitante = request.user
                chamado.destinatario = form.cleaned_data['destinatario']
                preparar_evento_fluxo_chamado(
                    chamado,
                    usuario=request.user,
                    observacao='Chamado criado.',
                )
                chamado.save()
                sincronizar_itens_solicitados(
                    chamado=chamado,
                    tipos_solicitados=form.cleaned_data.get('equipamentos_solicitados', []),
                    texto_itens=form.cleaned_data.get('outros_itens_solicitados', ''),
                )
        except ValidationError as exc:
            form.add_error(
                'outros_itens_solicitados',
                exc.messages[0] if exc.messages else 'Não foi possível salvar os itens solicitados.',
            )
        else:
            messages.success(request, 'Chamado criado com sucesso.')
            return redirect('detalhe_chamado', pk=chamado.pk)
    return render(
        request,
        'chamados/form.html',
        {
            'form': form,
            'title': page_title,
            'template_info': template_info,
            'template_key': template_key,
        },
    )


@login_required
def detalhe_chamado(request, pk):
    chamado = get_object_or_404(
        Chamado.objects.select_related('equipamento', 'solicitante', 'destinatario', 'responsavel', 'aprovado_por').prefetch_related(
            'itens_solicitados__equipamento_entregue',
            'itens_solicitados__entregue_por',
        ),
        pk=pk,
    )
    if not _pode_visualizar_chamado(request.user, chamado):
        messages.error(request, 'Acesso negado.')
        return redirect('dashboard')
    return render(request, 'chamados/detalhe.html', _contexto_detalhe_chamado(request, chamado))


@login_required
@require_POST
def excluir_chamado(request, pk):
    chamado = get_object_or_404(
        Chamado.objects.select_related('equipamento', 'solicitante', 'destinatario', 'responsavel', 'aprovado_por').prefetch_related(
            'itens_solicitados',
        ),
        pk=pk,
    )
    if not _pode_excluir_chamado(request.user):
        messages.error(request, 'Somente administradores podem excluir chamados.')
        return redirect('detalhe_chamado', pk=chamado.pk)

    try:
        total_reservas = excluir_chamado_administrativo(chamado=chamado, usuario=request.user)
    except ValidationError as exc:
        messages.error(request, exc.messages[0] if exc.messages else 'Nao foi possivel excluir o chamado.')
        return redirect('detalhe_chamado', pk=chamado.pk)

    messages.success(
        request,
        f'Chamado #{pk} excluido. {total_reservas} reserva(s) ativa(s) foram devolvidas ao estoque.',
    )
    return redirect('chamados')


@login_required
@require_POST
def enviar_assinatura_termo(request, pk):
    chamado = get_object_or_404(Chamado.objects.select_related('solicitante', 'destinatario', 'responsavel'), pk=pk)
    if not _pode_gerenciar_chamado(request.user):
        messages.error(request, 'Acesso negado.')
        return redirect('detalhe_chamado', pk=chamado.pk)

    aceite_digital = obter_ou_criar_aceite_termo(chamado)
    try:
        envio = enviar_link_assinatura_termo(
            aceite=aceite_digital,
            request=request,
            enviado_por=request.user,
        )
    except ValidationError as exc:
        messages.warning(request, exc.messages[0] if exc.messages else 'Nao foi possivel enviar o link.')
    else:
        if envio['email_enviado']:
            messages.success(
                request,
                f'Link de assinatura enviado por notificacao interna e e-mail para {envio["email_destino"]}.',
            )
        else:
            messages.warning(
                request,
                'Link de assinatura enviado por notificacao interna. Cadastre um e-mail do colaborador para envio externo.',
            )

    return redirect('detalhe_chamado', pk=chamado.pk)


@login_required
@require_POST
def renovar_assinatura_termo(request, pk):
    chamado = get_object_or_404(Chamado.objects.select_related('solicitante', 'destinatario', 'responsavel'), pk=pk)
    if not _pode_gerenciar_chamado(request.user):
        messages.error(request, 'Acesso negado.')
        return redirect('detalhe_chamado', pk=chamado.pk)

    aceite_digital = obter_ou_criar_aceite_termo(chamado)
    try:
        aceite_digital = renovar_link_assinatura_termo(aceite=aceite_digital, usuario=request.user)
    except ValidationError as exc:
        messages.warning(request, exc.messages[0] if exc.messages else 'Nao foi possivel renovar o link.')
    else:
        messages.success(
            request,
            f'Novo link de assinatura gerado com validade ate {aceite_digital.expires_at_label}.',
        )

    return redirect('detalhe_chamado', pk=chamado.pk)


@login_required
def editar_chamado(request, pk):
    chamado = get_object_or_404(Chamado, pk=pk)
    if not _pode_editar_chamado(request.user, chamado):
        messages.error(request, 'Acesso negado.')
        return redirect('dashboard')
    status_anterior = chamado.status

    form = ChamadoUpdateForm(
        request.POST or None,
        instance=chamado,
        usuario_padrao=chamado.destinatario or chamado.solicitante,
    )
    if form.is_valid():
        try:
            with transaction.atomic():
                chamado = form.save(commit=False)
                chamado.destinatario = form.cleaned_data['destinatario']
                if chamado.status != status_anterior:
                    if chamado.status == StatusChamado.FILA:
                        chamado.fluxo_etapa = EtapaFluxoChamado.SOLICITADO
                    elif chamado.status == StatusChamado.EM_ATENDIMENTO:
                        chamado.fluxo_etapa = EtapaFluxoChamado.TRIAGEM
                    elif chamado.status == StatusChamado.AGUARDANDO_ATENDIMENTO:
                        chamado.fluxo_etapa = EtapaFluxoChamado.AGUARDANDO_APROVACAO
                    elif chamado.status == StatusChamado.ENCERRADO:
                        chamado.fluxo_etapa = EtapaFluxoChamado.ENCERRADO
                    elif status_anterior == StatusChamado.ENCERRADO:
                        chamado.sla_nivel = SLANivel.NORMAL
                        chamado.sla_alertado_em = None
                        chamado.sla_escalado_em = None
                preparar_evento_fluxo_chamado(
                    chamado,
                    usuario=request.user,
                    observacao='Chamado editado.',
                )
                chamado.save()
                sincronizar_itens_solicitados(
                    chamado=chamado,
                    tipos_solicitados=form.cleaned_data.get('equipamentos_solicitados', []),
                    texto_itens=form.cleaned_data.get('outros_itens_solicitados', ''),
                )
        except ValidationError as exc:
            form.add_error(
                'outros_itens_solicitados',
                exc.messages[0] if exc.messages else 'Não foi possível salvar os itens solicitados.',
            )
        else:
            messages.success(request, 'Chamado atualizado.')
        return redirect('detalhe_chamado', pk=chamado.pk)
    return render(request, 'chamados/form.html', {'form': form, 'title': 'Editar chamado', 'obj': chamado})


@login_required
@require_POST
def fluxo_chamado_action(request, pk):
    chamado = get_object_or_404(
        Chamado.objects.select_related('solicitante', 'destinatario', 'responsavel'),
        pk=pk,
    )
    if not _pode_visualizar_chamado(request.user, chamado):
        messages.error(request, 'Acesso negado.')
        return redirect('dashboard')

    acao = (request.POST.get('acao') or '').strip()
    link = reverse('detalhe_chamado', kwargs={'pk': chamado.pk})
    destinatarios = [usuario for usuario in [chamado.solicitante, chamado.destinatario] if usuario]

    def _salvar_sucesso(
        mensagem,
        titulo_notificacao=None,
        mensagem_notificacao=None,
        *,
        notificar_destinatarios=True,
        notificar_time=False,
    ):
        preparar_evento_fluxo_chamado(chamado, usuario=request.user, observacao=mensagem)
        chamado.save()
        if titulo_notificacao and notificar_destinatarios:
            notificar_usuarios(destinatarios, titulo_notificacao, mensagem_notificacao or mensagem, link=link)
        if titulo_notificacao and notificar_time:
            notificar_time_operacional(titulo_notificacao, mensagem_notificacao or mensagem, link=link)
        messages.success(request, mensagem)
        return redirect('detalhe_chamado', pk=chamado.pk)

    if acao == 'assumir':
        if not _pode_gerenciar_chamado(request.user):
            messages.error(request, 'Acesso negado.')
            return redirect('detalhe_chamado', pk=chamado.pk)
        chamado.responsavel = request.user
        chamado.status = StatusChamado.EM_ATENDIMENTO
        chamado.marcar_fluxo(EtapaFluxoChamado.TRIAGEM)
        return _salvar_sucesso(
            'Chamado assumido e enviado para triagem.',
            'Chamado assumido',
            f'{request.user.nome_completo} assumiu o chamado #{chamado.pk} para iniciar a triagem.',
        )

    if acao == 'sem_estoque':
        if not (request.user.is_admin or request.user.is_analista):
            messages.error(request, 'Acesso negado.')
            return redirect('detalhe_chamado', pk=chamado.pk)
        chamado.responsavel = request.user
        chamado.status = StatusChamado.AGUARDANDO_ATENDIMENTO
        chamado.marcar_fluxo(EtapaFluxoChamado.AGUARDANDO_ESTOQUE)
        return _salvar_sucesso(
            'Chamado marcado como aguardando estoque.',
            'Chamado sem estoque',
            f'O chamado #{chamado.pk} ficou aguardando estoque após conferência do time.',
        )

    if acao == 'enviar_aprovacao':
        if not (request.user.is_admin or request.user.is_analista):
            messages.error(request, 'Acesso negado.')
            return redirect('detalhe_chamado', pk=chamado.pk)
        chamado.responsavel = request.user
        chamado.status = StatusChamado.AGUARDANDO_ATENDIMENTO
        chamado.marcar_fluxo(EtapaFluxoChamado.AGUARDANDO_APROVACAO)
        return _salvar_sucesso(
            'A solicitação foi enviada para aprovação do colaborador.',
            'Chamado aguardando aprovação',
            f'O chamado #{chamado.pk} aguarda a aprovação de {chamado.usuario_destinatario.nome_completo}.',
        )

    if acao == 'aprovar_retirada':
        if not _pode_aprovar_retirada(request.user, chamado):
            messages.error(request, 'Acesso negado.')
            return redirect('detalhe_chamado', pk=chamado.pk)
        chamado.marcar_fluxo(
            EtapaFluxoChamado.APROVADO_PARA_RETIRADA,
            aprovado_por=request.user,
        )
        chamado.status = StatusChamado.EM_ATENDIMENTO
        return _salvar_sucesso(
            'Retirada aprovada com sucesso.',
            'Retirada aprovada',
            f'{request.user.nome_completo} aprovou a retirada do chamado #{chamado.pk}.',
            notificar_time=True,
        )

    if acao == 'separar':
        if not (request.user.is_admin or request.user.is_tecnico):
            messages.error(request, 'Acesso negado.')
            return redirect('detalhe_chamado', pk=chamado.pk)
        chamado.responsavel = request.user
        chamado.status = StatusChamado.EM_ATENDIMENTO
        chamado.marcar_fluxo(EtapaFluxoChamado.EM_SEPARACAO)
        return _salvar_sucesso(
            'Chamado em separação pelo técnico.',
            'Chamado em separação',
            f'O chamado #{chamado.pk} entrou em separação para entrega dos equipamentos.',
        )

    if acao == 'pronto':
        if not (request.user.is_admin or request.user.is_tecnico):
            messages.error(request, 'Acesso negado.')
            return redirect('detalhe_chamado', pk=chamado.pk)
        chamado.responsavel = request.user
        chamado.status = StatusChamado.EM_ATENDIMENTO
        chamado.marcar_fluxo(EtapaFluxoChamado.PRONTO_PARA_ENTREGA)
        return _salvar_sucesso(
            'Chamado pronto para entrega.',
            'Chamado pronto para entrega',
            f'O chamado #{chamado.pk} está pronto para registrar a entrega final.',
            notificar_time=True,
        )

    messages.error(request, 'Ação inválida.')
    return redirect('detalhe_chamado', pk=chamado.pk)


@login_required
def termo_chamado(request, pk):
    chamado = get_object_or_404(
        Chamado.objects.select_related('equipamento', 'solicitante', 'destinatario', 'responsavel', 'aprovado_por', 'solicitante__gestor').prefetch_related(
            'itens_solicitados__equipamento_entregue',
            'itens_solicitados__entregue_por',
        ),
        pk=pk,
    )
    if not _pode_visualizar_chamado(request.user, chamado):
        messages.error(request, 'Acesso negado.')
        return redirect('dashboard')

    entregas = chamado.movimentacoes.select_related('equipamento', 'realizado_por').filter(tipo='saida').order_by('created_at')
    recolhimentos = chamado.movimentacoes.select_related('equipamento', 'realizado_por').filter(
        tipo__in=['devolucao', 'troca']
    ).order_by('created_at')
    aceite_digital = obter_ou_criar_aceite_termo(chamado)

    return render(
        request,
        'chamados/termo.html',
        {
            'chamado': chamado,
            'itens_solicitados': chamado.itens_solicitados.all(),
            'entregas': entregas,
            'recolhimentos': recolhimentos,
            'aceite_digital': aceite_digital,
        },
    )


def assinar_termo_chamado(request, token):
    aceite_digital = get_object_or_404(
        TermoAceiteDigital.objects.select_related(
            'chamado__equipamento',
            'chamado__solicitante',
            'chamado__destinatario',
            'chamado__responsavel',
            'chamado__aprovado_por',
        ).prefetch_related(
            'chamado__itens_solicitados__equipamento_entregue',
            'chamado__itens_solicitados__entregue_por',
        ),
        token=token,
    )
    chamado = aceite_digital.chamado
    form = AssinaturaTermoForm(request.POST or None) if aceite_digital.pode_assinar else AssinaturaTermoForm()

    if request.method == 'POST' and aceite_digital.is_assinado:
        messages.info(request, 'Este termo ja foi assinado.')
        return redirect('assinar_termo_chamado', token=aceite_digital.token)

    if request.method == 'POST' and aceite_digital.is_expirado:
        messages.warning(request, 'Este link de assinatura expirou. Solicite um novo link ao time de Tecnologia.')
        return redirect('assinar_termo_chamado', token=aceite_digital.token)

    if request.method == 'POST' and aceite_digital.pode_assinar and form.is_valid():
        try:
            aceite_digital = registrar_assinatura_termo(
                aceite=aceite_digital,
                assinatura_data_url=form.cleaned_data['assinatura_data_url'],
                request=request,
                usuario=request.user,
            )
        except ValidationError as exc:
            form.add_error(None, exc.messages[0] if exc.messages else 'Nao foi possivel registrar a assinatura.')
        else:
            notificar_usuarios(
                [chamado.solicitante, chamado.destinatario, chamado.responsavel],
                f'Termo assinado: chamado #{chamado.pk}',
                f'{aceite_digital.nome_assinante} assinou o termo de responsabilidade do chamado #{chamado.pk}.',
                link=reverse('termo_chamado', kwargs={'pk': chamado.pk}),
            )
            return redirect('assinar_termo_chamado', token=aceite_digital.token)

    itens_solicitados = chamado.itens_solicitados.select_related('equipamento_entregue', 'entregue_por').order_by('id')
    return render(
        request,
        'chamados/assinatura.html',
        {
            'chamado': chamado,
            'aceite_digital': aceite_digital,
            'form': form,
            'itens_solicitados': itens_solicitados,
        },
    )


def _pdf_paragrafo(texto, estilo):
    return Paragraph(escape(str(texto or '-')).replace('\n', '<br/>'), estilo)


def _pdf_tabela(headers, rows, widths, styles):
    data = [[_pdf_paragrafo(header, styles['PdfTableHeader']) for header in headers]]
    data.extend([_pdf_paragrafo(value, styles['PdfTableCell']) for value in row] for row in rows)

    tabela = Table(data, colWidths=widths, repeatRows=1)
    tabela.setStyle(
        TableStyle(
            [
                ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#134a8b')),
                ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
                ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                ('FONTSIZE', (0, 0), (-1, -1), 9),
                ('LEADING', (0, 0), (-1, -1), 11),
                ('GRID', (0, 0), (-1, -1), 0.35, colors.HexColor('#d0d7e2')),
                ('BACKGROUND', (0, 1), (-1, -1), colors.whitesmoke),
                ('VALIGN', (0, 0), (-1, -1), 'TOP'),
                ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.whitesmoke, colors.HexColor('#edf3fb')]),
                ('LEFTPADDING', (0, 0), (-1, -1), 6),
                ('RIGHTPADDING', (0, 0), (-1, -1), 6),
                ('TOPPADDING', (0, 0), (-1, -1), 4),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
            ]
        )
    )
    return tabela


def _pdf_assinatura_imagem(aceite_digital, max_width=70 * mm):
    data_url = getattr(aceite_digital, 'assinatura_data_url', '') or ''
    prefix = 'data:image/png;base64,'
    if not data_url.startswith(prefix):
        return None

    try:
        image_bytes = base64.b64decode(data_url[len(prefix):], validate=True)
    except (binascii.Error, ValueError):
        return None

    imagem = RLImage(BytesIO(image_bytes))
    if not imagem.drawWidth:
        return None

    ratio = imagem.drawHeight / imagem.drawWidth
    imagem.drawWidth = max_width
    imagem.drawHeight = min(28 * mm, max_width * ratio)
    return imagem


@login_required
def termo_chamado_pdf(request, pk):
    chamado = get_object_or_404(
        Chamado.objects.select_related(
            'equipamento',
            'solicitante',
            'destinatario',
            'responsavel',
            'aprovado_por',
            'solicitante__gestor',
        ).prefetch_related(
            'itens_solicitados__equipamento_entregue',
            'itens_solicitados__entregue_por',
        ),
        pk=pk,
    )
    if not _pode_visualizar_chamado(request.user, chamado):
        messages.error(request, 'Acesso negado.')
        return redirect('dashboard')

    aceite_digital = obter_ou_criar_aceite_termo(chamado)
    buffer = BytesIO()
    from django.conf import settings as django_settings

    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        rightMargin=18 * mm,
        leftMargin=18 * mm,
        topMargin=18 * mm,
        bottomMargin=18 * mm,
        title=f'Termo de responsabilidade - Chamado #{chamado.pk}',
        author=getattr(django_settings, 'APP_NAME', 'FIAME System'),
    )

    styles = getSampleStyleSheet()
    styles.add(
        ParagraphStyle(
            name='PdfTitle',
            parent=styles['Title'],
            fontName='Helvetica-Bold',
            fontSize=18,
            leading=22,
            textColor=colors.HexColor('#102033'),
            spaceAfter=10,
        )
    )
    styles.add(
        ParagraphStyle(
            name='PdfSubtitle',
            parent=styles['BodyText'],
            fontName='Helvetica',
            fontSize=10,
            leading=13,
            textColor=colors.HexColor('#607084'),
            spaceAfter=8,
        )
    )
    styles.add(
        ParagraphStyle(
            name='PdfSection',
            parent=styles['Heading2'],
            fontName='Helvetica-Bold',
            fontSize=11,
            leading=13,
            textColor=colors.HexColor('#134a8b'),
            spaceBefore=8,
            spaceAfter=6,
        )
    )
    styles.add(
        ParagraphStyle(
            name='PdfBody',
            parent=styles['BodyText'],
            fontName='Helvetica',
            fontSize=9,
            leading=12,
            textColor=colors.HexColor('#2c3340'),
        )
    )
    styles.add(
        ParagraphStyle(
            name='PdfTableHeader',
            parent=styles['BodyText'],
            fontName='Helvetica-Bold',
            fontSize=9,
            leading=11,
        )
    )
    styles.add(
        ParagraphStyle(
            name='PdfTableCell',
            parent=styles['BodyText'],
            fontName='Helvetica',
            fontSize=8.5,
            leading=10,
        )
    )

    ent_rows = [
        [
            item.tipo_display,
            item.quantidade,
            item.observacao or '-',
            item.equipamento_entregue.id_patrimonio if item.equipamento_entregue else '-',
            item.entregue_por.nome_completo if item.entregue_por else '-',
            item.entregue_em.strftime('%d/%m/%Y %H:%M') if item.entregue_em else '-',
        ]
        for item in chamado.itens_solicitados.select_related('equipamento_entregue', 'entregue_por').all()
    ]
    recolhimentos = chamado.movimentacoes.select_related('equipamento', 'realizado_por').filter(
        tipo__in=['devolucao', 'troca']
    ).order_by('created_at')
    rec_rows = [
        [
            movimento.equipamento.id_patrimonio,
            movimento.equipamento.tipo_display,
            movimento.realizado_por.nome_completo if movimento.realizado_por else '-',
            movimento.created_at.strftime('%d/%m/%Y %H:%M'),
        ]
        for movimento in recolhimentos
    ]

    story = [
        _pdf_paragrafo(f'Termo de responsabilidade - Chamado #{chamado.pk}', styles['PdfTitle']),
        _pdf_paragrafo(
            (
                f'Documento gerado automaticamente pelo {getattr(django_settings, "APP_NAME", "FIAME System")} '
                'para formalizar entrega, recolhimento ou troca de equipamentos.'
            ),
            styles['PdfSubtitle'],
        ),
        _pdf_paragrafo('Dados do chamado', styles['PdfSection']),
        _pdf_tabela(
            ['Campo', 'Valor'],
            [
                ['Chamado', f'#{chamado.pk} - {chamado.titulo}'],
                ['Solicitante', chamado.solicitante.nome_completo],
                ['Colaborador', chamado.destinatario_nome_completo],
                ['Responsável', chamado.responsavel.nome_completo if chamado.responsavel else '-'],
                ['Serviço', chamado.get_servico_realizado_display() if chamado.servico_realizado else '-'],
                ['Prioridade', chamado.get_prioridade_display()],
                ['Status', chamado.get_status_display()],
                ['SLA', f'{chamado.sla_status_label} · {chamado.sla_restante_label}'],
            ],
            [52 * mm, 120 * mm],
            styles,
        ),
        Spacer(1, 4 * mm),
        _pdf_paragrafo('Dados do colaborador', styles['PdfSection']),
        _pdf_tabela(
            ['Campo', 'Valor'],
            [
                ['Nome', chamado.destinatario_nome_completo],
                ['Matrícula', chamado.destinatario_matricula],
                ['Contato', chamado.usuario_destinatario.contato or '-'],
                ['Site', chamado.usuario_destinatario.site or '-'],
                ['Andar / sala', chamado.usuario_destinatario.andar_sala or '-'],
                ['Gestor', chamado.usuario_destinatario.gestor.nome_completo if chamado.usuario_destinatario.gestor else '-'],
            ],
            [52 * mm, 120 * mm],
            styles,
        ),
        Spacer(1, 4 * mm),
        _pdf_paragrafo('Itens solicitados', styles['PdfSection']),
        _pdf_tabela(
            ['Item', 'Qtd', 'Observação', 'Equipamento', 'Entregue por', 'Data'],
            ent_rows or [['-', '-', '-', '-', '-', '-']],
            [35 * mm, 12 * mm, 42 * mm, 30 * mm, 32 * mm, 23 * mm],
            styles,
        ),
    ]

    if rec_rows:
        story.extend(
            [
                Spacer(1, 4 * mm),
                _pdf_paragrafo('Dados do equipamento recolhido', styles['PdfSection']),
                _pdf_tabela(
                    ['Patrimônio', 'Tipo', 'Responsável', 'Data'],
                    rec_rows,
                    [42 * mm, 45 * mm, 52 * mm, 35 * mm],
                    styles,
                ),
            ]
        )

    story.extend(
        [
            Spacer(1, 5 * mm),
            _pdf_paragrafo('Termo de responsabilidade', styles['PdfSection']),
            _pdf_paragrafo(
                'O integrante é responsável por manter os equipamentos corporativos em bom estado de conservação e limpeza.',
                styles['PdfBody'],
            ),
            _pdf_paragrafo('1. É proibido utilizar aplicativos sem licenciamento válido.', styles['PdfBody']),
            _pdf_paragrafo('2. Não é permitida a fixação de adesivos ou adereços nos equipamentos.', styles['PdfBody']),
            _pdf_paragrafo('3. Mudanças de responsabilidade devem ser comunicadas ao time de Tecnologia.', styles['PdfBody']),
            _pdf_paragrafo('4. Em caso de desligamento ou desnecessidade, os itens devem ser devolvidos.', styles['PdfBody']),
            _pdf_paragrafo('5. Furto, roubo ou perda devem ser comunicados pelos canais oficiais.', styles['PdfBody']),
            _pdf_paragrafo('6. O equipamento deve ser devolvido sem contas, senhas ou vínculos ativos.', styles['PdfBody']),
            Spacer(1, 6 * mm),
            _pdf_paragrafo('Aceite digital', styles['PdfSection']),
            _pdf_tabela(
                ['Campo', 'Valor'],
                [
                    ['Status', aceite_digital.status_operacional_label],
                    ['Expira em', aceite_digital.expires_at_label],
                    ['Enviado em', aceite_digital.enviado_em_label],
                    ['Envios', str(aceite_digital.envio_total)],
                    ['Assinante', aceite_digital.nome_assinante or chamado.destinatario_nome_completo],
                    ['Matrícula', aceite_digital.matricula_assinante or chamado.destinatario_matricula],
                    ['Assinado em', aceite_digital.assinado_em_label],
                    ['IP', aceite_digital.ip_assinatura or '-'],
                    ['Hash SHA-256', aceite_digital.documento_hash or '-'],
                ],
                [42 * mm, 132 * mm],
                styles,
            ),
            Spacer(1, 4 * mm),
            _pdf_paragrafo('Assinaturas', styles['PdfSection']),
            _pdf_tabela(
                ['Colaborador', 'Técnico'],
                [
                    [
                        (
                            f'Assinado digitalmente por {aceite_digital.nome_assinante}\n'
                            f'Matrícula: {aceite_digital.matricula_assinante}\n'
                            f'Data: {aceite_digital.assinado_em_label}'
                        )
                        if aceite_digital.is_assinado
                        else f'{chamado.destinatario_nome_completo}\nMatrícula: {chamado.destinatario_matricula}',
                        f'{chamado.responsavel.nome_completo if chamado.responsavel else "-"}\nData: ____/____/______',
                    ]
                ],
                [95 * mm, 95 * mm],
                styles,
            ),
        ]
    )

    assinatura_imagem = _pdf_assinatura_imagem(aceite_digital)
    if assinatura_imagem:
        story.extend(
            [
                Spacer(1, 3 * mm),
                _pdf_paragrafo('Imagem da assinatura digital capturada', styles['PdfSection']),
                assinatura_imagem,
            ]
        )

    doc.build(story)
    pdf_bytes = buffer.getvalue()
    buffer.close()

    response = HttpResponse(pdf_bytes, content_type='application/pdf')
    response['Content-Disposition'] = f'inline; filename="termo-chamado-{chamado.pk}.pdf"'
    return response


@login_required
def entregar_equipamento_chamado(request, pk):
    if not _pode_gerenciar_chamado(request.user):
        messages.error(request, 'Acesso negado.')
        return redirect('dashboard')

    chamado = get_object_or_404(Chamado.objects.select_related('equipamento', 'solicitante', 'destinatario', 'responsavel', 'aprovado_por'), pk=pk)
    if chamado.status == StatusChamado.ENCERRADO:
        messages.warning(request, 'Este chamado já está concluído.')
        return redirect('detalhe_chamado', pk=chamado.pk)
    if chamado.fluxo_etapa not in {
        EtapaFluxoChamado.APROVADO_PARA_RETIRADA,
        EtapaFluxoChamado.EM_SEPARACAO,
        EtapaFluxoChamado.PRONTO_PARA_ENTREGA,
    }:
        messages.warning(request, 'O chamado ainda não foi liberado para entrega.')
        return redirect('detalhe_chamado', pk=chamado.pk)

    if request.method != 'POST':
        return redirect('detalhe_chamado', pk=chamado.pk)

    form = EntregaEquipamentoChamadoForm(request.POST, chamado=chamado)
    if form.is_valid():
        concluir_chamado = form.cleaned_data.get('concluir_chamado', True)
        try:
            registrar_entregas_chamado(
                chamado=chamado,
                selecoes_itens=form.cleaned_data['itens_entrega'],
                realizado_por=request.user,
                observacoes=form.cleaned_data.get('observacoes', ''),
                concluir_chamado=concluir_chamado,
            )
        except ValidationError as exc:
            form.add_error('itens_entrega', exc.messages[0] if exc.messages else 'Não foi possível registrar a entrega.')
        else:
            if concluir_chamado and chamado.status == StatusChamado.ENCERRADO:
                aceite_digital = obter_ou_criar_aceite_termo(chamado)
                try:
                    envio = enviar_link_assinatura_termo(
                        aceite=aceite_digital,
                        request=request,
                        enviado_por=request.user,
                    )
                except ValidationError as exc:
                    detalhe = exc.messages[0] if exc.messages else 'Nao foi possivel enviar o link de assinatura.'
                    messages.warning(request, f'Entrega registrada e chamado encerrado. {detalhe}')
                else:
                    if envio['email_enviado']:
                        messages.success(
                            request,
                            'Entrega registrada e chamado encerrado. Link de assinatura enviado por notificacao interna e e-mail.',
                        )
                    else:
                        messages.warning(
                            request,
                            'Entrega registrada e chamado encerrado. Link de assinatura enviado por notificacao interna, sem e-mail externo.',
                        )
            else:
                messages.success(request, 'Entrega registrada e chamado atualizado.')
            return redirect('detalhe_chamado', pk=chamado.pk)

    contexto = _contexto_detalhe_chamado(request, chamado, entrega_form=form)
    return render(request, 'chamados/detalhe.html', contexto)


@login_required
def painel_tecnico(request):
    if not _pode_gerenciar_chamado(request.user):
        messages.error(request, 'Acesso negado.')
        return redirect('chamados')

    chamados = Chamado.objects.select_related(
        'equipamento',
        'solicitante',
        'destinatario',
        'responsavel',
        'aprovado_por',
    ).prefetch_related('itens_solicitados').order_by('-updated_at', '-created_at')

    painel_lanes = []
    for lane in PAINEL_OPERACIONAL_LANES:
        lane_qs = chamados.filter(fluxo_etapa__in=lane['etapas'])
        lane_items = [
            {
                'chamado': chamado,
                'acoes': _fluxo_acoes_chamado(request.user, chamado),
            }
            for chamado in lane_qs[:6]
        ]
        painel_lanes.append(
            {
                'key': lane['key'],
                'label': lane['label'],
                'description': lane['description'],
                'count': lane_qs.count(),
                'items': lane_items,
            }
        )

    chamados_em_aberto = chamados.exclude(status=StatusChamado.ENCERRADO)
    context = {
        'painel_total': chamados_em_aberto.count(),
        'painel_assumidos_por_mim': chamados_em_aberto.filter(responsavel=request.user).count(),
        'painel_criticos': chamados_em_aberto.filter(prioridade=PrioridadeChamado.CRITICA).count(),
        'painel_aguardando_aprovacao': chamados.filter(fluxo_etapa=EtapaFluxoChamado.AGUARDANDO_APROVACAO).count(),
        'painel_prontos': chamados.filter(fluxo_etapa=EtapaFluxoChamado.PRONTO_PARA_ENTREGA).count(),
        'painel_lanes': painel_lanes,
        'chamados_recentes': chamados[:10],
        'dashboard_charts': {
            'fluxo_chamados': {
                'labels': [lane['label'] for lane in painel_lanes],
                'values': [lane['count'] for lane in painel_lanes],
            }
        },
    }
    return render(request, 'chamados/painel_tecnico.html', context)
