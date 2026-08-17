from django.core.management.base import BaseCommand

from equipamentos.models import Equipamento


class Command(BaseCommand):
    help = 'Regenera QR Codes dos equipamentos usando a URL publica configurada.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--patrimonio',
            default='',
            help='Regenera apenas um patrimonio especifico.',
        )
        parser.add_argument(
            '--force',
            action='store_true',
            help='Regenera mesmo quando o arquivo atual ja parece atualizado.',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Mostra o que seria regenerado sem gravar arquivos.',
        )

    def handle(self, *args, **options):
        patrimonio = (options.get('patrimonio') or '').strip()
        verbosity = options.get('verbosity', 1)
        qs = Equipamento.objects.order_by('id_patrimonio')
        if patrimonio:
            qs = qs.filter(id_patrimonio__iexact=patrimonio)

        total = qs.count()
        alterados = 0
        for equipamento in qs:
            precisa_regenerar = options['force'] or not equipamento.qrcode_atualizado
            if not precisa_regenerar:
                continue

            alterados += 1
            if verbosity > 1:
                self.stdout.write(f'{equipamento.id_patrimonio}: {equipamento.qr_code_payload}')
            if not options['dry_run']:
                equipamento._gerar_qrcode()
                equipamento.save(update_fields=['qr_code', 'updated_at'])

        if options['dry_run']:
            if verbosity:
                self.stdout.write(self.style.WARNING(f'Dry-run: {alterados} de {total} QR Code(s) seriam regenerados.'))
        elif verbosity:
            self.stdout.write(self.style.SUCCESS(f'{alterados} de {total} QR Code(s) regenerado(s).'))
