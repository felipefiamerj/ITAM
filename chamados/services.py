import json
import re
import unicodedata
import uuid
from datetime import timedelta
from hashlib import sha256
from urllib.parse import urljoin

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.mail import send_mail
from django.db import transaction
from django.db.models import Count, Q
from django.urls import reverse
from django.utils import timezone

from equipamentos.models import Equipamento, MovimentacaoEquipamento, StatusEquipamento, TipoEquipamento
from equipamentos.services import aplicar_movimentacao_equipamento
from estoque.models import StatusReservaEstoque, reservas_ativas_por_chamado
from estoque.services import marcar_reserva_entregue
from notifications.services import notificar_time_operacional, notificar_usuarios

from .delivery import normalizar_selecoes_entrega
from .models import (
    Chamado,
    ChamadoFluxoEvento,
    ChamadoItemSolicitado,
    EtapaFluxoChamado,
    PrioridadeChamado,
    ServicoChamado,
    SLANivel,
    StatusChamado,
    StatusTermoAceite,
    TermoAceiteDigital,
)


def _normalizar_texto(texto):
    texto = (texto or '').strip().lower()
    texto = unicodedata.normalize('NFKD', texto)
    texto = ''.join(char for char in texto if not unicodedata.combining(char))
    texto = re.sub(r'[^a-z0-9]+', ' ', texto)
    texto = re.sub(r'\s+', ' ', texto)
    return texto.strip()


def _montar_lookup_tipos():
    lookup = {}
    for value, label in TipoEquipamento.choices:
        lookup[_normalizar_texto(value)] = value
        lookup[_normalizar_texto(label)] = value
    return lookup


_TIPO_EQUIPAMENTO_LOOKUP = _montar_lookup_tipos()

SLA_ETAPA_LIMITES_PADRAO_MINUTOS = {
    EtapaFluxoChamado.SOLICITADO: 4 * 60,
    EtapaFluxoChamado.TRIAGEM: 4 * 60,
    EtapaFluxoChamado.AGUARDANDO_ESTOQUE: 24 * 60,
    EtapaFluxoChamado.AGUARDANDO_APROVACAO: 24 * 60,
    EtapaFluxoChamado.APROVADO_PARA_RETIRADA: 8 * 60,
    EtapaFluxoChamado.EM_SEPARACAO: 8 * 60,
    EtapaFluxoChamado.PRONTO_PARA_ENTREGA: 8 * 60,
}


def limites_sla_etapa_minutos():
    raw = getattr(settings, 'ITAM_SLA_ETAPA_MINUTOS', '') or ''
    limites = dict(SLA_ETAPA_LIMITES_PADRAO_MINUTOS)
    if isinstance(raw, dict):
        pares = raw.items()
    else:
        pares = []
        for parte in str(raw).replace(';', ',').split(','):
            if ':' in parte:
                chave, valor = parte.split(':', 1)
            elif '=' in parte:
                chave, valor = parte.split('=', 1)
            else:
                continue
            pares.append((chave, valor))

    for chave, valor in pares:
        etapa = str(chave).strip()
        if etapa not in dict(EtapaFluxoChamado.choices):
            continue
        try:
            minutos = int(valor)
        except (TypeError, ValueError):
            continue
        if minutos > 0:
            limites[etapa] = minutos
    return limites


def _usuarios_afetados_sla(chamado):
    usuarios = [chamado.solicitante, chamado.destinatario, chamado.responsavel]
    vistos = set()
    resultado = []

    for usuario in usuarios:
        if not usuario or usuario.pk in vistos:
            continue
        vistos.add(usuario.pk)
        resultado.append(usuario)

    return resultado


def _usuarios_externos_sla(chamado):
    return [usuario for usuario in _usuarios_afetados_sla(chamado) if not usuario.is_operacional]


def calcular_prazo_sla(chamado, base=None):
    base = base or chamado.created_at or timezone.now()
    minutos = chamado.sla_duracao_minutos
    return base + timedelta(minutes=minutos)


def calcular_momento_alerta_sla(chamado, base=None):
    prazo = calcular_prazo_sla(chamado, base=base)
    janela = max(30, int(chamado.sla_duracao_minutos * 0.25))
    return prazo - timedelta(minutes=janela)


def avaliar_sla_chamado(chamado, now=None):
    now = now or timezone.now()
    prazo = calcular_prazo_sla(chamado)
    alerta = calcular_momento_alerta_sla(chamado)

    if chamado.status == StatusChamado.ENCERRADO:
        estado = 'encerrado'
    elif chamado.sla_nivel == SLANivel.ESCALADO or now >= prazo:
        estado = SLANivel.ESCALADO
    elif chamado.sla_nivel == SLANivel.ALERTA or now >= alerta:
        estado = SLANivel.ALERTA
    else:
        estado = SLANivel.NORMAL

    return {
        'estado': estado,
        'prazo_em': prazo,
        'alerta_em': alerta,
        'em_atraso': now >= prazo,
        'minutos_restantes': int((prazo - now).total_seconds() // 60),
    }


def _notificar_sla(chamado, *, nivel, now):
    link = reverse('detalhe_chamado', kwargs={'pk': chamado.pk})
    if nivel == SLANivel.ALERTA:
        titulo = f'SLA em atenção: chamado #{chamado.pk}'
        mensagem = (
            f'O chamado #{chamado.pk} - {chamado.titulo} entrou na janela de atenção do SLA. '
            f'Prazo estimado: {chamado.sla_prazo_em.strftime("%d/%m/%Y %H:%M")}.'
        )
    else:
        titulo = f'SLA escalonado: chamado #{chamado.pk}'
        mensagem = (
            f'O chamado #{chamado.pk} - {chamado.titulo} ultrapassou o SLA em '
            f'{max(1, int((now - chamado.sla_prazo_em).total_seconds() // 60))} minuto(s).'
        )

    externos = _usuarios_externos_sla(chamado)
    if externos:
        notificar_usuarios(externos, titulo, mensagem, link=link)
    notificar_time_operacional(titulo, mensagem, link=link)


def verificar_sla_chamados(now=None):
    now = now or timezone.now()
    chamados = (
        Chamado.objects.exclude(status=StatusChamado.ENCERRADO)
        .select_related('solicitante', 'destinatario', 'responsavel')
        .order_by('created_at')
    )

    alertados = 0
    escalados = 0

    for chamado in chamados.iterator():
        avaliacao = avaliar_sla_chamado(chamado, now=now)
        estado = avaliacao['estado']

        if estado == SLANivel.ESCALADO and chamado.sla_nivel != SLANivel.ESCALADO:
            chamado.sla_nivel = SLANivel.ESCALADO
            if not chamado.sla_alertado_em:
                chamado.sla_alertado_em = now
            chamado.sla_escalado_em = now
            if chamado.prioridade != PrioridadeChamado.CRITICA:
                chamado.prioridade = PrioridadeChamado.CRITICA
                update_fields = ['sla_nivel', 'sla_alertado_em', 'sla_escalado_em', 'prioridade', 'updated_at']
            else:
                update_fields = ['sla_nivel', 'sla_alertado_em', 'sla_escalado_em', 'updated_at']
            chamado.save(update_fields=update_fields)
            _notificar_sla(chamado, nivel=SLANivel.ESCALADO, now=now)
            escalados += 1
            continue

        if estado == SLANivel.ALERTA and chamado.sla_nivel == SLANivel.NORMAL:
            chamado.sla_nivel = SLANivel.ALERTA
            if not chamado.sla_alertado_em:
                chamado.sla_alertado_em = now
            chamado.save(update_fields=['sla_nivel', 'sla_alertado_em', 'updated_at'])
            _notificar_sla(chamado, nivel=SLANivel.ALERTA, now=now)
            alertados += 1

    return {
        'alertados': alertados,
        'escalados': escalados,
    }


def obter_evento_atual_fluxo_chamado(chamado):
    evento = (
        ChamadoFluxoEvento.objects.filter(
            chamado=chamado,
            etapa_nova=chamado.fluxo_etapa,
            status_novo=chamado.status,
        )
        .order_by('-criado_em', '-id')
        .first()
    )
    if evento:
        return evento

    return ChamadoFluxoEvento.objects.create(
        chamado=chamado,
        etapa_anterior='',
        etapa_nova=chamado.fluxo_etapa or EtapaFluxoChamado.SOLICITADO,
        status_anterior='',
        status_novo=chamado.status or StatusChamado.FILA,
        observacao='Evento gerado para controle de SLA por etapa.',
        criado_em=chamado.updated_at or chamado.created_at or timezone.now(),
    )


def avaliar_sla_etapa_chamado(chamado, now=None, evento=None):
    now = now or timezone.now()
    evento = evento or obter_evento_atual_fluxo_chamado(chamado)
    limite_minutos = limites_sla_etapa_minutos().get(chamado.fluxo_etapa)
    inicio = evento.criado_em if evento else chamado.updated_at or chamado.created_at or now
    minutos_na_etapa = max(0, int((now - inicio).total_seconds() // 60))

    if chamado.status == StatusChamado.ENCERRADO or not limite_minutos:
        estado = 'encerrado' if chamado.status == StatusChamado.ENCERRADO else SLANivel.NORMAL
        alerta_minutos = None
    else:
        percentual_alerta = max(
            1,
            min(99, int(getattr(settings, 'ITAM_SLA_ETAPA_ALERTA_PERCENTUAL', 75) or 75)),
        )
        alerta_minutos = max(1, int(limite_minutos * percentual_alerta / 100))
        if minutos_na_etapa >= limite_minutos:
            estado = SLANivel.ESCALADO
        elif minutos_na_etapa >= alerta_minutos:
            estado = SLANivel.ALERTA
        else:
            estado = SLANivel.NORMAL

    return {
        'estado': estado,
        'evento': evento,
        'inicio': inicio,
        'limite_minutos': limite_minutos,
        'alerta_minutos': alerta_minutos,
        'minutos_na_etapa': minutos_na_etapa,
        'minutos_restantes': (limite_minutos - minutos_na_etapa) if limite_minutos else None,
    }


def _formatar_duracao_minutos(minutos):
    minutos = max(0, int(minutos or 0))
    horas = minutos // 60
    resto = minutos % 60
    if horas and resto:
        return f'{horas}h {resto:02d}m'
    if horas:
        return f'{horas}h'
    return f'{resto}m'


def _notificar_sla_etapa(chamado, *, avaliacao, nivel):
    link = reverse('detalhe_chamado', kwargs={'pk': chamado.pk})
    etapa_label = chamado.fluxo_etapa_label
    tempo = _formatar_duracao_minutos(avaliacao['minutos_na_etapa'])
    limite = _formatar_duracao_minutos(avaliacao['limite_minutos'])
    if nivel == SLANivel.ALERTA:
        titulo = f'SLA da etapa em atencao: chamado #{chamado.pk}'
        mensagem = (
            f'O chamado #{chamado.pk} esta ha {tempo} em {etapa_label}. '
            f'Limite configurado da etapa: {limite}.'
        )
    else:
        titulo = f'SLA da etapa escalonado: chamado #{chamado.pk}'
        mensagem = (
            f'O chamado #{chamado.pk} ultrapassou o limite da etapa {etapa_label}. '
            f'Tempo na etapa: {tempo}; limite: {limite}.'
        )
    notificar_time_operacional(titulo, mensagem, link=link)


def verificar_sla_etapas_chamados(now=None):
    now = now or timezone.now()
    chamados = (
        Chamado.objects.exclude(status=StatusChamado.ENCERRADO)
        .select_related('solicitante', 'destinatario', 'responsavel')
        .order_by('updated_at', 'created_at')
    )

    avaliados = 0
    alertados = 0
    escalados = 0

    for chamado in chamados.iterator():
        avaliacao = avaliar_sla_etapa_chamado(chamado, now=now)
        evento = avaliacao['evento']
        estado = avaliacao['estado']
        avaliados += 1

        if estado == SLANivel.ESCALADO and not evento.sla_escalado_em:
            if not evento.sla_alertado_em:
                evento.sla_alertado_em = now
            evento.sla_escalado_em = now
            evento.save(update_fields=['sla_alertado_em', 'sla_escalado_em'])
            _notificar_sla_etapa(chamado, avaliacao=avaliacao, nivel=SLANivel.ESCALADO)
            escalados += 1
            continue

        if estado == SLANivel.ALERTA and not evento.sla_alertado_em:
            evento.sla_alertado_em = now
            evento.save(update_fields=['sla_alertado_em'])
            _notificar_sla_etapa(chamado, avaliacao=avaliacao, nivel=SLANivel.ALERTA)
            alertados += 1

    return {
        'avaliados': avaliados,
        'alertados': alertados,
        'escalados': escalados,
    }


PLAYBOOK_FLUXO_ORDEM = [
    EtapaFluxoChamado.SOLICITADO,
    EtapaFluxoChamado.TRIAGEM,
    EtapaFluxoChamado.AGUARDANDO_ESTOQUE,
    EtapaFluxoChamado.AGUARDANDO_APROVACAO,
    EtapaFluxoChamado.APROVADO_PARA_RETIRADA,
    EtapaFluxoChamado.EM_SEPARACAO,
    EtapaFluxoChamado.PRONTO_PARA_ENTREGA,
    EtapaFluxoChamado.ENCERRADO,
]
PLAYBOOK_FLUXO_INDICE = {etapa: indice for indice, etapa in enumerate(PLAYBOOK_FLUXO_ORDEM)}

PLAYBOOK_TITULOS = {
    ServicoChamado.ENTREGA: (
        'Entrega guiada',
        'Roteiro para conferir pedido, reservar estoque, aprovar retirada, separar itens e registrar o termo.',
    ),
    ServicoChamado.TROCA: (
        'Troca de equipamento',
        'Roteiro para identificar o patrimonio atual, preparar substituto, registrar troca e manter evidencias.',
    ),
    ServicoChamado.RECOLHIMENTO: (
        'Recolhimento',
        'Roteiro para localizar patrimonio, registrar devolucao e fechar o atendimento com rastreabilidade.',
    ),
    ServicoChamado.MANUTENCAO: (
        'Manutencao',
        'Roteiro para vincular patrimonio, diagnosticar, registrar manutencao e concluir o chamado.',
    ),
    ServicoChamado.INSTALACAO: (
        'Instalacao',
        'Roteiro para preparar materiais, executar instalacao e confirmar aceite do colaborador.',
    ),
    ServicoChamado.ORIENTACAO: (
        'Orientacao',
        'Roteiro simples para assumir, orientar o colaborador e registrar a resolucao.',
    ),
    ServicoChamado.OUTRO: (
        'Atendimento geral',
        'Roteiro operacional para manter dono, acao e encerramento claros.',
    ),
}

PLAYBOOK_SERVICOS_COM_ITENS = {
    ServicoChamado.ENTREGA,
    ServicoChamado.TROCA,
    ServicoChamado.INSTALACAO,
}


def _playbook_servico_chamado(chamado, itens_total):
    if chamado.servico_realizado:
        return chamado.servico_realizado
    if itens_total or chamado.tipo_equipamento_solicitado:
        return ServicoChamado.ENTREGA
    return ServicoChamado.OUTRO


def _playbook_etapa_atingida(chamado, etapa):
    if chamado.status == StatusChamado.ENCERRADO:
        return True
    atual = PLAYBOOK_FLUXO_INDICE.get(chamado.fluxo_etapa_atual, 0)
    alvo = PLAYBOOK_FLUXO_INDICE.get(etapa, 0)
    return atual >= alvo


def _playbook_estado(*, done=False, active=False, blocked=False):
    if done:
        return 'concluido'
    if blocked:
        return 'bloqueado'
    if active:
        return 'atual'
    return 'pendente'


def _playbook_etapa(key, titulo, descricao, *, done=False, active=False, blocked=False, detalhe=''):
    estado = _playbook_estado(done=done, active=active, blocked=blocked)
    labels = {
        'concluido': 'Concluido',
        'bloqueado': 'Bloqueado',
        'atual': 'Em andamento',
        'pendente': 'Pendente',
    }
    tones = {
        'concluido': 'success',
        'bloqueado': 'warning',
        'atual': 'info',
        'pendente': 'neutral',
    }
    return {
        'key': key,
        'titulo': titulo,
        'descricao': descricao,
        'detalhe': detalhe,
        'estado': estado,
        'label': labels[estado],
        'tone': tones[estado],
        'done': estado == 'concluido',
        'active': estado in {'atual', 'bloqueado'},
        'blocked': estado == 'bloqueado',
    }


def _playbook_resumo_itens(chamado):
    itens = list(chamado.itens_solicitados.select_related('equipamento_entregue').order_by('id'))
    materializaveis = [item for item in itens if item.tipo_equipamento != TipoEquipamento.OUTRO]
    if not materializaveis and chamado.tipo_equipamento_solicitado:
        return {
            'itens': itens,
            'materializaveis_total': 1,
            'entregues_total': 1 if chamado.equipamento_id else 0,
            'tipos_pendentes': [] if chamado.equipamento_id else [chamado.tipo_equipamento_solicitado],
            'labels_pendentes': [] if chamado.equipamento_id else [chamado.get_tipo_equipamento_solicitado_display()],
        }

    tipos_pendentes = []
    labels_pendentes = []
    entregues_total = 0
    for item in materializaveis:
        if item.equipamento_entregue_id:
            entregues_total += 1
            continue
        tipos_pendentes.append(item.tipo_equipamento)
        labels_pendentes.append(item.tipo_display)

    return {
        'itens': itens,
        'materializaveis_total': len(materializaveis),
        'entregues_total': entregues_total,
        'tipos_pendentes': tipos_pendentes,
        'labels_pendentes': labels_pendentes,
    }


def _playbook_estoque_por_tipo(tipos):
    tipos = list(dict.fromkeys(tipo for tipo in tipos if tipo))
    if not tipos:
        return {}
    return {
        row['tipo']: row['total']
        for row in Equipamento.objects.filter(status=StatusEquipamento.EM_ESTOQUE, tipo__in=tipos)
        .values('tipo')
        .annotate(total=Count('id'))
    }


def _playbook_aceite(chamado, aceite_digital=None):
    if aceite_digital is not None:
        return aceite_digital
    try:
        return chamado.aceite_digital
    except TermoAceiteDigital.DoesNotExist:
        return None


def gerar_playbook_chamado(chamado, *, aceite_digital=None):
    resumo_itens = _playbook_resumo_itens(chamado)
    servico = _playbook_servico_chamado(chamado, len(resumo_itens['itens']))
    titulo, descricao = PLAYBOOK_TITULOS.get(servico, PLAYBOOK_TITULOS[ServicoChamado.OUTRO])
    aceite_digital = _playbook_aceite(chamado, aceite_digital=aceite_digital)

    reservas = list(
        reservas_ativas_por_chamado(chamado)
        .select_related('equipamento', 'item_solicitado')
        .order_by('created_at')
    )
    reservas_por_item = {reserva.item_solicitado_id for reserva in reservas if reserva.item_solicitado_id}
    reservas_total = len(reservas)
    reservas_separadas = sum(1 for reserva in reservas if reserva.status == StatusReservaEstoque.SEPARADA)
    movimentos = chamado.movimentacoes.all()
    tem_saida = movimentos.filter(tipo='saida').exists()
    tem_recolhimento = movimentos.filter(tipo__in=['devolucao', 'troca']).exists()
    tem_manutencao = movimentos.filter(tipo__in=['manutencao', 'retorno_manutencao']).exists()

    materializaveis_total = resumo_itens['materializaveis_total']
    entregues_total = resumo_itens['entregues_total']
    cobertura_estoque = entregues_total + reservas_total
    requer_itens = servico in PLAYBOOK_SERVICOS_COM_ITENS
    requer_estoque = requer_itens and materializaveis_total > 0
    estoque_done = not requer_estoque or cobertura_estoque >= materializaveis_total

    tem_itens = bool(resumo_itens['itens']) or bool(chamado.tipo_equipamento_solicitado)
    triagem_done = bool(chamado.responsavel_id) or _playbook_etapa_atingida(chamado, EtapaFluxoChamado.TRIAGEM)
    entrega_concluida = chamado.status == StatusChamado.ENCERRADO and (
        not requer_estoque or entregues_total >= materializaveis_total or tem_saida
    )
    aprovacao_done = bool(chamado.aprovado_em) or _playbook_etapa_atingida(
        chamado,
        EtapaFluxoChamado.APROVADO_PARA_RETIRADA,
    )
    pendentes_sem_cobertura = []
    reserva_inteligente_disponivel = False
    if requer_estoque:
        estoque_por_tipo = _playbook_estoque_por_tipo(resumo_itens['tipos_pendentes'])
        for item in resumo_itens['itens']:
            if item.tipo_equipamento == TipoEquipamento.OUTRO or item.equipamento_entregue_id or item.pk in reservas_por_item:
                continue
            if estoque_por_tipo.get(item.tipo_equipamento, 0) <= 0:
                pendentes_sem_cobertura.append(item.tipo_display)
            else:
                reserva_inteligente_disponivel = True
        if not resumo_itens['itens'] and resumo_itens['tipos_pendentes']:
            for tipo, label in zip(resumo_itens['tipos_pendentes'], resumo_itens['labels_pendentes'], strict=False):
                if estoque_por_tipo.get(tipo, 0) <= 0:
                    pendentes_sem_cobertura.append(label)
                else:
                    reserva_inteligente_disponivel = True

    etapas = [
        _playbook_etapa(
            'triagem',
            'Triagem e responsavel',
            'Assumir o chamado e confirmar o melhor fluxo operacional.',
            done=triagem_done,
            active=chamado.fluxo_etapa == EtapaFluxoChamado.SOLICITADO,
            detalhe=chamado.responsavel.nome_completo if chamado.responsavel_id else 'Sem responsavel atribuido.',
        )
    ]

    if servico in {ServicoChamado.RECOLHIMENTO, ServicoChamado.MANUTENCAO}:
        etapas.append(
            _playbook_etapa(
                'patrimonio',
                'Patrimonio vinculado',
                'Identificar o equipamento afetado antes de movimentar ou encerrar.',
                done=bool(chamado.equipamento_id),
                active=triagem_done and not chamado.equipamento_id,
                blocked=triagem_done and not chamado.equipamento_id,
                detalhe=chamado.equipamento.id_patrimonio if chamado.equipamento_id else 'Informe o patrimonio no chamado.',
            )
        )

    if servico == ServicoChamado.TROCA:
        etapas.append(
            _playbook_etapa(
                'patrimonio_atual',
                'Patrimonio atual',
                'Confirmar qual equipamento sera substituido.',
                done=bool(chamado.equipamento_id),
                active=triagem_done and not chamado.equipamento_id,
                blocked=triagem_done and not chamado.equipamento_id,
                detalhe=chamado.equipamento.id_patrimonio if chamado.equipamento_id else 'Vincule o equipamento atual.',
            )
        )

    if requer_itens:
        etapas.append(
            _playbook_etapa(
                'pedido',
                'Pedido conferido',
                'Confirmar os itens ou materiais que serao separados para o colaborador.',
                done=tem_itens,
                active=triagem_done and not tem_itens,
                blocked=triagem_done and not tem_itens,
                detalhe=chamado.itens_solicitados_resumo if tem_itens else 'Nenhum item solicitado informado.',
            )
        )
        etapas.append(
            _playbook_etapa(
                'estoque',
                'Reserva/separacao de estoque',
                'Cobrir cada item com uma reserva ativa, item separado ou equipamento ja entregue.',
                done=estoque_done,
                active=tem_itens and not estoque_done,
                blocked=bool(pendentes_sem_cobertura),
                detalhe=f'{cobertura_estoque}/{materializaveis_total} item(ns) com cobertura de estoque.'
                if requer_estoque
                else 'Sem item patrimonial obrigatorio.',
            )
        )

    if servico in {ServicoChamado.ENTREGA, ServicoChamado.TROCA}:
        etapas.append(
            _playbook_etapa(
                'aprovacao',
                'Aprovacao do colaborador',
                'Liberar a retirada antes da separacao final.',
                done=aprovacao_done,
                active=chamado.fluxo_etapa == EtapaFluxoChamado.AGUARDANDO_APROVACAO,
                detalhe=chamado.aprovado_por_label if chamado.aprovado_por_id else 'Aguardando aprovacao no fluxo.',
            )
        )
        etapas.append(
            _playbook_etapa(
                'separacao',
                'Separacao fisica',
                'Preparar equipamentos, acessorios e conferencia final antes da entrega.',
                done=_playbook_etapa_atingida(chamado, EtapaFluxoChamado.PRONTO_PARA_ENTREGA) or entrega_concluida,
                active=chamado.fluxo_etapa
                in {EtapaFluxoChamado.APROVADO_PARA_RETIRADA, EtapaFluxoChamado.EM_SEPARACAO},
                detalhe=f'{reservas_separadas}/{reservas_total} reserva(s) marcada(s) como separada(s).'
                if reservas_total
                else 'Sem reservas separadas.',
            )
        )
        etapas.append(
            _playbook_etapa(
                'entrega',
                'Entrega registrada',
                'Registrar a saida dos equipamentos e vincular o patrimonio aos itens solicitados.',
                done=entrega_concluida,
                active=chamado.fluxo_etapa == EtapaFluxoChamado.PRONTO_PARA_ENTREGA,
                detalhe=f'{entregues_total}/{materializaveis_total} item(ns) entregue(s).'
                if requer_estoque
                else 'Registrar evidencia de entrega.',
            )
        )
        if servico == ServicoChamado.TROCA:
            etapas.append(
                _playbook_etapa(
                    'recolhimento',
                    'Recolhimento do item anterior',
                    'Registrar devolucao ou troca do patrimonio substituido.',
                    done=tem_recolhimento,
                    active=entrega_concluida and not tem_recolhimento,
                    detalhe='Movimentacao de devolucao/troca registrada.'
                    if tem_recolhimento
                    else 'Sem movimentacao de recolhimento vinculada.',
                )
            )
        etapas.append(
            _playbook_etapa(
                'termo',
                'Termo de responsabilidade',
                'Manter o aceite digital assinado ou pronto para cobranca.',
                done=bool(aceite_digital and aceite_digital.is_assinado),
                active=chamado.status == StatusChamado.ENCERRADO and not bool(aceite_digital and aceite_digital.is_assinado),
                detalhe=aceite_digital.status_operacional_label if aceite_digital else 'Termo ainda nao gerado.',
            )
        )
    elif servico == ServicoChamado.RECOLHIMENTO:
        etapas.extend(
            [
                _playbook_etapa(
                    'recolhimento',
                    'Devolucao registrada',
                    'Registrar movimentacao de devolucao do patrimonio para o estoque.',
                    done=tem_recolhimento,
                    active=bool(chamado.equipamento_id) and not tem_recolhimento,
                    detalhe='Movimentacao de devolucao/troca vinculada.'
                    if tem_recolhimento
                    else 'Aguardando registro de devolucao.',
                ),
                _playbook_etapa(
                    'fechamento',
                    'Fechamento com evidencia',
                    'Finalizar o chamado com observacao do recolhimento.',
                    done=chamado.status == StatusChamado.ENCERRADO,
                    active=tem_recolhimento and chamado.status != StatusChamado.ENCERRADO,
                    detalhe=chamado.solucao or 'Sem solucao registrada.',
                ),
            ]
        )
    elif servico == ServicoChamado.MANUTENCAO:
        etapas.extend(
            [
                _playbook_etapa(
                    'diagnostico',
                    'Diagnostico tecnico',
                    'Registrar achado, impacto e proxima acao no atendimento.',
                    done=bool(chamado.solucao) or tem_manutencao,
                    active=bool(chamado.equipamento_id) and not (chamado.solucao or tem_manutencao),
                    detalhe='Ha movimentacao de manutencao vinculada.'
                    if tem_manutencao
                    else chamado.solucao or 'Sem diagnostico registrado.',
                ),
                _playbook_etapa(
                    'fechamento',
                    'Fechamento da manutencao',
                    'Encerrar com retorno, descarte ou plano de continuidade.',
                    done=chamado.status == StatusChamado.ENCERRADO,
                    active=(bool(chamado.solucao) or tem_manutencao) and chamado.status != StatusChamado.ENCERRADO,
                    detalhe=chamado.sla_status_label,
                ),
            ]
        )
    else:
        execucao_done = bool(chamado.solucao) or chamado.status == StatusChamado.ENCERRADO
        etapas.extend(
            [
                _playbook_etapa(
                    'execucao',
                    'Atendimento executado',
                    'Registrar a orientacao, instalacao ou acao realizada.',
                    done=execucao_done,
                    active=triagem_done and not execucao_done,
                    detalhe=chamado.solucao or 'Sem solucao registrada.',
                ),
                _playbook_etapa(
                    'fechamento',
                    'Fechamento',
                    'Concluir o chamado quando a demanda estiver resolvida.',
                    done=chamado.status == StatusChamado.ENCERRADO,
                    active=execucao_done and chamado.status != StatusChamado.ENCERRADO,
                    detalhe=chamado.get_status_display(),
                ),
            ]
        )

    bloqueios = [etapa for etapa in etapas if etapa['blocked']]
    if pendentes_sem_cobertura:
        bloqueios.append(
            {
                'key': 'estoque_disponivel',
                'titulo': 'Sem estoque compativel',
                'descricao': ', '.join(dict.fromkeys(pendentes_sem_cobertura)),
            }
        )

    concluidas = sum(1 for etapa in etapas if etapa['done'])
    proxima_acao = next((etapa for etapa in etapas if etapa['blocked']), None) or next(
        (etapa for etapa in etapas if not etapa['done']),
        None,
    )
    progresso = round((concluidas / len(etapas)) * 100) if etapas else 100
    tone = 'success' if progresso == 100 else 'warning' if bloqueios else 'info'

    return {
        'key': servico,
        'titulo': titulo,
        'descricao': descricao,
        'tone': tone,
        'etapas': etapas,
        'progresso_percentual': progresso,
        'concluidas': concluidas,
        'total': len(etapas),
        'pendentes': len(etapas) - concluidas,
        'bloqueios': bloqueios,
        'proxima_acao': proxima_acao,
        'reserva_inteligente_disponivel': reserva_inteligente_disponivel,
    }


def _formatar_data_iso(value):
    if not value:
        return None
    return timezone.localtime(value).isoformat()


def _payload_item_termo(item):
    equipamento = item.equipamento_entregue
    entregue_por = item.entregue_por
    return {
        'id': item.pk,
        'tipo': item.tipo_display,
        'quantidade': item.quantidade,
        'observacao': item.observacao or '',
        'equipamento': equipamento.id_patrimonio if equipamento else '',
        'equipamento_tipo': equipamento.tipo_display if equipamento else '',
        'equipamento_marca': equipamento.marca if equipamento else '',
        'equipamento_modelo': equipamento.modelo if equipamento else '',
        'entregue_por': entregue_por.nome_completo if entregue_por else '',
        'entregue_em': _formatar_data_iso(item.entregue_em),
    }


def _payload_movimento_termo(movimento):
    return {
        'id': movimento.pk,
        'tipo': movimento.tipo,
        'equipamento': movimento.equipamento.id_patrimonio,
        'equipamento_tipo': movimento.equipamento.tipo_display,
        'realizado_por': movimento.realizado_por.nome_completo if movimento.realizado_por else '',
        'created_at': _formatar_data_iso(movimento.created_at),
    }


def montar_payload_termo(chamado):
    itens = chamado.itens_solicitados.select_related('equipamento_entregue', 'entregue_por').order_by('id')
    recolhimentos = chamado.movimentacoes.select_related('equipamento', 'realizado_por').filter(
        tipo__in=['devolucao', 'troca']
    ).order_by('created_at')
    return {
        'versao': '2026.1',
        'chamado': {
            'id': chamado.pk,
            'titulo': chamado.titulo,
            'descricao': chamado.descricao,
            'servico': chamado.get_servico_realizado_display() if chamado.servico_realizado else '',
            'status': chamado.get_status_display(),
            'prioridade': chamado.get_prioridade_display(),
            'solicitante': chamado.solicitante.nome_completo if chamado.solicitante_id else '',
            'colaborador': chamado.destinatario_nome_completo,
            'matricula': chamado.destinatario_matricula,
            'responsavel': chamado.responsavel.nome_completo if chamado.responsavel_id else '',
            'criado_em': _formatar_data_iso(chamado.created_at),
            'fechado_em': _formatar_data_iso(chamado.data_fechamento),
        },
        'itens_entregues': [_payload_item_termo(item) for item in itens],
        'recolhimentos': [_payload_movimento_termo(movimento) for movimento in recolhimentos],
    }


def calcular_hash_termo(chamado):
    payload = json.dumps(montar_payload_termo(chamado), ensure_ascii=False, sort_keys=True)
    return sha256(payload.encode('utf-8')).hexdigest()


def preparar_evento_fluxo_chamado(chamado, *, usuario=None, observacao=''):
    chamado._fluxo_evento_usuario = usuario
    chamado._fluxo_evento_observacao = observacao or ''
    return chamado


def calcular_expiracao_link_termo(base=None):
    dias = max(1, int(getattr(settings, 'ITAM_TERMO_ASSINATURA_VALIDADE_DIAS', 7) or 7))
    return (base or timezone.now()) + timedelta(days=dias)


def obter_ou_criar_aceite_termo(chamado):
    aceite, _ = TermoAceiteDigital.objects.get_or_create(chamado=chamado)
    if not aceite.expires_at and not aceite.is_assinado:
        aceite.expires_at = calcular_expiracao_link_termo()
        aceite.save(update_fields=['expires_at', 'updated_at'])
    return aceite


def assinatura_url_termo(aceite, request=None):
    path = reverse('assinar_termo_chamado', kwargs={'token': aceite.token})
    if request is not None:
        return request.build_absolute_uri(path)

    site_url = (getattr(settings, 'SITE_URL', '') or '').strip()
    if site_url.startswith(('http://', 'https://')):
        return urljoin(site_url.rstrip('/') + '/', path.lstrip('/'))
    return path


@transaction.atomic
def renovar_link_assinatura_termo(*, aceite, usuario=None):
    aceite = TermoAceiteDigital.objects.select_for_update().get(pk=aceite.pk)
    if aceite.status == StatusTermoAceite.ASSINADO:
        raise ValidationError('Este termo ja foi assinado.')

    aceite.token = uuid.uuid4()
    aceite.expires_at = calcular_expiracao_link_termo()
    aceite.enviado_em = None
    aceite.enviado_por = None
    aceite.email_enviado = False
    aceite.email_destino = ''
    aceite.save(
        update_fields=[
            'token',
            'expires_at',
            'enviado_em',
            'enviado_por',
            'email_enviado',
            'email_destino',
            'updated_at',
        ]
    )
    return aceite


@transaction.atomic
def enviar_link_assinatura_termo(*, aceite, request=None, enviado_por=None):
    aceite = TermoAceiteDigital.objects.select_for_update(of=('self',)).select_related(
        'chamado__solicitante',
        'chamado__destinatario',
        'chamado__responsavel',
    ).get(pk=aceite.pk)
    if aceite.status == StatusTermoAceite.ASSINADO:
        raise ValidationError('Este termo ja foi assinado.')
    if aceite.is_expirado:
        raise ValidationError('O link de assinatura expirou. Gere um novo link antes de enviar.')

    chamado = aceite.chamado
    destinatario = chamado.usuario_destinatario
    link = assinatura_url_termo(aceite, request=request)
    titulo = f'Termo para assinatura: chamado #{chamado.pk}'
    mensagem = (
        f'O termo de responsabilidade do chamado #{chamado.pk} esta aguardando sua assinatura. '
        f'O link expira em {aceite.expires_at_label}.'
    )

    notificar_usuarios([destinatario], titulo, mensagem, link=link)

    email_enviado = False
    email_destino = ''
    if destinatario and destinatario.email:
        email_destino = destinatario.email
        try:
            send_mail(
                titulo,
                (
                    f'Ola {destinatario.first_name or destinatario.matricula},\n\n'
                    f'O termo de responsabilidade do chamado #{chamado.pk} esta pronto para assinatura.\n\n'
                    f'Acesse o link abaixo para assinar digitalmente:\n{link}\n\n'
                    f'Validade do link: {aceite.expires_at_label}.\n\n'
                    f'{getattr(settings, "APP_NAME", "FIAME System")}'
                ),
                settings.DEFAULT_FROM_EMAIL,
                [destinatario.email],
                fail_silently=False,
            )
            email_enviado = True
        except Exception:
            email_enviado = False

    aceite.enviado_em = timezone.now()
    aceite.enviado_por = enviado_por if enviado_por and getattr(enviado_por, 'is_authenticated', False) else None
    aceite.envio_total = (aceite.envio_total or 0) + 1
    aceite.email_enviado = email_enviado
    aceite.email_destino = email_destino
    aceite.save(
        update_fields=[
            'enviado_em',
            'enviado_por',
            'envio_total',
            'email_enviado',
            'email_destino',
            'updated_at',
        ]
    )
    return {
        'aceite': aceite,
        'link': link,
        'email_enviado': email_enviado,
        'email_destino': email_destino,
    }


def cobrar_assinaturas_termos(*, aceites, request=None, enviado_por=None, renovar_expirados=True):
    if hasattr(aceites, 'values_list'):
        aceite_ids = list(aceites.values_list('pk', flat=True))
    else:
        aceite_ids = [aceite.pk if hasattr(aceite, 'pk') else int(aceite) for aceite in aceites]

    resumo = {
        'selecionados': len(aceite_ids),
        'enviados': 0,
        'renovados': 0,
        'emails': 0,
        'sem_email': 0,
        'ignorados': 0,
        'falhas': 0,
    }

    for aceite_id in aceite_ids:
        try:
            aceite = TermoAceiteDigital.objects.select_related('chamado').get(pk=aceite_id)
        except TermoAceiteDigital.DoesNotExist:
            resumo['falhas'] += 1
            continue

        if aceite.is_assinado:
            resumo['ignorados'] += 1
            continue

        try:
            if aceite.is_expirado:
                if not renovar_expirados:
                    raise ValidationError('O link de assinatura expirou.')
                aceite = renovar_link_assinatura_termo(aceite=aceite, usuario=enviado_por)
                resumo['renovados'] += 1

            envio = enviar_link_assinatura_termo(
                aceite=aceite,
                request=request,
                enviado_por=enviado_por,
            )
        except ValidationError:
            resumo['falhas'] += 1
            continue

        resumo['enviados'] += 1
        if envio['email_enviado']:
            resumo['emails'] += 1
        else:
            resumo['sem_email'] += 1

    return resumo


def termos_aceite_pendentes_para_cobranca(now=None):
    now = now or timezone.now()
    intervalo_dias = max(1, int(getattr(settings, 'ITAM_TERMO_ASSINATURA_COBRANCA_INTERVALO_DIAS', 1) or 1))
    limite_reenvio = now - timedelta(days=intervalo_dias)
    return (
        TermoAceiteDigital.objects.select_related(
            'chamado',
            'chamado__solicitante',
            'chamado__destinatario',
            'chamado__responsavel',
        )
        .filter(
            chamado__status=StatusChamado.ENCERRADO,
            status=StatusTermoAceite.PENDENTE,
        )
        .filter(
            Q(expires_at__lt=now)
            | Q(enviado_em__isnull=True)
            | Q(enviado_em__lte=limite_reenvio)
        )
        .order_by('expires_at', 'enviado_em', 'pk')
    )


def cobrar_assinaturas_pendentes_automaticamente(now=None):
    now = now or timezone.now()
    ativo = bool(getattr(settings, 'ITAM_TERMO_ASSINATURA_COBRANCA_AUTOMATICA', True))
    resumo = {
        'automatico': True,
        'ativo': ativo,
        'selecionados': 0,
        'enviados': 0,
        'renovados': 0,
        'emails': 0,
        'sem_email': 0,
        'ignorados': 0,
        'falhas': 0,
    }
    if not ativo:
        return resumo

    qs = termos_aceite_pendentes_para_cobranca(now=now)
    resumo.update(cobrar_assinaturas_termos(aceites=qs, request=None, enviado_por=None))

    if resumo['enviados'] or resumo['falhas']:
        notificar_time_operacional(
            'Cobranca automatica de termos digitais',
            (
                f'{resumo["enviados"]} termo(s) cobrados; '
                f'{resumo["renovados"]} link(s) renovado(s); '
                f'{resumo["sem_email"]} sem e-mail externo; '
                f'{resumo["falhas"]} falha(s).'
            ),
            link=reverse('painel_termos_chamados'),
        )

    return resumo


def _client_ip(request):
    if request is None:
        return None
    forwarded = request.headers.get('X-Forwarded-For', '')
    if forwarded:
        return forwarded.split(',')[0].strip() or None
    return request.META.get('REMOTE_ADDR') or None


@transaction.atomic
def registrar_assinatura_termo(*, aceite, assinatura_data_url, request=None, usuario=None):
    aceite = TermoAceiteDigital.objects.select_for_update().get(pk=aceite.pk)
    if aceite.status == StatusTermoAceite.ASSINADO:
        raise ValidationError('Este termo ja foi assinado.')
    if aceite.is_expirado:
        raise ValidationError('Este link de assinatura expirou. Solicite um novo link ao time de Tecnologia.')

    chamado = Chamado.objects.select_related('solicitante', 'destinatario', 'responsavel').get(pk=aceite.chamado_id)
    agora = timezone.now()
    aceite.status = StatusTermoAceite.ASSINADO
    aceite.nome_assinante = chamado.destinatario_nome_completo
    aceite.matricula_assinante = chamado.destinatario_matricula
    aceite.assinatura_data_url = assinatura_data_url
    aceite.documento_hash = calcular_hash_termo(chamado)
    aceite.ip_assinatura = _client_ip(request)
    aceite.user_agent = (request.headers.get('User-Agent', '') if request is not None else '')[:255]
    aceite.assinado_por = usuario if usuario and getattr(usuario, 'is_authenticated', False) else None
    aceite.assinado_em = agora
    aceite.save(
        update_fields=[
            'status',
            'nome_assinante',
            'matricula_assinante',
            'assinatura_data_url',
            'documento_hash',
            'ip_assinatura',
            'user_agent',
            'assinado_por',
            'assinado_em',
            'updated_at',
        ]
    )
    return aceite


def _resolver_tipo_item(texto):
    chave = _normalizar_texto(texto)
    if not chave:
        return None, ''

    tipo_equipamento = _TIPO_EQUIPAMENTO_LOOKUP.get(chave)
    if tipo_equipamento:
        return tipo_equipamento, ''

    return TipoEquipamento.OUTRO, (texto or '').strip()


def _parse_linha_item(texto):
    linha = (texto or '').strip()
    if not linha:
        return None

    linha = linha.lstrip('-*•').strip()
    if not linha:
        return None

    quantidade = 1
    observacao = ''
    tipo_texto = linha

    padrao = re.match(
        r'^(?P<tipo>.+?)\s*[xX*]\s*(?P<quantidade>\d+)\s*(?:[;,-]\s*(?P<obs>.*))?$',
        linha,
    )
    if padrao:
        tipo_texto = padrao.group('tipo').strip()
        quantidade = int(padrao.group('quantidade'))
        observacao = (padrao.group('obs') or '').strip()
    elif ';' in linha:
        partes = [parte.strip() for parte in linha.split(';')]
        tipo_texto = partes[0]
        if len(partes) > 1:
            if partes[1].isdigit():
                quantidade = int(partes[1])
                observacao = '; '.join(parte for parte in partes[2:] if parte)
            else:
                observacao = '; '.join(parte for parte in partes[1:] if parte)
    elif ' - ' in linha:
        partes = [parte.strip() for parte in linha.split(' - ', 2)]
        tipo_texto = partes[0]
        if len(partes) > 1 and partes[1].isdigit():
            quantidade = int(partes[1])
            observacao = partes[2] if len(partes) > 2 else ''
        else:
            observacao = ' - '.join(parte for parte in partes[1:] if parte)

    if quantidade < 1:
        raise ValidationError('A quantidade precisa ser maior que zero.')

    tipo_equipamento, tipo_outro = _resolver_tipo_item(tipo_texto)
    if not tipo_equipamento:
        raise ValidationError('Informe o tipo do item solicitado.')

    return {
        'tipo_equipamento': tipo_equipamento,
        'tipo_outro': tipo_outro,
        'quantidade': quantidade,
        'observacao': observacao,
    }


def _parse_itens_solicitados(texto):
    itens = []
    erros = []
    for indice, linha in enumerate((texto or '').splitlines(), start=1):
        linha = linha.strip()
        if not linha:
            continue
        try:
            item = _parse_linha_item(linha)
        except ValidationError as exc:
            erros.append(f'Linha {indice}: {exc.messages[0] if exc.messages else "Item inválido."}')
            continue
        if item:
            itens.append(item)
    return itens, erros


def _quantidade_item(item):
    return max(1, int(item.get('quantidade') or 1))


def _expandir_itens_solicitados_por_quantidade(itens):
    expandidos = []
    for item in itens:
        quantidade = _quantidade_item(item)
        if item['tipo_equipamento'] == TipoEquipamento.OUTRO:
            expandidos.append({**item, 'quantidade': quantidade})
            continue

        for _ in range(quantidade):
            expandidos.append({**item, 'quantidade': 1})
    return expandidos


@transaction.atomic
def normalizar_itens_patrimoniais_para_unidades(chamado):
    itens = list(
        chamado.itens_solicitados.exclude(tipo_equipamento=TipoEquipamento.OUTRO)
        .filter(quantidade__gt=1)
        .order_by('id')
    )
    unidades_criadas = 0

    for item in itens:
        quantidade = max(1, int(item.quantidade or 1))
        if quantidade <= 1:
            continue

        item.quantidade = 1
        item.save(update_fields=['quantidade'])
        ChamadoItemSolicitado.objects.bulk_create(
            [
                ChamadoItemSolicitado(
                    chamado_id=item.chamado_id,
                    tipo_equipamento=item.tipo_equipamento,
                    tipo_outro=item.tipo_outro,
                    quantidade=1,
                    observacao=item.observacao,
                )
                for _ in range(quantidade - 1)
            ]
        )
        unidades_criadas += quantidade - 1

    return unidades_criadas


@transaction.atomic
def registrar_entrega_chamado(*, chamado, equipamento, realizado_por, observacoes='', concluir_chamado=True):
    reserva = reservas_ativas_por_chamado(chamado).filter(equipamento=equipamento).first()
    if equipamento.status not in {StatusEquipamento.EM_ESTOQUE, StatusEquipamento.RESERVADO}:
        raise ValidationError('Selecione um equipamento disponível em estoque.')
    if equipamento.status == StatusEquipamento.RESERVADO and not reserva:
        raise ValidationError('Este equipamento reservado não pertence a este chamado.')

    destinatario = chamado.usuario_destinatario
    descricao = f'Entrega vinculada ao chamado #{chamado.pk} - {chamado.titulo}'
    if chamado.tipo_equipamento_solicitado:
        descricao = f'{descricao} ({chamado.get_tipo_equipamento_solicitado_display()})'
    if destinatario:
        descricao = f'{descricao} - {destinatario.nome_completo}'

    movimentacao = MovimentacaoEquipamento.objects.create(
        equipamento=equipamento,
        tipo='saida',
        descricao=descricao,
        usuario_anterior=equipamento.responsavel,
        usuario_novo=destinatario,
        realizado_por=realizado_por,
        chamado=chamado,
        observacoes=observacoes or '',
    )

    aplicar_movimentacao_equipamento(equipamento, movimentacao)
    if reserva:
        marcar_reserva_entregue(reserva=reserva, usuario=realizado_por)

    chamado.equipamento = equipamento
    chamado.responsavel = realizado_por

    if concluir_chamado:
        chamado.status = StatusChamado.ENCERRADO
        chamado.fluxo_etapa = EtapaFluxoChamado.ENCERRADO
        resumo = f'Equipamento entregue: {equipamento.id_patrimonio}.'
        if observacoes:
            resumo = f'{resumo}\nObservações da entrega: {observacoes}'

        if chamado.solucao:
            if resumo not in chamado.solucao:
                chamado.solucao = f'{chamado.solucao.rstrip()}\n\n{resumo}'
        else:
            chamado.solucao = resumo
    else:
        chamado.status = StatusChamado.EM_ATENDIMENTO
        chamado.fluxo_etapa = EtapaFluxoChamado.EM_SEPARACAO

    preparar_evento_fluxo_chamado(
        chamado,
        usuario=realizado_por,
        observacao='Entrega individual registrada.',
    )
    chamado.save()
    return movimentacao


@transaction.atomic
def excluir_chamado_administrativo(*, chamado, usuario, motivo=''):
    if not getattr(usuario, 'is_admin', False):
        raise ValidationError('Somente administradores podem excluir chamados.')

    if chamado.itens_solicitados.filter(equipamento_entregue__isnull=False).exists():
        raise ValidationError('Este chamado possui itens entregues e nao pode ser excluido.')

    reservas_ativas = list(reservas_ativas_por_chamado(chamado).select_related('equipamento').order_by('id'))
    observacoes = motivo or 'Exclusao administrativa do chamado.'

    for reserva in reservas_ativas:
        movimentacao = MovimentacaoEquipamento.objects.create(
            equipamento=reserva.equipamento,
            tipo='liberacao_reserva',
            descricao=f'Liberacao administrativa antes da exclusao do chamado #{chamado.pk} - {chamado.titulo}',
            realizado_por=usuario,
            chamado=chamado,
            observacoes=observacoes,
        )
        aplicar_movimentacao_equipamento(reserva.equipamento, movimentacao)

    total_reservas = len(reservas_ativas)
    chamado.delete()
    return total_reservas


@transaction.atomic
def registrar_entregas_chamado(*, chamado, selecoes_itens, realizado_por, observacoes='', concluir_chamado=True):
    normalizar_itens_patrimoniais_para_unidades(chamado)
    selecoes = normalizar_selecoes_entrega(selecoes_itens)
    itens = list(
        chamado.itens_solicitados.select_related('equipamento_entregue', 'entregue_por').order_by('id')
    )

    if not itens:
        raise ValidationError('Este chamado nao possui itens solicitados.')
    if not selecoes:
        raise ValidationError('Selecione pelo menos um item para registrar a entrega.')

    destinatario = chamado.usuario_destinatario
    itens_por_id = {item.id: item for item in itens}
    equipamentos_usados = set()
    movimentos_criados = []
    agora = timezone.now()

    for item_id, equipamento_id in selecoes.items():
        item = itens_por_id.get(item_id)
        if not item:
            raise ValidationError('A selecao enviada nao corresponde aos itens deste chamado.')

        equipamento = Equipamento.objects.select_related('responsavel').filter(pk=equipamento_id).first()
        if not equipamento:
            raise ValidationError('Selecione um equipamento valido.')
        reserva = reservas_ativas_por_chamado(chamado).filter(
            item_solicitado_id=item.id,
            equipamento=equipamento,
        ).first()
        if equipamento.status not in {StatusEquipamento.EM_ESTOQUE, StatusEquipamento.RESERVADO}:
            raise ValidationError(f'O equipamento {equipamento.id_patrimonio} nao esta disponivel em estoque.')
        if equipamento.status == StatusEquipamento.RESERVADO and not reserva:
            raise ValidationError(f'O equipamento {equipamento.id_patrimonio} reservado nao pertence a este chamado.')
        if equipamento.id in equipamentos_usados:
            raise ValidationError('O mesmo equipamento nao pode ser selecionado para mais de um item.')
        equipamentos_usados.add(equipamento.id)

        if item.equipamento_entregue_id and item.equipamento_entregue_id != equipamento.id:
            raise ValidationError(f'O item {item.tipo_display} ja possui um equipamento selecionado.')

        if not item.equipamento_entregue_id:
            descricao = f'Entrega vinculada ao chamado #{chamado.pk} - {chamado.titulo} ({item.tipo_display})'
            if destinatario:
                descricao = f'{descricao} - {destinatario.nome_completo}'
            movimentacao = MovimentacaoEquipamento.objects.create(
                equipamento=equipamento,
                tipo='saida',
                descricao=descricao,
                usuario_anterior=equipamento.responsavel,
                usuario_novo=destinatario,
                realizado_por=realizado_por,
                chamado=chamado,
                observacoes=observacoes or '',
            )

            aplicar_movimentacao_equipamento(equipamento, movimentacao)
            movimentos_criados.append(movimentacao)
            if reserva:
                marcar_reserva_entregue(reserva=reserva, usuario=realizado_por)

        item.equipamento_entregue = equipamento
        item.entregue_por = realizado_por
        if not item.entregue_em:
            item.entregue_em = agora
        item.save(update_fields=['equipamento_entregue', 'entregue_por', 'entregue_em'])

    pendentes = [item for item in itens if item.tipo_equipamento != TipoEquipamento.OUTRO and not item.equipamento_entregue_id]
    if concluir_chamado and pendentes:
        raise ValidationError(
            f'Selecione um equipamento para todos os {len(pendentes)} item(ns) antes de encerrar o chamado.'
        )

    chamado.responsavel = realizado_por
    if not chamado.equipamento_id:
        primeiro_entregue = next((item.equipamento_entregue for item in itens if item.equipamento_entregue_id), None)
        if primeiro_entregue:
            chamado.equipamento = primeiro_entregue

    if concluir_chamado and not pendentes:
        chamado.status = StatusChamado.ENCERRADO
        chamado.fluxo_etapa = EtapaFluxoChamado.ENCERRADO
        linhas_resumo = [
            f'- {item.tipo_display}: {item.equipamento_entregue.id_patrimonio}'
            for item in itens
            if item.equipamento_entregue_id
        ]
        resumo = 'Itens entregues:\n' + '\n'.join(linhas_resumo)
        if observacoes:
            resumo = f'{resumo}\n\nObservacoes da entrega:\n{observacoes}'

        if chamado.solucao:
            if resumo not in chamado.solucao:
                chamado.solucao = f'{chamado.solucao.rstrip()}\n\n{resumo}'
        else:
            chamado.solucao = resumo
    else:
        chamado.status = StatusChamado.EM_ATENDIMENTO
        chamado.fluxo_etapa = EtapaFluxoChamado.EM_SEPARACAO

    preparar_evento_fluxo_chamado(
        chamado,
        usuario=realizado_por,
        observacao='Entrega de itens registrada.',
    )
    chamado.save()
    return movimentos_criados


@transaction.atomic
def sincronizar_itens_solicitados(*, chamado, tipos_solicitados=None, texto_itens=''):
    itens = []
    erros = []

    tipos_solicitados = list(tipos_solicitados or [])
    for tipo in tipos_solicitados:
        tipo_equipamento, tipo_outro = _resolver_tipo_item(tipo)
        if not tipo_equipamento:
            continue
        itens.append(
            {
                'tipo_equipamento': tipo_equipamento,
                'tipo_outro': tipo_outro,
                'quantidade': 1,
                'observacao': '',
            }
        )

    if texto_itens:
        itens_texto, erros = _parse_itens_solicitados(texto_itens)
        itens.extend(itens_texto)

    if erros:
        raise ValidationError(erros)

    itens = _expandir_itens_solicitados_por_quantidade(itens)

    objetos_existentes = list(chamado.itens_solicitados.order_by('id'))
    objetos_salvos = []

    for indice, item in enumerate(itens):
        if indice < len(objetos_existentes):
            objeto = objetos_existentes[indice]
            alterado = (
                objeto.tipo_equipamento != item['tipo_equipamento']
                or objeto.tipo_outro != item['tipo_outro']
                or objeto.quantidade != item['quantidade']
                or objeto.observacao != item['observacao']
            )
            objeto.tipo_equipamento = item['tipo_equipamento']
            objeto.tipo_outro = item['tipo_outro']
            objeto.quantidade = item['quantidade']
            objeto.observacao = item['observacao']
            if alterado:
                objeto.equipamento_entregue = None
                objeto.entregue_por = None
                objeto.entregue_em = None
                objeto.save(
                    update_fields=[
                        'tipo_equipamento',
                        'tipo_outro',
                        'quantidade',
                        'observacao',
                        'equipamento_entregue',
                        'entregue_por',
                        'entregue_em',
                    ]
                )
            else:
                objeto.save(
                    update_fields=[
                        'tipo_equipamento',
                        'tipo_outro',
                        'quantidade',
                        'observacao',
                    ]
                )
        else:
            objeto = ChamadoItemSolicitado.objects.create(
                chamado=chamado,
                tipo_equipamento=item['tipo_equipamento'],
                tipo_outro=item['tipo_outro'],
                quantidade=item['quantidade'],
                observacao=item['observacao'],
            )

        objetos_salvos.append(objeto)

    for objeto in objetos_existentes[len(itens):]:
        objeto.delete()

    if objetos_salvos:
        chamado.tipo_equipamento_solicitado = objetos_salvos[0].tipo_equipamento
    else:
        chamado.tipo_equipamento_solicitado = ''

    chamado.save(update_fields=['tipo_equipamento_solicitado', 'updated_at'])

    return objetos_salvos
