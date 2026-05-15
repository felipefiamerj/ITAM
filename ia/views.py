from datetime import timedelta

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.shortcuts import redirect, render
from django.utils import timezone

from equipamentos.models import Equipamento

from .monitoring import recalcular_scores, resumo_monitoramento
from .recommendations import build_operational_copilot


def _exigir_operacional(request):
    if not request.user.is_operacional:
        return redirect('chamados')
    return None


def _processar_recalculo(request):
    if request.GET.get('recalcular'):
        atualizados = recalcular_scores()
        messages.success(request, f'{atualizados} equipamentos recalculados.')


@login_required
def copilot_view(request):
    redirecionamento = _exigir_operacional(request)
    if redirecionamento:
        return redirecionamento

    _processar_recalculo(request)
    contexto = build_operational_copilot(request.user)
    return render(request, 'ia/index.html', contexto)


@login_required
def monitoring_view(request):
    redirecionamento = _exigir_operacional(request)
    if redirecionamento:
        return redirecionamento

    _processar_recalculo(request)

    resumo = resumo_monitoramento()
    limite = timezone.now() - timedelta(minutes=getattr(settings, 'ITAM_HEARTBEAT_STALE_MINUTES', 10))
    equipamentos_alerta = Equipamento.objects.order_by('score_saude', '-updated_at')[:10]
    equipamentos_sem_sinal = (
        Equipamento.objects.filter(monitoramento_ativo=True)
        .filter(Q(last_seen_at__isnull=True) | Q(last_seen_at__lt=limite))
        .order_by('last_seen_at', 'id_patrimonio')[:10]
    )

    return render(
        request,
        'ia/monitoring.html',
        {
            **resumo,
            'equipamentos_alerta': equipamentos_alerta,
            'equipamentos_sem_sinal': equipamentos_sem_sinal,
        },
    )
