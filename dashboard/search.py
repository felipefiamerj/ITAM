from __future__ import annotations

import os
from urllib.parse import urlencode

from django.db.models import Case, IntegerField, Q, Value, When
from django.urls import reverse
from django.utils import timezone

from accounts.models import Usuario
from chamados.models import Chamado
from equipamentos.models import EntradaLote, Equipamento


SEARCH_LIMITS = {
    'equipamentos': 6,
    'chamados': 6,
    'usuarios': 6,
    'lotes': 4,
}

RECENT_LIMITS = {
    'equipamentos': 5,
    'chamados': 5,
    'usuarios': 5,
    'lotes': 4,
}


def normalize_query(raw_query):
    return ' '.join((raw_query or '').split()).strip()


def _as_int(raw_query):
    candidate = raw_query.lstrip('#').strip()
    return int(candidate) if candidate.isdigit() else None


def _format_datetime(value):
    if not value:
        return '-'
    return timezone.localtime(value).strftime('%d/%m/%Y %H:%M')


def _build_lookup_condition(query, lookups, numeric_pk=None):
    condition = Q()
    if numeric_pk is not None:
        condition |= Q(pk=numeric_pk)

    for lookup in lookups:
        condition |= Q(**{lookup: query})

    return condition


def _build_relevance_expression(query, rules, numeric_pk=None):
    whens = []
    if numeric_pk is not None:
        whens.append(When(pk=numeric_pk, then=Value(1200)))

    for lookup, score in rules:
        whens.append(When(**{lookup: query}, then=Value(score)))

    return Case(*whens, default=Value(0), output_field=IntegerField())


def _status_badge_class(status_value):
    mapping = {
        'em_uso': 'text-bg-info',
        'em_estoque': 'text-bg-primary',
        'em_manutencao': 'text-bg-warning',
        'descartado': 'text-bg-secondary',
        'aguardando': 'text-bg-warning',
        'fila': 'text-bg-warning',
        'em_atendimento': 'text-bg-info',
        'aguardando_atendimento': 'text-bg-secondary',
        'encerrado': 'text-bg-success',
        'aberto': 'text-bg-warning',
        'em_analise': 'text-bg-warning',
        'aguardando_usuario': 'text-bg-secondary',
        'resolvido': 'text-bg-success',
        'fechado': 'text-bg-success',
        'pendente': 'text-bg-warning',
        'processando': 'text-bg-warning',
        'concluido': 'text-bg-success',
        'erro': 'text-bg-danger',
    }
    return mapping.get(status_value, 'badge-soft')


def _user_status_badge_class(usuario):
    if usuario.solicitacao_pendente:
        return 'text-bg-warning'
    if usuario.ativo:
        return 'text-bg-success'
    return 'text-bg-secondary'


def _quick_actions(user):
    if user.is_solicitante:
        return [
            {
                'label': 'Chamados',
                'description': 'Acompanhe suas solicitacoes',
                'icon': 'fa-ticket-simple',
                'url': reverse('chamados'),
            },
            {
                'label': 'Novo chamado',
                'description': 'Abra uma nova solicitacao',
                'icon': 'fa-circle-plus',
                'url': reverse('criar_chamado'),
            },
            {
                'label': 'Meu perfil',
                'description': 'Dados da sua conta',
                'icon': 'fa-user',
                'url': reverse('meu_perfil'),
            },
        ]

    actions = []

    if user.is_admin or user.is_analista or user.is_tecnico:
        actions.extend(
            [
                {
                    'label': 'Novo equipamento',
                    'description': 'Cadastrar um ativo manualmente',
                    'icon': 'fa-plus',
                    'url': reverse('criar_equipamento'),
                },
                {
                    'label': 'Importar CSV',
                    'description': 'Carregar uma base em lote',
                    'icon': 'fa-file-arrow-up',
                    'url': reverse('importar_equipamentos_csv'),
                },
                {
                    'label': 'Novo chamado',
                    'description': 'Abrir uma nova solicitação',
                    'icon': 'fa-circle-plus',
                    'url': reverse('criar_chamado'),
                },
            ]
        )

    if user.is_admin:
        actions.extend(
            [
                {
                    'label': 'Aprovações',
                    'description': 'Analisar solicitações pendentes',
                    'icon': 'fa-user-clock',
                    'url': reverse('usuarios_pendentes'),
                },
                {
                    'label': 'Usuários',
                    'description': 'Gerenciar contas e perfis',
                    'icon': 'fa-users-gear',
                    'url': reverse('lista_usuarios'),
                },
                {
                    'label': 'Novo usuário',
                    'description': 'Criar uma conta manualmente',
                    'icon': 'fa-user-plus',
                    'url': reverse('criar_usuario'),
                },
            ]
        )

    return actions


def _equipment_item(equipamento):
    descricao = ' Â· '.join(
        parte
        for parte in [
            equipamento.tipo_display,
            ' '.join(
                parte
                for parte in [
                    equipamento.marca.strip() if equipamento.marca else '',
                    equipamento.modelo.strip() if equipamento.modelo else '',
                ]
                if parte
            ),
        ]
        if parte
    )

    meta_parts = [equipamento.localizacao_resumida]
    if equipamento.responsavel:
        meta_parts.append(f'ResponsÃ¡vel: {equipamento.responsavel.nome_completo}')

    return {
        'title': equipamento.id_patrimonio,
        'subtitle': descricao or equipamento.get_tipo_display(),
        'meta': ' Â· '.join(meta_parts),
        'url': reverse('detalhe_equipamento', kwargs={'id_patrimonio': equipamento.id_patrimonio}),
        'icon': 'fa-boxes-stacked',
        'badge': equipamento.get_status_display(),
        'badge_class': _status_badge_class(equipamento.status),
    }


def _chamado_item(chamado):
    equipamento = chamado.equipamento.id_patrimonio if chamado.equipamento else 'Sem equipamento'
    if chamado.responsavel:
        responsavel = f'{chamado.responsavel.nome_completo} ({chamado.responsavel.papel_fluxo})'
    else:
        responsavel = 'Sem responsável'

    return {
        'title': f'#{chamado.pk} Â· {chamado.titulo}',
        'subtitle': f'{chamado.fluxo_etapa_label} Â· {chamado.get_status_display()} Â· {chamado.get_prioridade_display()}',
        'meta': f'Colaborador: {chamado.destinatario_nome_completo} · Solicitante: {chamado.solicitante.nome_completo} · Responsável: {responsavel} · Fluxo: {chamado.fluxo_etapa_label} · {equipamento}',
        'url': reverse('detalhe_chamado', kwargs={'pk': chamado.pk}),
        'icon': 'fa-ticket-simple',
        'badge': chamado.get_status_display(),
        'badge_class': _status_badge_class(chamado.status),
    }


def _usuario_item(usuario):
    meta_parts = [
        usuario.site or 'Sem site',
        usuario.setor or 'Sem setor',
        usuario.andar_sala or 'Sem andar/sala',
    ]

    return {
        'title': usuario.nome_completo,
        'subtitle': usuario.matricula,
        'meta': ' Â· '.join(meta_parts),
        'url': reverse('perfil_usuario', kwargs={'pk': usuario.pk}),
        'icon': 'fa-user-gear',
        'badge': usuario.status_acesso,
        'badge_class': _user_status_badge_class(usuario),
    }


def _lote_item(lote):
    descricao = lote.descricao or os.path.basename(lote.arquivo.name or 'lote')
    meta_parts = [
        f'{lote.itens_importados}/{lote.total_itens} itens importados',
        _format_datetime(lote.created_at),
    ]
    if lote.criado_por:
        meta_parts.append(f'Por {lote.criado_por.nome_completo}')

    return {
        'title': descricao,
        'subtitle': lote.get_status_display(),
        'meta': ' Â· '.join(meta_parts),
        'url': reverse('estoque'),
        'icon': 'fa-warehouse',
        'badge': lote.get_status_display(),
        'badge_class': _status_badge_class(lote.status),
    }


def _equipment_group(user, query=None):
    qs = Equipamento.objects.select_related('responsavel', 'criado_por')
    if query:
        numeric_pk = _as_int(query)
        lookups = [
            'id_patrimonio__iexact',
            'id_patrimonio__istartswith',
            'service_tag__iexact',
            'service_tag__istartswith',
            'numero_serie__iexact',
            'numero_serie__istartswith',
            'marca__iexact',
            'marca__istartswith',
            'modelo__iexact',
            'modelo__istartswith',
            'tipo__icontains',
            'tipo_outro__icontains',
            'marca__icontains',
            'modelo__icontains',
            'site__icontains',
            'setor__icontains',
            'andar_sala__icontains',
            'descricao__icontains',
            'responsavel__matricula__icontains',
            'responsavel__first_name__icontains',
            'responsavel__last_name__icontains',
            'criado_por__matricula__icontains',
        ]
        qs = qs.filter(_build_lookup_condition(query, lookups, numeric_pk=numeric_pk))
        qs = qs.annotate(
            _search_relevance=_build_relevance_expression(
                query,
                [
                    ('id_patrimonio__iexact', 1000),
                    ('service_tag__iexact', 990),
                    ('numero_serie__iexact', 980),
                    ('id_patrimonio__istartswith', 970),
                    ('service_tag__istartswith', 960),
                    ('numero_serie__istartswith', 950),
                    ('marca__iexact', 940),
                    ('modelo__iexact', 930),
                    ('marca__istartswith', 920),
                    ('modelo__istartswith', 910),
                    ('tipo_outro__iexact', 900),
                    ('tipo_outro__istartswith', 890),
                    ('marca__icontains', 860),
                    ('modelo__icontains', 850),
                    ('tipo__icontains', 840),
                    ('tipo_outro__icontains', 830),
                    ('site__icontains', 820),
                    ('setor__icontains', 810),
                    ('andar_sala__icontains', 800),
                    ('descricao__icontains', 790),
                    ('responsavel__matricula__icontains', 780),
                    ('criado_por__matricula__icontains', 770),
                ],
                numeric_pk=numeric_pk,
            )
        ).order_by('-_search_relevance', '-created_at', 'id_patrimonio')
        return {
            'key': 'equipamentos',
            'label': 'Equipamentos',
            'icon': 'fa-boxes-stacked',
            'description': 'Ativos, localizaÃ§Ã£o e responsÃ¡vel atual.',
            'count': qs.count(),
            'items': [_equipment_item(equipamento) for equipamento in qs[:SEARCH_LIMITS['equipamentos']]],
            'see_all_url': reverse('equipamentos') + (f'?{urlencode({"q": query})}' if query else ''),
            'see_all_label': 'Abrir lista',
        }

    itens = list(Equipamento.objects.select_related('responsavel', 'criado_por').order_by('-created_at')[:RECENT_LIMITS['equipamentos']])
    return {
        'key': 'equipamentos',
        'label': 'Equipamentos recentes',
        'icon': 'fa-boxes-stacked',
        'description': 'Ãšltimos ativos cadastrados.',
        'count': len(itens),
        'items': [_equipment_item(equipamento) for equipamento in itens],
        'see_all_url': reverse('equipamentos'),
        'see_all_label': 'Abrir lista',
    }


def _chamados_queryset(user):
    qs = Chamado.objects.select_related('equipamento', 'solicitante', 'destinatario', 'responsavel')
    if user.is_admin or user.is_analista or user.is_tecnico:
        return qs
    return qs.filter(Q(solicitante=user) | Q(destinatario=user))


def _chamado_group(user, query=None):
    qs = _chamados_queryset(user)
    if query:
        numeric_pk = _as_int(query)
        lookups = [
            'titulo__iexact',
            'titulo__istartswith',
            'descricao__icontains',
            'solucao__icontains',
            'status__icontains',
            'prioridade__icontains',
            'equipamento__id_patrimonio__icontains',
            'solicitante__matricula__icontains',
            'solicitante__first_name__icontains',
            'solicitante__last_name__icontains',
            'destinatario__matricula__icontains',
            'destinatario__first_name__icontains',
            'destinatario__last_name__icontains',
            'responsavel__matricula__icontains',
            'responsavel__first_name__icontains',
            'responsavel__last_name__icontains',
        ]
        qs = qs.filter(_build_lookup_condition(query, lookups, numeric_pk=numeric_pk))
        qs = qs.annotate(
            _search_relevance=_build_relevance_expression(
                query,
                [
                    ('titulo__iexact', 1000),
                    ('titulo__istartswith', 990),
                    ('descricao__icontains', 940),
                    ('solucao__icontains', 930),
                    ('equipamento__id_patrimonio__icontains', 920),
                    ('solicitante__matricula__icontains', 910),
                    ('destinatario__matricula__icontains', 905),
                    ('responsavel__matricula__icontains', 900),
                    ('status__icontains', 890),
                    ('prioridade__icontains', 880),
                ],
                numeric_pk=numeric_pk,
            )
        ).order_by('-_search_relevance', '-updated_at', '-created_at', 'pk')
        return {
            'key': 'chamados',
            'label': 'Chamados',
            'icon': 'fa-ticket-simple',
            'description': 'SolicitaÃ§Ãµes, histÃ³rico e responsÃ¡veis.',
            'count': qs.count(),
            'items': [_chamado_item(chamado) for chamado in qs[:SEARCH_LIMITS['chamados']]],
            'see_all_url': reverse('chamados') + (f'?{urlencode({"q": query})}' if query else ''),
            'see_all_label': 'Abrir lista',
        }

    itens = list(qs.order_by('-created_at')[:RECENT_LIMITS['chamados']])
    return {
        'key': 'chamados',
        'label': 'Chamados recentes',
        'icon': 'fa-ticket-simple',
        'description': 'Ãšltimas solicitaÃ§Ãµes registradas.',
        'count': len(itens),
        'items': [_chamado_item(chamado) for chamado in itens],
        'see_all_url': reverse('chamados'),
        'see_all_label': 'Abrir lista',
    }


def _usuarios_queryset(user):
    if user.is_admin:
        return Usuario.objects.select_related('gestor', 'aprovado_por')
    return Usuario.objects.select_related('gestor', 'aprovado_por').filter(pk=user.pk)


def _usuario_group(user, query=None):
    if not user.is_admin and not query:
        return None

    qs = _usuarios_queryset(user)
    if query:
        numeric_pk = _as_int(query)
        lookups = [
            'matricula__iexact',
            'matricula__istartswith',
            'first_name__iexact',
            'last_name__iexact',
            'first_name__istartswith',
            'last_name__istartswith',
            'email__icontains',
            'site__icontains',
            'setor__icontains',
            'andar_sala__icontains',
            'motivo_recusa__icontains',
        ]
        qs = qs.filter(_build_lookup_condition(query, lookups, numeric_pk=numeric_pk))
        qs = qs.annotate(
            _search_relevance=_build_relevance_expression(
                query,
                [
                    ('matricula__iexact', 1000),
                    ('matricula__istartswith', 990),
                    ('first_name__iexact', 980),
                    ('last_name__iexact', 970),
                    ('first_name__istartswith', 960),
                    ('last_name__istartswith', 950),
                    ('email__icontains', 920),
                    ('site__icontains', 910),
                    ('setor__icontains', 900),
                    ('andar_sala__icontains', 890),
                ],
                numeric_pk=numeric_pk,
            )
        ).order_by('-_search_relevance', 'first_name', 'last_name', 'matricula')
        return {
            'key': 'usuarios',
            'label': 'UsuÃ¡rios',
            'icon': 'fa-user-gear',
            'description': 'Perfis, matrÃ­cula e localizaÃ§Ã£o.',
            'count': qs.count(),
            'items': [_usuario_item(usuario) for usuario in qs[:SEARCH_LIMITS['usuarios']]],
            'see_all_url': (
                reverse('lista_usuarios') + (f'?{urlencode({"q": query})}' if query else '')
                if user.is_admin
                else reverse('meu_perfil')
            ),
            'see_all_label': 'Abrir lista' if user.is_admin else 'Meu perfil',
        }

    if user.is_admin:
        itens = list(Usuario.objects.select_related('gestor', 'aprovado_por').order_by('-created_at')[:RECENT_LIMITS['usuarios']])
        return {
            'key': 'usuarios',
            'label': 'UsuÃ¡rios recentes',
            'icon': 'fa-user-gear',
            'description': 'Contas mais recentes e perfis ativos.',
            'count': len(itens),
            'items': [_usuario_item(usuario) for usuario in itens],
            'see_all_url': reverse('lista_usuarios'),
            'see_all_label': 'Abrir lista',
        }

    return None


def _lote_group(query=None):
    qs = EntradaLote.objects.select_related('criado_por')
    if query:
        numeric_pk = _as_int(query)
        lookups = [
            'descricao__icontains',
            'arquivo__icontains',
            'status__icontains',
            'criado_por__matricula__icontains',
            'criado_por__first_name__icontains',
            'criado_por__last_name__icontains',
        ]
        qs = qs.filter(_build_lookup_condition(query, lookups, numeric_pk=numeric_pk))
        qs = qs.annotate(
            _search_relevance=_build_relevance_expression(
                query,
                [
                    ('descricao__icontains', 950),
                    ('arquivo__icontains', 930),
                    ('status__icontains', 910),
                    ('criado_por__matricula__icontains', 890),
                ],
                numeric_pk=numeric_pk,
            )
        ).order_by('-_search_relevance', '-created_at', 'pk')
        return {
            'key': 'lotes',
            'label': 'Lotes',
            'icon': 'fa-warehouse',
            'description': 'ImportaÃ§Ãµes em massa e histÃ³rico de carga.',
            'count': qs.count(),
            'items': [_lote_item(lote) for lote in qs[:SEARCH_LIMITS['lotes']]],
            'see_all_url': reverse('estoque'),
            'see_all_label': 'Abrir estoque',
        }

    itens = list(qs.order_by('-created_at')[:RECENT_LIMITS['lotes']])
    return {
        'key': 'lotes',
        'label': 'Lotes recentes',
        'icon': 'fa-warehouse',
        'description': 'Ãšltimas cargas importadas.',
        'count': len(itens),
        'items': [_lote_item(lote) for lote in itens],
        'see_all_url': reverse('estoque'),
        'see_all_label': 'Abrir estoque',
    }


def _groups_for_query(user, query):
    if user.is_solicitante:
        return [
            _chamado_group(user, query),
        ]

    if query and len(query) >= 2:
        return [
            _equipment_group(user, query),
            _chamado_group(user, query),
            _usuario_group(user, query),
            _lote_group(query),
        ]

    return [
        _equipment_group(user),
        _chamado_group(user),
        _usuario_group(user),
        _lote_group(),
    ]


def build_search_payload(user, raw_query):
    query = normalize_query(raw_query)
    query_mode = bool(query and len(query) >= 2)
    short_query = bool(query and len(query) < 2)

    groups = [group for group in _groups_for_query(user, query) if group and group['items']]
    total_resultados = sum(group['count'] for group in groups)

    if query_mode:
        if total_resultados:
            summary = f'Encontramos {total_resultados} resultado(s) em ativos, chamados, usuÃ¡rios e lotes.'
        else:
            summary = f'Nenhum resultado encontrado para "{query}". Tente patrimÃ´nio, matrÃ­cula, site, setor ou status.'
        mode_label = 'Resultados'
        total_label = 'resultados'
    elif short_query:
        summary = 'Digite ao menos 2 caracteres para uma busca precisa. Enquanto isso, seguem itens recentes.'
        mode_label = 'Acesso rÃ¡pido'
        total_label = 'itens'
    else:
        summary = 'Mostrando itens recentes e atalhos rÃ¡pidos para vocÃª comeÃ§ar.'
        mode_label = 'Acesso rÃ¡pido'
        total_label = 'itens'

    return {
        'query': query,
        'query_mode': query_mode,
        'short_query': short_query,
        'mode_label': mode_label,
        'total_label': total_label,
        'total_resultados': total_resultados,
        'summary': summary,
        'updated_at': _format_datetime(timezone.now()),
        'groups': groups,
        'quick_actions': _quick_actions(user),
    }




