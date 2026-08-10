# Operacao em producao

Este guia fecha o ciclo minimo de producao do FIAME System: preparar ambiente, validar deploy, subir servicos, proteger dados e observar saude.

## 1. Preparar ambiente

1. Copie `.env.production.example` para `.env`.
2. Defina `DJANGO_ENV=production`, `DEBUG=False`, `SECRET_KEY`, `ALLOWED_HOSTS`, `SITE_URL`, banco, Redis e SMTP.
3. Instale dependencias e aplique migracoes:

```powershell
.\scripts\bootstrap.ps1
```

## 2. Validar antes de liberar

Rode o checklist local:

```powershell
.\scripts\deploy-check.ps1
```

Com a aplicacao no ar, valide HTTP e contrato da API:

```powershell
.\scripts\deploy-check.ps1 -BaseUrl https://itam.seu-dominio.com -ApiKey SUA_CHAVE
```

O comando executa `manage.py check`, `check --deploy`, `verificar_instalacao`, checagem de migracoes, `collectstatic --dry-run` e smoke test opcional.

## 3. Subir e parar servicos no Windows

Para iniciar ASGI, Celery worker e Celery beat:

```powershell
.\scripts\start-all.ps1 -Host 0.0.0.0 -Port 8000
```

Os logs ficam em `logs/asgi.*.log`, `logs/worker.*.log`, `logs/beat.*.log`. Os PIDs ficam em `logs/pids/`.

Para parar:

```powershell
.\scripts\stop-all.ps1
```

Se algum processo nao responder:

```powershell
.\scripts\stop-all.ps1 -Force
```

Em producao permanente, use estes comandos dentro de um gerenciador de servico, como NSSM, WinSW, systemd ou supervisor. Mantenha ASGI, worker e beat como processos separados.

## 4. Backup

Crie backup do banco e da pasta `media`:

```powershell
.\scripts\backup.ps1
```

Por padrao os arquivos vao para `backups/`. O script suporta PostgreSQL via `pg_dump` e SQLite por copia do arquivo. Para PostgreSQL, `pg_dump` precisa estar no `PATH`.

Para backup apenas do banco:

```powershell
.\scripts\backup.ps1 -SkipMedia
```

Para backup apenas da midia:

```powershell
.\scripts\backup.ps1 -SkipDatabase
```

Agende este script no Windows Task Scheduler ou no cron do servidor. Guarde uma copia fora da maquina da aplicacao.

## 5. Restore

Restore e destrutivo e exige confirmacao explicita:

```powershell
.\scripts\restore.ps1 -DatabaseBackup .\backups\itam-db-YYYYMMDD-HHMMSS.dump -ConfirmRestore RESTORE
```

Com midia:

```powershell
.\scripts\restore.ps1 -DatabaseBackup .\backups\itam-db-YYYYMMDD-HHMMSS.dump -MediaBackup .\backups\itam-media-YYYYMMDD-HHMMSS.zip -ConfirmRestore RESTORE
```

Depois do restore:

```powershell
python manage.py migrate --noinput
.\scripts\deploy-check.ps1
```

## 6. Logs e retencao

O Django grava logs rotativos em:

- `logs/itam.log`
- `logs/itam-error.log`

Para arquivar logs antigos ou grandes:

```powershell
.\scripts\rotate-logs.ps1
```

Parametros uteis:

```powershell
.\scripts\rotate-logs.ps1 -ArchiveAfterDays 7 -RetentionDays 30 -MaxSizeMB 50
```

Agende a rotacao diariamente.

## 7. Monitoramento externo

Configure o monitor externo para chamar:

```text
GET /health/
```

Resposta `200` indica banco e cache OK. Resposta `503` indica dependencia indisponivel.

## 8. Checklist rapido de release

- `.env` revisado e sem `DEBUG=True`.
- `SECRET_KEY` forte.
- PostgreSQL ou MySQL em producao.
- Redis ativo para cache, Channels e Celery.
- SMTP real configurado.
- `python manage.py test --keepdb` passando no ambiente de homologacao.
- `.\scripts\backup.ps1` executado antes da atualizacao.
- `.\scripts\deploy-check.ps1` passando antes e depois da atualizacao.
- `.\scripts\smoke-test.ps1` passando contra a URL publica.
