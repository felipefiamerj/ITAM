"""Consultas e resumos do módulo de estoque."""

from django.db.models import Count, Q

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


def resumo_por_status():
    return (
        Equipamento.objects.values('status')
        .annotate(total=Count('id'))
        .order_by('-total', 'status')
    )


def resumo_por_site(limit=8):
    return (
        Equipamento.objects.exclude(site='')
        .values('site')
        .annotate(
            total=Count('id'),
            em_uso=Count('id', filter=Q(status=StatusEquipamento.EM_USO)),
            em_estoque=Count('id', filter=Q(status=StatusEquipamento.EM_ESTOQUE)),
            em_manutencao=Count('id', filter=Q(status=StatusEquipamento.EM_MANUTENCAO)),
            descartado=Count('id', filter=Q(status=StatusEquipamento.DESCARTADO)),
        )
        .order_by('-total', 'site')[:limit]
    )


def resumo_por_localizacao(limit=12):
    return (
        Equipamento.objects.exclude(site='').exclude(setor='').exclude(andar_sala='')
        .values('site', 'setor', 'andar_sala')
        .annotate(
            total=Count('id'),
            em_uso=Count('id', filter=Q(status=StatusEquipamento.EM_USO)),
            em_estoque=Count('id', filter=Q(status=StatusEquipamento.EM_ESTOQUE)),
            em_manutencao=Count('id', filter=Q(status=StatusEquipamento.EM_MANUTENCAO)),
            descartado=Count('id', filter=Q(status=StatusEquipamento.DESCARTADO)),
        )
        .order_by('-total', 'site', 'setor', 'andar_sala')[:limit]
    )
