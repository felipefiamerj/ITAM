from django.conf import settings


def _nav_item(label, url_name, *active_routes):
    return {
        'label': label,
        'url_name': url_name,
        'active_routes': active_routes or (url_name,),
    }


def _navigation_items(user):
    if not getattr(user, 'is_authenticated', False):
        return ()

    chamados = _nav_item(
        'Chamados',
        'chamados',
        'chamados',
        'detalhe_chamado',
        'editar_chamado',
        'criar_chamado',
        'entregar_equipamento_chamado',
    )
    fila_operacional = _nav_item('Fila operacional', 'painel_tecnico')
    indicadores = _nav_item('Indicadores', 'produtividade_operacional')
    equipamentos = _nav_item(
        'Equipamentos',
        'equipamentos',
        'equipamentos',
        'detalhe_equipamento',
        'editar_equipamento',
        'criar_equipamento',
        'registrar_movimentacao',
        'importar_equipamentos_csv',
        'lifecycle_dashboard',
    )
    termos = _nav_item(
        'Termos digitais',
        'painel_termos_chamados',
        'painel_termos_chamados',
        'compliance_termos_chamados',
        'compliance_termos_csv',
        'compliance_termos_pdf',
        'termo_chamado',
        'termo_chamado_pdf',
        'enviar_assinatura_termo',
        'renovar_assinatura_termo',
    )
    estoque = _nav_item('Estoque', 'estoque', 'estoque', 'reserva_inteligente_estoque')
    relatorios = _nav_item('Relatórios', 'relatorios')
    copiloto_ia = _nav_item('Copiloto de IA', 'ia', 'ia', 'ia_monitoramento')

    if user.is_solicitante:
        return (
            _nav_item('Portal', 'dashboard'),
            chamados,
        )

    itens_operacionais = (
        fila_operacional,
        indicadores,
        equipamentos,
        chamados,
        termos,
        estoque,
        relatorios,
        copiloto_ia,
    )
    if user.is_admin:
        return (_nav_item('Dashboard', 'dashboard'),) + itens_operacionais
    if user.is_analista:
        return (
            estoque,
            fila_operacional,
            indicadores,
            chamados,
            termos,
            equipamentos,
            relatorios,
            copiloto_ia,
        )
    return itens_operacionais


def site_context(request):
    return {
        'app_name': getattr(settings, 'APP_NAME', 'FIAME System'),
        'app_short_name': getattr(settings, 'APP_SHORT_NAME', 'FIAME'),
        'static_version': getattr(settings, 'APP_STATIC_VERSION', '20260731'),
        'support_email': getattr(settings, 'DEFAULT_FROM_EMAIL', ''),
        'site_url': getattr(settings, 'SITE_URL', ''),
        'navigation_items': _navigation_items(request.user),
    }
