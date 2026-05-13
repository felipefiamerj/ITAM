from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render

from equipamentos.models import Equipamento, StatusEquipamento, TipoEquipamento
from itam.charting import build_choice_chart, build_top_chart

from .models import (
    equipamentos_em_manutencao,
    equipamentos_em_estoque,
    lotes_recentes,
    resumo_por_localizacao,
    resumo_por_site,
    resumo_por_status,
    resumo_por_tipo,
)


@login_required
def estoque_view(request):
    if not request.user.is_operacional:
        return redirect('chamados')
    tipos_resumo = list(resumo_por_tipo())
    status_resumo = list(resumo_por_status())
    context = {
        'total_equipamentos': Equipamento.objects.count(),
        'total_sites': Equipamento.objects.exclude(site='').values('site').distinct().count(),
        'total_localizacoes': Equipamento.objects.exclude(site='').exclude(setor='').exclude(andar_sala='').values(
            'site', 'setor', 'andar_sala'
        ).distinct().count(),
        'total_em_estoque': equipamentos_em_estoque().count(),
        'total_em_uso': Equipamento.objects.filter(status=StatusEquipamento.EM_USO).count(),
        'total_descartados': Equipamento.objects.filter(status=StatusEquipamento.DESCARTADO).count(),
        'total_aguardando': Equipamento.objects.filter(status=StatusEquipamento.AGUARDANDO_APROVACAO).count(),
        'resumo_por_tipo': tipos_resumo,
        'resumo_por_status': status_resumo,
        'resumo_por_site': resumo_por_site(),
        'resumo_por_localizacao': resumo_por_localizacao(),
        'lotes': lotes_recentes(),
        'limite_alerta': settings.ITAM_ESTOQUE_ALERTA_MINIMO,
        'equipamentos_alerta': equipamentos_em_estoque().order_by('tipo', 'id_patrimonio')[:20],
        'equipamentos_alerta_total': Equipamento.objects.filter(score_saude__lt=70).count(),
        'equipamentos_total': Equipamento.objects.count(),
        'equipamentos_em_manutencao': equipamentos_em_manutencao().count(),
        'estoque_charts': {
            'equipamentos_por_status': build_choice_chart(
                status_resumo,
                StatusEquipamento.choices,
                'status',
            ),
            'equipamentos_por_tipo': build_top_chart(
                tipos_resumo,
                'tipo',
                label_map=dict(TipoEquipamento.choices),
                limit=8,
            ),
        },
    }
    return render(request, 'estoque/index.html', context)


