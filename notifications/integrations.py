import logging
from urllib.parse import urljoin

import requests
from django.conf import settings
from django.db import transaction

logger = logging.getLogger(__name__)


def corporate_webhooks_enabled():
    return bool(getattr(settings, 'ITAM_CORPORATE_WEBHOOKS_ENABLED', False))


def configured_webhooks():
    webhooks = []
    teams_url = getattr(settings, 'ITAM_TEAMS_WEBHOOK_URL', '')
    slack_url = getattr(settings, 'ITAM_SLACK_WEBHOOK_URL', '')
    if teams_url:
        webhooks.append(('teams', teams_url))
    if slack_url:
        webhooks.append(('slack', slack_url))
    return webhooks


def enqueue_corporate_notification(titulo, mensagem='', link='', *, audience='operacional'):
    if not corporate_webhooks_enabled() or not configured_webhooks():
        return

    transaction.on_commit(
        lambda: send_corporate_notification(
            titulo,
            mensagem=mensagem,
            link=link,
            audience=audience,
        )
    )


def send_corporate_notification(titulo, mensagem='', link='', *, audience='operacional'):
    if not corporate_webhooks_enabled():
        return []

    results = []
    for provider, url in configured_webhooks():
        payload = build_webhook_payload(provider, titulo, mensagem, link, audience=audience)
        results.append(_post_webhook(provider, url, payload))
    return results


def build_webhook_payload(provider, titulo, mensagem='', link='', *, audience='operacional'):
    absolute_link = _absolute_link(link)
    app_name = getattr(settings, 'APP_NAME', 'FIAME System')
    title = f'{app_name}: {titulo}'
    text = _message_text(mensagem, absolute_link)

    if provider == 'teams':
        return {
            '@type': 'MessageCard',
            '@context': 'https://schema.org/extensions',
            'summary': title,
            'themeColor': '2563EB',
            'title': title,
            'text': text.replace('\n', '<br>'),
            'sections': [
                {
                    'facts': [
                        {'name': 'Destino', 'value': audience},
                    ]
                }
            ],
        }

    if provider == 'slack':
        slack_text = f'*{title}*\n{text}'
        return {
            'text': slack_text,
            'blocks': [
                {
                    'type': 'section',
                    'text': {
                        'type': 'mrkdwn',
                        'text': slack_text,
                    },
                }
            ],
        }

    return {
        'title': title,
        'message': mensagem,
        'link': absolute_link,
        'audience': audience,
    }


def _post_webhook(provider, url, payload):
    timeout = getattr(settings, 'ITAM_WEBHOOK_TIMEOUT_SECONDS', 5)
    result = {
        'provider': provider,
        'ok': False,
        'status_code': None,
        'error': '',
    }
    try:
        response = requests.post(url, json=payload, timeout=timeout)
        result['status_code'] = response.status_code
        response.raise_for_status()
        result['ok'] = True
    except requests.RequestException as exc:
        result['error'] = str(exc)
        logger.warning('Falha ao enviar webhook corporativo para %s: %s', provider, exc)
    return result


def _absolute_link(link):
    if not link:
        return ''
    if link.startswith(('http://', 'https://')):
        return link
    site_url = getattr(settings, 'SITE_URL', '')
    if not site_url:
        return link
    return urljoin(site_url.rstrip('/') + '/', link.lstrip('/'))


def _message_text(mensagem, link):
    parts = []
    if mensagem:
        parts.append(mensagem)
    if link:
        parts.append(f'Link: {link}')
    return '\n'.join(parts) or 'Nova notificacao operacional.'
