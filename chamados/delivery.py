import json

from django.core.exceptions import ValidationError


def normalizar_selecoes_entrega(selecoes):
    """Converte a selecao de itens e equipamentos para ids inteiros validados."""
    if selecoes in (None, '', {}):
        return {}

    if isinstance(selecoes, str):
        try:
            selecoes = json.loads(selecoes)
        except json.JSONDecodeError as exc:
            raise ValidationError('Sele\u00e7\u00f5es de equipamentos inv\u00e1lidas.') from exc

    if not isinstance(selecoes, dict):
        raise ValidationError('Sele\u00e7\u00f5es de equipamentos inv\u00e1lidas.')

    normalizadas = {}
    for item_id_raw, equipamento_id_raw in selecoes.items():
        try:
            item_id = int(item_id_raw)
            equipamento_id = int(equipamento_id_raw)
        except (TypeError, ValueError) as exc:
            raise ValidationError('Sele\u00e7\u00f5es de equipamentos inv\u00e1lidas.') from exc
        normalizadas[item_id] = equipamento_id

    return normalizadas
