from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.http import HttpResponse
from django.views.decorators.http import require_POST
from html import escape
from io import BytesIO
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from .forms import (
    ChamadoCreateForm,
    ChamadoUpdateForm,
    EntregaEquipamentoChamadoForm,
    REQUEST_TEMPLATE_CARDS,
    get_request_template,
)
from .models import Chamado, EtapaFluxoChamado, STATUS_CHAMADO_EM_FLUXO, PrioridadeChamado, SLANivel, StatusChamado
from .services import excluir_chamado_administrativo, registrar_entregas_chamado, sincronizar_itens_solicitados
from notifications.services import notificar_time_operacional, notificar_usuarios


def _pode_gerenciar_chamado(user):
    return user.is_authenticated and (user.is_admin or user.is_analista or user.is_tecnico)


def _pode_visualizar_chamado(user, chamado):
    return _pode_gerenciar_chamado(user) or chamado.solicitante_id == user.id or chamado.destinatario_id == user.id


def _pode_editar_chamado(user, chamado):
    return _pode_gerenciar_chamado(user) or chamado.solicitante_id == user.id


def _pode_excluir_chamado(user):
    return user.is_authenticated and user.is_admin


def _pode_aprovar_retirada(user, chamado):
    return user.is_authenticated and chamado.destinatario_id == user.id


def _fluxo_acoes_chamado(user, chamado):
    return {
        'pode_assumir': _pode_gerenciar_chamado(user) and chamado.fluxo_etapa in {
            EtapaFluxoChamado.SOLICITADO,
            EtapaFluxoChamado.AGUARDANDO_ESTOQUE,
        },
        'pode_marcar_sem_estoque': user.is_authenticated and (user.is_admin or user.is_analista) and chamado.fluxo_etapa in {
            EtapaFluxoChamado.TRIAGEM,
            EtapaFluxoChamado.AGUARDANDO_ESTOQUE,
        },
        'pode_enviar_para_aprovacao': user.is_authenticated and (user.is_admin or user.is_analista) and chamado.fluxo_etapa in {
            EtapaFluxoChamado.TRIAGEM,
            EtapaFluxoChamado.AGUARDANDO_ESTOQUE,
        },
        'pode_aprovar_retirada': _pode_aprovar_retirada(user, chamado) and chamado.fluxo_etapa == EtapaFluxoChamado.AGUARDANDO_APROVACAO,
        'pode_marcar_separacao': user.is_authenticated and (user.is_admin or user.is_tecnico) and chamado.fluxo_etapa in {
            EtapaFluxoChamado.APROVADO_PARA_RETIRADA,
            EtapaFluxoChamado.EM_SEPARACAO,
        },
        'pode_marcar_pronto': user.is_authenticated and (user.is_admin or user.is_tecnico) and chamado.fluxo_etapa == EtapaFluxoChamado.EM_SEPARACAO,
    }


def _sla_queryset_filter(qs, sla):
    if sla == 'alerta':
        return qs.filter(sla_nivel=SLANivel.ALERTA)
    if sla == 'escalado':
        return qs.filter(sla_nivel=SLANivel.ESCALADO)
    return qs


def _contexto_detalhe_chamado(request, chamado, entrega_form=None):
    acoes_fluxo = _fluxo_acoes_chamado(request.user, chamado)
    fluxo_etapas = chamado.fluxo_etapas
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

    return render(
        request,
        'chamados/termo.html',
        {
            'chamado': chamado,
            'itens_solicitados': chamado.itens_solicitados.all(),
            'entregas': entregas,
            'recolhimentos': recolhimentos,
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
        author=getattr(django_settings, 'APP_NAME', 'ITAM System'),
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
            'Documento gerado automaticamente pelo ITAM System para formalizar entrega, recolhimento ou troca de equipamentos.',
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
            _pdf_paragrafo('Assinaturas', styles['PdfSection']),
            _pdf_tabela(
                ['Colaborador', 'Técnico'],
                [
                    [
                        f'{chamado.destinatario_nome_completo}\nMatrícula: {chamado.destinatario_matricula}',
                        f'{chamado.responsavel.nome_completo if chamado.responsavel else "-"}\nData: ____/____/______',
                    ]
                ],
                [95 * mm, 95 * mm],
                styles,
            ),
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
        try:
            registrar_entregas_chamado(
                chamado=chamado,
                selecoes_itens=form.cleaned_data['itens_entrega'],
                realizado_por=request.user,
                observacoes=form.cleaned_data.get('observacoes', ''),
                concluir_chamado=form.cleaned_data.get('concluir_chamado', True),
            )
        except ValidationError as exc:
            form.add_error('itens_entrega', exc.messages[0] if exc.messages else 'Não foi possível registrar a entrega.')
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

    fluxo_etapas = [
        {
            'key': 'recebidos',
            'label': 'Recebidos',
            'description': 'Chamados novos que ainda estão na porta de entrada da operação.',
            'etapas': [EtapaFluxoChamado.SOLICITADO, EtapaFluxoChamado.TRIAGEM],
        },
        {
            'key': 'estoque',
            'label': 'Aguardando estoque',
            'description': 'Chamados que precisam de disponibilidade antes de avançar.',
            'etapas': [EtapaFluxoChamado.AGUARDANDO_ESTOQUE],
        },
        {
            'key': 'aprovacao',
            'label': 'Aguardando aprovação',
            'description': 'Pedidos liberados para o colaborador confirmar a retirada.',
            'etapas': [EtapaFluxoChamado.AGUARDANDO_APROVACAO],
        },
        {
            'key': 'separacao',
            'label': 'Separação e entrega',
            'description': 'Itens aprovados, em separação ou prontos para a entrega final.',
            'etapas': [
                EtapaFluxoChamado.APROVADO_PARA_RETIRADA,
                EtapaFluxoChamado.EM_SEPARACAO,
                EtapaFluxoChamado.PRONTO_PARA_ENTREGA,
            ],
        },
    ]

    painel_lanes = []
    for lane in fluxo_etapas:
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
