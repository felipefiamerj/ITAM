from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render

from .forms import ChamadoCreateForm, ChamadoUpdateForm
from .models import Chamado


@login_required
def lista_chamados(request):
    qs = Chamado.objects.select_related('equipamento', 'solicitante', 'responsavel').order_by('-created_at')

    if not (request.user.is_admin or request.user.is_analista or request.user.is_tecnico):
        qs = qs.filter(solicitante=request.user)

    q = request.GET.get('q', '')
    status = request.GET.get('status', '')
    if q:
        qs = qs.filter(
            Q(titulo__icontains=q)
            | Q(descricao__icontains=q)
            | Q(equipamento__id_patrimonio__icontains=q)
            | Q(solicitante__matricula__icontains=q)
        )
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
        {'page_obj': page, 'q': q, 'status': status, 'query_string': query_string},
    )


@login_required
def criar_chamado(request):
    form = ChamadoCreateForm(request.POST or None)
    if form.is_valid():
        chamado = form.save(commit=False)
        chamado.solicitante = request.user
        chamado.save()
        messages.success(request, 'Chamado criado com sucesso.')
        return redirect('detalhe_chamado', pk=chamado.pk)
    return render(request, 'chamados/form.html', {'form': form, 'title': 'Novo chamado'})


@login_required
def detalhe_chamado(request, pk):
    chamado = get_object_or_404(Chamado.objects.select_related('equipamento', 'solicitante', 'responsavel'), pk=pk)
    if not (request.user.is_admin or request.user.is_analista or request.user.is_tecnico or chamado.solicitante_id == request.user.id):
        messages.error(request, 'Acesso negado.')
        return redirect('dashboard')
    return render(request, 'chamados/detalhe.html', {'chamado': chamado})


@login_required
def editar_chamado(request, pk):
    chamado = get_object_or_404(Chamado, pk=pk)
    if not (request.user.is_admin or request.user.is_analista or request.user.is_tecnico or chamado.solicitante_id == request.user.id):
        messages.error(request, 'Acesso negado.')
        return redirect('dashboard')

    form = ChamadoUpdateForm(request.POST or None, instance=chamado)
    if form.is_valid():
        chamado = form.save(commit=False)
        chamado.save()
        messages.success(request, 'Chamado atualizado.')
        return redirect('detalhe_chamado', pk=chamado.pk)
    return render(request, 'chamados/form.html', {'form': form, 'title': 'Editar chamado', 'obj': chamado})
