from accounts.permissions import pode_acessar_operacao

from .models import EtapaFluxoChamado

PAINEL_OPERACIONAL_LANES = (
    {
        'key': 'recebidos',
        'label': 'Recebidos',
        'description': 'Chamados novos que ainda est\u00e3o na porta de entrada da opera\u00e7\u00e3o.',
        'etapas': (EtapaFluxoChamado.SOLICITADO, EtapaFluxoChamado.TRIAGEM),
    },
    {
        'key': 'estoque',
        'label': 'Aguardando estoque',
        'description': 'Chamados que precisam de disponibilidade antes de avan\u00e7ar.',
        'etapas': (EtapaFluxoChamado.AGUARDANDO_ESTOQUE,),
    },
    {
        'key': 'aprovacao',
        'label': 'Aguardando aprova\u00e7\u00e3o',
        'description': 'Pedidos liberados para o colaborador confirmar a retirada.',
        'etapas': (EtapaFluxoChamado.AGUARDANDO_APROVACAO,),
    },
    {
        'key': 'separacao',
        'label': 'Separa\u00e7\u00e3o e entrega',
        'description': 'Itens aprovados, em separa\u00e7\u00e3o ou prontos para a entrega final.',
        'etapas': (
            EtapaFluxoChamado.APROVADO_PARA_RETIRADA,
            EtapaFluxoChamado.EM_SEPARACAO,
            EtapaFluxoChamado.PRONTO_PARA_ENTREGA,
        ),
    },
)


def pode_gerenciar_chamado(user):
    return pode_acessar_operacao(user)


def pode_visualizar_chamado(user, chamado):
    return (
        pode_gerenciar_chamado(user)
        or chamado.solicitante_id == getattr(user, 'id', None)
        or chamado.destinatario_id == getattr(user, 'id', None)
    )


def pode_editar_chamado(user, chamado):
    return pode_gerenciar_chamado(user) or chamado.solicitante_id == getattr(user, 'id', None)


def pode_excluir_chamado(user):
    return bool(getattr(user, 'is_authenticated', False) and getattr(user, 'is_admin', False))


def pode_aprovar_retirada(user, chamado):
    return bool(
        getattr(user, 'is_authenticated', False)
        and chamado.destinatario_id == getattr(user, 'id', None)
    )


def acoes_fluxo_chamado(user, chamado):
    pode_gerenciar = pode_gerenciar_chamado(user)
    pode_organizar_estoque = bool(
        getattr(user, 'is_authenticated', False)
        and (getattr(user, 'is_admin', False) or getattr(user, 'is_analista', False))
    )
    pode_preparar_entrega = bool(
        getattr(user, 'is_authenticated', False)
        and (getattr(user, 'is_admin', False) or getattr(user, 'is_tecnico', False))
    )

    return {
        'pode_assumir': pode_gerenciar and chamado.fluxo_etapa in {
            EtapaFluxoChamado.SOLICITADO,
            EtapaFluxoChamado.AGUARDANDO_ESTOQUE,
        },
        'pode_marcar_sem_estoque': pode_organizar_estoque and chamado.fluxo_etapa in {
            EtapaFluxoChamado.TRIAGEM,
            EtapaFluxoChamado.AGUARDANDO_ESTOQUE,
        },
        'pode_enviar_para_aprovacao': pode_organizar_estoque and chamado.fluxo_etapa in {
            EtapaFluxoChamado.TRIAGEM,
            EtapaFluxoChamado.AGUARDANDO_ESTOQUE,
        },
        'pode_aprovar_retirada': (
            pode_aprovar_retirada(user, chamado)
            and chamado.fluxo_etapa == EtapaFluxoChamado.AGUARDANDO_APROVACAO
        ),
        'pode_marcar_separacao': pode_preparar_entrega and chamado.fluxo_etapa in {
            EtapaFluxoChamado.APROVADO_PARA_RETIRADA,
            EtapaFluxoChamado.EM_SEPARACAO,
        },
        'pode_marcar_pronto': (
            pode_preparar_entrega
            and chamado.fluxo_etapa == EtapaFluxoChamado.EM_SEPARACAO
        ),
    }
