from django.core.management.base import BaseCommand

from equipamentos.telemetria import marcar_equipamentos_sem_sinal


class Command(BaseCommand):
    help = 'Marca como offline os equipamentos sem heartbeat dentro da janela configurada.'

    def handle(self, *args, **options):
        atualizados = marcar_equipamentos_sem_sinal()
        self.stdout.write(self.style.SUCCESS(f'{atualizados} equipamentos marcados como offline.'))
