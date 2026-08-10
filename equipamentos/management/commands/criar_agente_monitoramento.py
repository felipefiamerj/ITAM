from django.core.management.base import BaseCommand, CommandError

from accounts.models import Usuario
from equipamentos.models import AgenteMonitoramento


class Command(BaseCommand):
    help = 'Cria um agente de monitoramento e imprime o token de instalacao.'

    def add_arguments(self, parser):
        parser.add_argument('nome', help='Nome do agente, por exemplo Unidade SP ou Notebook Field.')
        parser.add_argument('--host', default='', help='Host principal esperado para o agente.')
        parser.add_argument('--criado-por', default='', help='Matricula do usuario responsavel pelo cadastro.')
        parser.add_argument('--inativo', action='store_true', help='Cria o agente ja inativo.')

    def handle(self, *args, **options):
        criado_por = None
        matricula = (options['criado_por'] or '').strip()
        if matricula:
            criado_por = Usuario.objects.filter(matricula__iexact=matricula).first()
            if not criado_por:
                raise CommandError(f'Usuario com matricula {matricula} nao encontrado.')

        agente = AgenteMonitoramento.objects.create(
            nome=options['nome'].strip(),
            host_name=(options['host'] or '').strip(),
            ativo=not options['inativo'],
            criado_por=criado_por,
        )

        self.stdout.write(self.style.SUCCESS('Agente de monitoramento criado.'))
        self.stdout.write(f'ID: {agente.pk}')
        self.stdout.write(f'Nome: {agente.nome}')
        self.stdout.write(f'Host: {agente.host_name or "-"}')
        self.stdout.write(f'Ativo: {"sim" if agente.ativo else "nao"}')
        self.stdout.write('')
        self.stdout.write('Token do agente:')
        self.stdout.write(agente.token)
        self.stdout.write('')
        self.stdout.write('Use este token no header X-ITAM-AGENT-TOKEN ou no campo agent_token do payload.')
