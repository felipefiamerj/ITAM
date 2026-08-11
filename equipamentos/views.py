from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET

from accounts.permissions import pode_acessar_operacao

from .forms import EquipamentoForm, ImportacaoEquipamentosCSVForm, MovimentacaoEquipamentoForm
from .models import Equipamento
from .services import aplicar_movimentacao_equipamento, importar_equipamentos_csv


def _exigir_operacional(request):
    if not pode_acessar_operacao(request.user):
        messages.error(request, 'Acesso negado.')
        return redirect('chamados')
    return None


@require_GET
def qr_equipamento_publico(request, id_patrimonio):
    equipamento = get_object_or_404(
        Equipamento.objects.select_related('responsavel'),
        id_patrimonio=id_patrimonio,
    )
    return render(
        request,
        'equipamentos/qr_publico.html',
        {
            'equipamento': equipamento,
        },
    )


@login_required
def lista_equipamentos(request):
    redirecionamento = _exigir_operacional(request)
    if redirecionamento:
        return redirecionamento

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
def importar_equipamentos_csv_view(request):
    redirecionamento = _exigir_operacional(request)
    if redirecionamento:
        return redirecionamento

    form = ImportacaoEquipamentosCSVForm(request.POST or None, request.FILES or None)
    if request.method == 'POST' and form.is_valid():
        try:
            resultado = importar_equipamentos_csv(
                form.cleaned_data['arquivo'],
                criado_por=request.user,
                descricao=form.cleaned_data.get('descricao', ''),
            )
        except ValidationError as exc:
            form.add_error('arquivo', exc.messages[0] if exc.messages else 'Não foi possível importar o CSV.')
        except Exception:
            form.add_error('arquivo', 'O arquivo não pôde ser processado. Verifique o formato e tente novamente.')
        else:
            mensagens = [
                f"{resultado['criados']} criados",
                f"{resultado['atualizados']} atualizados",
                f"{resultado['erros']} linhas com erro",
            ]
            if resultado['erros']:
                messages.warning(request, f"Importação concluída com avisos: {', '.join(mensagens)}.")
            else:
                messages.success(request, f"Importação concluída: {', '.join(mensagens)}.")
            return redirect('estoque')

    return render(
        request,
        'equipamentos/importar_csv.html',
        {
            'form': form,
            'titulo': 'Importar equipamentos por CSV',
        },
    )


@login_required
def detalhe_equipamento(request, id_patrimonio):
    redirecionamento = _exigir_operacional(request)
    if redirecionamento:
        return redirecionamento

    equipamento = get_object_or_404(
        Equipamento.objects.select_related('responsavel', 'criado_por'),
        id_patrimonio=id_patrimonio,
    )
    if not equipamento.qrcode_atualizado:
        equipamento._gerar_qrcode()
        equipamento.save(update_fields=['qr_code', 'updated_at'])
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
            'pode_gerenciar': pode_acessar_operacao(request.user),
        },
    )


@login_required
def criar_equipamento(request):
    redirecionamento = _exigir_operacional(request)
    if redirecionamento:
        return redirecionamento

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
    redirecionamento = _exigir_operacional(request)
    if redirecionamento:
        return redirecionamento

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
    redirecionamento = _exigir_operacional(request)
    if redirecionamento:
        return redirecionamento

    equipamento = get_object_or_404(Equipamento, id_patrimonio=id_patrimonio)
    form = MovimentacaoEquipamentoForm(request.POST or None)
    if form.is_valid():
        movimentacao = form.save(commit=False)
        movimentacao.equipamento = equipamento
        movimentacao.usuario_anterior = equipamento.responsavel
        movimentacao.realizado_por = request.user
        movimentacao.save()
        aplicar_movimentacao_equipamento(equipamento, movimentacao)
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
            'pode_gerenciar': pode_acessar_operacao(request.user),
        },
    )
