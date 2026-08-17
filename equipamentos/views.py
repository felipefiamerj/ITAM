from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Prefetch, Q, Sum
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_http_methods

from accounts.permissions import pode_acessar_operacao

from .forms import ESPECIFICACOES_POR_TIPO, EquipamentoForm, ImportacaoEquipamentosCSVForm, MovimentacaoEquipamentoForm
from .lifecycle import lifecycle_assets_queryset, sync_lifecycle_alerts, sync_lifecycle_for_equipment
from .models import (
    AlertaCicloVida,
    DivergenciaInventario,
    Equipamento,
    MovimentacaoEquipamento,
    TipoAlertaCicloVida,
)
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
            'alertas_ciclo_vida': equipamento.alertas_ciclo_vida.filter(ativo=True),
            'divergencias_inventario': equipamento.divergencias_inventario.filter(ativa=True),
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
        sync_lifecycle_for_equipment(equipamento)
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
        sync_lifecycle_for_equipment(equipamento)
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
        sync_lifecycle_for_equipment(equipamento)
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
            'alertas_ciclo_vida': equipamento.alertas_ciclo_vida.filter(ativo=True),
            'divergencias_inventario': equipamento.divergencias_inventario.filter(ativa=True),
            'pode_gerenciar': pode_acessar_operacao(request.user),
        },
    )


@login_required
def lifecycle_dashboard(request):
    redirecionamento = _exigir_operacional(request)
    if redirecionamento:
        return redirecionamento

    if request.method == 'POST' and request.POST.get('action') == 'refresh_alerts':
        result = sync_lifecycle_alerts()
        messages.success(
            request,
            (
                f"Ciclo de vida atualizado: {result['processed']} ativo(s) analisado(s), "
                f"{result['activated']} alerta(s) aberto(s) e {result['resolved']} resolvido(s)."
            ),
        )
        return redirect('lifecycle_dashboard')

    active_alerts = AlertaCicloVida.objects.filter(ativo=True)
    alert_prefetch = Prefetch(
        'alertas_ciclo_vida',
        queryset=active_alerts.order_by('severidade', 'tipo'),
        to_attr='alertas_ciclo_vida_ativos',
    )
    assets = lifecycle_assets_queryset(
        Equipamento.objects.filter(alertas_ciclo_vida__ativo=True)
        .select_related('responsavel')
        .prefetch_related(alert_prefetch)
        .distinct()
    ).order_by('score_saude', 'id_patrimonio')
    query = request.GET.get('q', '').strip()
    if query:
        assets = assets.filter(
            Q(id_patrimonio__icontains=query)
            | Q(marca__icontains=query)
            | Q(modelo__icontains=query)
            | Q(responsavel__first_name__icontains=query)
            | Q(responsavel__last_name__icontains=query)
        )

    paginator = Paginator(assets, 15)
    page = paginator.get_page(request.GET.get('page'))
    total_maintenance_cost = (
        MovimentacaoEquipamento.objects.aggregate(total=Sum('custo_manutencao'))['total'] or 0
    )
    data_incomplete = Equipamento.objects.exclude(status='descartado').filter(
        Q(data_aquisicao__isnull=True)
        | Q(valor_aquisicao__isnull=True)
        | Q(fornecedor='')
        | Q(garantia_ate__isnull=True)
    ).count()
    context = {
        'page_obj': page,
        'q': query,
        'active_alert_count': active_alerts.count(),
        'critical_alert_count': active_alerts.filter(severidade='critical').count(),
        'warranty_alert_count': active_alerts.filter(tipo=TipoAlertaCicloVida.GARANTIA).count(),
        'replacement_alert_count': active_alerts.filter(tipo=TipoAlertaCicloVida.SUBSTITUICAO).count(),
        'inventory_divergence_count': DivergenciaInventario.objects.filter(ativa=True).count(),
        'data_incomplete_count': data_incomplete,
        'total_maintenance_cost': total_maintenance_cost,
        'recent_maintenance': MovimentacaoEquipamento.objects.filter(custo_manutencao__isnull=False)
        .select_related('equipamento', 'realizado_por')
        .order_by('-created_at')[:10],
    }
    return render(request, 'equipamentos/lifecycle.html', context)


@require_http_methods(['GET', 'POST'])
@login_required
def obter_campos_especificacoes(request):
    """Retorna os campos específicos para um tipo de equipamento"""
    tipo = request.GET.get('tipo') or request.POST.get('tipo')

    if not tipo:
        return JsonResponse({'erro': 'Tipo de equipamento não informado'}, status=400)

    especificacoes = ESPECIFICACOES_POR_TIPO.get(tipo, [])

    return JsonResponse({
        'tipo': tipo,
        'especificacoes': especificacoes,
    })
