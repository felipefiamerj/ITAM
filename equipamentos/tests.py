import json
import shutil
import tempfile
from io import StringIO

from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse

from accounts.models import Usuario

from .models import AgenteMonitoramento, CondicaoEquipamento, Equipamento, StatusEquipamento, TelemetriaEvento
from .services import importar_equipamentos_csv

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
    def test_qrcode_usa_texto_completo_do_equipamento(self):
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

    @override_settings(ITAM_QR_BASE_URL='https://itam.example.com')
    def test_regenerar_qrcodes_atualiza_payload_antigo(self):
        equipamento = Equipamento.objects.create(
            id_patrimonio='PAT-QR-002',
            tipo='monitor',
            marca='Cisco',
            modelo='9300',
        )
        nome_original = equipamento.qr_code.name

        Equipamento.objects.filter(pk=equipamento.pk).update(modelo='9500')

        saida = StringIO()
        call_command('regenerar_qrcodes', patrimonio='PAT-QR-002', stdout=saida)

        equipamento.refresh_from_db()
        self.assertIn('1 de 1 QR Code(s) regenerado(s).', saida.getvalue())
        self.assertNotEqual(equipamento.qr_code.name, nome_original)
        self.assertIn('Modelo: 9500', equipamento.qr_payload)
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
