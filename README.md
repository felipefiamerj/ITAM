# FIAME System

FIAME System é uma plataforma de gestão de ativos de TI e operações de suporte. O sistema foi desenvolvido em Django e integra controle de equipamentos, chamados, solicitações de acesso, estoque e notificações.

## Visão geral

O projeto oferece:

- Autenticação via matrícula e senha.
- Autenticação TOTP em dois fatores obrigatória para administradores, com códigos de recuperação.
- Solicitações de acesso de novos usuários.
- Aprovação de cadastros de usuários pelo administrador com link de primeiro acesso por e-mail.
- Gestão de usuários com níveis de acesso (solicitante, técnico, analista, administrador).
- Catálogo de equipamentos com status, condição e histórico de movimentações.
- Rastreamento de equipamentos com QR Code gerado automaticamente.
- Movimentações de equipamentos, incluindo entrada, saída, devolução, manutenção, transferência e troca.
- Chamados técnicos para suporte com prioridade, status e solução.
- Painel de dashboard com métricas de equipamentos, chamados, usuários e histórico de auditoria.
- Central administrativa de saúde com diagnóstico de banco, Redis, automações, disco, backups, restauração e ambiente.
- Importação em lote de equipamentos via Excel.
- Sistema de notificações internas.
- Suporte a execução de tarefas assíncronas com Celery e rotinas preparadas para tempo real com Channels.
- A marca exibida no sistema pode ser alterada por `APP_NAME` e `APP_SHORT_NAME` no `.env`.

## Apps principais

- `accounts`: gerencia usuários, login, solicitações de acesso, aprovação de contas, primeiro acesso e controle de permissões.
- `equipamentos`: mantém o catálogo de ativos, gera QR Code para cada equipamento e registra movimentações.
- `chamados`: gerencia tickets de suporte associados a equipamentos ou usuários.
- `estoque`: oferece consultas e resumos de estoque de equipamentos e lotes.
- `dashboard`: apresenta indicadores e relatórios de uso, chamados e equipamentos.
- `ia`: contém funcionalidades para monitoramento inteligente de ativos.
- `notifications`: cuida de notificações internas para usuários e administradores.

## Recursos de usuários

- Solicitação rápida de acesso (novo usuário) diretamente na tela de login.
- Aprovação ou recusa de solicitações pelo administrador.
- Primeiro acesso com link seguro enviado por e-mail ou senha temporária como contingência.
- Troca de senha inicial forçada para contas recém-aprovadas.
- Filtros de usuários por status: ativo, pendente, inativo, solicitante e operacional.

## Recursos de equipamentos

- Cadastro completo de equipamentos com informações de patrimônio, tipo, marca, modelo, IMEI, número de série e localidade.
- Status de equipamento: em estoque, em uso, em manutenção, descartado ou aguardando aprovação.
- Condição do equipamento: ótimo, bom, regular, ruim ou inútil.
- Histórico de movimentações com registro de técnico, usuário anterior, novo usuário e chamado vinculado.
- Entrada em lote via arquivo Excel para facilitar importações de inventário.
- Score de saúde do equipamento com integração a funcionalidades de IA.

## Recursos de chamados

- Abertura de chamados com título, descrição, prioridade e equipamento associado.
- Atribuição de responsável técnico e controle de status do chamado.
- Fechamento automático do chamado com registro de data de fechamento.
- Relatórios de chamados abertos, críticos e recentes no dashboard.

## Tecnologias utilizadas

- Python 3.12/3.13 + Django 5.2 LTS
- Django REST Framework
- Django Crispy Forms + Bootstrap 5
- django-guardian para permissões objeto
- django-auditlog para auditoria de alterações
- Celery + django-celery-beat + django-celery-results para automações e rotinas agendadas
- Channels + Redis para WebSockets e mensagens em tempo real
- OpenAPI em `/api/schema/` e painel de documentacao em `/api/docs/`
- Rate limit em fluxos publicos de autenticacao e endpoints de API
- qrcode para geração de QR Code de equipamentos
- reportlab para geração de documentos/impressos (quando necessário)

## Configuração e execução

1. Ative o ambiente virtual:

```powershell
& .\.venv312\Scripts\Activate.ps1
python --version
```

2. Instale as dependências:

```powershell
pip install -r requirements.txt
```

Para desenvolvimento e CI local, instale tambem o lint:

```powershell
pip install -r requirements-dev.txt
python -m ruff check .
```

3. Configure o arquivo `.env` com variáveis de ambiente, se desejar.
   - `APP_NAME=FIAME System`
   - `APP_SHORT_NAME=FIAME`
   - `DJANGO_ENV=development` ou `production`
   - `SITE_URL=https://seu-dominio`
   - `SECRET_KEY=...`
   - `REDIS_URL=redis://127.0.0.1:6379/0`
   - `CACHE_URL=redis://127.0.0.1:6379/1`
   - `CELERY_BROKER_URL=redis://127.0.0.1:6379/2`
   - `ITAM_API_SHARED_KEY_SHA256=...` para autenticar integracoes sem armazenar a chave em texto puro
     - Gere o hash com `python manage.py hash_api_key sua-chave-com-32-caracteres-ou-mais`
   - `ITAM_ADMIN_2FA_REQUIRED=True` para exigir TOTP de administradores
   - `ITAM_TWO_FACTOR_ENCRYPTION_KEY=...` para usar uma chave dedicada na criptografia dos segredos TOTP

4. Inicie o Redis local com Docker Desktop:

```powershell
docker compose -f compose.redis.yml up -d
```

5. Crie e aplique as migrações:

```powershell
python manage.py makemigrations
python manage.py migrate
```

6. Crie um superusuário:

```powershell
python manage.py createsuperuser
```

7. Inicie o servidor de desenvolvimento:

```powershell
python manage.py runserver
```

Esse comando usa Daphne via ASGI e atende HTTP + WebSockets, incluindo `/ws/notifications/`.

8. Acesse o sistema em:

```text
http://127.0.0.1:8000/
```

## Observações

- O sistema utiliza `SQLite` por padrão, mas pode ser configurado para `PostgreSQL` via variável `DB_ENGINE` no `.env`.
- Para rodar com banco local, ajuste `DB_NAME`, `DB_USER`, `DB_PASSWORD`, `DB_HOST` e `DB_PORT` conforme seu ambiente.
- Em producao, use `.env.production.example` como base e defina `DJANGO_ENV=production`, `DEBUG=False`, `SECRET_KEY` forte, `ALLOWED_HOSTS`, `SITE_URL`, `REDIS_URL` e `CACHE_URL`.
- O projeto define cookies seguros, HSTS e redirecionamento HTTPS por padrao quando `DJANGO_ENV=production`.
- Bootstrap, Font Awesome, Chart.js e fontes estao vendorizados em `static/vendor/`, sem dependencia de CDN em runtime.
- O guia de operacao em producao esta em `docs/deployment/production.md`.
- O `DEBUG` está ativado por padrão no ambiente de desenvolvimento.
- A administração de arquivos está em `staticfiles/` e `media/`.
- Para validar a instalação em uma máquina de cliente, rode:

```powershell
python manage.py verificar_instalacao
```

- O endpoint publico `/health/` valida banco e cache para monitoramento externo.
- Depois de subir o servidor, rode um smoke test local:

```powershell
.\scripts\smoke-test.ps1 -BaseUrl http://127.0.0.1:8000
```

Com chave de API configurada, valide tambem o contrato OpenAPI:

```powershell
.\scripts\smoke-test.ps1 -BaseUrl http://127.0.0.1:8000 -ApiKey SUA_CHAVE
```

- Para validar um deploy antes de liberar, rode:

```powershell
.\scripts\deploy-check.ps1
```

- Para backup, restore e retencao de logs, use:

```powershell
.\scripts\backup.ps1
.\scripts\restore.ps1 -DatabaseBackup .\backups\itam-db-YYYYMMDD-HHMMSS.dump -ConfirmRestore RESTORE
.\scripts\install-backup-task.ps1 -At 19:00 -RetentionDays 30
.\scripts\rotate-logs.ps1
```

O backup local inclui banco e arquivos persistentes de `media`, valida os arquivos gerados e remove copias com mais de 30 dias. QR Codes nao entram por padrao porque podem ser recriados com `python manage.py regenerar_qrcodes --force`. No Windows, o instalador cria uma tarefa diaria e executa o backup assim que possivel quando o computador estiver desligado no horario programado. Pela interface, a restauracao exige a palavra de confirmacao, a senha atual e um codigo 2FA valido.

- Para subir ASGI e as automacoes no Windows, inicie o Redis e depois use os scripts em `scripts/`:

```powershell
docker compose -f compose.redis.yml up -d
.\scripts\bootstrap.ps1
.\scripts\start-all.ps1 -ListenHost 127.0.0.1 -Port 8000
.\scripts\install-runtime-task.ps1
```

`install-runtime-task.ps1` registra a tarefa `ITAM Runtime` no logon do Windows. Ela inicia Docker, Redis, ASGI, worker e beat sem duplicar processos ja ativos.

- Se preferir iniciar manualmente, os serviços ficam assim:

```powershell
.\scripts\start-asgi.ps1
.\scripts\start-worker.ps1
.\scripts\start-beat.ps1
```

- Para Celery em produção, rode um worker e o beat em processos separados:

```powershell
celery -A itam worker -l info
celery -A itam beat -l info
```

- O beat tambem executa a cobranca automatica diaria dos termos digitais pendentes. Ajuste `ITAM_TERMO_ASSINATURA_COBRANCA_HORA`, `ITAM_TERMO_ASSINATURA_COBRANCA_MINUTO` e `ITAM_TERMO_ASSINATURA_COBRANCA_INTERVALO_DIAS` conforme a rotina da operacao.
- O beat verifica a saúde do sistema a cada cinco minutos. Administradores acompanham o estado e o histórico em **Conta > Saúde do sistema**; mudanças para atenção ou crítico geram alertas sem repetição, seguidos de aviso de recuperação.
- Para WebSockets em producao, use um servidor ASGI compativel como Daphne ou Uvicorn.
- Sem `REDIS_URL`, o projeto usa fallback em memória para desenvolvimento e testes.
- O agente Windows de monitoramento esta documentado em `docs/monitoring/windows-agent.md`.
- As integracoes corporativas de SMTP, Teams e Slack estao documentadas em `docs/integrations/corporate.md`.
- O workflow `.github/workflows/ci.yml` valida lint, checks, migracoes e testes em Python 3.12 e 3.13 com PostgreSQL e Redis.

## Status das alterações

- A base foi migrada para Django 5.2 LTS e preparada para Python 3.12/3.13.
- Se você deseja que eu implemente funcionalidades específicas de solicitação, entrega, troca de equipamentos ou comprovantes assinados, por favor descreva os requisitos detalhadamente para que eu possa aplicar as mudanças no código.
