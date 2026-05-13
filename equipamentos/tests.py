import shutil
import tempfile

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings

from accounts.models import Usuario

from .models import CondicaoEquipamento, Equipamento, StatusEquipamento
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
