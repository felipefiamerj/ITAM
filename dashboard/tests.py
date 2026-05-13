import shutil
import tempfile

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from accounts.models import Usuario
from chamados.models import Chamado, StatusChamado
from equipamentos.models import EntradaLote, Equipamento, StatusEquipamento


class BuscaGlobalTests(TestCase):
    def setUp(self):
        self.media_dir = tempfile.mkdtemp()
        self.media_override = override_settings(MEDIA_ROOT=self.media_dir)
        self.media_override.enable()

        self.admin = Usuario.objects.create_superuser(
            matricula='admin',
            password='test12345',
            first_name='Admin',
            last_name='ITAM',
        )
        self.viewer = Usuario.objects.create_user(
            matricula='3003',
            password='test12345',
            first_name='Carla',
            last_name='Silva',
        )

    def tearDown(self):
        self.media_override.disable()
        shutil.rmtree(self.media_dir, ignore_errors=True)

    def _criar_equipamento(self, id_patrimonio, **kwargs):
        return Equipamento.objects.create(
            id_patrimonio=id_patrimonio,
            tipo=kwargs.pop('tipo', 'notebook_padrao'),
            marca=kwargs.pop('marca', 'Marca'),
            modelo=kwargs.pop('modelo', 'Modelo'),
            status=kwargs.pop('status', StatusEquipamento.EM_ESTOQUE),
            condicao=kwargs.pop('condicao', 'bom'),
            score_saude=kwargs.pop('score_saude', 90),
            **kwargs,
        )

    def test_busca_global_page_renderiza(self):
        self.client.force_login(self.admin)

        response = self.client.get(reverse('busca_global'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Busca global inteligente')

    def test_dashboard_mostra_fluxo_operacional_para_equipe(self):
        tecnico = Usuario.objects.create_user(
            matricula='4004',
            password='test12345',
            first_name='Tacio',
            last_name='Tecnico',
            nivel_acesso='tecnico',
        )
        Chamado.objects.create(
            titulo='Chamado na fila',
            descricao='Primeira etapa do fluxo.',
            solicitante=self.viewer,
            status=StatusChamado.FILA,
        )
        Chamado.objects.create(
            titulo='Chamado em atendimento',
            descricao='Responsavel definido.',
            solicitante=self.viewer,
            responsavel=tecnico,
            status=StatusChamado.EM_ATENDIMENTO,
        )
        Chamado.objects.create(
            titulo='Chamado aguardando retorno',
            descricao='Pausado para proxima acao.',
            solicitante=self.viewer,
            responsavel=tecnico,
            status=StatusChamado.AGUARDANDO_ATENDIMENTO,
        )
        Chamado.objects.create(
            titulo='Chamado encerrado',
            descricao='Fluxo finalizado.',
            solicitante=self.viewer,
            responsavel=self.admin,
            status=StatusChamado.ENCERRADO,
        )

        self.client.force_login(self.admin)
        response = self.client.get(reverse('dashboard'))

        self.assertEqual(response.status_code, 200)
        fluxo = response.context['chamados_fluxo']
        self.assertEqual(len(fluxo), 4)
        self.assertEqual(
            [bloco['status'] for bloco in fluxo],
            [
                StatusChamado.FILA,
                StatusChamado.EM_ATENDIMENTO,
                StatusChamado.AGUARDANDO_ATENDIMENTO,
                StatusChamado.ENCERRADO,
            ],
        )
        self.assertEqual(fluxo[0]['count'], 1)
        self.assertEqual(fluxo[1]['count'], 1)
        self.assertEqual(fluxo[2]['count'], 1)
        self.assertEqual(fluxo[3]['count'], 1)
        self.assertContains(response, 'Fluxo operacional de chamados')
        self.assertContains(response, 'Chamado encerrado')

    def test_busca_global_api_retorna_resultados_em_multiplos_grupos_para_admin(self):
        auto_user = Usuario.objects.create_user(
            matricula='9001',
            password='test12345',
            first_name='BuscaTop',
            last_name='Operador',
        )
        equipamento = self._criar_equipamento(
            'PAT-BT-01',
            marca='BuscaTop',
            modelo='Workstation',
            site='RJ-Matriz',
            setor='TI',
            andar_sala='12o Andar - Sala 1',
        )
        chamado = Chamado.objects.create(
            titulo='BuscaTop falhou',
            descricao='Chamado para validar a busca global.',
            solicitante=auto_user,
            status=StatusChamado.FILA,
        )
        arquivo = SimpleUploadedFile('lote.csv', b'coluna1,coluna2\n1,2\n', content_type='text/csv')
        # O modelo de lote e criado diretamente para manter o teste leve.
        EntradaLote.objects.create(
            arquivo=arquivo,
            descricao='Carga BuscaTop',
            total_itens=1,
            itens_importados=1,
            itens_com_erro=0,
            status='concluido',
            criado_por=self.admin,
        )

        self.client.force_login(self.admin)
        response = self.client.get(reverse('api_busca_global'), {'q': 'BuscaTop'})

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        grupos = {grupo['key']: grupo for grupo in payload['groups']}

        self.assertTrue(payload['query_mode'])
        self.assertGreaterEqual(payload['total_resultados'], 4)
        self.assertIn('equipamentos', grupos)
        self.assertIn('chamados', grupos)
        self.assertIn('usuarios', grupos)
        self.assertIn('lotes', grupos)
        self.assertEqual(grupos['equipamentos']['items'][0]['title'], equipamento.id_patrimonio)
        self.assertTrue(any(action['label'] == 'Importar CSV' for action in payload['quick_actions']))
        self.assertTrue(any(item['title'].startswith('#') for item in grupos['chamados']['items']))
        self.assertTrue(any('BuscaTop' in item['title'] for item in grupos['usuarios']['items']))
        self.assertIn('conclu', grupos['lotes']['items'][0]['badge'].lower())

    def test_busca_global_api_respeita_visibilidade_do_usuario(self):
        outro_usuario = Usuario.objects.create_user(
            matricula='9010',
            password='test12345',
            first_name='BuscaSecreta',
            last_name='Tecnico',
        )
        Chamado.objects.create(
            titulo='BuscaSecreta indisponivel',
            descricao='Chamado que nao deve aparecer para o solicitante comum.',
            solicitante=outro_usuario,
            status=StatusChamado.FILA,
        )

        self.client.force_login(self.viewer)
        response = self.client.get(reverse('api_busca_global'), {'q': 'BuscaSecreta'})

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        grupos = {grupo['key']: grupo for grupo in payload['groups']}

        self.assertEqual(payload['query_mode'], True)
        self.assertNotIn('chamados', grupos)
        self.assertNotIn('usuarios', grupos)

    def test_dashboard_redireciona_solicitante_para_chamados(self):
        self.client.force_login(self.viewer)

        response = self.client.get(reverse('dashboard'))

        self.assertRedirects(response, reverse('chamados'))

    def test_busca_global_api_limita_solicitante_ao_modulo_de_chamados(self):
        self._criar_equipamento('PAT-LIMIT-01')
        Chamado.objects.create(
            titulo='Chamado limitacao',
            descricao='Chamado usado para validar a busca limitada.',
            solicitante=self.viewer,
            status=StatusChamado.FILA,
        )

        self.client.force_login(self.viewer)
        response = self.client.get(reverse('api_busca_global'))

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        grupos = {grupo['key']: grupo for grupo in payload['groups']}

        self.assertEqual(set(grupos.keys()), {'chamados'})
        self.assertTrue(any(action['label'] == 'Chamados' for action in payload['quick_actions']))
        self.assertTrue(any(action['label'] == 'Novo chamado' for action in payload['quick_actions']))
        self.assertTrue(any(action['label'] == 'Meu perfil' for action in payload['quick_actions']))
