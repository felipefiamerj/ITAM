import json
import shutil
import tempfile
from datetime import date, timedelta
from decimal import Decimal
from io import StringIO
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from accounts.models import Usuario

from .forms import EquipamentoForm
from .health import build_telemetry_health
from .inventory import sync_inventory_divergences
from .lifecycle import sync_lifecycle_for_equipment
from .models import (
    AgenteMonitoramento,
    AlertaCicloVida,
    CampoDivergenciaInventario,
    CondicaoEquipamento,
    DivergenciaInventario,
    Equipamento,
    MovimentacaoEquipamento,
    StatusEquipamento,
    StatusMonitoramento,
    TelemetriaEvento,
    TipoAlertaCicloVida,
)
from .services import importar_equipamentos_csv
from .telemetria import marcar_equipamentos_sem_sinal

CSV_FIXTURE = """id_patrimonio,tipo,tipo_outro,marca,modelo,service_tag,imei,numero_serie,monitor_patrimonio,status,condicao,responsavel,site,setor,andar_sala,descricao,data_aquisicao,valor_aquisicao,garantia_ate,vida_util_estimada_meses,score_saude
PAT-100001,notebook_padrao,,Dell,Latitude 7420,ST123,,SN123,,ativo,novo,,RJ-Matriz,TI,12º Andar - Sala 1,Notebook principal,2024-01-01,5000.00,2026-01-01,36,88
PAT-100002,outro,Scanner Zebra,Zebra,ZT410,,123456,ABC,,estoque,bom,,SP-Filial,Logística,3º Andar - Sala 10,Impressora de etiquetas,2023-05-10,1500.50,2025-05-10,24,74
"""


class ImportacaoEquipamentosCSVTests(TestCase):
    def setUp(self):
        self.media_dir = tempfile.mkdtemp()
        self.media_override = override_settings(MEDIA_ROOT=self.media_dir)
        self.media_override.enable()
        self.user = Usuario.objects.create_superuser(matricula='admin', password='test12345', first_name='Admin', last_name='ITAM')

    def tearDown(self):
        self.media_override.disable()
        shutil.rmtree(self.media_dir, ignore_errors=True)

    def test_importa_csv_e_normaliza_campos(self):
        arquivo = SimpleUploadedFile('equipamentos.csv', CSV_FIXTURE.encode('utf-8'), content_type='text/csv')

        resultado = importar_equipamentos_csv(arquivo, criado_por=self.user, descricao='Lote teste')

        self.assertEqual(resultado['total_linhas'], 2)
        self.assertEqual(resultado['criados'], 2)
        self.assertEqual(resultado['atualizados'], 0)
        self.assertEqual(resultado['erros'], 0)
        self.assertEqual(Equipamento.objects.count(), 2)

        notebook = Equipamento.objects.get(id_patrimonio='PAT-100001')
        self.assertEqual(notebook.status, StatusEquipamento.EM_USO)
        self.assertEqual(notebook.condicao, CondicaoEquipamento.OTIMO)
        self.assertIn('RJ-Matriz', notebook.localizacao_resumida)
        self.assertIn('TI', notebook.localizacao_resumida)

        outro = Equipamento.objects.get(id_patrimonio='PAT-100002')
        self.assertEqual(outro.tipo, 'outro')
        self.assertEqual(outro.tipo_outro, 'Scanner Zebra')
        self.assertEqual(outro.status, StatusEquipamento.EM_ESTOQUE)
        self.assertEqual(outro.condicao, CondicaoEquipamento.BOM)

    @override_settings(ITAM_QR_BASE_URL='https://itam.example.com')
    def test_qrcode_usa_link_publico_do_equipamento(self):
        equipamento = Equipamento.objects.create(
            id_patrimonio='PAT-QR-001',
            tipo='monitor',
            marca='Cisco',
            modelo='9300',
            service_tag='021',
            responsavel=self.user,
            condicao=CondicaoEquipamento.OTIMO,
            score_saude=100,
            site='EG',
            setor='CCR',
        )

        self.assertEqual(equipamento.qr_code_payload, 'https://itam.example.com/q/PAT-QR-001/')
        self.assertEqual(equipamento.qr_public_url, equipamento.qr_code_payload)
        self.assertIn('Patrimônio: PAT-QR-001', equipamento.qr_payload)
        self.assertIn('Marca: Cisco', equipamento.qr_payload)
        self.assertIn('Modelo: 9300', equipamento.qr_payload)
        self.assertIn('Service tag: 021', equipamento.qr_payload)
        self.assertIn('Responsável: Admin ITAM', equipamento.qr_payload)
        self.assertIn('Condição: Ótimo', equipamento.qr_payload)
        self.assertIn('Saúde: 100', equipamento.qr_payload)
        self.assertIn('Site: EG', equipamento.qr_payload)
        self.assertIn('Setor: CCR', equipamento.qr_payload)
        self.assertTrue(equipamento.qr_code.name.startswith('qrcodes/qr_pat-qr-001_'))
        self.assertTrue(equipamento.qrcode_atualizado)
        self.assertNotIn('PATRIMONIO:', equipamento.qr_payload)

    def test_ficha_publica_do_qrcode_exibe_dados_minimos_sem_login(self):
        equipamento = Equipamento.objects.create(
            id_patrimonio='PAT-QR-003',
            tipo='monitor',
            marca='Cisco',
            modelo='9300',
            service_tag='021',
            responsavel=self.user,
            condicao=CondicaoEquipamento.OTIMO,
            score_saude=100,
            site='EG',
            setor='CCR',
        )

        response = self.client.get(reverse('qr_equipamento_publico', args=[equipamento.id_patrimonio]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'PAT-QR-003')
        self.assertContains(response, 'Cisco')
        self.assertContains(response, '9300')
        self.assertContains(response, '021')
        self.assertContains(response, 'Admin ITAM')
        self.assertContains(response, 'Ótimo')
        self.assertContains(response, '100')
        self.assertContains(response, 'EG')
        self.assertContains(response, 'CCR')
        self.assertNotContains(response, 'Historico')

    def test_detalhe_exibe_registro_no_singular(self):
        equipamento = Equipamento.objects.create(
            id_patrimonio='PAT-HIST-001',
            tipo='monitor',
            status=StatusEquipamento.EM_ESTOQUE,
            condicao=CondicaoEquipamento.BOM,
        )
        MovimentacaoEquipamento.objects.create(
            equipamento=equipamento,
            tipo='entrada',
            descricao='Entrada de teste.',
            realizado_por=self.user,
        )
        self.client.force_login(self.user)

        response = self.client.get(reverse('detalhe_equipamento', args=[equipamento.id_patrimonio]))

        self.assertContains(response, '1 registro')
        self.assertNotContains(response, '1 registros')

    @override_settings(ITAM_QR_BASE_URL='https://itam.example.com')
    def test_regenerar_qrcodes_atualiza_link_publico_antigo(self):
        equipamento = Equipamento.objects.create(
            id_patrimonio='PAT-QR-002',
            tipo='monitor',
            marca='Cisco',
            modelo='9300',
        )
        nome_original = equipamento.qr_code.name

        saida = StringIO()
        with override_settings(ITAM_QR_BASE_URL='https://itam-novo.example.com'):
            call_command('regenerar_qrcodes', patrimonio='PAT-QR-002', stdout=saida)

            equipamento.refresh_from_db()
            self.assertIn('1 de 1 QR Code(s) regenerado(s).', saida.getvalue())
            self.assertNotEqual(equipamento.qr_code.name, nome_original)
            self.assertEqual(equipamento.qr_code_payload, 'https://itam-novo.example.com/q/PAT-QR-002/')
            self.assertTrue(equipamento.qrcode_atualizado)

    def test_criar_agente_monitoramento_command_imprime_token(self):
        saida = StringIO()

        call_command('criar_agente_monitoramento', 'Agente teste', '--host', 'NOTE-01', stdout=saida)

        agente = AgenteMonitoramento.objects.get(nome='Agente teste')
        self.assertEqual(agente.host_name, 'NOTE-01')
        self.assertTrue(agente.ativo)
        self.assertIn(agente.token, saida.getvalue())

    def test_telemetria_ingestao_atualiza_equipamento_por_header_do_agente(self):
        agente = AgenteMonitoramento.objects.create(nome='Agente endpoint')
        equipamento = Equipamento.objects.create(
            id_patrimonio='PAT-MON-001',
            tipo='notebook_padrao',
            marca='Dell',
            modelo='Latitude',
            service_tag='ST-MON-001',
            status=StatusEquipamento.EM_USO,
            condicao=CondicaoEquipamento.BOM,
        )

        payload = {
            'host_name': 'NOTE-MON-001',
            'metadata': {
                'agent_name': 'itam-windows-agent',
                'agent_version': '2026.1',
            },
            'devices': [
                {
                    'id_patrimonio': 'PAT-MON-001',
                    'service_tag': 'ST-MON-001',
                    'event_type': 'heartbeat',
                    'severity': 'info',
                    'battery_level': 88,
                    'disk_free_percent': 42,
                    'message': 'Heartbeat de teste.',
                }
            ],
        }

        response = self.client.post(
            reverse('api_telemetria_ingestao'),
            data=json.dumps(payload),
            content_type='application/json',
            HTTP_X_ITAM_AGENT_TOKEN=agente.token,
        )

        self.assertEqual(response.status_code, 200)
        equipamento.refresh_from_db()
        agente.refresh_from_db()
        self.assertTrue(equipamento.monitoramento_ativo)
        self.assertEqual(equipamento.last_telemetria_agente, agente)
        self.assertIsNotNone(equipamento.last_seen_at)
        self.assertEqual(agente.host_name, 'NOTE-MON-001')
        self.assertEqual(agente.metadata['agent_name'], 'itam-windows-agent')
        self.assertEqual(TelemetriaEvento.objects.filter(equipamento=equipamento, agente=agente).count(), 1)

    @patch('equipamentos.telemetria.notificar_time_operacional')
    def test_monitoramento_marca_offline_sem_agente_associado(self, notificar):
        equipamento = Equipamento.objects.create(
            id_patrimonio='PAT-MON-SEM-AGENTE',
            tipo='notebook_padrao',
            status=StatusEquipamento.EM_USO,
            condicao=CondicaoEquipamento.BOM,
            monitoramento_ativo=True,
            monitoramento_status='online',
            last_seen_at=timezone.now() - timedelta(minutes=30),
        )

        atualizados = marcar_equipamentos_sem_sinal()
        equipamento.refresh_from_db()

        self.assertEqual(atualizados, 1)
        self.assertEqual(equipamento.monitoramento_status, 'offline')
        evento = TelemetriaEvento.objects.get(equipamento=equipamento, tipo='desconectado')
        self.assertIsNone(evento.agente)
        notificar.assert_called_once()

    @patch('equipamentos.telemetria.notificar_time_operacional')
    def test_alerta_telemetria_respeita_intervalo_de_notificacao(self, notificar):
        agente = AgenteMonitoramento.objects.create(nome='Agente de alerta')
        equipamento = Equipamento.objects.create(
            id_patrimonio='PAT-MON-ALERTA',
            tipo='notebook_padrao',
            status=StatusEquipamento.EM_USO,
            condicao=CondicaoEquipamento.BOM,
        )
        payload = {
            'host_name': 'NOTE-ALERTA',
            'devices': [
                {
                    'id_patrimonio': equipamento.id_patrimonio,
                    'event_type': 'erro_driver',
                    'message': 'Falha de driver.',
                }
            ],
        }

        for _ in range(2):
            response = self.client.post(
                reverse('api_telemetria_ingestao'),
                data=json.dumps(payload),
                content_type='application/json',
                HTTP_X_ITAM_AGENT_TOKEN=agente.token,
            )
            self.assertEqual(response.status_code, 200)

        self.assertEqual(TelemetriaEvento.objects.filter(equipamento=equipamento, tipo='erro_driver').count(), 2)
        notificar.assert_called_once()


class EquipmentTelemetryHealthTests(TestCase):
    def setUp(self):
        self.user = Usuario.objects.create_superuser(
            matricula='telemetry-admin',
            password='test12345',
            first_name='Admin',
            last_name='Telemetry',
        )
        self.agent = AgenteMonitoramento.objects.create(
            nome='Agente Windows teste',
            host_name='NOTE-HEALTH-01',
            metadata={'manufacturer': 'Dell', 'model': 'Latitude'},
        )

    def _equipment(self, **overrides):
        values = {
            'id_patrimonio': 'HEALTH-001',
            'tipo': 'notebook_padrao',
            'monitoramento_ativo': True,
            'monitoramento_status': StatusMonitoramento.ONLINE,
            'last_seen_at': timezone.now(),
            'last_telemetria_agente': self.agent,
            'last_telemetria_payload': {
                'cpu_load_percent': 24,
                'memory_total_mb': 16384,
                'memory_free_mb': 8192,
                'disk_free_percent': 42,
                'disks': [{'name': 'C:', 'free_gb': 107.5, 'size_gb': 256, 'free_percent': 42}],
                'battery_level': 88,
                'os_caption': 'Windows 11 Enterprise',
                'logged_user': 'CORP\\usuario',
                'last_boot_at': '2026-08-17T08:00:00-03:00',
                'severity': 'info',
            },
        }
        values.update(overrides)
        return Equipamento.objects.create(**values)

    def test_diagnostico_saudavel_quando_indicadores_estao_dentro_dos_limites(self):
        equipment = self._equipment()

        diagnostic = build_telemetry_health(equipment)

        self.assertEqual(diagnostic['status'], 'healthy')
        self.assertEqual(diagnostic['connection_label'], 'Online')
        self.assertTrue(all(metric['status'] == 'healthy' for metric in diagnostic['metrics']))

    def test_diagnostico_aponta_memoria_livre_baixa(self):
        equipment = self._equipment(
            id_patrimonio='HEALTH-002',
            last_telemetria_payload={
                'cpu_load_percent': 20,
                'memory_total_mb': 16384,
                'memory_free_mb': 1024,
                'disk_free_percent': 40,
                'battery_level': 80,
            },
        )

        diagnostic = build_telemetry_health(equipment)
        memory = next(metric for metric in diagnostic['metrics'] if metric['key'] == 'memory')

        self.assertEqual(diagnostic['status'], 'warning')
        self.assertEqual(memory['status'], 'warning')
        self.assertIn('6% livre', memory['value'])

    @override_settings(ITAM_HEARTBEAT_STALE_MINUTES=10)
    def test_diagnostico_critico_quando_heartbeat_esta_atrasado(self):
        equipment = self._equipment(
            id_patrimonio='HEALTH-003',
            last_seen_at=timezone.now() - timedelta(minutes=11),
        )

        diagnostic = build_telemetry_health(equipment)

        self.assertEqual(diagnostic['status'], 'critical')
        self.assertEqual(diagnostic['connection_label'], 'Sem sinal')

    def test_detalhe_exibe_metricas_e_historico_do_agente(self):
        equipment = self._equipment(id_patrimonio='HEALTH-004')
        TelemetriaEvento.objects.create(
            equipamento=equipment,
            agente=self.agent,
            tipo='heartbeat',
            severidade='info',
            mensagem='Heartbeat de validacao.',
            payload=equipment.last_telemetria_payload,
        )
        self.client.force_login(self.user)

        response = self.client.get(reverse('detalhe_equipamento', args=[equipment.id_patrimonio]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Diagnóstico do agente Windows')
        self.assertContains(response, '24%')
        self.assertContains(response, '8.0 GB livres de 16.0 GB')
        self.assertContains(response, 'Windows 11 Enterprise')
        self.assertContains(response, 'Heartbeat de validacao.')


class EquipmentLifecycleTests(TestCase):
    def setUp(self):
        self.user = Usuario.objects.create_superuser(
            matricula='lifecycle-admin',
            password='test12345',
            first_name='Admin',
            last_name='Lifecycle',
        )

    def test_calcula_substituicao_e_custo_acumulado(self):
        equipment = Equipamento.objects.create(
            id_patrimonio='LIFE-001',
            tipo='notebook_padrao',
            data_aquisicao=date(2024, 2, 29),
            vida_util_estimada_meses=12,
        )
        MovimentacaoEquipamento.objects.create(
            equipamento=equipment,
            tipo='manutencao',
            descricao='Troca de bateria',
            custo_manutencao=Decimal('250.00'),
        )
        MovimentacaoEquipamento.objects.create(
            equipamento=equipment,
            tipo='retorno_manutencao',
            descricao='Servico concluido',
            custo_manutencao=Decimal('100.00'),
        )

        self.assertEqual(equipment.data_prevista_substituicao, date(2025, 2, 28))
        self.assertEqual(equipment.custo_acumulado_manutencao, Decimal('350.00'))

    def test_formulario_preserva_especificacoes_existentes(self):
        equipment = Equipamento.objects.create(
            id_patrimonio='SPEC-001',
            tipo='notebook_padrao',
            especificacoes={'processador': 'Intel Core i7', 'memoria_gb': 16, 'armazenamento_gb': 512},
        )

        form = EquipamentoForm(instance=equipment)

        self.assertEqual(form['spec_processador'].value(), 'Intel Core i7')
        self.assertEqual(form['spec_memoria_gb'].value(), 16)
        self.assertEqual(form['spec_armazenamento_gb'].value(), 512)
        self.assertEqual(len(form.specification_fields), 6)

    def test_sincroniza_alertas_de_garantia_substituicao_e_condicao(self):
        today = timezone.localdate()
        equipment = Equipamento.objects.create(
            id_patrimonio='LIFE-002',
            tipo='notebook_padrao',
            data_aquisicao=date(today.year - 4, today.month, min(today.day, 28)),
            vida_util_estimada_meses=36,
            garantia_ate=today + timedelta(days=30),
            condicao=CondicaoEquipamento.RUIM,
        )

        activated, resolved = sync_lifecycle_for_equipment(equipment, notify=False)

        self.assertEqual(activated, 3)
        self.assertEqual(resolved, 0)
        self.assertSetEqual(
            set(AlertaCicloVida.objects.filter(equipamento=equipment, ativo=True).values_list('tipo', flat=True)),
            {
                TipoAlertaCicloVida.GARANTIA,
                TipoAlertaCicloVida.SUBSTITUICAO,
                TipoAlertaCicloVida.CONDICAO,
            },
        )

    def test_compara_inventario_com_tolerancia_e_resolve_divergencias(self):
        equipment = Equipamento.objects.create(
            id_patrimonio='INV-001',
            tipo='notebook_padrao',
            numero_serie='SERIAL-CERTO',
            usuario_windows_esperado='ffiame',
            especificacoes={'memoria_gb': 16, 'armazenamento_gb': 256},
        )
        divergent_payload = {
            'memory_total_mb': 8192,
            'disks': [{'size_gb': 237.5}],
            'serial': 'SERIAL-ERRADO',
            'logged_user': 'CORP\\ffiame',
        }

        active_count = sync_inventory_divergences(equipment, divergent_payload, notify=False)

        self.assertEqual(active_count, 2)
        self.assertSetEqual(
            set(DivergenciaInventario.objects.filter(equipamento=equipment, ativa=True).values_list('campo', flat=True)),
            {CampoDivergenciaInventario.MEMORIA, CampoDivergenciaInventario.SERIAL},
        )

        matching_payload = {
            **divergent_payload,
            'memory_total_mb': 16384,
            'serial': 'SERIAL-CERTO',
        }
        active_count = sync_inventory_divergences(equipment, matching_payload, notify=False)

        self.assertEqual(active_count, 0)
        self.assertFalse(DivergenciaInventario.objects.filter(equipamento=equipment, ativa=True).exists())

    def test_dashboard_exibe_alerta_prioritario(self):
        equipment = Equipamento.objects.create(
            id_patrimonio='LIFE-003',
            tipo='notebook_padrao',
            condicao=CondicaoEquipamento.RUIM,
        )
        sync_lifecycle_for_equipment(equipment, notify=False)
        self.client.force_login(self.user)

        response = self.client.get(reverse('lifecycle_dashboard'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'LIFE-003')
        self.assertContains(response, 'Condição física crítica')
