import shutil
import tempfile

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from accounts.models import Usuario

from equipamentos.services import importar_equipamentos_csv


CSV_FIXTURE = """id_patrimonio,tipo,tipo_outro,marca,modelo,service_tag,imei,numero_serie,monitor_patrimonio,status,condicao,responsavel,site,setor,andar_sala,descricao,data_aquisicao,valor_aquisicao,garantia_ate,vida_util_estimada_meses,score_saude
PAT-200001,mouse,,Logitech,MK270,ST999,,SN999,,estoque,bom,,RJ-Matriz,TI,10º Andar - Sala 22,Mouse reserva,2024-01-01,120.00,2026-01-01,24,95
"""


class EstoqueApiTests(TestCase):
    def setUp(self):
        self.media_dir = tempfile.mkdtemp()
        self.media_override = override_settings(MEDIA_ROOT=self.media_dir)
        self.media_override.enable()
        self.user = Usuario.objects.create_superuser(matricula='admin', password='test12345', first_name='Admin', last_name='ITAM')
        arquivo = SimpleUploadedFile('equipamentos.csv', CSV_FIXTURE.encode('utf-8'), content_type='text/csv')
        importar_equipamentos_csv(arquivo, criado_por=self.user, descricao='Lote API')
        self.client.force_login(self.user)

    def tearDown(self):
        self.media_override.disable()
        shutil.rmtree(self.media_dir, ignore_errors=True)

    def test_resumo_api_returns_totals_and_locations(self):
        response = self.client.get(reverse('api_estoque_resumo'))

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload['totais']['total_equipamentos'], 1)
        self.assertEqual(payload['totais']['em_estoque'], 1)
        self.assertEqual(payload['por_site'][0]['site'], 'RJ-Matriz')
        self.assertEqual(payload['por_localizacao'][0]['label'], 'RJ-Matriz · TI · 10º Andar - Sala 22')
        self.assertEqual(payload['lotes'][0]['status'], 'concluido')
