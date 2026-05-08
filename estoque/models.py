"""Consultas e resumos do módulo de estoque."""

from django.db.models import Count

from equipamentos.models import EntradaLote, Equipamento, StatusEquipamento


def equipamentos_em_estoque():
    return Equipamento.objects.filter(status=StatusEquipamento.EM_ESTOQUE)


def equipamentos_em_manutencao():
    return Equipamento.objects.filter(status=StatusEquipamento.EM_MANUTENCAO)


def lotes_recentes(limit=10):
    return EntradaLote.objects.select_related('criado_por').order_by('-created_at')[:limit]


def resumo_por_tipo():
    return (
        equipamentos_em_estoque()
        .values('tipo')
        .annotate(total=Count('id'))
        .order_by('-total', 'tipo')
    )
