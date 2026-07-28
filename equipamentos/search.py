from django.db.models import Q


def aplicar_filtro_busca_equipamentos(qs, termo):
    termo = (termo or '').strip()
    if not termo:
        return qs

    return qs.filter(
        Q(id_patrimonio__icontains=termo)
        | Q(tipo__icontains=termo)
        | Q(tipo_outro__icontains=termo)
        | Q(marca__icontains=termo)
        | Q(modelo__icontains=termo)
        | Q(service_tag__icontains=termo)
        | Q(site__icontains=termo)
        | Q(setor__icontains=termo)
        | Q(andar_sala__icontains=termo)
    )
