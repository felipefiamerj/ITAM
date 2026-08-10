import shutil
import tempfile
from datetime import timedelta

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from accounts.models import Usuario
from chamados.models import Chamado, ChamadoItemSolicitado, EtapaFluxoChamado, PrioridadeChamado, StatusChamado
from equipamentos.models import Equipamento, StatusEquipamento, TipoEquipamento

from .predictions import build_predictive_insights


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
        self.assertContains(response, 'Copiloto de IA')
        self.assertContains(response, 'Recomendações por frente')
        self.assertContains(response, 'Ações priorizadas')

        contexto = response.context
        self.assertGreaterEqual(contexto['recomendacoes_total'], 4)
        self.assertLess(contexto['index_operacional'], 100)
        self.assertEqual(contexto['primary_recommendation']['source_key'], 'chamados')
        self.assertTrue(any(item['source_key'] == 'governanca' for item in contexto['recomendacoes']))
        self.assertIn('recomendacoes_por_origem', contexto['copilot_charts'])
        self.assertIn('recomendacoes_por_horizonte', contexto['copilot_charts'])
        self.assertIn('predictive_insights', contexto)
        self.assertContains(response, 'Demanda e risco de SLA')

    def test_previsoes_usam_historico_estoque_e_sla(self):
        chamado_demanda = Chamado.objects.create(
            titulo='Mouse para equipe',
            descricao='Demanda historica para previsao de estoque.',
            solicitante=self.viewer,
            prioridade=PrioridadeChamado.MEDIA,
            status=StatusChamado.ENCERRADO,
        )
        item = ChamadoItemSolicitado.objects.create(
            chamado=chamado_demanda,
            tipo_equipamento=TipoEquipamento.MOUSE,
            quantidade=8,
        )
        ChamadoItemSolicitado.objects.filter(pk=item.pk).update(created_at=timezone.now() - timedelta(days=10))

        self._criar_equipamento(
            'MOUSE-STOCK-01',
            tipo=TipoEquipamento.MOUSE,
            status=StatusEquipamento.EM_ESTOQUE,
        )

        chamado_sla = Chamado.objects.create(
            titulo='Notebook parado',
            descricao='Chamado critico antigo para risco de SLA.',
            solicitante=self.viewer,
            prioridade=PrioridadeChamado.CRITICA,
            status=StatusChamado.FILA,
            fluxo_etapa=EtapaFluxoChamado.AGUARDANDO_ESTOQUE,
        )
        Chamado.objects.filter(pk=chamado_sla.pk).update(created_at=timezone.now() - timedelta(hours=5))

        insights = build_predictive_insights(forecast_days=30, history_days=30)

        mouse_forecast = next(item for item in insights['demand_forecast'] if item['tipo'] == TipoEquipamento.MOUSE)
        self.assertEqual(mouse_forecast['previsao_periodo'], 8)
        self.assertEqual(mouse_forecast['em_estoque'], 1)
        self.assertGreaterEqual(mouse_forecast['risk_score'], 85)

        self.assertEqual(insights['sla_risk'][0]['id'], chamado_sla.pk)
        self.assertGreaterEqual(insights['sla_risk'][0]['risk_score'], 95)
        self.assertEqual(insights['metrics']['rupture_risk'], 1)
        self.assertEqual(insights['metrics']['sla_critical'], 1)

    def test_copiloto_redireciona_solicitante(self):
        self.client.force_login(self.viewer)

        response = self.client.get(reverse('ia'))

        self.assertRedirects(response, reverse('chamados'))

    def test_monitoramento_permanece_disponivel_em_subrota(self):
        self.client.force_login(self.admin)

        response = self.client.get(reverse('ia_monitoramento'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Monitoramento preditivo')
