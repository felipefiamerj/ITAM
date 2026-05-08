from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import render

from equipamentos.models import Equipamento

from .monitoring import recalcular_scores, resumo_monitoramento


@login_required
def monitoring_view(request):
    if request.GET.get('recalcular'):
        atualizados = recalcular_scores()
        messages.success(request, f'{atualizados} equipamentos recalculados.')

    resumo = resumo_monitoramento()
    equipamentos_alerta = Equipamento.objects.order_by('score_saude', '-updated_at')[:10]

    return render(
        request,
        'ia/monitoring.html',
        {
            **resumo,
            'equipamentos_alerta': equipamentos_alerta,
        },
    )
