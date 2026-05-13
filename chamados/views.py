from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from .forms import (
    ChamadoCreateForm,
    ChamadoUpdateForm,
    EntregaEquipamentoChamadoForm,
    REQUEST_TEMPLATE_CARDS,
    get_request_template,
)
from .models import Chamado, EtapaFluxoChamado, StatusChamado
from .services import registrar_entregas_chamado, sincronizar_itens_solicitados
from notifications.services import notificar_time_operacional, notificar_usuarios


def _pode_gerenciar_chamado(user):
    return user.is_authenticated and (user.is_admin or user.is_analista or user.is_tecnico)


def _pode_visualizar_chamado(user, chamado):
    return _pode_gerenciar_chamado(user) or chamado.solicitante_id == user.id or chamado.destinatario_id == user.id


def _pode_editar_chamado(user, chamado):
    return _pode_gerenciar_chamado(user) or chamado.solicitante_id == user.id


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


def _contexto_detalhe_chamado(request, chamado, entrega_form=None):
    acoes_fluxo = _fluxo_acoes_chamado(request.user, chamado)
    contexto = {
        'chamado': chamado,
        'pode_gerenciar_chamado': _pode_gerenciar_chamado(request.user),
        'pode_editar_chamado': _pode_editar_chamado(request.user, chamado),
        'acoes_fluxo': acoes_fluxo,
        'fluxo_etapas': chamado.fluxo_etapas,
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

    if not (request.user.is_admin or request.user.is_analista or request.user.is_tecnico):
        qs = qs.filter(Q(solicitante=request.user) | Q(destinatario=request.user))

    q = request.GET.get('q', '')
    status = request.GET.get('status', '')
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

    params = request.GET.copy()
    params.pop('page', None)
    query_string = params.urlencode()

    paginator = Paginator(qs, 20)
    page = paginator.get_page(request.GET.get('page'))
    return render(
        request,
        'chamados/lista.html',
        {
            'page_obj': page,
            'q': q,
            'status': status,
            'status_choices': StatusChamado.choices,
            'query_string': query_string,
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
                exc.messages[0] if exc.messages else 'NÃ£o foi possÃ­vel salvar os itens solicitados.',
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
                chamado.save()
                sincronizar_itens_solicitados(
                    chamado=chamado,
                    tipos_solicitados=form.cleaned_data.get('equipamentos_solicitados', []),
                    texto_itens=form.cleaned_data.get('outros_itens_solicitados', ''),
                )
        except ValidationError as exc:
            form.add_error(
                'outros_itens_solicitados',
                exc.messages[0] if exc.messages else 'NÃ£o foi possÃ­vel salvar os itens solicitados.',
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


@login_required
def entregar_equipamento_chamado(request, pk):
    if not _pode_gerenciar_chamado(request.user):
        messages.error(request, 'Acesso negado.')
        return redirect('dashboard')

    chamado = get_object_or_404(Chamado.objects.select_related('equipamento', 'solicitante', 'destinatario', 'responsavel', 'aprovado_por'), pk=pk)
    if chamado.status == StatusChamado.ENCERRADO:
        messages.warning(request, 'Este chamado jÃ¡ estÃ¡ concluÃ­do.')
        return redirect('detalhe_chamado', pk=chamado.pk)
    if chamado.fluxo_etapa not in {
        EtapaFluxoChamado.APROVADO_PARA_RETIRADA,
        EtapaFluxoChamado.EM_SEPARACAO,
        EtapaFluxoChamado.PRONTO_PARA_ENTREGA,
    }:
        messages.warning(request, 'O chamado ainda nÃ£o foi liberado para entrega.')
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
            form.add_error('itens_entrega', exc.messages[0] if exc.messages else 'NÃ£o foi possÃ­vel registrar a entrega.')
        else:
            messages.success(request, 'Entrega registrada e chamado atualizado.')
            return redirect('detalhe_chamado', pk=chamado.pk)

    contexto = _contexto_detalhe_chamado(request, chamado, entrega_form=form)
    return render(request, 'chamados/detalhe.html', contexto)




