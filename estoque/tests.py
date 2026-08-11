import shutil
import tempfile

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from accounts.models import Usuario
from chamados.models import Chamado, StatusChamado
from chamados.services import sincronizar_itens_solicitados
from equipamentos.models import Equipamento, StatusEquipamento
from equipamentos.services import importar_equipamentos_csv

from .models import ReservaEstoque
from .services import (
    criar_reserva_estoque,
    criar_reservas_inteligentes,
    marcar_reserva_entregue,
    sugerir_reservas_inteligentes,
)

CSV_FIXTURE = """id_patrimonio,tipo,tipo_outro,marca,modelo,service_tag,imei,numero_serie,monitor_patrimonio,status,condicao,responsavel,site,setor,andar_sala,descricao,data_aquisicao,valor_aquisicao,garantia_ate,vida_util_estimada_meses,score_saude
PAT-200001,mouse,,Logitech,MK270,ST999,,SN999,,estoque,bom,,RJ-Matriz,TI,10o Andar - Sala 22,Mouse reserva,2024-01-01,120.00,2026-01-01,24,95
"""


class EstoqueApiTests(TestCase):
    def setUp(self):
        self.media_dir = tempfile.mkdtemp()
        self.media_override = override_settings(MEDIA_ROOT=self.media_dir)
        self.media_override.enable()
        self.user = Usuario.objects.create_superuser(
            matricula='admin',
            password='test12345',
            first_name='Admin',
            last_name='ITAM',
        )
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
        self.assertIn('RJ-Matriz', payload['por_localizacao'][0]['label'])
        self.assertIn('TI', payload['por_localizacao'][0]['label'])
        self.assertIn('Andar - Sala 22', payload['por_localizacao'][0]['label'])
        self.assertEqual(payload['lotes'][0]['status'], 'concluido')

    def test_reservas_api_cria_e_processa_reserva(self):
        chamado = Chamado.objects.create(
            titulo='Reserva API',
            descricao='Chamado usado para validar as reservas do estoque.',
            solicitante=self.user,
            status=StatusChamado.FILA,
        )
        sincronizar_itens_solicitados(chamado=chamado, tipos_solicitados=['mouse'])
        item = chamado.itens_solicitados.get()
        equipamento = Equipamento.objects.get(id_patrimonio='PAT-200001')

        response = self.client.post(
            reverse('api_reservas_estoque'),
            {
                'chamado': chamado.pk,
                'item_solicitado': item.pk,
                'equipamento': equipamento.id_patrimonio,
                'observacoes': 'Separar para entrega.',
            },
        )

        self.assertEqual(response.status_code, 201)
        payload = response.json()
        self.assertEqual(payload['status'], 'reservada')
        self.assertEqual(payload['equipamento']['id_patrimonio'], equipamento.id_patrimonio)

        list_response = self.client.get(reverse('api_reservas_estoque'))
        self.assertEqual(list_response.status_code, 200)
        self.assertEqual(list_response.json()['count'], 1)

        action_response = self.client.post(
            reverse('api_reserva_estoque_acao', args=[payload['id']]),
            {'acao': 'separar'},
        )
        self.assertEqual(action_response.status_code, 200)
        self.assertEqual(action_response.json()['status'], 'separada')

    def test_entrega_direta_registra_separacao_implicita(self):
        chamado = Chamado.objects.create(
            titulo='Entrega direta',
            descricao='Chamado usado para validar a trilha da reserva.',
            solicitante=self.user,
            status=StatusChamado.EM_ATENDIMENTO,
        )
        equipamento = Equipamento.objects.get(id_patrimonio='PAT-200001')
        reserva = criar_reserva_estoque(
            chamado=chamado,
            equipamento=equipamento,
            solicitante=self.user,
        )

        marcar_reserva_entregue(reserva=reserva, usuario=self.user)
        reserva.refresh_from_db()

        self.assertEqual(reserva.status, 'entregue')
        self.assertIsNotNone(reserva.separated_at)
        self.assertIsNotNone(reserva.delivered_at)
        self.assertEqual(reserva.separado_por, self.user)

    def test_api_equipamentos_permite_filtrar_por_status_e_tipo(self):
        equipamento = Equipamento.objects.get(id_patrimonio='PAT-200001')
        outro = Equipamento.objects.create(
            id_patrimonio='PAT-200002',
            tipo='notebook_padrao',
            marca='Dell',
            modelo='Latitude',
            status=StatusEquipamento.EM_USO,
            condicao='bom',
            score_saude=88,
        )

        response = self.client.get(reverse('api_equipamentos'), {'status': StatusEquipamento.EM_ESTOQUE, 'tipo': 'mouse'})

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload['count'], 1)
        self.assertEqual(payload['results'][0]['pk'], equipamento.pk)
        self.assertEqual(payload['results'][0]['id_patrimonio'], 'PAT-200001')
        self.assertNotEqual(payload['results'][0]['id_patrimonio'], outro.id_patrimonio)

    def test_reserva_inteligente_prefere_mesmo_site_com_boa_saude(self):
        destinatario = Usuario.objects.create_user(
            matricula='7001',
            password='test12345',
            first_name='Maria',
            last_name='Site',
            site='SP-Matriz',
            setor='TI',
        )
        chamado = Chamado.objects.create(
            titulo='Reserva inteligente',
            descricao='Chamado com item pendente.',
            solicitante=self.user,
            destinatario=destinatario,
            status=StatusChamado.FILA,
        )
        sincronizar_itens_solicitados(chamado=chamado, tipos_solicitados=['mouse'])
        item = chamado.itens_solicitados.get()
        equipamento_local = Equipamento.objects.get(id_patrimonio='PAT-200001')
        equipamento_local.site = 'SP-Matriz'
        equipamento_local.setor = 'TI'
        equipamento_local.score_saude = 88
        equipamento_local.save(update_fields=['site', 'setor', 'score_saude'])
        Equipamento.objects.create(
            id_patrimonio='PAT-200900',
            tipo='mouse',
            marca='Logitech',
            modelo='MX',
            status=StatusEquipamento.EM_ESTOQUE,
            condicao='bom',
            score_saude=99,
            site='RJ-Matriz',
            setor='TI',
        )

        plano = sugerir_reservas_inteligentes(chamado)

        self.assertEqual(plano['total_sugeridos'], 1)
        self.assertEqual(plano['sugestoes'][0]['item'], item)
        self.assertEqual(plano['sugestoes'][0]['equipamento'], equipamento_local)

        resultado = criar_reservas_inteligentes(chamado=chamado, solicitante=self.user)

        self.assertEqual(len(resultado['reservas']), 1)
        reserva = resultado['reservas'][0]
        self.assertEqual(reserva.item_solicitado, item)
        self.assertEqual(reserva.equipamento_id, equipamento_local.pk)
        equipamento_local.refresh_from_db()
        self.assertEqual(equipamento_local.status, StatusEquipamento.RESERVADO)

    def test_reserva_inteligente_nao_duplica_item_ja_reservado(self):
        chamado = Chamado.objects.create(
            titulo='Reserva parcial',
            descricao='Chamado com dois itens.',
            solicitante=self.user,
            status=StatusChamado.FILA,
        )
        sincronizar_itens_solicitados(chamado=chamado, tipos_solicitados=['mouse', 'adaptador'])
        itens = list(chamado.itens_solicitados.order_by('id'))
        primeiro = Equipamento.objects.get(id_patrimonio='PAT-200001')
        segundo = Equipamento.objects.create(
            id_patrimonio='PAT-200901',
            tipo='adaptador',
            marca='Dell',
            modelo='USB-C',
            status=StatusEquipamento.EM_ESTOQUE,
            condicao='bom',
            score_saude=91,
        )

        criar_reserva_estoque(chamado=chamado, item_solicitado=itens[0], equipamento=primeiro, solicitante=self.user)
        resultado = criar_reservas_inteligentes(chamado=chamado, solicitante=self.user)

        self.assertEqual(len(resultado['reservas']), 1)
        self.assertEqual(resultado['reservas'][0].item_solicitado, itens[1])
        self.assertEqual(resultado['reservas'][0].equipamento, segundo)
        self.assertEqual(ReservaEstoque.objects.filter(chamado=chamado).count(), 2)

    def test_reserva_inteligente_post_pelo_detalhe_do_chamado(self):
        chamado = Chamado.objects.create(
            titulo='Reserva pelo detalhe',
            descricao='Chamado para validar botao do playbook.',
            solicitante=self.user,
            status=StatusChamado.FILA,
        )
        sincronizar_itens_solicitados(chamado=chamado, tipos_solicitados=['mouse'])
        detalhe_url = reverse('detalhe_chamado', args=[chamado.pk])

        detail = self.client.get(detalhe_url)
        self.assertEqual(detail.status_code, 200)
        self.assertContains(detail, 'Reservar automaticamente')

        response = self.client.post(
            reverse('reserva_inteligente_estoque'),
            {
                'chamado': chamado.pk,
                'next': detalhe_url,
            },
        )

        self.assertRedirects(response, detalhe_url)
        self.assertEqual(ReservaEstoque.objects.filter(chamado=chamado).count(), 1)
        self.assertEqual(Equipamento.objects.get(id_patrimonio='PAT-200001').status, StatusEquipamento.RESERVADO)

    def test_reserva_em_lote_cria_multiplas_reservas(self):
        chamado = Chamado.objects.create(
            titulo='Reserva em lote',
            descricao='Chamado para validar reserva em lote.',
            solicitante=self.user,
            status=StatusChamado.FILA,
        )
        primeiro = Equipamento.objects.get(id_patrimonio='PAT-200001')
        segundo = Equipamento.objects.create(
            id_patrimonio='PAT-200003',
            tipo='mouse',
            marca='Dell',
            modelo='Mouse Pro',
            status=StatusEquipamento.EM_ESTOQUE,
            condicao='bom',
            score_saude=92,
        )

        response = self.client.post(
            reverse('estoque'),
            {
                'acao': 'reservar_lote',
                'chamado': chamado.pk,
                'equipamentos': [primeiro.id_patrimonio, segundo.id_patrimonio],
            },
        )

        self.assertRedirects(response, reverse('estoque'))
        self.assertEqual(ReservaEstoque.objects.filter(chamado=chamado).count(), 2)
        self.assertEqual(
            Equipamento.objects.filter(pk__in=[primeiro.pk, segundo.pk], status=StatusEquipamento.RESERVADO).count(),
            2,
        )

    def test_reserva_em_lote_rejeita_reserva_total_sem_selecao(self):
        chamado = Chamado.objects.create(
            titulo='Reserva filtrada',
            descricao='Chamado para validar bloqueio da reserva total sem selecao.',
            solicitante=self.user,
            status=StatusChamado.FILA,
        )
        primeiro = Equipamento.objects.get(id_patrimonio='PAT-200001')
        segundo = Equipamento.objects.create(
            id_patrimonio='PAT-200003',
            tipo='mouse',
            marca='Dell',
            modelo='Mouse Pro',
            status=StatusEquipamento.EM_ESTOQUE,
            condicao='bom',
            score_saude=92,
        )
        terceiro = Equipamento.objects.create(
            id_patrimonio='PAT-200999',
            tipo='mouse',
            marca='Dell',
            modelo='Mouse X',
            status=StatusEquipamento.EM_USO,
            condicao='bom',
            score_saude=89,
        )

        response = self.client.post(
            reverse('estoque'),
            {
                'acao': 'reservar_lote',
                'chamado': chamado.pk,
                'filtro_busca': 'PAT-20000',
                'reservar_todos_filtrados': '1',
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Selecione pelo menos um equipamento para reservar em lote.')
        self.assertEqual(ReservaEstoque.objects.filter(chamado=chamado).count(), 0)
        self.assertEqual(
            Equipamento.objects.filter(pk__in=[primeiro.pk, segundo.pk], status=StatusEquipamento.RESERVADO).count(),
            0,
        )
        terceiro.refresh_from_db()
        self.assertEqual(terceiro.status, StatusEquipamento.EM_USO)
