import hashlib
import json
import os
import re
import shutil
import subprocess
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from django.conf import settings
from django.utils import timezone

TASK_NAME = 'ITAM Daily Backup'


class BackupOperationError(RuntimeError):
    pass


@dataclass(frozen=True)
class BackupTaskStatus:
    installed: bool = False
    state: str = 'Unavailable'
    state_label: str = 'Indisponivel'
    last_run: datetime | None = None
    next_run: datetime | None = None
    last_result: int | None = None
    last_result_label: str = 'Sem execucao'
    error: str = ''


@dataclass(frozen=True)
class BackupSet:
    manifest_file: str
    created_at: datetime
    database_file: str
    media_file: str
    total_bytes: int
    status: str
    retention_days: int | None
    restorable: bool = False

    @property
    def size_label(self):
        size = float(self.total_bytes)
        for unit in ('B', 'KB', 'MB', 'GB'):
            if size < 1024 or unit == 'GB':
                return f'{size:.1f} {unit}' if unit != 'B' else f'{int(size)} B'
            size /= 1024
        return f'{size:.1f} GB'

    @property
    def status_label(self):
        return 'Concluido' if self.status == 'complete' else 'Incompleto'


def _powershell_executable():
    executable = shutil.which('powershell.exe') or shutil.which('powershell')
    if not executable:
        raise BackupOperationError('PowerShell nao esta disponivel neste servidor.')
    return executable


def _run_powershell(arguments, timeout=60):
    command = [
        _powershell_executable(),
        '-NoProfile',
        '-NonInteractive',
        '-ExecutionPolicy',
        'Bypass',
        *arguments,
    ]
    creation_flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0) if os.name == 'nt' else 0
    try:
        result = subprocess.run(
            command,
            cwd=settings.BASE_DIR,
            capture_output=True,
            text=True,
            errors='replace',
            timeout=timeout,
            creationflags=creation_flags,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise BackupOperationError(f'Nao foi possivel executar a operacao de backup: {exc}') from exc
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or 'Falha sem detalhes.').strip()
        raise BackupOperationError(detail)
    return result.stdout.strip()


def configure_backup_task(retention_days, schedule_times):
    if _has_active_restore():
        raise BackupOperationError('Aguarde a restauracao em andamento terminar.')
    installer = Path(settings.BASE_DIR) / 'scripts' / 'install-backup-task.ps1'
    if not installer.is_file():
        raise BackupOperationError(f'Instalador da tarefa nao encontrado: {installer}')
    return _run_powershell(
        [
            '-File',
            str(installer),
            '-RetentionDays',
            str(retention_days),
            '-Times',
            ','.join(schedule_times),
        ]
    )


def run_backup_now():
    if _has_active_restore():
        raise BackupOperationError('Aguarde a restauracao em andamento terminar.')
    command = f"Start-ScheduledTask -TaskName '{TASK_NAME}'"
    return _run_powershell(['-Command', command], timeout=30)


def _parse_task_datetime(value):
    if not value:
        return None
    parsed = datetime.fromisoformat(value)
    if timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed, timezone.get_current_timezone())
    return parsed


def get_backup_task_status():
    script = f"""
$ErrorActionPreference = 'Stop'
$task = Get-ScheduledTask -TaskName '{TASK_NAME}' -ErrorAction SilentlyContinue
if (-not $task) {{
  [pscustomobject]@{{ installed = $false }} | ConvertTo-Json -Compress
  exit 0
}}
$info = Get-ScheduledTaskInfo -TaskName '{TASK_NAME}'
function Format-TaskDate([datetime]$Value) {{
  if ($Value.Year -le 1900) {{ return $null }}
  return $Value.ToString('o')
}}
[pscustomobject]@{{
  installed = $true
  state = $task.State.ToString()
  last_run = Format-TaskDate $info.LastRunTime
  next_run = Format-TaskDate $info.NextRunTime
  last_result = [int64]$info.LastTaskResult
}} | ConvertTo-Json -Compress
"""
    try:
        output = _run_powershell(['-Command', script], timeout=30)
        payload = json.loads(output.splitlines()[-1])
    except (BackupOperationError, json.JSONDecodeError, IndexError) as exc:
        return BackupTaskStatus(error=str(exc))

    if not payload.get('installed'):
        return BackupTaskStatus(error='Tarefa de backup nao instalada.')

    state = payload.get('state') or 'Unknown'
    state_labels = {
        'Ready': 'Agendado',
        'Running': 'Em execucao',
        'Disabled': 'Desativado',
        'Queued': 'Na fila',
    }
    last_result = payload.get('last_result')
    if last_result == 0:
        result_label = 'Concluido'
    elif last_result in (267009, 0x41301):
        result_label = 'Em execucao'
    elif last_result is None:
        result_label = 'Sem execucao'
    else:
        result_label = f'Falha ({last_result})'

    return BackupTaskStatus(
        installed=True,
        state=state,
        state_label=state_labels.get(state, state),
        last_run=_parse_task_datetime(payload.get('last_run')),
        next_run=_parse_task_datetime(payload.get('next_run')),
        last_result=last_result,
        last_result_label=result_label,
    )


def _backup_root():
    configured = Path(settings.BACKUP_DIR)
    return configured if configured.is_absolute() else Path(settings.BASE_DIR) / configured


def _parse_manifest(manifest):
    values = {}
    file_entries = []
    for raw_line in manifest.read_text(encoding='utf-8-sig').splitlines():
        if '=' not in raw_line:
            continue
        key, value = raw_line.split('=', 1)
        if key == 'file':
            parts = value.split('|', 2)
            if len(parts) == 3:
                file_entries.append({'name': parts[0], 'size': parts[1], 'hash': parts[2]})
        else:
            values[key] = value

    if not file_entries and values.get('files'):
        for raw_path in values['files'].split(';'):
            backup_path = Path(raw_path)
            if backup_path.is_file():
                file_entries.append(
                    {'name': backup_path.name, 'size': str(backup_path.stat().st_size), 'hash': ''}
                )
    return values, file_entries


def list_backup_sets(limit=12):
    backup_root = _backup_root()
    if not backup_root.is_dir():
        return []

    backup_sets = []
    manifests = sorted(backup_root.glob('itam-backup-*.manifest.txt'), reverse=True)
    selected_manifests = manifests if limit is None else manifests[:limit]
    for manifest in selected_manifests:
        try:
            values, file_entries = _parse_manifest(manifest)

            created_at = datetime.fromisoformat(values['created_at'])
            if timezone.is_naive(created_at):
                created_at = timezone.make_aware(created_at, timezone.get_current_timezone())

            database_file = ''
            media_file = ''
            total_bytes = 0
            for entry in file_entries:
                filename = entry['name']
                size = entry['size']
                total_bytes += int(size)
                if filename.startswith('itam-db-'):
                    database_file = filename
                elif filename.startswith('itam-media-'):
                    media_file = filename

            backup_sets.append(
                BackupSet(
                    manifest_file=manifest.name,
                    created_at=created_at,
                    database_file=database_file,
                    media_file=media_file,
                    total_bytes=total_bytes,
                    status='complete' if values.get('status') == 'complete' or database_file else 'incomplete',
                    retention_days=int(values['retention_days']) if values.get('retention_days') else None,
                    restorable=bool(
                        values.get('status') == 'complete'
                        and database_file
                        and media_file
                        and created_at >= timezone.now() - timedelta(days=30)
                        and all(entry['hash'] for entry in file_entries)
                    ),
                )
            )
        except (OSError, ValueError, KeyError):
            continue
    return backup_sets


def _sha256(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest().upper()


def resolve_restore_point(manifest_name):
    if not re.fullmatch(r'itam-backup-\d{8}-\d{6}\.manifest\.txt', manifest_name or ''):
        raise BackupOperationError('Ponto de restauracao invalido.')

    manifest = _backup_root() / manifest_name
    if not manifest.is_file():
        raise BackupOperationError('O manifesto do ponto de restauracao nao foi encontrado.')

    try:
        values, file_entries = _parse_manifest(manifest)
        created_at = datetime.fromisoformat(values['created_at'])
        if timezone.is_naive(created_at):
            created_at = timezone.make_aware(created_at, timezone.get_current_timezone())
    except (OSError, ValueError, KeyError) as exc:
        raise BackupOperationError('O manifesto do ponto de restauracao esta invalido.') from exc

    if created_at < timezone.now() - timedelta(days=30):
        raise BackupOperationError('Esse ponto de restauracao tem mais de 30 dias.')
    if values.get('status') != 'complete':
        raise BackupOperationError('Somente backups concluidos podem ser restaurados.')

    resolved_files = {}
    for entry in file_entries:
        filename = entry['name']
        if Path(filename).name != filename or not entry['hash']:
            raise BackupOperationError('O ponto de restauracao nao possui validacao de integridade completa.')
        path = _backup_root() / filename
        if not path.is_file() or path.stat().st_size != int(entry['size']):
            raise BackupOperationError(f'Arquivo ausente ou com tamanho invalido: {filename}.')
        if _sha256(path) != entry['hash'].upper():
            raise BackupOperationError(f'Falha de integridade no arquivo: {filename}.')
        if filename.startswith('itam-db-'):
            resolved_files['database'] = path
        elif filename.startswith('itam-media-'):
            resolved_files['media'] = path

    if 'database' not in resolved_files:
        raise BackupOperationError('O ponto nao possui backup do banco de dados.')
    if 'media' not in resolved_files:
        raise BackupOperationError('O ponto nao possui backup completo dos arquivos de midia.')
    return resolved_files


def _restore_status_dir():
    path = Path(settings.BASE_DIR) / 'logs' / 'restore'
    path.mkdir(parents=True, exist_ok=True)
    return path


def _has_active_restore():
    cutoff = timezone.now() - timedelta(hours=4)
    for status_file in _restore_status_dir().glob('*.json'):
        try:
            payload = json.loads(status_file.read_text(encoding='utf-8-sig'))
            updated_at = datetime.fromisoformat(payload.get('updated_at', ''))
            if timezone.is_naive(updated_at):
                updated_at = timezone.make_aware(updated_at, timezone.get_current_timezone())
            if payload.get('status') in {'queued', 'running'} and updated_at >= cutoff:
                return True
        except (OSError, ValueError, json.JSONDecodeError):
            continue
    return False


def start_restore_point(manifest_name, retention_days, schedule_times):
    if _has_active_restore():
        raise BackupOperationError('Ja existe uma restauracao em andamento.')
    task_status = get_backup_task_status()
    if task_status.state == 'Running' or task_status.last_result in (267009, 0x41301):
        raise BackupOperationError('Aguarde o backup em andamento terminar antes de restaurar.')
    files = resolve_restore_point(manifest_name)
    script = Path(settings.BASE_DIR) / 'scripts' / 'restore-point.ps1'
    if not script.is_file():
        raise BackupOperationError(f'Script de restauracao nao encontrado: {script}')
    operation_id = uuid.uuid4()
    status_file = _restore_status_dir() / f'{operation_id}.json'
    status_file.write_text(
        json.dumps(
            {
                'status': 'queued',
                'stage': 'queued',
                'message': 'Restauracao preparada.',
                'updated_at': timezone.now().isoformat(),
            }
        ),
        encoding='utf-8',
    )

    command = [
        _powershell_executable(),
        '-NoProfile',
        '-NonInteractive',
        '-ExecutionPolicy',
        'Bypass',
        '-File',
        str(script),
        '-DatabaseBackup',
        str(files['database']),
        '-StatusFile',
        str(status_file),
        '-RetentionDays',
        str(retention_days),
        '-Times',
        ','.join(schedule_times),
    ]
    if files.get('media'):
        command.extend(['-MediaBackup', str(files['media'])])

    output_log = _restore_status_dir() / f'{operation_id}.log'
    creation_flags = 0
    if os.name == 'nt':
        creation_flags = getattr(subprocess, 'CREATE_NEW_PROCESS_GROUP', 0) | getattr(
            subprocess, 'DETACHED_PROCESS', 0
        )
    try:
        with output_log.open('a', encoding='utf-8') as output:
            subprocess.Popen(
                command,
                cwd=settings.BASE_DIR,
                stdin=subprocess.DEVNULL,
                stdout=output,
                stderr=subprocess.STDOUT,
                creationflags=creation_flags,
                close_fds=True,
            )
    except OSError as exc:
        status_file.write_text(
            json.dumps(
                {
                    'status': 'failed',
                    'stage': 'finished',
                    'message': f'Nao foi possivel iniciar a restauracao: {exc}',
                    'updated_at': timezone.now().isoformat(),
                }
            ),
            encoding='utf-8',
        )
        raise BackupOperationError(f'Nao foi possivel iniciar a restauracao: {exc}') from exc
    return operation_id


def get_restore_status(operation_id):
    status_file = _restore_status_dir() / f'{operation_id}.json'
    if not status_file.is_file():
        raise BackupOperationError('Operacao de restauracao nao encontrada.')
    try:
        return json.loads(status_file.read_text(encoding='utf-8-sig'))
    except (OSError, json.JSONDecodeError) as exc:
        raise BackupOperationError('Nao foi possivel ler o andamento da restauracao.') from exc
