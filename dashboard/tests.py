import hashlib
import json
import shutil
import tempfile
import uuid
from datetime import timedelta
from io import StringIO
from pathlib import Path
from unittest.mock import MagicMock, patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import CommandError, call_command
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from accounts.models import NivelAcesso, Usuario
from chamados.models import Chamado, StatusChamado
from equipamentos.models import (
    AgenteMonitoramento,
    DivergenciaInventario,
    EntradaLote,
    Equipamento,
    StatusEquipamento,
    StatusMonitoramento,
)
from itam.settings import _database_config_from_url, config

from .backup_service import (
    BackupOperationError,
    BackupSet,
    BackupTaskStatus,
    list_backup_sets,
    resolve_restore_point,
)
from .forms import BackupConfigurationForm
from .health_service import (
    HealthDiagnostic,
    _backup_diagnostic,
    _celery_diagnostic,
    _disk_diagnostic,
    _restore_validation_diagnostic,
    _telemetry_diagnostic,
    persist_health_diagnostics,
)
from .models import (
    BackupConfiguration,
    RestoreValidation,
    SystemHealthComponent,
    SystemHealthEvent,
    SystemHealthStatus,
)


class SettingsParsingTests(SimpleTestCase):
    def test_database_url_postgresql(self):
        config = _database_config_from_url('postgresql://itam:senha@db.local:5432/itam?sslmode=require')

        self.assertEqual(config['ENGINE'], 'django.db.backends.postgresql')
        self.assertEqual(config['NAME'], 'itam')
        self.assertEqual(config['USER'], 'itam')
        self.assertEqual(config['PASSWORD'], 'senha')
        self.assertEqual(config['HOST'], 'db.local')
        self.assertEqual(config['PORT'], '5432')
        self.assertEqual(config['OPTIONS']['sslmode'], 'require')

    def test_database_url_sqlite_relativo(self):
        config = _database_config_from_url('sqlite:///db.sqlite3')

        self.assertEqual(config['ENGINE'], 'django.db.backends.sqlite3')
        self.assertTrue(str(config['NAME']).endswith('db.sqlite3'))

    def test_bool_config_ignora_env_invalido_e_usa_dotenv(self):
        with (
            patch.dict('os.environ', {'DEBUG': 'release'}),
            patch.dict('itam.settings.DOTENV_VALUES', {'DEBUG': 'True'}, clear=True),
        ):
            self.assertIs(config('DEBUG', default=False, cast=bool), True)


class BackupConfigurationFormTests(SimpleTestCase):
    def test_normaliza_e_ordena_horarios(self):
        form = BackupConfigurationForm(
            data={
                'retention_days': 7,
                'schedule_times_json': json.dumps(['18:30', '08:00', '12:15']),
            },
            instance=BackupConfiguration(),
        )

        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data['schedule_times'], ['08:00', '12:15', '18:30'])

    def test_rejeita_horarios_repetidos(self):
        form = BackupConfigurationForm(
            data={
                'retention_days': 3,
                'schedule_times_json': json.dumps(['19:00', '19:00']),
            },
            instance=BackupConfiguration(),
        )

        self.assertFalse(form.is_valid())
        self.assertIn('Nao repita o mesmo horario.', form.errors['schedule_times_json'])

    def test_exige_ao_menos_um_horario(self):
        form = BackupConfigurationForm(
            data={'retention_days': 3, 'schedule_times_json': '[]'},
            instance=BackupConfiguration(),
        )

        self.assertFalse(form.is_valid())
        self.assertIn('Informe pelo menos um horario', form.errors['schedule_times_json'][0])

    def test_limita_retencao_a_trinta_dias(self):
        form = BackupConfigurationForm(
            data={
                'retention_days': 31,
                'schedule_times_json': json.dumps(['19:00']),
            },
            instance=BackupConfiguration(),
        )

        self.assertFalse(form.is_valid())
        self.assertIn('30', form.errors['retention_days'][0])


class BackupConfigurationViewTests(TestCase):
    def setUp(self):
        self.admin = Usuario.objects.create_superuser(
            matricula='backup-admin',
            password='test12345',
            first_name='Admin',
            last_name='Backup',
        )
        self.viewer = Usuario.objects.create_user(
            matricula='backup-viewer',
            password='test12345',
            first_name='Viewer',
            last_name='Backup',
        )

    @patch('dashboard.views.get_backup_task_status', return_value=BackupTaskStatus(installed=True))
    @patch('dashboard.views.list_backup_sets', return_value=[])
    def test_admin_acessa_painel(self, _mock_sets, _mock_status):
        self.client.force_login(self.admin)

        response = self.client.get(reverse('backup_configuration'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Execuções e volume por dia')
        self.assertContains(response, 'Histórico e pontos de restauração')
        self.assertContains(response, 'Executar agora')

    def test_solicitante_nao_acessa_painel(self):
        self.client.force_login(self.viewer)

        response = self.client.get(reverse('backup_configuration'))

        self.assertRedirects(response, reverse('dashboard'))

    @patch('dashboard.views.get_backup_task_status', return_value=BackupTaskStatus(installed=True))
    @patch('dashboard.views.list_backup_sets', return_value=[])
    @patch('dashboard.views.configure_backup_task')
    def test_salva_configuracao_depois_de_atualizar_tarefa(self, mock_configure, _mock_sets, _mock_status):
        self.client.force_login(self.admin)

        response = self.client.post(
            reverse('backup_configuration'),
            {
                'action': 'save',
                'retention_days': 5,
                'schedule_times_json': json.dumps(['18:00', '08:00']),
            },
        )

        self.assertRedirects(response, reverse('backup_configuration'))
        mock_configure.assert_called_once_with(5, ['08:00', '18:00'])
        configuration = BackupConfiguration.load()
        self.assertEqual(configuration.retention_days, 5)
        self.assertEqual(configuration.schedule_times, ['08:00', '18:00'])
        self.assertEqual(configuration.updated_by, self.admin)

    @patch('dashboard.views.run_backup_now')
    def test_administrador_inicia_backup_imediato(self, mock_run):
        self.client.force_login(self.admin)

        response = self.client.post(reverse('backup_configuration'), {'action': 'run_now'})

        self.assertRedirects(response, reverse('backup_configuration'))
        mock_run.assert_called_once_with()

    @patch('dashboard.views.run_backup_now')
    def test_backup_imediato_responde_json_para_acompanhamento(self, mock_run):
        self.client.force_login(self.admin)

        response = self.client.post(
            reverse('backup_configuration'),
            {'action': 'run_now'},
            HTTP_X_REQUESTED_WITH='XMLHttpRequest',
            HTTP_ACCEPT='application/json',
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['started'])
        self.assertIn('requested_at', response.json())
        mock_run.assert_called_once_with()

    @patch('dashboard.views.get_backup_task_status', return_value=BackupTaskStatus(installed=True, state='Ready'))
    @patch('dashboard.views.list_backup_sets')
    def test_status_confirma_novo_backup_concluido(self, mock_sets, _mock_status):
        requested_at = timezone.now()
        mock_sets.return_value = [
            BackupSet(
                manifest_file='itam-backup-20260812-120000.manifest.txt',
                created_at=requested_at + timedelta(seconds=2),
                database_file='itam-db.dump',
                media_file='itam-media.zip',
                total_bytes=1024,
                status='complete',
                retention_days=3,
            )
        ]
        self.client.force_login(self.admin)

        response = self.client.get(
            reverse('backup_status'),
            {'requested_at': requested_at.isoformat()},
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['complete'])
        self.assertFalse(response.json()['failed'])

    @patch('dashboard.views.start_restore_point', return_value=uuid.UUID('11111111-1111-1111-1111-111111111111'))
    def test_restauracao_exige_confirmacao_forte(self, mock_start):
        self.client.force_login(self.admin)

        response = self.client.post(
            reverse('backup_configuration'),
            {
                'action': 'restore',
                'manifest': 'itam-backup-20260812-120000.manifest.txt',
                'confirmation': 'restaurar',
            },
        )

        self.assertEqual(response.status_code, 400)
        self.assertFalse(response.json()['started'])
        mock_start.assert_not_called()

    @patch('dashboard.views.start_restore_point', return_value=uuid.UUID('11111111-1111-1111-1111-111111111111'))
    def test_administrador_inicia_ponto_de_restauracao(self, mock_start):
        configuration = BackupConfiguration.load()
        self.client.force_login(self.admin)

        response = self.client.post(
            reverse('backup_configuration'),
            {
                'action': 'restore',
                'manifest': 'itam-backup-20260812-120000.manifest.txt',
                'confirmation': 'RESTAURAR',
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['started'])
        self.assertIn('/restauracao/11111111-1111-1111-1111-111111111111/', response.json()['status_url'])
        mock_start.assert_called_once_with(
            'itam-backup-20260812-120000.manifest.txt',
            retention_days=configuration.retention_days,
            schedule_times=configuration.schedule_times,
        )


class RestorePointIntegrityTests(SimpleTestCase):
    def setUp(self):
        self.backup_dir = tempfile.mkdtemp()
        self.settings_override = override_settings(BACKUP_DIR=self.backup_dir)
        self.settings_override.enable()

    def tearDown(self):
        self.settings_override.disable()
        shutil.rmtree(self.backup_dir, ignore_errors=True)

    def _create_restore_point(self, database_content=b'database backup', created_at=None, status='complete'):
        created_at = created_at or timezone.now()
        timestamp = created_at.strftime('%Y%m%d-%H%M%S')
        database_name = f'itam-db-{timestamp}.dump'
        media_name = f'itam-media-{timestamp}.zip'
        manifest_name = f'itam-backup-{timestamp}.manifest.txt'
        database_path = tempfile.NamedTemporaryFile(dir=self.backup_dir, delete=False)
        database_path.close()
        final_database_path = shutil.move(database_path.name, f'{self.backup_dir}/{database_name}')
        with open(final_database_path, 'wb') as stream:
            stream.write(database_content)
        media_content = b'media backup'
        media_path = f'{self.backup_dir}/{media_name}'
        with open(media_path, 'wb') as stream:
            stream.write(media_content)
        database_digest = hashlib.sha256(database_content).hexdigest().upper()
        media_digest = hashlib.sha256(media_content).hexdigest().upper()
        manifest_path = f'{self.backup_dir}/{manifest_name}'
        with open(manifest_path, 'w', encoding='utf-8') as stream:
            stream.write(f'created_at={created_at.isoformat()}\n')
            stream.write(f'status={status}\n')
            stream.write(f'file={database_name}|{len(database_content)}|{database_digest}\n')
            stream.write(f'file={media_name}|{len(media_content)}|{media_digest}\n')
        return manifest_name, final_database_path

    def test_aceita_ponto_com_hash_integro(self):
        manifest_name, database_path = self._create_restore_point()

        files = resolve_restore_point(manifest_name)

        self.assertEqual(files['database'], Path(database_path))

    def test_rejeita_ponto_com_arquivo_alterado(self):
        manifest_name, database_path = self._create_restore_point()
        with open(database_path, 'ab') as stream:
            stream.write(b'alterado')

        with self.assertRaisesMessage(BackupOperationError, 'tamanho invalido'):
            resolve_restore_point(manifest_name)

    def test_rejeita_nome_de_manifesto_fora_do_padrao(self):
        with self.assertRaisesMessage(BackupOperationError, 'Ponto de restauracao invalido'):
            resolve_restore_point('../.env')

    def test_rejeita_ponto_com_mais_de_trinta_dias(self):
        manifest_name, _database_path = self._create_restore_point(
            created_at=timezone.now() - timedelta(days=31)
        )

        with self.assertRaisesMessage(BackupOperationError, 'mais de 30 dias'):
            resolve_restore_point(manifest_name)

    def test_nao_oferece_manifesto_incompleto_como_ponto(self):
        self._create_restore_point(status='incomplete')

        points = list_backup_sets()

        self.assertEqual(len(points), 1)
        self.assertFalse(points[0].restorable)


class SystemHealthServiceTests(TestCase):
    def test_telemetria_recente_e_classificada_como_saudavel(self):
        agente = AgenteMonitoramento.objects.create(nome='Agente teste')
        Equipamento.objects.create(
            id_patrimonio='MON-001',
            tipo='notebook_padrao',
            monitoramento_ativo=True,
            monitoramento_status=StatusMonitoramento.ONLINE,
            last_seen_at=timezone.now(),
            last_telemetria_agente=agente,
        )

        diagnostic = _telemetry_diagnostic()

        self.assertEqual(diagnostic.status, SystemHealthStatus.HEALTHY)
        self.assertEqual(diagnostic.details['online_count'], 1)
        self.assertEqual(diagnostic.details['stale_count'], 0)

    def test_telemetria_atrasada_e_classificada_como_critica(self):
        agente = AgenteMonitoramento.objects.create(nome='Agente teste')
        Equipamento.objects.create(
            id_patrimonio='MON-002',
            tipo='notebook_padrao',
            monitoramento_ativo=True,
            monitoramento_status=StatusMonitoramento.OFFLINE,
            last_seen_at=timezone.now() - timedelta(minutes=11),
            last_telemetria_agente=agente,
        )

        diagnostic = _telemetry_diagnostic()

        self.assertEqual(diagnostic.status, SystemHealthStatus.CRITICAL)
        self.assertEqual(diagnostic.details['stale_assets'], ['MON-002'])

    def test_telemetria_inclui_detalhes_da_divergencia_e_do_ultimo_heartbeat(self):
        agente = AgenteMonitoramento.objects.create(nome='Agente inventario')
        equipamento = Equipamento.objects.create(
            id_patrimonio='MON-003',
            tipo='notebook_padrao',
            numero_serie='SERIE-CADASTRO',
            monitoramento_ativo=True,
            monitoramento_status=StatusMonitoramento.ONLINE,
            last_seen_at=timezone.now(),
            last_telemetria_agente=agente,
        )
        DivergenciaInventario.objects.create(
            equipamento=equipamento,
            campo='serial',
            valor_cadastrado='SERIE-CADASTRO',
            valor_detectado='SERIE-AGENTE',
        )

        diagnostic = _telemetry_diagnostic()

        self.assertEqual(diagnostic.status, SystemHealthStatus.WARNING)
        self.assertEqual(diagnostic.details['last_heartbeat_asset'], 'MON-003')
        self.assertEqual(diagnostic.details['last_heartbeat_agent'], 'Agente inventario')
        divergence = diagnostic.details['divergences'][0]
        self.assertEqual(divergence['asset_id'], 'MON-003')
        self.assertEqual(divergence['field_label'], 'Serial')
        self.assertEqual(divergence['registered_value'], 'SERIE-CADASTRO')
        self.assertEqual(divergence['detected_value'], 'SERIE-AGENTE')
        self.assertTrue(divergence['checked_at'])

    @patch('dashboard.health_service.notificar_admins')
    def test_notifica_falha_uma_vez_e_recuperacao_uma_vez(self, mock_notify):
        failure = HealthDiagnostic(
            key='database',
            name='Banco de dados',
            status=SystemHealthStatus.CRITICAL,
            summary='Banco indisponivel.',
        )
        healthy = HealthDiagnostic(
            key='database',
            name='Banco de dados',
            status=SystemHealthStatus.HEALTHY,
            summary='Banco respondendo.',
        )

        persist_health_diagnostics([failure])
        persist_health_diagnostics([failure])
        persist_health_diagnostics([healthy])
        persist_health_diagnostics([healthy])

        self.assertEqual(mock_notify.call_count, 2)
        self.assertIn('Alerta de saude', mock_notify.call_args_list[0].args[0])
        self.assertIn('Servico recuperado', mock_notify.call_args_list[1].args[0])
        self.assertEqual(SystemHealthEvent.objects.count(), 2)

    @patch('dashboard.health_service.notificar_admins')
    def test_aviso_nao_notificavel_e_persistido_sem_alerta(self, mock_notify):
        diagnostic = HealthDiagnostic(
            key='security',
            name='Ambiente e seguranca',
            status=SystemHealthStatus.WARNING,
            summary='Homologacao com DEBUG ativo.',
            notify=False,
        )

        persist_health_diagnostics([diagnostic])

        self.assertEqual(SystemHealthComponent.objects.get().status, SystemHealthStatus.WARNING)
        mock_notify.assert_not_called()

    @patch('dashboard.health_service.shutil.disk_usage')
    def test_classifica_armazenamento_com_menos_de_quinze_porcento_como_atencao(self, mock_usage):
        mock_usage.return_value = shutil._ntuple_diskusage(total=1000, used=880, free=120)

        diagnostic = _disk_diagnostic()

        self.assertEqual(diagnostic.status, SystemHealthStatus.WARNING)
        self.assertEqual(diagnostic.details['free_percent'], 12.0)

    @patch(
        'dashboard.health_service.get_backup_task_status',
        return_value=BackupTaskStatus(installed=True, state='Ready', last_result=0),
    )
    @patch('dashboard.health_service.list_backup_sets')
    def test_classifica_backup_com_mais_de_quarenta_e_oito_horas_como_critico(
        self, mock_backups, _mock_task
    ):
        mock_backups.return_value = [
            BackupSet(
                manifest_file='itam-backup-20260810-100001.manifest.txt',
                created_at=timezone.now() - timedelta(hours=49),
                database_file='itam-db.dump',
                media_file='itam-media.zip',
                total_bytes=2048,
                status='complete',
                retention_days=30,
                restorable=True,
            )
        ]

        diagnostic = _backup_diagnostic()

        self.assertEqual(diagnostic.status, SystemHealthStatus.CRITICAL)
        self.assertIn('48 horas', diagnostic.summary)

    def test_classifica_validacao_aprovada_com_mais_de_trinta_dias_como_atencao(self):
        RestoreValidation.objects.create(
            tested_at=timezone.now() - timedelta(days=31),
            result='success',
            backup_manifest='itam-backup-20260701-100001.manifest.txt',
        )

        diagnostic = _restore_validation_diagnostic()

        self.assertEqual(diagnostic.status, SystemHealthStatus.WARNING)
        self.assertIn('30 dias', diagnostic.summary)

    def test_verificacao_manual_do_worker_usa_heartbeat_e_nao_a_leitura_da_tela(self):
        SystemHealthComponent.objects.create(
            component_key='celery',
            name='Automacoes',
            status=SystemHealthStatus.HEALTHY,
            summary='Anteriormente saudavel.',
            details={'heartbeat_at': (timezone.now() - timedelta(minutes=20)).isoformat()},
            checked_at=timezone.now(),
            status_changed_at=timezone.now(),
        )

        diagnostic = _celery_diagnostic(source='manual')

        self.assertEqual(diagnostic.status, SystemHealthStatus.WARNING)
        self.assertIn('sem confirmacao', diagnostic.summary)
        self.assertTrue(diagnostic.notify)


class SystemHealthViewTests(TestCase):
    def setUp(self):
        self.admin = Usuario.objects.create_superuser(
            matricula='health-admin',
            password='test12345',
            first_name='Admin',
            last_name='Health',
        )
        self.viewer = Usuario.objects.create_user(
            matricula='health-viewer',
            password='test12345',
            first_name='Viewer',
            last_name='Health',
        )
        self.now = timezone.now()
        self.components = [
            SystemHealthComponent(
                component_key='database',
                name='Banco de dados',
                status=SystemHealthStatus.HEALTHY,
                summary='Conexao respondendo.',
                checked_at=self.now,
                status_changed_at=self.now,
            )
        ]

    def test_solicitante_nao_acessa_central(self):
        self.client.force_login(self.viewer)

        response = self.client.get(reverse('system_health'))

        self.assertRedirects(response, reverse('dashboard'))

    @patch('dashboard.views.perform_system_health_checks')
    @patch('dashboard.views.list_backup_sets', return_value=[])
    def test_admin_acessa_central(self, _mock_backups, mock_checks):
        mock_checks.return_value = self.components
        self.client.force_login(self.admin)

        response = self.client.get(reverse('system_health'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Saúde do sistema')
        self.assertContains(response, 'Banco de dados')
        self.assertContains(response, 'Registrar teste de restauração')

    @patch('dashboard.views.perform_system_health_checks')
    @patch('dashboard.views.list_backup_sets', return_value=[])
    def test_central_mostra_heartbeat_e_divergencia_completa(self, _mock_backups, mock_checks):
        mock_checks.return_value = self.components
        agente = AgenteMonitoramento.objects.create(nome='Agente Windows')
        equipamento = Equipamento.objects.create(
            id_patrimonio='MON-004',
            tipo='notebook_padrao',
            numero_serie='SERIE-CADASTRO',
            monitoramento_ativo=True,
            monitoramento_status=StatusMonitoramento.ONLINE,
            last_seen_at=self.now,
            last_telemetria_agente=agente,
        )
        DivergenciaInventario.objects.create(
            equipamento=equipamento,
            campo='serial',
            valor_cadastrado='SERIE-CADASTRO',
            valor_detectado='SERIE-AGENTE',
        )
        self.client.force_login(self.admin)

        response = self.client.get(reverse('system_health'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Agentes e heartbeats')
        self.assertContains(response, 'MON-004')
        self.assertContains(response, 'Agente Windows')
        self.assertContains(response, 'Diagnóstico')
        self.assertEqual(response.context['telemetry']['warning_count'], 1)
        self.assertContains(response, 'em atenção')
        self.assertContains(response, timezone.localtime(self.now).strftime('%d/%m/%Y %H:%M:%S'))
        self.assertContains(response, 'SERIE-CADASTRO')
        self.assertContains(response, 'SERIE-AGENTE')

    @patch('dashboard.views.perform_system_health_checks')
    @patch('dashboard.views.list_backup_sets', return_value=[])
    def test_verificacao_manual_atualiza_diagnostico(self, _mock_backups, mock_checks):
        mock_checks.return_value = self.components
        self.client.force_login(self.admin)

        response = self.client.post(reverse('system_health'), {'action': 'check_now'})

        self.assertRedirects(response, reverse('system_health'), fetch_redirect_response=False)
        mock_checks.assert_called_once_with(source='manual')

    @patch('dashboard.views.perform_system_health_checks')
    @patch('dashboard.views.list_backup_sets')
    def test_registra_validacao_de_restauracao(self, mock_backups, mock_checks):
        backup = BackupSet(
            manifest_file='itam-backup-20260812-100001.manifest.txt',
            created_at=self.now,
            database_file='itam-db-20260812-100001.dump',
            media_file='itam-media-20260812-100001.zip',
            total_bytes=2048,
            status='complete',
            retention_days=30,
            restorable=True,
        )
        mock_backups.return_value = [backup]
        mock_checks.return_value = self.components
        self.client.force_login(self.admin)

        response = self.client.post(
            reverse('system_health'),
            {
                'action': 'record_restore_test',
                'tested_at': timezone.localtime(self.now).strftime('%Y-%m-%dT%H:%M'),
                'result': 'success',
                'backup_manifest': backup.manifest_file,
                'notes': 'Login e dados conferidos.',
            },
        )

        self.assertRedirects(response, reverse('system_health'))
        validation = RestoreValidation.objects.get()
        self.assertEqual(validation.recorded_by, self.admin)
        self.assertEqual(validation.backup_manifest, backup.manifest_file)
        self.assertEqual(validation.result, 'success')

    @patch('dashboard.views.perform_system_health_checks')
    @patch('dashboard.views.list_backup_sets', return_value=[])
    def test_rejeita_manifesto_que_nao_esta_disponivel(self, _mock_backups, mock_checks):
        mock_checks.return_value = self.components
        self.client.force_login(self.admin)

        response = self.client.post(
            reverse('system_health'),
            {
                'action': 'record_restore_test',
                'tested_at': timezone.localtime(self.now).strftime('%Y-%m-%dT%H:%M'),
                'result': 'success',
                'backup_manifest': '../arquivo-invalido',
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Selecione um ponto de restauracao disponivel.')
        self.assertFalse(RestoreValidation.objects.exists())

    @patch('dashboard.views.perform_system_health_checks')
    @patch('dashboard.views.list_backup_sets')
    def test_rejeita_data_futura_no_teste_de_restauracao(self, mock_backups, mock_checks):
        backup = BackupSet(
            manifest_file='itam-backup-20260812-100001.manifest.txt',
            created_at=self.now,
            database_file='itam-db-20260812-100001.dump',
            media_file='itam-media-20260812-100001.zip',
            total_bytes=2048,
            status='complete',
            retention_days=30,
            restorable=True,
        )
        mock_backups.return_value = [backup]
        mock_checks.return_value = self.components
        self.client.force_login(self.admin)

        response = self.client.post(
            reverse('system_health'),
            {
                'action': 'record_restore_test',
                'tested_at': timezone.localtime(self.now + timedelta(days=1)).strftime('%Y-%m-%dT%H:%M'),
                'result': 'success',
                'backup_manifest': backup.manifest_file,
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'A data do teste nao pode estar no futuro.')
        self.assertFalse(RestoreValidation.objects.exists())


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
        self.assertEqual(len(fluxo), 3)
        self.assertEqual(
            [bloco['status'] for bloco in fluxo],
            [
                StatusChamado.FILA,
                StatusChamado.EM_ATENDIMENTO,
                StatusChamado.ENCERRADO,
            ],
        )
        self.assertEqual(fluxo[0]['count'], 1)
        self.assertEqual(fluxo[1]['count'], 1)
        self.assertEqual(fluxo[2]['count'], 1)
        self.assertContains(response, 'Ação imediata')
        self.assertContains(response, 'Abrir agora')
        self.assertContains(response, 'Fluxo operacional de chamados')
        self.assertContains(response, 'Chamado encerrado')

    def test_dashboard_mostra_estoque_para_analista(self):
        analista = Usuario.objects.create_user(
            matricula='4005',
            password='test12345',
            first_name='Ana',
            last_name='Estoque',
            nivel_acesso=NivelAcesso.ANALISTA,
        )

        self.client.force_login(analista)
        response = self.client.get(reverse('dashboard'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Operação do estoque')
        self.assertContains(response, 'Reserva rápida e em lote')
        self.assertContains(response, 'Chamados')

    def test_dashboard_mostra_painel_operacional_para_tecnico(self):
        tecnico = Usuario.objects.create_user(
            matricula='4006',
            password='test12345',
            first_name='Tacio',
            last_name='Fila',
            nivel_acesso=NivelAcesso.TECNICO,
        )

        self.client.force_login(tecnico)
        response = self.client.get(reverse('dashboard'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Fila operacional')
        self.assertContains(response, 'Fila de execução e entrega em um único lugar.')
        self.assertContains(response, 'Lista completa')
        self.assertIsNotNone(response.context[-1].get('painel_total'))

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
        Chamado.objects.create(
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

    def test_dashboard_renderiza_portal_para_solicitante(self):
        Chamado.objects.create(
            titulo='Portal do solicitante',
            descricao='Chamado usado para validar o portal.',
            solicitante=self.viewer,
            status=StatusChamado.FILA,
        )
        self.client.force_login(self.viewer)

        response = self.client.get(reverse('dashboard'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Portal do solicitante')
        self.assertEqual(response.context['portal_chamados_total'], 1)
        self.assertEqual(response.context['portal_chamados_abertos'], 1)
        self.assertEqual(response.context['portal_chamados_encerrados'], 0)
        self.assertNotIn('Location', response.headers)

    def test_api_relatorios_retorna_indicadores_para_operacional(self):
        self._criar_equipamento('PAT-REL-01', score_saude=55)
        Chamado.objects.create(
            titulo='Relatorio operacional',
            descricao='Chamado usado para validar o endpoint de relatorios.',
            solicitante=self.viewer,
            status=StatusChamado.FILA,
        )

        self.client.force_login(self.admin)
        response = self.client.get(reverse('api_relatorios'))

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIn('relatorios', payload)
        self.assertIn('dashboard_charts', payload)
        self.assertEqual(payload['relatorios']['chamados_total'], 1)
        self.assertEqual(payload['relatorios']['equipamentos_total'], 1)
        self.assertEqual(payload['relatorios']['equipamentos_alerta'], 1)
        self.assertTrue(payload['atividade_recente'] is not None)

    def test_openapi_schema_disponivel_para_usuario_autenticado(self):
        self.client.force_login(self.admin)

        response = self.client.get(reverse('api_schema'))

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload['openapi'], '3.1.0')
        self.assertIn('/api/equipamentos/', payload['paths'])
        self.assertIn('ApiKeyAuth', payload['components']['securitySchemes'])

    def test_base_usa_assets_locais_sem_cdn(self):
        self.client.force_login(self.admin)

        response = self.client.get(reverse('dashboard'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '/static/vendor/bootstrap/5.3.3/css/bootstrap.min.css')
        self.assertContains(response, '/static/vendor/fontawesome/6.5.2/css/all.min.css')
        self.assertContains(response, '/static/vendor/google-fonts/inter-sora/inter-sora.css')
        self.assertNotContains(response, 'https://cdn.jsdelivr.net')
        self.assertNotContains(response, 'https://cdnjs.cloudflare.com')
        self.assertNotContains(response, 'https://fonts.googleapis.com')

    def test_healthcheck_publico_confirma_dependencias_basicas(self):
        response = self.client.get(reverse('health'))

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload['status'], 'ok')
        self.assertTrue(payload['checks']['database']['ok'])
        self.assertTrue(payload['checks']['cache']['ok'])

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

    @patch('dashboard.management.commands.verificar_instalacao.redis.from_url')
    @override_settings(
        REDIS_URL='redis://127.0.0.1:6379/0',
        EMAIL_BACKEND='django.core.mail.backends.smtp.EmailBackend',
        EMAIL_HOST='smtp.example.com',
        EMAIL_HOST_USER='usuario',
        EMAIL_HOST_PASSWORD='senha',
    )
    def test_verificar_instalacao_confirma_ambiente_pronto(self, mock_from_url):
        cliente = MagicMock()
        cliente.ping.return_value = True
        mock_from_url.return_value = cliente

        saida = StringIO()
        call_command('verificar_instalacao', stdout=saida)

        self.assertIn('Ambiente pronto para instalacao.', saida.getvalue())
        cliente.ping.assert_called_once()

    @patch('dashboard.management.commands.verificar_instalacao.redis.from_url')
    @override_settings(
        DJANGO_ENV='production',
        DEBUG=False,
        SECRET_KEY='django-insecure-short',
        REDIS_URL='redis://127.0.0.1:6379/0',
        ALLOWED_HOSTS=['itam.example.com'],
        SITE_URL='https://itam.example.com',
        SECURE_SSL_REDIRECT=True,
        SECURE_HSTS_SECONDS=31536000,
        SESSION_COOKIE_SECURE=True,
        CSRF_COOKIE_SECURE=True,
    )
    def test_verificar_instalacao_rejeita_secret_key_fraca_em_producao(self, mock_from_url):
        cliente = MagicMock()
        cliente.ping.return_value = True
        mock_from_url.return_value = cliente

        with self.assertRaisesMessage(CommandError, 'SECRET_KEY precisa ser longa'):
            call_command('verificar_instalacao', stdout=StringIO())

    @patch('dashboard.management.commands.verificar_instalacao.redis.from_url')
    @override_settings(
        DJANGO_ENV='production',
        DEBUG=False,
        SECRET_KEY='FIAME-production-check-secret-with-more-than-fifty-characters',
        REDIS_URL='redis://127.0.0.1:6379/0',
        ALLOWED_HOSTS=['itam.example.com'],
        SITE_URL='https://itam.example.com',
        SECURE_SSL_REDIRECT=True,
        SECURE_HSTS_SECONDS=31536000,
        SESSION_COOKIE_SECURE=True,
        CSRF_COOKIE_SECURE=True,
        EMAIL_BACKEND='django.core.mail.backends.console.EmailBackend',
    )
    def test_verificar_instalacao_rejeita_email_console_em_producao(self, mock_from_url):
        cliente = MagicMock()
        cliente.ping.return_value = True
        mock_from_url.return_value = cliente

        with self.assertRaisesMessage(CommandError, 'EMAIL_BACKEND esta em console'):
            call_command('verificar_instalacao', stdout=StringIO())
