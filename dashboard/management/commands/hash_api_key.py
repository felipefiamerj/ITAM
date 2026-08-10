import hashlib

from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = 'Gera o SHA-256 de uma chave de API para uso em ITAM_API_SHARED_KEY_SHA256.'

    def add_arguments(self, parser):
        parser.add_argument('api_key', help='Chave de API em texto puro. Nao use uma chave reaproveitada.')

    def handle(self, *args, **options):
        api_key = (options['api_key'] or '').strip()
        if len(api_key) < 32:
            raise CommandError('Use uma chave com pelo menos 32 caracteres aleatorios.')

        self.stdout.write(hashlib.sha256(api_key.encode('utf-8')).hexdigest())
