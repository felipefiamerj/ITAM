def pode_acessar_operacao(user):
    """Indica se o usuario pode executar atividades operacionais no sistema."""
    return bool(
        getattr(user, 'is_authenticated', False)
        and getattr(user, 'is_operacional', False)
    )
