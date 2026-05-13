"""Helpers para montar dados de gráficos a partir de resumos agregados."""


def build_choice_chart(summary, choices, key):
    counts = {item[key]: int(item.get('total', 0)) for item in summary}
    labels = []
    values = []

    for value, label in choices:
        labels.append(label)
        values.append(counts.get(value, 0))

    return {
        'labels': labels,
        'values': values,
    }


def build_top_chart(summary, label_key, label_map=None, limit=None):
    items = list(summary)
    if limit is not None:
        items = items[:limit]

    labels = []
    values = []

    for item in items:
        label = item[label_key]
        if label_map:
            label = label_map.get(label, label)
        labels.append(label)
        values.append(int(item.get('total', 0)))

    return {
        'labels': labels,
        'values': values,
    }
