from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.urls import path
from django.utils import timezone

from equipamentos.models import Equipamento, StatusEquipamento

from .models import lotes_recentes, resumo_por_localizacao, resumo_por_site, resumo_por_status


def _status_label(status_value):
    for value, label in StatusEquipamento.choices:
        if value == status_value:
            return label
    return status_value


def _serialize_lote(lote):
    return {
        'id': lote.pk,
        'descricao': lote.descricao or lote.arquivo.name,
        'status': lote.status,
        'status_display': lote.get_status_display(),
        'total_itens': lote.total_itens,
        'itens_importados': lote.itens_importados,
        'itens_com_erro': lote.itens_com_erro,
        'created_at': timezone.localtime(lote.created_at).strftime('%d/%m/%Y %H:%M'),
    }


def _serialize_equipment(equipamento):
    return {
        'id_patrimonio': equipamento.id_patrimonio,
        'tipo_display': equipamento.tipo_display,
        'status': equipamento.status,
        'status_display': equipamento.get_status_display(),
        'score_saude': equipamento.score_saude,
        'localizacao': equipamento.localizacao_resumida,
    }


@login_required
def estoque_resumo_api(request):
    total_equipamentos = Equipamento.objects.count()
    por_status = list(resumo_por_status())
    por_site = list(resumo_por_site())
    por_localizacao = list(resumo_por_localizacao())
    lotes = list(lotes_recentes())
    alertas = list(
        Equipamento.objects.filter(score_saude__lt=70)
        .select_related('responsavel')
        .order_by('score_saude', 'id_patrimonio')[:12]
    )

    return JsonResponse(
        {
            'updated_at': timezone.localtime(timezone.now()).strftime('%d/%m/%Y %H:%M:%S'),
            'totais': {
                'total_equipamentos': total_equipamentos,
                'em_uso': Equipamento.objects.filter(status=StatusEquipamento.EM_USO).count(),
                'em_estoque': Equipamento.objects.filter(status=StatusEquipamento.EM_ESTOQUE).count(),
                'em_manutencao': Equipamento.objects.filter(status=StatusEquipamento.EM_MANUTENCAO).count(),
                'descartados': Equipamento.objects.filter(status=StatusEquipamento.DESCARTADO).count(),
                'aguardando': Equipamento.objects.filter(status=StatusEquipamento.AGUARDANDO_APROVACAO).count(),
                'alertas': Equipamento.objects.filter(score_saude__lt=70).count(),
            },
            'por_status': [
                {'status': item['status'], 'label': _status_label(item['status']), 'total': item['total']}
                for item in por_status
            ],
            'por_site': [
                {
                    'site': item['site'] or 'Sem site',
                    'total': item['total'],
                    'em_uso': item['em_uso'],
                    'em_estoque': item['em_estoque'],
                    'em_manutencao': item['em_manutencao'],
                    'descartado': item['descartado'],
                }
                for item in por_site
            ],
            'por_localizacao': [
                {
                    'site': item['site'] or 'Sem site',
                    'setor': item['setor'] or 'Sem setor',
                    'andar_sala': item['andar_sala'] or 'Sem andar/sala',
                    'label': ' · '.join(
                        [parte for parte in [item['site'], item['setor'], item['andar_sala']] if parte]
                    ),
                    'total': item['total'],
                    'em_uso': item['em_uso'],
                    'em_estoque': item['em_estoque'],
                    'em_manutencao': item['em_manutencao'],
                    'descartado': item['descartado'],
                }
                for item in por_localizacao
            ],
            'lotes': [_serialize_lote(lote) for lote in lotes],
            'alertas': [_serialize_equipment(equipamento) for equipamento in alertas],
        }
    )


urlpatterns = [
    path('resumo/', estoque_resumo_api, name='api_estoque_resumo'),
]
