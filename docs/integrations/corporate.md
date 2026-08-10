# Integracoes corporativas

Este pacote cobre as integracoes operacionais de saida do FIAME System:

- Email SMTP real para primeiro acesso, recuperacao de senha e termos digitais.
- Webhooks corporativos para Microsoft Teams e Slack.
- Comando de teste para validar as credenciais antes de liberar producao.

## 1. Email SMTP

Configure no `.env`:

```env
EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend
EMAIL_HOST=smtp.seu-provedor.com
EMAIL_PORT=587
EMAIL_HOST_USER=usuario-smtp
EMAIL_HOST_PASSWORD=senha-smtp
DEFAULT_FROM_EMAIL=FIAME System <noreply@seu-dominio.com>
ITAM_ADMIN_EMAILS=suporte@seu-dominio.com
```

Teste:

```powershell
python manage.py testar_integracoes --email-to suporte@seu-dominio.com
```

`ITAM_ADMIN_EMAILS` recebe avisos administrativos, incluindo solicitacoes de recuperacao de senha.

## 2. Microsoft Teams

Crie um Incoming Webhook ou Workflow no canal desejado e configure:

```env
ITAM_CORPORATE_WEBHOOKS_ENABLED=True
ITAM_TEAMS_WEBHOOK_URL=https://...
ITAM_WEBHOOK_TIMEOUT_SECONDS=5
```

Teste:

```powershell
python manage.py testar_integracoes --webhooks
```

## 3. Slack

Crie um Incoming Webhook no Slack e configure:

```env
ITAM_CORPORATE_WEBHOOKS_ENABLED=True
ITAM_SLACK_WEBHOOK_URL=https://hooks.slack.com/services/...
ITAM_WEBHOOK_TIMEOUT_SECONDS=5
```

Teste:

```powershell
python manage.py testar_integracoes --webhooks
```

Teams e Slack podem ficar ativos ao mesmo tempo.

## 4. Quando os webhooks disparam

Os webhooks sao disparados quando o sistema notifica:

- administradores via `notificar_admins`
- time operacional via `notificar_time_operacional`

Isso inclui alertas como SLA, telemetria critica, termos pendentes e eventos operacionais que ja usam esses canais internos.

Notificacoes pessoais para um unico usuario continuam apenas dentro do sistema, para evitar vazamento de informacao em canais coletivos.

## 5. Validacao de producao

Antes de liberar:

```powershell
python manage.py verificar_instalacao
python manage.py testar_integracoes --email-to suporte@seu-dominio.com --webhooks
```

Se usar `--all`, informe tambem `--email-to`:

```powershell
python manage.py testar_integracoes --all --email-to suporte@seu-dominio.com
```

## 6. Proximas integracoes

AD/LDAP/SSO, GLPI/Jira/ServiceNow e ERP/CMDB dependem de dados do ambiente corporativo: URL, protocolo, credenciais, mapeamento de grupos e contrato de API. A base atual deixa email e alertas corporativos prontos; essas integracoes de entrada devem ser implementadas com os detalhes do fornecedor escolhido.
