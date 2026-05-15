from django.test import TestCase
from django.urls import reverse

from accounts.models import NivelAcesso, Usuario
from equipamentos.models import Equipamento, StatusEquipamento

from .forms import EntregaEquipamentoChamadoForm
from .models import Chamado, EtapaFluxoChamado, StatusChamado
from .services import registrar_entregas_chamado, sincronizar_itens_solicitados


class ChamadoModelTests(TestCase):
    def setUp(self):
        self.solicitante = Usuario.objects.create_user(
            matricula='1001',
            password='senha-forte-123',
            first_name='Ana',
            last_name='Silva',
        )
        self.destinatario = Usuario.objects.create_user(
            matricula='1002',
            password='senha-forte-123',
            first_name='Bruno',
            last_name='Souza',
        )

    def test_criar_chamado_mostra_catalogo_de_solicitacoes(self):
        self.client.force_login(self.solicitante)
        response = self.client.get(reverse('criar_chamado'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Escolha o tipo de solicitacao')
        self.assertContains(response, 'Acompanhe a aprovação no próprio chamado')
        self.assertContains(response, 'Aguardando aprovação')
        self.assertContains(response, 'Workplace as a Service')
        self.assertContains(response, '?template=workplace')
        self.assertNotContains(response, 'Perifericos')
        self.assertNotContains(response, 'Fluxo livre')
        self.assertNotContains(response, '?template=perifericos')
        self.assertNotContains(response, '?template=padrao')

    def test_criar_chamado_com_template_perifericos_filtra_os_itens(self):
        self.client.force_login(self.solicitante)
        response = self.client.get(f"{reverse('criar_chamado')}?template=perifericos")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Solicitacao de Equipamento Perifericos')
        self.assertContains(response, 'Voltar aos modelos')
        self.assertContains(response, 'Como o chamado segue')
        self.assertContains(response, 'Aguardando aprovação')

        form = response.context['form']
        labels = [label for _, label in form.fields['equipamentos_solicitados'].choices]
        self.assertIn('Adaptador', labels)
        self.assertIn('Mouse', labels)
        self.assertIn('Teclado', labels)
        self.assertFalse(any('Notebook' in label for label in labels))
        self.assertFalse(any('Desktop' in label for label in labels))

    def test_encerrar_chamado_preenche_data_fechamento(self):
        chamado = Chamado.objects.create(
            titulo='Notebook sem acesso',
            descricao='Solicitacao de teste',
            solicitante=self.solicitante,
            status=StatusChamado.FILA,
        )

        self.assertIsNone(chamado.data_fechamento)

        chamado.encerrar()
        chamado.save()
        chamado.refresh_from_db()

        self.assertEqual(chamado.status, StatusChamado.ENCERRADO)
        self.assertIsNotNone(chamado.data_fechamento)

    def test_reabrir_chamado_remove_data_fechamento(self):
        chamado = Chamado.objects.create(
            titulo='Notebook sem acesso',
            descricao='Solicitacao de teste',
            solicitante=self.solicitante,
            status=StatusChamado.ENCERRADO,
        )

        self.assertIsNotNone(chamado.data_fechamento)

        chamado.status = StatusChamado.FILA
        chamado.save()
        chamado.refresh_from_db()

        self.assertIsNone(chamado.data_fechamento)

    def test_entrega_form_organiza_equipamentos_por_item(self):
        chamado = Chamado.objects.create(
            titulo='Funcionario novo',
            descricao='Entrega inicial do colaborador.',
            solicitante=self.solicitante,
            status=StatusChamado.FILA,
        )
        sincronizar_itens_solicitados(
            chamado=chamado,
            tipos_solicitados=['adaptador', 'mouse'],
        )

        Equipamento.objects.create(
            id_patrimonio='PAT-1001',
            tipo='adaptador',
            marca='Dell',
            modelo='A1',
            status=StatusEquipamento.EM_ESTOQUE,
            condicao='bom',
            score_saude=90,
        )
        Equipamento.objects.create(
            id_patrimonio='PAT-1002',
            tipo='mouse',
            marca='Logitech',
            modelo='M2',
            status=StatusEquipamento.EM_ESTOQUE,
            condicao='bom',
            score_saude=90,
        )

        form = EntregaEquipamentoChamadoForm(chamado=chamado)

        self.assertEqual(form.fields['itens_entrega'].widget.__class__.__name__, 'HiddenInput')
        self.assertEqual(form.itens_solicitados_total, 2)
        self.assertEqual(form.itens_selecionados_total, 0)
        self.assertEqual(form.equipamentos_compativeis_total, 2)
        self.assertEqual([grupo['label'] for grupo in form.equipamentos_compativeis_por_tipo], ['Adaptador', 'Mouse'])
        self.assertEqual(form.equipamentos_compativeis_por_tipo[0]['equipamentos'][0].id_patrimonio, 'PAT-1001')

    def test_detalhe_chamado_mostra_selecao_direta_dos_cards(self):
        operacional = Usuario.objects.create_user(
            matricula='2001',
            password='senha-forte-123',
            first_name='Carlos',
            last_name='Tecnico',
            nivel_acesso=NivelAcesso.TECNICO,
        )
        chamado = Chamado.objects.create(
            titulo='Entrega guiada',
            descricao='Teste da visualizacao por cards.',
            solicitante=self.solicitante,
            destinatario=self.destinatario,
            status=StatusChamado.FILA,
            fluxo_etapa=EtapaFluxoChamado.PRONTO_PARA_ENTREGA,
        )
        sincronizar_itens_solicitados(
            chamado=chamado,
            tipos_solicitados=['adaptador'],
        )
        Equipamento.objects.create(
            id_patrimonio='PAT-2001',
            tipo='adaptador',
            marca='Dell',
            modelo='A2',
            status=StatusEquipamento.EM_ESTOQUE,
            condicao='bom',
            score_saude=95,
        )

        self.client.force_login(operacional)
        response = self.client.get(reverse('detalhe_chamado', args=[chamado.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Fluxo de entrega')
        self.assertContains(response, 'Seleção por item')
        self.assertContains(response, 'Escolha 1 equipamento para cada item solicitado')
        self.assertContains(response, 'Cards de estoque')
        self.assertContains(response, 'data-select-equipamento')
        self.assertContains(response, 'id_itens_entrega')
        self.assertNotContains(response, 'Equipamento para entrega')
        self.assertContains(response, 'Colaborador')
        self.assertContains(response, self.destinatario.nome_completo)

    def test_fluxo_chamado_assumir_aprovar_e_liberar_retirada(self):
        analista = Usuario.objects.create_user(
            matricula='2005',
            password='senha-forte-123',
            first_name='Lia',
            last_name='Analista',
            nivel_acesso=NivelAcesso.ANALISTA,
        )
        chamado = Chamado.objects.create(
            titulo='Fluxo completo',
            descricao='Teste do fluxo de triagem e aprovacao.',
            solicitante=self.solicitante,
            destinatario=self.destinatario,
            status=StatusChamado.FILA,
        )

        self.client.force_login(analista)
        response = self.client.post(reverse('fluxo_chamado_action', args=[chamado.pk]), {'acao': 'assumir'})
        self.assertRedirects(response, reverse('detalhe_chamado', args=[chamado.pk]))
        chamado.refresh_from_db()
        self.assertEqual(chamado.responsavel, analista)
        self.assertEqual(chamado.status, StatusChamado.EM_ATENDIMENTO)
        self.assertEqual(chamado.fluxo_etapa, EtapaFluxoChamado.TRIAGEM)

        response = self.client.post(reverse('fluxo_chamado_action', args=[chamado.pk]), {'acao': 'enviar_aprovacao'})
        self.assertRedirects(response, reverse('detalhe_chamado', args=[chamado.pk]))
        chamado.refresh_from_db()
        self.assertEqual(chamado.fluxo_etapa, EtapaFluxoChamado.AGUARDANDO_APROVACAO)

        self.client.force_login(self.destinatario)
        pending = self.client.get(reverse('detalhe_chamado', args=[chamado.pk]))
        self.assertEqual(pending.status_code, 200)
        self.assertContains(pending, 'Sua aprovação está pendente')
        self.assertContains(pending, 'Aguardando aprovação')

        response = self.client.post(reverse('fluxo_chamado_action', args=[chamado.pk]), {'acao': 'aprovar_retirada'})
        self.assertRedirects(response, reverse('detalhe_chamado', args=[chamado.pk]))
        chamado.refresh_from_db()
        self.assertEqual(chamado.fluxo_etapa, EtapaFluxoChamado.APROVADO_PARA_RETIRADA)
        self.assertEqual(chamado.aprovado_por, self.destinatario)
        self.assertIsNotNone(chamado.aprovado_em)

        detail = self.client.get(reverse('detalhe_chamado', args=[chamado.pk]))
        self.assertEqual(detail.status_code, 200)
        self.assertContains(detail, 'Aprovação registrada')
        self.assertContains(detail, 'Fluxo inteligente')
        self.assertContains(detail, 'Aprovado para retirada')

    def test_termo_mostra_todos_os_itens_entregues(self):
        operacional = Usuario.objects.create_user(
            matricula='2002',
            password='senha-forte-123',
            first_name='Bianca',
            last_name='Tecnico',
            nivel_acesso=NivelAcesso.TECNICO,
        )
        chamado = Chamado.objects.create(
            titulo='Entrega completa',
            descricao='Teste do termo com multiplos itens.',
            solicitante=self.solicitante,
            destinatario=self.destinatario,
            status=StatusChamado.FILA,
        )
        sincronizar_itens_solicitados(
            chamado=chamado,
            tipos_solicitados=['adaptador', 'mouse'],
        )
        itens = list(chamado.itens_solicitados.order_by('id'))

        equipamento1 = Equipamento.objects.create(
            id_patrimonio='PAT-3001',
            tipo='adaptador',
            marca='Dell',
            modelo='A3',
            status=StatusEquipamento.EM_ESTOQUE,
            condicao='bom',
            score_saude=90,
        )
        equipamento2 = Equipamento.objects.create(
            id_patrimonio='PAT-3002',
            tipo='mouse',
            marca='Logitech',
            modelo='M3',
            status=StatusEquipamento.EM_ESTOQUE,
            condicao='bom',
            score_saude=90,
        )

        registrar_entregas_chamado(
            chamado=chamado,
            selecoes_itens={
                itens[0].id: equipamento1.id,
                itens[1].id: equipamento2.id,
            },
            realizado_por=operacional,
            observacoes='Entrega completa.',
            concluir_chamado=True,
        )

        self.assertTrue(chamado.movimentacoes.filter(usuario_novo=self.destinatario).exists())

        self.client.force_login(operacional)
        response = self.client.get(reverse('termo_chamado', args=[chamado.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Itens entregues')
        self.assertContains(response, 'Dados do colaborador')
        self.assertContains(response, self.destinatario.nome_completo)
        self.assertContains(response, 'Assinatura do colaborador')
        self.assertContains(response, 'PAT-3001')
        self.assertContains(response, 'PAT-3002')
        self.assertContains(response, 'Adaptador')
        self.assertContains(response, 'Mouse')

    def test_chamado_aberto_em_nome_de_outro_aparece_para_destinatario(self):
        gestor = Usuario.objects.create_user(
            matricula='3001',
            password='senha-forte-123',
            first_name='Daniel',
            last_name='Gestor',
            nivel_acesso=NivelAcesso.ADMIN,
        )
        chamado = Chamado.objects.create(
            titulo='Pedido em nome de terceiro',
            descricao='Gestor abriu a solicitacao para o colaborador.',
            solicitante=gestor,
            destinatario=self.destinatario,
            status=StatusChamado.FILA,
        )

        self.client.force_login(self.destinatario)
        response = self.client.get(reverse('chamados'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Acompanhe seus chamados e aprovações.')
        self.assertContains(response, 'Resumo da busca atual')
        self.assertContains(response, 'Abrir chamado')
        self.assertContains(response, 'Pedido em nome de terceiro')
        self.assertContains(response, 'Solicitacao feita pelo gestor')
        self.assertContains(response, gestor.nome_completo)
        self.assertContains(response, 'Acompanhamento rápido')

        detail = self.client.get(reverse('detalhe_chamado', args=[chamado.pk]))
        self.assertEqual(detail.status_code, 200)
        self.assertContains(detail, 'Origem da solicitação')
        self.assertContains(detail, gestor.nome_completo)
        self.assertContains(detail, self.destinatario.nome_completo)
