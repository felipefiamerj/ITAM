# Agente Windows de monitoramento

O backend ja recebe telemetria em `/api/telemetria/ingestao/`. Este agente PowerShell coleta sinais basicos da maquina Windows e envia heartbeat periodico para o FIAME System.

## 1. Criar token do agente

No servidor do FIAME:

```powershell
python manage.py criar_agente_monitoramento "Unidade SP"
```

Com usuario responsavel:

```powershell
python manage.py criar_agente_monitoramento "Unidade SP" --criado-por 9000
```

Guarde o token impresso. Ele sera usado no endpoint Windows.

## 2. Preparar identificador do equipamento

O agente so atualiza um ativo se o backend conseguir encontrar o equipamento por pelo menos um destes campos:

- `AssetId`, enviado como `id_patrimonio`
- `ServiceTag`
- `SerialNumber`

O caminho mais simples e preencher `AssetId` com o patrimonio cadastrado no sistema.

## 3. Teste manual no endpoint Windows

Copie a pasta `agents/windows` para a maquina monitorada e crie um arquivo `itam-agent.config.json` baseado em `itam-agent.config.example.json`.

Execute:

```powershell
.\itam-agent.ps1 -ConfigPath .\itam-agent.config.json -PrintPayload
```

O retorno esperado contem:

```text
Telemetria enviada: processados=1
```

Se `processados=0`, confira se o patrimonio, service tag ou serial existem em Equipamentos.

## 4. Instalar como tarefa agendada

Execute PowerShell como administrador:

```powershell
.\install-scheduled-task.ps1 `
  -BaseUrl https://itam.seu-dominio.com `
  -AgentToken TOKEN_DO_AGENTE `
  -AssetId PATRIMONIO-001 `
  -RunNow
```

Por padrao a tarefa roda a cada 5 minutos como `SYSTEM`.

Para trocar a periodicidade:

```powershell
.\install-scheduled-task.ps1 -BaseUrl https://itam.seu-dominio.com -AgentToken TOKEN -AssetId PATRIMONIO-001 -IntervalMinutes 10 -Force
```

## 5. Dados coletados

- Hostname e IP principal
- Fabricante, modelo e dominio da maquina
- Serial/Service Tag via WMI/CIM
- Usuario logado
- Sistema operacional e ultimo boot
- CPU, memoria livre e total
- Discos locais e menor percentual livre
- Bateria, quando existir

Alertas sao enviados como `warning` ou `critical` quando bateria ou disco passam dos limites configurados.

## 6. Verificacao no FIAME

No sistema, abra:

```text
/ia/monitoramento/
```

Ou consulte o ativo em:

```text
/api/telemetria/equipamentos/PATRIMONIO-001/
```

O Celery beat tambem executa a verificacao de maquinas sem sinal e muda ativos atrasados para offline.
