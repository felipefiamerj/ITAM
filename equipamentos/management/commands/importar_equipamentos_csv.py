from pathlib import Path

from django.core.files import File
from django.core.management.base import BaseCommand, CommandError

from accounts.models import Usuario
from equipamentos.services import importar_equipamentos_csv


class Command(BaseCommand):
    help = 'Importa equipamentos a partir de um CSV em lote.'

    def add_arguments(self, parser):
        default_file = Path(__file__).resolve().parents[3] / 'equipamentos_50000.csv'
        parser.add_argument(
            '--file',
            default=str(default_file),
            help='Caminho do CSV a ser importado.',
        )
        parser.add_argument(
            '--descricao',
            default='Importação CSV',
            help='Descrição opcional do lote.',
        )
        parser.add_argument(
            '--usuario',
            default='',
            help='Matrícula do usuário que ficará como criador do lote.',
        )

    def handle(self, *args, **options):
        file_path = Path(options['file']).expanduser().resolve()
        if not file_path.exists():
            raise CommandError(f'Arquivo não encontrado: {file_path}')

        usuario = None
        matricula = (options.get('usuario') or '').strip()
        if matricula:
            usuario = Usuario.objects.filter(matricula__iexact=matricula).first()
            if not usuario:
                raise CommandError(f'Usuário não encontrado para a matrícula {matricula}.')
        else:
            usuario = Usuario.objects.filter(is_superuser=True).first()

        with file_path.open('rb') as handle:
            arquivo = File(handle, name=file_path.name)
            resultado = importar_equipamentos_csv(
                arquivo,
                criado_por=usuario,
                descricao=options.get('descricao') or file_path.stem,
            )

        lote = resultado['lote']
        self.stdout.write(self.style.SUCCESS('Importação concluída com sucesso.'))
        self.stdout.write(
            f"Lote #{lote.pk} | linhas={resultado['total_linhas']} | criados={resultado['criados']} | "
            f"atualizados={resultado['atualizados']} | erros={resultado['erros']}"
        )
