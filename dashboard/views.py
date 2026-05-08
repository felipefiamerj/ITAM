from auditlog.models import LogEntry
from django.contrib.auth.decorators import login_required
from django.shortcuts import render

from accounts.models import Usuario
from chamados.models import Chamado, PrioridadeChamado, StatusChamado
from equipamentos.models import Equipamento, StatusEquipamento
from notifications.models import Notification


@login_required
def dashboard_view(request):
    equipamentos = Equipamento.objects.select_related('responsavel', 'criado_por')
    chamados = Chamado.objects.select_related('equipamento', 'solicitante', 'responsavel')

    chamados_abertos = chamados.exclude(status__in=[StatusChamado.RESOLVIDO, StatusChamado.FECHADO])
    chamados_criticos = chamados_abertos.filter(prioridade=PrioridadeChamado.CRITICA)

    context = {
        'total_equipamentos': equipamentos.count(),
        'equipamentos_em_uso': equipamentos.filter(status=StatusEquipamento.EM_USO).count(),
        'equipamentos_em_estoque': equipamentos.filter(status=StatusEquipamento.EM_ESTOQUE).count(),
        'equipamentos_em_manutencao': equipamentos.filter(status=StatusEquipamento.EM_MANUTENCAO).count(),
        'equipamentos_alerta': equipamentos.filter(score_saude__lt=70).count(),
        'chamados_abertos': chamados_abertos.count(),
        'chamados_criticos': chamados_criticos.count(),
        'usuarios_pendentes': Usuario.objects.filter(solicitacao_pendente=True).count(),
        'usuarios_ativos': Usuario.objects.filter(ativo=True, solicitacao_pendente=False).count(),
        'usuarios_pendentes_recentes': Usuario.objects.filter(solicitacao_pendente=True)
        .select_related('gestor')
        .order_by('created_at')[:5],
        'atividade_recente': LogEntry.objects.filter(
            content_type__app_label__in=['accounts', 'chamados', 'equipamentos']
        )
        .select_related('actor', 'content_type')
        .order_by('-timestamp')[:8],
        'chamados_recentes': chamados.order_by('-created_at')[:5],
        'equipamentos_recentes': equipamentos.order_by('-created_at')[:5],
        'notificacoes_nao_lidas': Notification.objects.filter(user=request.user, is_read=False).count(),
    }
    return render(request, 'dashboard/index.html', context)
