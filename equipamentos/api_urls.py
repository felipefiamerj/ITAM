import json

from django.core.exceptions import PermissionDenied, ValidationError
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.urls import path
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from itam.api_auth import api_auth_required

from .models import Equipamento
from .search import aplicar_filtro_busca_equipamentos
from .telemetria import processar_pacote_telemetria


def _exigir_operacional(request):
    if not getattr(request.user, 'is_authenticated', False) or not getattr(request.user, 'is_operacional', False):
        raise PermissionDenied


def _cliente_ip(request):
    forwarded = request.headers.get('X-Forwarded-For')
    if forwarded:
        return forwarded.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR')


def _token_do_request(request, payload):
    header_token = request.headers.get('X-ITAM-AGENT-TOKEN', '').strip()
    if header_token:
        return header_token

    authorization = request.headers.get('Authorization', '').strip()
    if authorization.lower().startswith('bearer '):
        return authorization[7:].strip()

    return (payload.get('agent_token') or payload.get('token') or '').strip()


def _json_body(request):
    if not request.body:
        return {}
    try:
        return json.loads(request.body.decode('utf-8'))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValidationError('JSON inválido.') from exc


def _serializar_evento(evento):
    return {
        'tipo': evento.tipo,
        'tipo_display': evento.get_tipo_display(),
        'severidade': evento.severidade,
        'severidade_display': evento.get_severidade_display(),
        'mensagem': evento.mensagem,
        'payload': evento.payload,
        'created_at': timezone.localtime(evento.created_at).strftime('%d/%m/%Y %H:%M:%S'),
        'agente': evento.agente.nome if evento.agente else None,
        'host_name': evento.agente.host_name if evento.agente else None,
    }


@api_auth_required
@require_GET
def equipamentos_api(request):
    _exigir_operacional(request)

    qs = Equipamento.objects.select_related('responsavel').order_by('id_patrimonio')

    q = request.GET.get('q', '').strip()
    status = request.GET.get('status', '').strip()
    tipo = request.GET.get('tipo', '').strip()
    site = request.GET.get('site', '').strip()
    try:
        limit = int(request.GET.get('limit', '200'))
    except ValueError:
        limit = 200
    try:
        offset = int(request.GET.get('offset', '0'))
    except ValueError:
        offset = 0

    limit = max(1, min(limit, 200))
    offset = max(0, offset)

    qs = aplicar_filtro_busca_equipamentos(qs, q)
    if status:
        qs = qs.filter(status=status)
    if tipo:
        qs = qs.filter(tipo=tipo)
    if site:
        qs = qs.filter(site__icontains=site)

    total = qs.count()
    equipamentos = qs[offset: offset + limit]
    next_offset = offset + len(equipamentos)
    data = [
        {
            'pk': equipamento.pk,
            'id_patrimonio': equipamento.id_patrimonio,
            'tipo': equipamento.tipo,
            'tipo_display': equipamento.tipo_display,
            'marca': equipamento.marca,
            'modelo': equipamento.modelo,
            'status': equipamento.status,
            'status_display': equipamento.get_status_display(),
            'responsavel': equipamento.responsavel.nome_completo if equipamento.responsavel else None,
            'score_saude': equipamento.score_saude,
            'site': equipamento.site,
            'setor': equipamento.setor,
            'andar_sala': equipamento.andar_sala,
            'localizacao': equipamento.localizacao_resumida,
        }
        for equipamento in equipamentos
    ]
    return JsonResponse(
        {
            'count': total,
            'offset': offset,
            'limit': limit,
            'next_offset': next_offset if next_offset < total else None,
            'has_more': next_offset < total,
            'results': data,
        }
    )


@api_auth_required
@require_GET
def equipamento_api(request, id_patrimonio):
    _exigir_operacional(request)

    equipamento = get_object_or_404(Equipamento.objects.select_related('responsavel'), id_patrimonio=id_patrimonio)
    data = {
        'pk': equipamento.pk,
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
        'site': equipamento.site,
        'setor': equipamento.setor,
        'andar_sala': equipamento.andar_sala,
        'localizacao': equipamento.localizacao_resumida,
    }
    return JsonResponse(data)


@csrf_exempt
@require_POST
def telemetria_ingest_api(request):
    try:
        payload = _json_body(request)
        token = _token_do_request(request, payload)
        if token:
            payload['agent_token'] = token
        resultado = processar_pacote_telemetria(payload, remote_ip=_cliente_ip(request))
    except ValidationError as exc:
        return JsonResponse({'ok': False, 'errors': exc.messages or ['Payload inválido.']}, status=400)
    except PermissionDenied as exc:
        return JsonResponse({'ok': False, 'errors': [str(exc) or 'Acesso negado.']}, status=403)

    return JsonResponse(
        {
            'ok': True,
            'agente': resultado['agente'].nome,
            'processados': resultado['processados'],
            'alertas': resultado['alertas'],
            'erros': resultado['erros'],
            'stale_minutes': resultado['stale_minutes'],
        }
    )


@api_auth_required
@require_GET
def telemetria_equipamento_api(request, id_patrimonio):
    _exigir_operacional(request)

    equipamento = get_object_or_404(
        Equipamento.objects.select_related('last_telemetria_agente'),
        id_patrimonio=id_patrimonio,
    )
    eventos = equipamento.telemetria_eventos.select_related('agente').order_by('-created_at')[:20]
    return JsonResponse(
        {
            'id_patrimonio': equipamento.id_patrimonio,
            'monitoramento_ativo': equipamento.monitoramento_ativo,
            'monitoramento_status': equipamento.monitoramento_status,
            'monitoramento_status_display': equipamento.get_monitoramento_status_display(),
            'monitoramento_em_atraso': equipamento.monitoramento_em_atraso,
            'last_seen_at': timezone.localtime(equipamento.last_seen_at).strftime('%d/%m/%Y %H:%M:%S')
            if equipamento.last_seen_at
            else None,
            'last_telemetria_agente': equipamento.last_telemetria_agente.nome if equipamento.last_telemetria_agente else None,
            'score_saude': equipamento.score_saude,
            'eventos': [_serializar_evento(evento) for evento in eventos],
        }
    )


urlpatterns = [
    path('equipamentos/', equipamentos_api, name='api_equipamentos'),
    path('equipamentos/<str:id_patrimonio>/', equipamento_api, name='api_equipamento'),
    path('telemetria/ingestao/', telemetria_ingest_api, name='api_telemetria_ingestao'),
    path('telemetria/equipamentos/<str:id_patrimonio>/', telemetria_equipamento_api, name='api_telemetria_equipamento'),
]
