from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import render
from django.utils import timezone

from .api_auth import api_auth_required


def _json_response(description='OK', schema=None):
    return {
        'description': description,
        'content': {
            'application/json': {
                'schema': schema or {'type': 'object'},
            }
        },
    }


def _error_responses():
    return {
        '400': _json_response('Requisicao invalida.'),
        '403': _json_response('Acesso negado.'),
        '429': _json_response('Limite de requisicoes excedido.'),
    }


def _path_param(name, schema_type='string', description=''):
    return {
        'name': name,
        'in': 'path',
        'required': True,
        'description': description,
        'schema': {'type': schema_type},
    }


def _query_param(name, description='', schema_type='string'):
    return {
        'name': name,
        'in': 'query',
        'required': False,
        'description': description,
        'schema': {'type': schema_type},
    }


def _operation(summary, tags, responses=None, parameters=None, request_body=None):
    operation = {
        'summary': summary,
        'tags': tags,
        'security': [{'ApiKeyAuth': []}, {'BearerAuth': []}, {'SessionAuth': []}],
        'responses': {
            '200': _json_response(),
            **_error_responses(),
            **(responses or {}),
        },
    }
    if parameters:
        operation['parameters'] = parameters
    if request_body:
        operation['requestBody'] = request_body
    return operation


def build_openapi_schema():
    item_array = {'type': 'object', 'properties': {'count': {'type': 'integer'}, 'results': {'type': 'array'}}}
    form_body = {
        'required': True,
        'content': {
            'application/x-www-form-urlencoded': {
                'schema': {'type': 'object'},
            }
        },
    }
    json_body = {
        'required': True,
        'content': {
            'application/json': {
                'schema': {'type': 'object'},
            }
        },
    }

    paths = {
        '/api/contas/me/': {
            'get': _operation('Dados do usuario autenticado', ['Contas']),
        },
        '/api/contas/usuarios/': {
            'get': _operation(
                'Lista usuarios para administradores',
                ['Contas'],
                responses={'200': _json_response(schema=item_array)},
                parameters=[_query_param('q', 'Busca por matricula, nome, email, site ou setor.')],
            ),
        },
        '/api/contas/usuarios/{id}/': {
            'get': _operation(
                'Detalha um usuario',
                ['Contas'],
                parameters=[_path_param('id', 'integer', 'ID interno do usuario.')],
            ),
        },
        '/api/chamados/': {
            'get': _operation(
                'Lista chamados visiveis ao usuario',
                ['Chamados'],
                responses={'200': _json_response(schema=item_array)},
                parameters=[_query_param('q', 'Busca textual.'), _query_param('status', 'Status do chamado.')],
            ),
        },
        '/api/chamados/{id}/': {
            'get': _operation(
                'Detalha um chamado',
                ['Chamados'],
                parameters=[_path_param('id', 'integer', 'ID do chamado.')],
            ),
        },
        '/api/chamados/painel/': {
            'get': _operation('Painel tecnico por lanes operacionais', ['Chamados']),
        },
        '/api/equipamentos/': {
            'get': _operation(
                'Lista equipamentos para equipe operacional',
                ['Equipamentos'],
                responses={'200': _json_response(schema=item_array)},
                parameters=[
                    _query_param('q', 'Busca textual.'),
                    _query_param('status', 'Status do equipamento.'),
                    _query_param('tipo', 'Tipo do equipamento.'),
                    _query_param('site', 'Filtro por site.'),
                    _query_param('limit', 'Limite de resultados.', 'integer'),
                    _query_param('offset', 'Offset de paginacao.', 'integer'),
                ],
            ),
        },
        '/api/equipamentos/{id_patrimonio}/': {
            'get': _operation(
                'Detalha um equipamento pelo patrimonio',
                ['Equipamentos'],
                parameters=[_path_param('id_patrimonio', 'string', 'Patrimonio do equipamento.')],
            ),
        },
        '/api/telemetria/ingestao/': {
            'post': _operation(
                'Ingere telemetria enviada por agente de monitoramento',
                ['Telemetria'],
                responses={'201': _json_response('Telemetria processada.')},
                request_body=json_body,
            ),
        },
        '/api/telemetria/equipamentos/{id_patrimonio}/': {
            'get': _operation(
                'Consulta eventos recentes de telemetria de um equipamento',
                ['Telemetria'],
                parameters=[_path_param('id_patrimonio', 'string', 'Patrimonio do equipamento.')],
            ),
        },
        '/api/estoque/resumo/': {
            'get': _operation('Resumo operacional do estoque', ['Estoque']),
        },
        '/api/estoque/reservas/': {
            'get': _operation('Lista reservas ativas de estoque', ['Estoque'], responses={'200': _json_response(schema=item_array)}),
            'post': _operation('Cria uma reserva de estoque', ['Estoque'], responses={'201': _json_response('Reserva criada.')}, request_body=form_body),
        },
        '/api/estoque/reservas/{id}/acao/': {
            'post': _operation(
                'Executa acao em reserva de estoque',
                ['Estoque'],
                parameters=[_path_param('id', 'integer', 'ID da reserva.')],
                request_body=form_body,
            ),
        },
        '/api/busca/': {
            'get': _operation('Busca global respeitando visibilidade do usuario', ['Dashboard'], parameters=[_query_param('q', 'Busca textual.')]),
        },
        '/api/relatorios/': {
            'get': _operation('Indicadores e graficos operacionais', ['Dashboard']),
        },
        '/api/auditoria/': {
            'get': _operation('Ultimos eventos de auditoria', ['Dashboard']),
        },
    }

    server_url = getattr(settings, 'SITE_URL', '') or '/'
    return {
        'openapi': '3.1.0',
        'info': {
            'title': f'{settings.APP_NAME} API',
            'version': '2026.1',
            'description': f'Contrato OpenAPI para integracoes internas do {settings.APP_NAME}.',
        },
        'servers': [{'url': server_url}],
        'tags': [
            {'name': 'Contas'},
            {'name': 'Dashboard'},
            {'name': 'Equipamentos'},
            {'name': 'Estoque'},
            {'name': 'Chamados'},
            {'name': 'Telemetria'},
        ],
        'components': {
            'securitySchemes': {
                'ApiKeyAuth': {'type': 'apiKey', 'in': 'header', 'name': 'X-ITAM-API-Key'},
                'BearerAuth': {'type': 'http', 'scheme': 'bearer'},
                'SessionAuth': {'type': 'apiKey', 'in': 'cookie', 'name': 'sessionid'},
            }
        },
        'paths': paths,
        'x-generated-at': timezone.now().isoformat(),
    }


@api_auth_required
def openapi_schema_view(request):
    return JsonResponse(build_openapi_schema(), json_dumps_params={'indent': 2})


@login_required
def openapi_docs_view(request):
    return render(request, 'api/docs.html', {'schema_url': '/api/schema/'})
