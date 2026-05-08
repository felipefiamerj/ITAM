from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.shortcuts import render

from equipamentos.models import Equipamento

from .models import equipamentos_em_manutencao, equipamentos_em_estoque, lotes_recentes, resumo_por_tipo


@login_required
def estoque_view(request):
    context = {
        'total_em_estoque': equipamentos_em_estoque().count(),
        'resumo_por_tipo': resumo_por_tipo(),
        'lotes': lotes_recentes(),
        'limite_alerta': settings.ITAM_ESTOQUE_ALERTA_MINIMO,
        'equipamentos_alerta': equipamentos_em_estoque().order_by('tipo', 'id_patrimonio')[:20],
        'equipamentos_total': Equipamento.objects.count(),
        'equipamentos_em_manutencao': equipamentos_em_manutencao().count(),
    }
    return render(request, 'estoque/index.html', context)
