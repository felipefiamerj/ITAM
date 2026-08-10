import json
from datetime import timedelta

from django.core import mail
from django.test import TestCase
from django.test.utils import override_settings
from django.urls import reverse
from django.utils import timezone

from accounts.models import NivelAcesso, Usuario
from equipamentos.models import Equipamento, StatusEquipamento
from estoque.models import ReservaEstoque, StatusReservaEstoque
from estoque.services import criar_reserva_estoque
from notifications.models import Notification

from .forms import EntregaEquipamentoChamadoForm
from .models import (
    Chamado,
    ChamadoFluxoEvento,
    EtapaFluxoChamado,
    PrioridadeChamado,
    ServicoChamado,
    StatusChamado,
    StatusTermoAceite,
    TermoAceiteDigital,
)
from .services import (
    cobrar_assinaturas_pendentes_automaticamente,
    gerar_playbook_chamado,
    obter_ou_criar_aceite_termo,
    registrar_entregas_chamado,
    sincronizar_itens_solicitados,
    verificar_sla_etapas_chamados,
)
from .tasks import cobrar_assinaturas_termos_task, verificar_sla_etapas_chamados_task

ASSINATURA_TESTE_DATA_URL = (
    'data:image/png;base64,'
    'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAFgwJ/lIhgrwAAAABJRU5ErkJggg=='
)


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
        self.assertContains(response, 'O que você precisa?')
        self.assertContains(response, 'Acompanhe a aprovação no próprio chamado')
        self.assertContains(response, 'Aguardando aprovação')
        self.assertContains(response, 'Monte seu kit de trabalho')
        self.assertContains(response, '?template=workplace')
        self.assertContains(response, 'Peça periféricos')
        self.assertContains(response, 'Fluxo livre')
        self.assertContains(response, '?template=perifericos')
        self.assertContains(response, '?template=padrao')

    def test_criar_chamado_com_template_perifericos_filtra_os_itens(self):
        self.client.force_login(self.solicitante)
        response = self.client.get(f"{reverse('criar_chamado')}?template=perifericos")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Peça periféricos Mouse, teclado e conexões')
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

    def test_criar_chamado_registra_evento_inicial_de_fluxo(self):
        chamado = Chamado.objects.create(
            titulo='Evento inicial',
            descricao='Teste da trilha de fluxo.',
            solicitante=self.solicitante,
            destinatario=self.destinatario,
            status=StatusChamado.FILA,
        )

        evento = ChamadoFluxoEvento.objects.get(chamado=chamado)
        self.assertEqual(evento.etapa_anterior, '')
        self.assertEqual(evento.etapa_nova, EtapaFluxoChamado.SOLICITADO)
        self.assertEqual(evento.status_anterior, '')
        self.assertEqual(evento.status_novo, StatusChamado.FILA)

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
        self.assertContains(response, 'Progresso')
        self.assertContains(response, 'Clique em um card para escolher o equipamento daquele item')
        self.assertContains(response, 'Cards de estoque')
        self.assertContains(response, 'data-select-equipamento')
        self.assertContains(response, 'id_itens_entrega')
        self.assertNotContains(response, 'Equipamento para entrega')
        self.assertContains(response, 'Colaborador')
        self.assertContains(response, self.destinatario.nome_completo)

    def test_playbook_chamado_sinaliza_estoque_e_reserva(self):
        operacional = Usuario.objects.create_user(
            matricula='2003',
            password='senha-forte-123',
            first_name='Davi',
            last_name='Estoque',
            nivel_acesso=NivelAcesso.TECNICO,
        )
        chamado = Chamado.objects.create(
            titulo='Playbook entrega',
            descricao='Teste do roteiro automatico.',
            solicitante=self.solicitante,
            destinatario=self.destinatario,
            responsavel=operacional,
            servico_realizado=ServicoChamado.ENTREGA,
            status=StatusChamado.EM_ATENDIMENTO,
            fluxo_etapa=EtapaFluxoChamado.TRIAGEM,
        )
        sincronizar_itens_solicitados(chamado=chamado, tipos_solicitados=['mouse'])

        playbook = gerar_playbook_chamado(chamado)
        etapas = {etapa['key']: etapa for etapa in playbook['etapas']}
        self.assertEqual(etapas['estoque']['estado'], 'bloqueado')
        self.assertTrue(playbook['bloqueios'])

        equipamento = Equipamento.objects.create(
            id_patrimonio='PAT-2301',
            tipo='mouse',
            marca='Logitech',
            modelo='M4',
            status=StatusEquipamento.EM_ESTOQUE,
            condicao='bom',
            score_saude=91,
        )
        item = chamado.itens_solicitados.get()
        criar_reserva_estoque(
            chamado=chamado,
            equipamento=equipamento,
            solicitante=operacional,
            item_solicitado=item,
        )

        playbook = gerar_playbook_chamado(chamado)
        etapas = {etapa['key']: etapa for etapa in playbook['etapas']}
        self.assertEqual(etapas['estoque']['estado'], 'concluido')
        self.assertFalse(playbook['bloqueios'])
        self.assertEqual(playbook['proxima_acao']['key'], 'aprovacao')

    def test_detalhe_chamado_exibe_playbook_automatico(self):
        operacional = Usuario.objects.create_user(
            matricula='2004',
            password='senha-forte-123',
            first_name='Elisa',
            last_name='Tecnica',
            nivel_acesso=NivelAcesso.TECNICO,
        )
        chamado = Chamado.objects.create(
            titulo='Playbook visivel',
            descricao='Teste da visualizacao do roteiro.',
            solicitante=self.solicitante,
            destinatario=self.destinatario,
            responsavel=operacional,
            servico_realizado=ServicoChamado.ENTREGA,
            status=StatusChamado.AGUARDANDO_ATENDIMENTO,
            fluxo_etapa=EtapaFluxoChamado.AGUARDANDO_APROVACAO,
        )
        sincronizar_itens_solicitados(chamado=chamado, tipos_solicitados=['adaptador'])
        equipamento = Equipamento.objects.create(
            id_patrimonio='PAT-2401',
            tipo='adaptador',
            marca='Dell',
            modelo='A4',
            status=StatusEquipamento.EM_ESTOQUE,
            condicao='bom',
            score_saude=93,
        )
        criar_reserva_estoque(
            chamado=chamado,
            equipamento=equipamento,
            solicitante=operacional,
            item_solicitado=chamado.itens_solicitados.get(),
        )

        self.client.force_login(operacional)
        response = self.client.get(reverse('detalhe_chamado', args=[chamado.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Playbook automatico')
        self.assertContains(response, 'Entrega guiada')
        self.assertContains(response, 'Reserva/separacao de estoque')
        self.assertContains(response, 'Aprovacao do colaborador')
        self.assertContains(response, 'Proxima acao')

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
        self.assertContains(detail, 'Estado atual')
        self.assertContains(detail, 'Aprovado para retirada')
        self.assertTrue(
            ChamadoFluxoEvento.objects.filter(
                chamado=chamado,
                etapa_anterior=EtapaFluxoChamado.AGUARDANDO_APROVACAO,
                etapa_nova=EtapaFluxoChamado.APROVADO_PARA_RETIRADA,
                usuario=self.destinatario,
            ).exists()
        )
        self.assertContains(detail, 'Historico do fluxo')

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

    def test_detalhe_chamado_exibe_link_de_assinatura_digital(self):
        operacional = Usuario.objects.create_user(
            matricula='2003',
            password='senha-forte-123',
            first_name='Nina',
            last_name='Tecnico',
            nivel_acesso=NivelAcesso.TECNICO,
        )
        chamado = Chamado.objects.create(
            titulo='Entrega com aceite',
            descricao='Teste do link seguro.',
            solicitante=self.solicitante,
            destinatario=self.destinatario,
            status=StatusChamado.ENCERRADO,
        )

        self.client.force_login(operacional)
        response = self.client.get(reverse('detalhe_chamado', args=[chamado.pk]))

        aceite = TermoAceiteDigital.objects.get(chamado=chamado)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Aceite digital do termo')
        self.assertContains(response, reverse('assinar_termo_chamado', args=[aceite.token]))
        self.assertContains(response, 'Abrir assinatura')

    @override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
    def test_enviar_link_assinatura_notifica_e_envia_email(self):
        self.destinatario.email = 'bruno@example.com'
        self.destinatario.save(update_fields=['email'])
        operacional = Usuario.objects.create_user(
            matricula='2010',
            password='senha-forte-123',
            first_name='Iris',
            last_name='Tecnico',
            nivel_acesso=NivelAcesso.TECNICO,
        )
        chamado = Chamado.objects.create(
            titulo='Envio de aceite',
            descricao='Teste de envio do link.',
            solicitante=self.solicitante,
            destinatario=self.destinatario,
            status=StatusChamado.ENCERRADO,
        )
        aceite = obter_ou_criar_aceite_termo(chamado)

        self.client.force_login(operacional)
        response = self.client.post(reverse('enviar_assinatura_termo', args=[chamado.pk]))

        self.assertRedirects(response, reverse('detalhe_chamado', args=[chamado.pk]))
        aceite.refresh_from_db()
        self.assertEqual(aceite.envio_total, 1)
        self.assertTrue(aceite.email_enviado)
        self.assertEqual(aceite.email_destino, 'bruno@example.com')
        self.assertIsNotNone(aceite.enviado_em)
        self.assertEqual(aceite.enviado_por, operacional)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn(str(aceite.token), mail.outbox[0].body)
        self.assertTrue(
            Notification.objects.filter(
                user=self.destinatario,
                title=f'Termo para assinatura: chamado #{chamado.pk}',
            ).exists()
        )

    def test_link_expirado_nao_permite_assinar(self):
        chamado = Chamado.objects.create(
            titulo='Assinatura vencida',
            descricao='Teste de expiracao do aceite.',
            solicitante=self.solicitante,
            destinatario=self.destinatario,
            status=StatusChamado.ENCERRADO,
        )
        aceite = obter_ou_criar_aceite_termo(chamado)
        aceite.expires_at = timezone.now() - timedelta(minutes=5)
        aceite.save(update_fields=['expires_at'])
        url = reverse('assinar_termo_chamado', args=[aceite.token])

        response = self.client.post(
            url,
            {
                'assinatura_data_url': ASSINATURA_TESTE_DATA_URL,
                'aceite_termos': 'on',
            },
        )

        self.assertRedirects(response, url)
        aceite.refresh_from_db()
        self.assertEqual(aceite.status, StatusTermoAceite.PENDENTE)
        self.assertFalse(aceite.documento_hash)

        page = self.client.get(url)
        self.assertContains(page, 'Link expirado')
        self.assertNotContains(page, 'Concluir assinatura')

    def test_renovar_link_assinatura_atualiza_token_e_expiracao(self):
        operacional = Usuario.objects.create_user(
            matricula='2011',
            password='senha-forte-123',
            first_name='Ravi',
            last_name='Tecnico',
            nivel_acesso=NivelAcesso.TECNICO,
        )
        chamado = Chamado.objects.create(
            titulo='Renovar aceite',
            descricao='Teste de novo token.',
            solicitante=self.solicitante,
            destinatario=self.destinatario,
            status=StatusChamado.ENCERRADO,
        )
        aceite = obter_ou_criar_aceite_termo(chamado)
        token_antigo = aceite.token
        aceite.expires_at = timezone.now() - timedelta(days=1)
        aceite.enviado_em = timezone.now() - timedelta(days=2)
        aceite.envio_total = 2
        aceite.email_enviado = True
        aceite.email_destino = 'antigo@example.com'
        aceite.save(update_fields=['expires_at', 'enviado_em', 'envio_total', 'email_enviado', 'email_destino'])

        self.client.force_login(operacional)
        response = self.client.post(reverse('renovar_assinatura_termo', args=[chamado.pk]))

        self.assertRedirects(response, reverse('detalhe_chamado', args=[chamado.pk]))
        aceite.refresh_from_db()
        self.assertNotEqual(aceite.token, token_antigo)
        self.assertGreater(aceite.expires_at, timezone.now())
        self.assertIsNone(aceite.enviado_em)
        self.assertFalse(aceite.email_enviado)
        self.assertEqual(aceite.email_destino, '')

    @override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
    def test_entrega_concluida_envia_link_assinatura(self):
        self.destinatario.email = 'bruno@example.com'
        self.destinatario.save(update_fields=['email'])
        operacional = Usuario.objects.create_user(
            matricula='2012',
            password='senha-forte-123',
            first_name='Maya',
            last_name='Tecnico',
            nivel_acesso=NivelAcesso.TECNICO,
        )
        chamado = Chamado.objects.create(
            titulo='Entrega final com aceite',
            descricao='Teste do disparo automatico.',
            solicitante=self.solicitante,
            destinatario=self.destinatario,
            status=StatusChamado.EM_ATENDIMENTO,
            fluxo_etapa=EtapaFluxoChamado.PRONTO_PARA_ENTREGA,
        )
        sincronizar_itens_solicitados(chamado=chamado, tipos_solicitados=['mouse'])
        item = chamado.itens_solicitados.get()
        equipamento = Equipamento.objects.create(
            id_patrimonio='PAT-3501',
            tipo='mouse',
            marca='Logitech',
            modelo='M5',
            status=StatusEquipamento.EM_ESTOQUE,
            condicao='bom',
            score_saude=90,
        )

        self.client.force_login(operacional)
        response = self.client.post(
            reverse('entregar_equipamento_chamado', args=[chamado.pk]),
            {
                'itens_entrega': json.dumps({str(item.pk): equipamento.pk}),
                'concluir_chamado': 'on',
            },
        )

        self.assertRedirects(response, reverse('detalhe_chamado', args=[chamado.pk]))
        chamado.refresh_from_db()
        aceite = TermoAceiteDigital.objects.get(chamado=chamado)
        self.assertEqual(chamado.status, StatusChamado.ENCERRADO)
        self.assertEqual(aceite.envio_total, 1)
        self.assertTrue(aceite.email_enviado)
        self.assertEqual(len(mail.outbox), 1)
        self.assertTrue(
            ChamadoFluxoEvento.objects.filter(
                chamado=chamado,
                etapa_nova=EtapaFluxoChamado.ENCERRADO,
                status_novo=StatusChamado.ENCERRADO,
                usuario=operacional,
            ).exists()
        )

    def test_painel_termos_bloqueia_usuario_nao_operacional(self):
        self.client.force_login(self.solicitante)
        response = self.client.get(reverse('painel_termos_chamados'))

        self.assertRedirects(response, reverse('chamados'))

    def test_painel_termos_filtra_expirados(self):
        operacional = Usuario.objects.create_user(
            matricula='2013',
            password='senha-forte-123',
            first_name='Theo',
            last_name='Tecnico',
            nivel_acesso=NivelAcesso.TECNICO,
        )
        chamado_expirado = Chamado.objects.create(
            titulo='Termo vencido',
            descricao='Teste do filtro de expirados.',
            solicitante=self.solicitante,
            destinatario=self.destinatario,
            responsavel=operacional,
            status=StatusChamado.ENCERRADO,
        )
        chamado_ativo = Chamado.objects.create(
            titulo='Termo ativo',
            descricao='Teste do filtro de expirados.',
            solicitante=self.solicitante,
            destinatario=self.destinatario,
            responsavel=operacional,
            status=StatusChamado.ENCERRADO,
        )
        aceite_expirado = obter_ou_criar_aceite_termo(chamado_expirado)
        aceite_expirado.expires_at = timezone.now() - timedelta(days=1)
        aceite_expirado.save(update_fields=['expires_at'])
        obter_ou_criar_aceite_termo(chamado_ativo)

        self.client.force_login(operacional)
        response = self.client.get(reverse('painel_termos_chamados'), {'situacao': 'expirados'})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Termo vencido')
        self.assertContains(response, 'Expirado')
        self.assertNotContains(response, 'Termo ativo')

    @override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
    def test_painel_termos_cobranca_em_massa_renova_expirados_e_envia(self):
        self.destinatario.email = 'bruno@example.com'
        self.destinatario.save(update_fields=['email'])
        outro_destinatario = Usuario.objects.create_user(
            matricula='1003',
            password='senha-forte-123',
            first_name='Clara',
            last_name='Moura',
            email='clara@example.com',
        )
        operacional = Usuario.objects.create_user(
            matricula='2014',
            password='senha-forte-123',
            first_name='Vera',
            last_name='Tecnico',
            nivel_acesso=NivelAcesso.TECNICO,
        )
        chamado_expirado = Chamado.objects.create(
            titulo='Cobrar vencido',
            descricao='Teste da cobranca em massa.',
            solicitante=self.solicitante,
            destinatario=self.destinatario,
            responsavel=operacional,
            status=StatusChamado.ENCERRADO,
        )
        chamado_ativo = Chamado.objects.create(
            titulo='Cobrar ativo',
            descricao='Teste da cobranca em massa.',
            solicitante=self.solicitante,
            destinatario=outro_destinatario,
            responsavel=operacional,
            status=StatusChamado.ENCERRADO,
        )
        aceite_expirado = obter_ou_criar_aceite_termo(chamado_expirado)
        aceite_expirado.expires_at = timezone.now() - timedelta(days=1)
        aceite_expirado.save(update_fields=['expires_at'])
        token_antigo = aceite_expirado.token
        aceite_ativo = obter_ou_criar_aceite_termo(chamado_ativo)

        self.client.force_login(operacional)
        response = self.client.post(
            reverse('painel_termos_chamados'),
            {
                'acao': 'selecionados',
                'situacao': 'pendentes',
                'aceites': [str(aceite_expirado.pk), str(aceite_ativo.pk)],
            },
        )

        self.assertRedirects(response, f"{reverse('painel_termos_chamados')}?situacao=pendentes")
        aceite_expirado.refresh_from_db()
        aceite_ativo.refresh_from_db()
        self.assertNotEqual(aceite_expirado.token, token_antigo)
        self.assertGreater(aceite_expirado.expires_at, timezone.now())
        self.assertEqual(aceite_expirado.envio_total, 1)
        self.assertEqual(aceite_ativo.envio_total, 1)
        self.assertEqual(len(mail.outbox), 2)
        self.assertTrue(
            Notification.objects.filter(
                user=self.destinatario,
                title=f'Termo para assinatura: chamado #{chamado_expirado.pk}',
            ).exists()
        )

    @override_settings(
        EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
        SITE_URL='https://itam.example.com',
        ITAM_TERMO_ASSINATURA_COBRANCA_AUTOMATICA=True,
        ITAM_TERMO_ASSINATURA_COBRANCA_INTERVALO_DIAS=1,
    )
    def test_cobranca_automatica_renova_expirados_e_pula_envio_recente(self):
        self.destinatario.email = 'bruno@example.com'
        self.destinatario.save(update_fields=['email'])
        outro_destinatario = Usuario.objects.create_user(
            matricula='1004',
            password='senha-forte-123',
            first_name='Davi',
            last_name='Costa',
            email='davi@example.com',
        )
        operacional = Usuario.objects.create_user(
            matricula='2015',
            password='senha-forte-123',
            first_name='Zoe',
            last_name='Operacao',
            nivel_acesso=NivelAcesso.TECNICO,
        )
        agora = timezone.now()
        chamado_expirado = Chamado.objects.create(
            titulo='Automatico vencido',
            descricao='Teste da cobranca automatica.',
            solicitante=self.solicitante,
            destinatario=self.destinatario,
            responsavel=operacional,
            status=StatusChamado.ENCERRADO,
        )
        chamado_recente = Chamado.objects.create(
            titulo='Automatico recente',
            descricao='Teste da cobranca automatica.',
            solicitante=self.solicitante,
            destinatario=outro_destinatario,
            responsavel=operacional,
            status=StatusChamado.ENCERRADO,
        )
        aceite_expirado = obter_ou_criar_aceite_termo(chamado_expirado)
        aceite_expirado.expires_at = agora - timedelta(days=1)
        aceite_expirado.enviado_em = agora - timedelta(hours=2)
        aceite_expirado.envio_total = 1
        aceite_expirado.save(update_fields=['expires_at', 'enviado_em', 'envio_total'])
        token_antigo = aceite_expirado.token

        aceite_recente = obter_ou_criar_aceite_termo(chamado_recente)
        aceite_recente.enviado_em = agora - timedelta(hours=2)
        aceite_recente.envio_total = 3
        aceite_recente.save(update_fields=['enviado_em', 'envio_total'])

        resumo = cobrar_assinaturas_pendentes_automaticamente(now=agora)

        aceite_expirado.refresh_from_db()
        aceite_recente.refresh_from_db()
        self.assertEqual(resumo['selecionados'], 1)
        self.assertEqual(resumo['enviados'], 1)
        self.assertEqual(resumo['renovados'], 1)
        self.assertNotEqual(aceite_expirado.token, token_antigo)
        self.assertEqual(aceite_expirado.envio_total, 2)
        self.assertEqual(aceite_recente.envio_total, 3)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn('https://itam.example.com', mail.outbox[0].body)
        self.assertTrue(
            Notification.objects.filter(
                user=operacional,
                title='Cobranca automatica de termos digitais',
            ).exists()
        )

    @override_settings(ITAM_TERMO_ASSINATURA_COBRANCA_AUTOMATICA=False)
    def test_task_cobranca_automatica_respeita_configuracao_desativada(self):
        chamado = Chamado.objects.create(
            titulo='Automatico desligado',
            descricao='Teste da task desativada.',
            solicitante=self.solicitante,
            destinatario=self.destinatario,
            status=StatusChamado.ENCERRADO,
        )
        obter_ou_criar_aceite_termo(chamado)

        resumo = cobrar_assinaturas_termos_task()

        self.assertFalse(resumo['ativo'])
        self.assertEqual(resumo['enviados'], 0)

    def test_compliance_termos_exibe_metricas_e_evidencia_assinada(self):
        operacional = Usuario.objects.create_user(
            matricula='2016',
            password='senha-forte-123',
            first_name='Gael',
            last_name='Auditor',
            nivel_acesso=NivelAcesso.TECNICO,
        )
        chamado = Chamado.objects.create(
            titulo='Compliance assinado',
            descricao='Teste do relatorio de compliance.',
            solicitante=self.solicitante,
            destinatario=self.destinatario,
            responsavel=operacional,
            status=StatusChamado.ENCERRADO,
        )
        chamado.data_fechamento = timezone.now() - timedelta(hours=4)
        chamado.save(update_fields=['data_fechamento'])
        aceite = obter_ou_criar_aceite_termo(chamado)
        self.client.post(
            reverse('assinar_termo_chamado', args=[aceite.token]),
            {
                'assinatura_data_url': ASSINATURA_TESTE_DATA_URL,
                'aceite_termos': 'on',
            },
        )
        aceite.refresh_from_db()

        self.client.force_login(operacional)
        response = self.client.get(reverse('compliance_termos_chamados'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Evidencias dos termos digitais')
        self.assertContains(response, 'Compliance assinado')
        self.assertContains(response, self.destinatario.nome_completo)
        self.assertContains(response, 'Hash OK')
        self.assertContains(response, aceite.documento_hash_curto)

    def test_compliance_termos_csv_exporta_linhas_filtradas(self):
        operacional = Usuario.objects.create_user(
            matricula='2017',
            password='senha-forte-123',
            first_name='Luna',
            last_name='Auditor',
            nivel_acesso=NivelAcesso.TECNICO,
        )
        chamado = Chamado.objects.create(
            titulo='Compliance CSV',
            descricao='Teste de exportacao CSV.',
            solicitante=self.solicitante,
            destinatario=self.destinatario,
            responsavel=operacional,
            status=StatusChamado.ENCERRADO,
        )
        obter_ou_criar_aceite_termo(chamado)

        self.client.force_login(operacional)
        response = self.client.get(reverse('compliance_termos_csv'), {'q': str(chamado.pk)})

        self.assertEqual(response.status_code, 200)
        self.assertIn('text/csv', response['Content-Type'])
        self.assertIn('compliance-termos-digitais.csv', response['Content-Disposition'])
        content = response.content.decode('utf-8')
        self.assertIn('Chamado,Titulo,Colaborador', content)
        self.assertIn('Compliance CSV', content)
        self.assertIn(self.destinatario.matricula, content)

    def test_compliance_termos_pdf_exporta_documento(self):
        operacional = Usuario.objects.create_user(
            matricula='2018',
            password='senha-forte-123',
            first_name='Noah',
            last_name='Auditor',
            nivel_acesso=NivelAcesso.TECNICO,
        )
        chamado = Chamado.objects.create(
            titulo='Compliance PDF',
            descricao='Teste de exportacao PDF.',
            solicitante=self.solicitante,
            destinatario=self.destinatario,
            responsavel=operacional,
            status=StatusChamado.ENCERRADO,
        )
        obter_ou_criar_aceite_termo(chamado)

        self.client.force_login(operacional)
        response = self.client.get(reverse('compliance_termos_pdf'), {'q': str(chamado.pk)})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/pdf')
        self.assertTrue(response.content.startswith(b'%PDF'))

    def test_produtividade_operacional_bloqueia_usuario_nao_operacional(self):
        self.client.force_login(self.solicitante)
        response = self.client.get(reverse('produtividade_operacional'))

        self.assertRedirects(response, reverse('chamados'))

    def test_produtividade_operacional_exibe_gargalos_e_ranking(self):
        operacional = Usuario.objects.create_user(
            matricula='2019',
            password='senha-forte-123',
            first_name='Kai',
            last_name='Produtividade',
            nivel_acesso=NivelAcesso.TECNICO,
        )
        agora = timezone.now()
        chamado_aberto = Chamado.objects.create(
            titulo='Gargalo estoque',
            descricao='Teste do painel de produtividade.',
            solicitante=self.solicitante,
            destinatario=self.destinatario,
            responsavel=operacional,
            status=StatusChamado.EM_ATENDIMENTO,
            fluxo_etapa=EtapaFluxoChamado.AGUARDANDO_ESTOQUE,
            prioridade=PrioridadeChamado.CRITICA,
        )
        Chamado.objects.filter(pk=chamado_aberto.pk).update(
            created_at=agora - timedelta(days=2),
            updated_at=agora - timedelta(days=1),
        )
        chamado_fechado = Chamado.objects.create(
            titulo='Fechado rapido',
            descricao='Teste do ranking de fechamento.',
            solicitante=self.solicitante,
            destinatario=self.destinatario,
            responsavel=operacional,
            status=StatusChamado.ENCERRADO,
        )
        Chamado.objects.filter(pk=chamado_fechado.pk).update(
            created_at=agora - timedelta(hours=6),
            data_fechamento=agora - timedelta(hours=1),
            updated_at=agora - timedelta(hours=1),
        )

        self.client.force_login(operacional)
        response = self.client.get(reverse('produtividade_operacional'), {'periodo': '30'})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Gargalos, SLA e ritmo da operação')
        self.assertContains(response, 'Gargalo estoque')
        self.assertContains(response, 'Aguardando estoque')
        self.assertContains(response, operacional.nome_completo)
        self.assertContains(response, 'Fechado rapido')
        self.assertContains(response, 'Fechamentos por responsavel')

    @override_settings(
        ITAM_SLA_ETAPA_MINUTOS='aguardando_estoque:60',
        ITAM_SLA_ETAPA_ALERTA_PERCENTUAL=50,
    )
    def test_verificar_sla_etapa_escalona_evento_e_nao_duplica(self):
        operacional = Usuario.objects.create_user(
            matricula='2020',
            password='senha-forte-123',
            first_name='Sara',
            last_name='SLA',
            nivel_acesso=NivelAcesso.TECNICO,
        )
        agora = timezone.now()
        chamado = Chamado.objects.create(
            titulo='SLA etapa estoque',
            descricao='Teste de SLA por etapa.',
            solicitante=self.solicitante,
            destinatario=self.destinatario,
            responsavel=operacional,
            status=StatusChamado.AGUARDANDO_ATENDIMENTO,
            fluxo_etapa=EtapaFluxoChamado.AGUARDANDO_ESTOQUE,
        )
        evento = ChamadoFluxoEvento.objects.get(chamado=chamado)
        ChamadoFluxoEvento.objects.filter(pk=evento.pk).update(criado_em=agora - timedelta(minutes=90))

        resumo = verificar_sla_etapas_chamados(now=agora)

        evento.refresh_from_db()
        self.assertEqual(resumo['avaliados'], 1)
        self.assertEqual(resumo['escalados'], 1)
        self.assertIsNotNone(evento.sla_alertado_em)
        self.assertIsNotNone(evento.sla_escalado_em)
        self.assertTrue(
            Notification.objects.filter(
                user=operacional,
                title=f'SLA da etapa escalonado: chamado #{chamado.pk}',
            ).exists()
        )

        novo_resumo = verificar_sla_etapas_chamados(now=agora + timedelta(minutes=5))
        self.assertEqual(novo_resumo['escalados'], 0)
        self.assertEqual(
            Notification.objects.filter(
                user=operacional,
                title=f'SLA da etapa escalonado: chamado #{chamado.pk}',
            ).count(),
            1,
        )

    @override_settings(
        ITAM_SLA_ETAPA_MINUTOS='em_separacao:60',
        ITAM_SLA_ETAPA_ALERTA_PERCENTUAL=50,
    )
    def test_task_sla_etapa_alerta_evento_atual(self):
        operacional = Usuario.objects.create_user(
            matricula='2021',
            password='senha-forte-123',
            first_name='Tais',
            last_name='SLA',
            nivel_acesso=NivelAcesso.TECNICO,
        )
        agora = timezone.now()
        chamado = Chamado.objects.create(
            titulo='SLA etapa separacao',
            descricao='Teste de alerta por etapa.',
            solicitante=self.solicitante,
            destinatario=self.destinatario,
            responsavel=operacional,
            status=StatusChamado.EM_ATENDIMENTO,
            fluxo_etapa=EtapaFluxoChamado.EM_SEPARACAO,
        )
        evento = ChamadoFluxoEvento.objects.get(chamado=chamado)
        ChamadoFluxoEvento.objects.filter(pk=evento.pk).update(criado_em=agora - timedelta(minutes=40))

        with override_settings(USE_TZ=True):
            resumo = verificar_sla_etapas_chamados_task()

        evento.refresh_from_db()
        self.assertGreaterEqual(resumo['avaliados'], 1)
        self.assertEqual(resumo['alertados'], 1)
        self.assertEqual(resumo['escalados'], 0)
        self.assertIsNotNone(evento.sla_alertado_em)
        self.assertIsNone(evento.sla_escalado_em)

    def test_link_publico_de_assinatura_registra_evidencias(self):
        chamado = Chamado.objects.create(
            titulo='Assinatura publica',
            descricao='Teste do aceite por token.',
            solicitante=self.solicitante,
            destinatario=self.destinatario,
            status=StatusChamado.ENCERRADO,
        )
        sincronizar_itens_solicitados(chamado=chamado, tipos_solicitados=['mouse'])
        aceite = obter_ou_criar_aceite_termo(chamado)
        url = reverse('assinar_termo_chamado', args=[aceite.token])

        response = self.client.post(
            url,
            {
                'assinatura_data_url': ASSINATURA_TESTE_DATA_URL,
                'aceite_termos': 'on',
            },
            HTTP_USER_AGENT='Navegador de teste',
        )

        self.assertRedirects(response, url)
        aceite.refresh_from_db()
        self.assertEqual(aceite.status, StatusTermoAceite.ASSINADO)
        self.assertEqual(aceite.nome_assinante, self.destinatario.nome_completo)
        self.assertEqual(aceite.matricula_assinante, self.destinatario.matricula)
        self.assertEqual(len(aceite.documento_hash), 64)
        self.assertEqual(aceite.user_agent, 'Navegador de teste')
        self.assertTrue(aceite.assinatura_data_url.startswith('data:image/png;base64,'))

        signed_page = self.client.get(url)
        self.assertContains(signed_page, 'Termo assinado')
        self.assertContains(signed_page, aceite.documento_hash)

    def test_termo_html_exibe_aceite_digital_assinado(self):
        operacional = Usuario.objects.create_user(
            matricula='2004',
            password='senha-forte-123',
            first_name='Otavio',
            last_name='Tecnico',
            nivel_acesso=NivelAcesso.TECNICO,
        )
        chamado = Chamado.objects.create(
            titulo='Termo com hash',
            descricao='Teste do termo assinado.',
            solicitante=self.solicitante,
            destinatario=self.destinatario,
            responsavel=operacional,
            status=StatusChamado.ENCERRADO,
        )
        aceite = obter_ou_criar_aceite_termo(chamado)
        self.client.force_login(self.destinatario)
        self.client.post(
            reverse('assinar_termo_chamado', args=[aceite.token]),
            {
                'assinatura_data_url': ASSINATURA_TESTE_DATA_URL,
                'aceite_termos': 'on',
            },
        )
        aceite.refresh_from_db()

        self.client.force_login(operacional)
        response = self.client.get(reverse('termo_chamado', args=[chamado.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Aceite digital')
        self.assertContains(response, 'Assinatura digital do colaborador')
        self.assertContains(response, aceite.documento_hash_curto)
        self.assertContains(response, self.destinatario.nome_completo)

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

    def test_api_chamados_retorna_lista_para_operacional(self):
        tecnico = Usuario.objects.create_user(
            matricula='3005',
            password='senha-forte-123',
            first_name='Tecnico',
            last_name='API',
            nivel_acesso=NivelAcesso.TECNICO,
        )
        chamado = Chamado.objects.create(
            titulo='API de chamados',
            descricao='Chamado usado para validar o endpoint da lista.',
            solicitante=self.solicitante,
            destinatario=self.destinatario,
            responsavel=tecnico,
            status=StatusChamado.EM_ATENDIMENTO,
            fluxo_etapa=EtapaFluxoChamado.TRIAGEM,
        )

        self.client.force_login(tecnico)
        response = self.client.get(reverse('api_chamados'))

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload['count'], 1)
        self.assertEqual(payload['results'][0]['id'], chamado.pk)
        self.assertEqual(payload['results'][0]['fluxo_etapa'], EtapaFluxoChamado.TRIAGEM)

    def test_admin_pode_excluir_chamado_e_devolver_reservas(self):
        admin = Usuario.objects.create_user(
            matricula='3010',
            password='senha-forte-123',
            first_name='Admin',
            last_name='Chamados',
            nivel_acesso=NivelAcesso.ADMIN,
        )
        chamado = Chamado.objects.create(
            titulo='Chamado para exclusao',
            descricao='Chamado criado para validar a exclusao administrativa.',
            solicitante=admin,
            status=StatusChamado.FILA,
        )
        primeiro = Equipamento.objects.create(
            id_patrimonio='PAT-4001',
            tipo='mouse',
            marca='Logitech',
            modelo='M100',
            status=StatusEquipamento.EM_ESTOQUE,
            condicao='bom',
            score_saude=95,
        )
        segundo = Equipamento.objects.create(
            id_patrimonio='PAT-4002',
            tipo='mouse',
            marca='Dell',
            modelo='MS111',
            status=StatusEquipamento.EM_ESTOQUE,
            condicao='bom',
            score_saude=93,
        )

        criar_reserva_estoque(
            chamado=chamado,
            equipamento=primeiro,
            solicitante=admin,
        )
        criar_reserva_estoque(
            chamado=chamado,
            equipamento=segundo,
            solicitante=admin,
        )

        self.client.force_login(admin)
        response = self.client.post(reverse('excluir_chamado', args=[chamado.pk]))

        self.assertRedirects(response, reverse('chamados'))
        self.assertFalse(Chamado.objects.filter(pk=chamado.pk).exists())
        self.assertEqual(ReservaEstoque.objects.filter(chamado_id=chamado.pk).count(), 0)
        self.assertFalse(
            ReservaEstoque.objects.filter(
                chamado_id=chamado.pk,
                status__in=[StatusReservaEstoque.RESERVADA, StatusReservaEstoque.SEPARADA],
            ).exists()
        )

        primeiro.refresh_from_db()
        segundo.refresh_from_db()
        self.assertEqual(primeiro.status, StatusEquipamento.EM_ESTOQUE)
        self.assertEqual(segundo.status, StatusEquipamento.EM_ESTOQUE)

    def test_nao_admin_nao_pode_excluir_chamado(self):
        tecnico = Usuario.objects.create_user(
            matricula='3011',
            password='senha-forte-123',
            first_name='Tecnico',
            last_name='Bloqueado',
            nivel_acesso=NivelAcesso.TECNICO,
        )
        chamado = Chamado.objects.create(
            titulo='Chamado protegido',
            descricao='Chamado criado para validar o bloqueio de exclusao.',
            solicitante=self.solicitante,
            status=StatusChamado.FILA,
        )

        self.client.force_login(tecnico)
        response = self.client.post(reverse('excluir_chamado', args=[chamado.pk]))

        self.assertRedirects(response, reverse('detalhe_chamado', args=[chamado.pk]))
        self.assertTrue(Chamado.objects.filter(pk=chamado.pk).exists())
