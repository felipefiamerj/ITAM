from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from .forms import EquipamentoForm, MovimentacaoEquipamentoForm
from .models import Equipamento, StatusEquipamento


def _pode_gerenciar(user):
    return user.is_authenticated and (user.is_admin or user.is_analista or user.is_tecnico)


def _aplicar_movimentacao(equipamento, movimentacao):
    tipo = movimentacao.tipo

    if tipo == 'entrada':
        equipamento.status = StatusEquipamento.EM_ESTOQUE
        equipamento.responsavel = None
        equipamento.data_atribuicao = None
    elif tipo == 'saida':
        equipamento.status = StatusEquipamento.EM_USO
        equipamento.responsavel = movimentacao.usuario_novo or equipamento.responsavel
        equipamento.data_atribuicao = timezone.now()
    elif tipo == 'devolucao':
        equipamento.status = StatusEquipamento.EM_ESTOQUE
        equipamento.responsavel = None
        equipamento.data_atribuicao = None
    elif tipo == 'manutencao':
        equipamento.status = StatusEquipamento.EM_MANUTENCAO
        equipamento.responsavel = None
        equipamento.data_atribuicao = None
    elif tipo == 'retorno_manutencao':
        equipamento.status = StatusEquipamento.EM_ESTOQUE
        equipamento.responsavel = None
        equipamento.data_atribuicao = None
    elif tipo == 'descarte':
        equipamento.status = StatusEquipamento.DESCARTADO
        equipamento.responsavel = None
        equipamento.data_atribuicao = None
    elif tipo in {'transferencia', 'troca'} and movimentacao.usuario_novo:
        equipamento.status = StatusEquipamento.EM_USO
        equipamento.responsavel = movimentacao.usuario_novo
        equipamento.data_atribuicao = timezone.now()

    equipamento.save()


@login_required
def lista_equipamentos(request):
    qs = Equipamento.objects.select_related('responsavel', 'criado_por').order_by('-created_at')
    q = request.GET.get('q', '')
    status = request.GET.get('status', '')
    tipo = request.GET.get('tipo', '')

    if q:
        qs = qs.filter(
            Q(id_patrimonio__icontains=q)
            | Q(marca__icontains=q)
            | Q(modelo__icontains=q)
            | Q(service_tag__icontains=q)
        )
    if status:
        qs = qs.filter(status=status)
    if tipo:
        qs = qs.filter(tipo=tipo)

    params = request.GET.copy()
    params.pop('page', None)
    query_string = params.urlencode()

    paginator = Paginator(qs, 20)
    page = paginator.get_page(request.GET.get('page'))
    return render(
        request,
        'equipamentos/lista.html',
        {'page_obj': page, 'q': q, 'status': status, 'tipo': tipo, 'query_string': query_string},
    )


@login_required
def detalhe_equipamento(request, id_patrimonio):
    equipamento = get_object_or_404(
        Equipamento.objects.select_related('responsavel', 'criado_por'),
        id_patrimonio=id_patrimonio,
    )
    movimentacoes = equipamento.movimentacoes.select_related(
        'usuario_anterior',
        'usuario_novo',
        'realizado_por',
        'chamado',
    ).order_by('-created_at')

    return render(
        request,
        'equipamentos/detalhe.html',
        {
            'equipamento': equipamento,
            'movimentacoes': movimentacoes,
            'movimentacao_form': MovimentacaoEquipamentoForm(),
            'pode_gerenciar': _pode_gerenciar(request.user),
        },
    )


@login_required
def criar_equipamento(request):
    if not _pode_gerenciar(request.user):
        messages.error(request, 'Acesso negado.')
        return redirect('equipamentos')

    form = EquipamentoForm(request.POST or None, request.FILES or None)
    if form.is_valid():
        equipamento = form.save(commit=False)
        equipamento.criado_por = request.user
        equipamento.save()
        messages.success(request, 'Equipamento criado com sucesso.')
        return redirect('detalhe_equipamento', id_patrimonio=equipamento.id_patrimonio)
    return render(request, 'equipamentos/form.html', {'form': form, 'title': 'Novo equipamento'})


@login_required
def editar_equipamento(request, id_patrimonio):
    if not _pode_gerenciar(request.user):
        messages.error(request, 'Acesso negado.')
        return redirect('equipamentos')

    equipamento = get_object_or_404(Equipamento, id_patrimonio=id_patrimonio)
    form = EquipamentoForm(request.POST or None, request.FILES or None, instance=equipamento)
    if form.is_valid():
        form.save()
        messages.success(request, 'Equipamento atualizado.')
        return redirect('detalhe_equipamento', id_patrimonio=equipamento.id_patrimonio)
    return render(request, 'equipamentos/form.html', {'form': form, 'title': 'Editar equipamento', 'obj': equipamento})


@login_required
@transaction.atomic
def registrar_movimentacao(request, id_patrimonio):
    if not _pode_gerenciar(request.user):
        messages.error(request, 'Acesso negado.')
        return redirect('equipamentos')

    equipamento = get_object_or_404(Equipamento, id_patrimonio=id_patrimonio)
    form = MovimentacaoEquipamentoForm(request.POST or None)
    if form.is_valid():
        movimentacao = form.save(commit=False)
        movimentacao.equipamento = equipamento
        movimentacao.usuario_anterior = equipamento.responsavel
        movimentacao.realizado_por = request.user
        movimentacao.save()
        _aplicar_movimentacao(equipamento, movimentacao)
        messages.success(request, 'Movimentação registrada.')
        return redirect('detalhe_equipamento', id_patrimonio=equipamento.id_patrimonio)

    return render(
        request,
        'equipamentos/detalhe.html',
        {
            'equipamento': equipamento,
            'movimentacoes': equipamento.movimentacoes.select_related(
                'usuario_anterior',
                'usuario_novo',
                'realizado_por',
                'chamado',
            ).order_by('-created_at'),
            'movimentacao_form': form,
            'pode_gerenciar': _pode_gerenciar(request.user),
        },
    )
