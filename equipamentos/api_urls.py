from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.urls import path

from .models import Equipamento


def equipamentos_api(request):
    equipamentos = Equipamento.objects.select_related('responsavel').order_by('id_patrimonio')[:200]
    data = [
        {
            'id_patrimonio': equipamento.id_patrimonio,
            'tipo': equipamento.tipo,
            'tipo_display': equipamento.tipo_display,
            'marca': equipamento.marca,
            'modelo': equipamento.modelo,
            'status': equipamento.status,
            'status_display': equipamento.get_status_display(),
            'responsavel': equipamento.responsavel.nome_completo if equipamento.responsavel else None,
            'score_saude': equipamento.score_saude,
        }
        for equipamento in equipamentos
    ]
    return JsonResponse({'results': data})


def equipamento_api(request, id_patrimonio):
    equipamento = get_object_or_404(Equipamento.objects.select_related('responsavel'), id_patrimonio=id_patrimonio)
    data = {
        'id_patrimonio': equipamento.id_patrimonio,
        'tipo': equipamento.tipo,
        'tipo_display': equipamento.tipo_display,
        'marca': equipamento.marca,
        'modelo': equipamento.modelo,
        'service_tag': equipamento.service_tag,
        'imei': equipamento.imei,
        'numero_serie': equipamento.numero_serie,
        'status': equipamento.status,
        'status_display': equipamento.get_status_display(),
        'condicao': equipamento.condicao,
        'condicao_display': equipamento.get_condicao_display(),
        'responsavel': equipamento.responsavel.nome_completo if equipamento.responsavel else None,
        'score_saude': equipamento.score_saude,
    }
    return JsonResponse(data)


urlpatterns = [
    path('equipamentos/', equipamentos_api, name='api_equipamentos'),
    path('equipamentos/<str:id_patrimonio>/', equipamento_api, name='api_equipamento'),
]
