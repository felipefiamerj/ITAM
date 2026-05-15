# ITAM System

ITAM System é uma plataforma de gestão de ativos de TI e operações de suporte. O sistema foi desenvolvido em Django e integra controle de equipamentos, chamados, solicitações de acesso, estoque e notificações.

## Visão geral

O projeto oferece:

- Autenticação via matrícula e senha.
- Solicitações de acesso de novos usuários.
- Aprovação de cadastros de usuários pelo administrador com link de primeiro acesso por e-mail.
- Gestão de usuários com níveis de acesso (solicitante, técnico, analista, administrador).
- Catálogo de equipamentos com status, condição e histórico de movimentações.
- Rastreamento de equipamentos com QR Code gerado automaticamente.
- Movimentações de equipamentos, incluindo entrada, saída, devolução, manutenção, transferência e troca.
- Chamados técnicos para suporte com prioridade, status e solução.
- Painel de dashboard com métricas de equipamentos, chamados, usuários e histórico de auditoria.
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

- Python 3.10 + Django 4.2.x
- Django REST Framework
- Django Crispy Forms + Bootstrap 5
- django-guardian para permissões objeto
- django-auditlog para auditoria de alterações
- Celery + django-celery-beat + django-celery-results para automações e rotinas agendadas
- Channels + Redis para WebSockets e mensagens em tempo real
- qrcode para geração de QR Code de equipamentos
- reportlab para geração de documentos/impressos (quando necessário)

## Configuração e execução

1. Ative o ambiente virtual:

```powershell
& .\.venv\Scripts\Activate.ps1
```

2. Instale as dependências:

```powershell
pip install -r requirements.txt
```

3. Configure o arquivo `.env` com variáveis de ambiente, se desejar.
   - `APP_NAME=Nome da empresa ou do sistema`
   - `APP_SHORT_NAME=Sigla`
   - `DJANGO_ENV=development` ou `production`
   - `SITE_URL=https://seu-dominio`
   - `SECRET_KEY=...`
   - `REDIS_URL=redis://127.0.0.1:6379/0`

4. Crie e aplique as migrações:

```powershell
python manage.py makemigrations
python manage.py migrate
```

5. Crie um superusuário:

```powershell
python manage.py createsuperuser
```

6. Inicie o servidor de desenvolvimento:

```powershell
python manage.py runserver
```

7. Acesse o sistema em:

```text
http://127.0.0.1:8000/
```

## Observações

- O sistema utiliza `SQLite` por padrão, mas pode ser configurado para `PostgreSQL` via variável `DB_ENGINE` no `.env`.
- Para rodar com banco local, ajuste `DB_NAME`, `DB_USER`, `DB_PASSWORD`, `DB_HOST` e `DB_PORT` conforme seu ambiente.
- O `DEBUG` está ativado por padrão no ambiente de desenvolvimento.
- A administração de arquivos está em `staticfiles/` e `media/`.
- Para validar a instalação em uma máquina de cliente, rode:

```powershell
python manage.py verificar_instalacao
```

- Para subir o sistema completo no Windows sem Docker, use os scripts em `scripts/`:

```powershell
.\scripts\bootstrap.ps1
.\scripts\start-all.ps1
```

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

- Para WebSockets, use um servidor ASGI compatível como Daphne ou Uvicorn.
- Sem `REDIS_URL`, o projeto usa fallback em memória para desenvolvimento e testes.

## Status das alterações

- A base foi validada em Python 3.10 com PostgreSQL local e migrações recriadas do zero.
- Se você deseja que eu implemente funcionalidades específicas de solicitação, entrega, troca de equipamentos ou comprovantes assinados, por favor descreva os requisitos detalhadamente para que eu possa aplicar as mudanças no código.
