import shutil
import tempfile
from datetime import timedelta

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from accounts.models import Usuario
from chamados.models import Chamado
from chamados.models import EtapaFluxoChamado, PrioridadeChamado, StatusChamado
from equipamentos.models import Equipamento, StatusEquipamento


class CopilotoOperacionalTests(TestCase):
    def setUp(self):
        self.media_dir = tempfile.mkdtemp()
        self.media_override = override_settings(MEDIA_ROOT=self.media_dir)
        self.media_override.enable()

        self.admin = Usuario.objects.create_superuser(
            matricula='9000',
            password='test12345',
            first_name='Ana',
            last_name='Admin',
        )
        self.viewer = Usuario.objects.create_user(
            matricula='9001',
            password='test12345',
            first_name='Beto',
            last_name='Viewer',
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
            status=kwargs.pop('status', StatusEquipamento.EM_USO),
            condicao=kwargs.pop('condicao', 'bom'),
            score_saude=kwargs.pop('score_saude', 90),
            **kwargs,
        )

    def test_copiloto_operacional_prioriza_chamados_e_equipamentos(self):
        self._criar_equipamento(
            'PAT-CRIT-01',
            score_saude=55,
            monitoramento_ativo=False,
            garantia_ate=timezone.localdate() + timedelta(days=90),
        )
        self._criar_equipamento(
            'PAT-PLAN-01',
            score_saude=88,
            monitoramento_ativo=False,
            garantia_ate=timezone.localdate() - timedelta(days=5),
        )
        self._criar_equipamento(
            'PAT-MON-01',
            score_saude=92,
            monitoramento_ativo=True,
            last_seen_at=timezone.now() - timedelta(minutes=30),
            monitoramento_status='offline',
        )

        chamado_critico = Usuario.objects.create_user(
            matricula='9010',
            password='test12345',
            first_name='Cliente',
            last_name='Critico',
        )
        Chamado.objects.create(
            titulo='Notebook sem retorno',
            descricao='Chamado critico para teste do copiloto.',
            solicitante=chamado_critico,
            prioridade=PrioridadeChamado.CRITICA,
            status=StatusChamado.FILA,
        )
        Chamado.objects.create(
            titulo='Aguardando aprovacao',
            descricao='Fluxo travado para validar recomendacao de aprovacao.',
            solicitante=self.viewer,
            prioridade=PrioridadeChamado.MEDIA,
            status=StatusChamado.FILA,
            fluxo_etapa=EtapaFluxoChamado.AGUARDANDO_APROVACAO,
        )

        Usuario.objects.create_user(
            matricula='9011',
            password='test12345',
            first_name='Novo',
            last_name='Usuario',
            gestor=self.admin,
            solicitacao_pendente=True,
        )

        self.client.force_login(self.admin)
        response = self.client.get(reverse('ia'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Copiloto operacional')
        self.assertContains(response, 'Recomendações por frente')
        self.assertContains(response, 'Ações priorizadas')

        contexto = response.context
        self.assertGreaterEqual(contexto['recomendacoes_total'], 4)
        self.assertLess(contexto['index_operacional'], 100)
        self.assertEqual(contexto['primary_recommendation']['source_key'], 'chamados')
        self.assertTrue(any(item['source_key'] == 'governanca' for item in contexto['recomendacoes']))
        self.assertIn('recomendacoes_por_origem', contexto['copilot_charts'])
        self.assertIn('recomendacoes_por_horizonte', contexto['copilot_charts'])

    def test_copiloto_redireciona_solicitante(self):
        self.client.force_login(self.viewer)

        response = self.client.get(reverse('ia'))

        self.assertRedirects(response, reverse('chamados'))

    def test_monitoramento_permanece_disponivel_em_subrota(self):
        self.client.force_login(self.admin)

        response = self.client.get(reverse('ia_monitoramento'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Monitoramento preditivo')
