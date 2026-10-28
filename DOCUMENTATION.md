# Documentação Técnica Abrangente, Arquitetura e Operação

Este documento constitui o **manual técnico definitivo de arquitetura, funcionamento interno, subsistemas de sincronização, protocolo de atualização, telemetria de falhas, suíte de testes e procedimentos de auditoria** do **Google Drive para Linux (Ubuntu)**.

---

## 📑 Índice
1. [Visão Geral e Filosofia de Engenharia](#1-visão-geral-e-filosofia-de-engenharia)
2. [Estrutura Completa de Diretórios e Responsabilidade dos Arquivos](#2-estrutura-completa-de-diretórios-e-responsabilidade-dos-arquivos)
3. [Arquitetura de Dados em Tempo de Execução](#3-arquitetura-de-dados-em-tempo-de-execução)
4. [Motor de Sincronização e Sistema de Arquivos Virtual (FUSE VFS)](#4-motor-de-sincronização-e-sistema-de-arquivos-virtual-fuse-vfs)
5. [Lançador de Aplicativos Autônomos (Google Docs, Planilhas e Slides)](#5-lançador-de-aplicativos-autônomos-google-docs-planilhas-e-slides)
6. [Subsistema de Auto-Atualização Oficial (Protocolo Google Omaha)](#6-subsistema-de-auto-atualização-oficial-protocolo-google-omaha)
7. [Captura de Logs, Diagnóstico e Criação Automática de Issues no GitHub](#7-captura-de-logs-diagnóstico-e-criação-automática-de-issues-no-github)
8. [Servidor Integrado de Model Context Protocol (MCP) para Agentes de IA](#8-servidor-integrado-de-model-context-protocol-mcp-para-agentes-de-ia)
9. [Ciclo de Vida do Daemon e Execução no Ubuntu](#9-ciclo-de-vida-do-daemon-e-execução-no-ubuntu)
10. [Suíte de Testes Automatizados e Auditoria de Qualidade e Segurança](#10-suíte-de-testes-automatizados-e-auditoria-de-qualidade-e-segurança)

---

## 1. Visão Geral e Filosofia de Engenharia

O **Google Drive para Linux** foi concebido para preencher a lacuna histórica deixada pela ausência de um cliente oficial do Google Drive Desktop para distribuições Linux. Desenvolvido a partir da engenharia reversa das chamadas, estruturas de dados e protocolos do cliente oficial para Windows (`GoogleDriveFS.exe`), o projeto segue princípios rígidos de engenharia:

* **Zero-Disk Streaming (Economia Máxima de Armazenamento):** Arquivos armazenados no Google Drive são projetados na árvore de diretórios local sem ocupar espaço físico no disco até que sejam efetivamente requisitados por uma aplicação.
* **Isolamento e Estabilidade em Espaço de Usuário (User Space Safety):** Ao contrário do cliente do Windows que depende de um driver de filtro de kernel (`googledrivefs.sys`), nosso cliente opera 100% em espaço de usuário através do subsistema **FUSE (`libfuse3`)**. Uma falha no cliente nunca causa instabilidade no Kernel Linux.
* **Transparência de Protocolo:** Utilização dos mesmos protocolos de produção que a Google emprega para clientes corporativos: chamadas REST/gRPC da Google Drive API v3, autenticação OAuth 2.0 PKCE e o protocolo oficial de distribuição de software **Google Omaha v2/v3**.
* **Experiência Visual Nativa:** Em vez de abrir arquivos do Google Workspace como abas comuns de navegador, o sistema os projeta como **janelas autônomas de aplicativo (PWA)** no dock do Ubuntu, agrupadas sob seus respectivos ícones oficiais de alta resolução.

---

## 2. Estrutura Completa de Diretórios e Responsabilidade dos Arquivos

Abaixo está o mapeamento exato de cada componente do repositório:

```
google-drive-linux/
├── core/                               # Núcleo da aplicação e motores de baixo nível
│   ├── config.py                       # Gerenciador de configurações, caminhos de diretórios e padrões
│   ├── db.py                           # Camada ORM/SQLite para metadados, contas e filas de I/O
│   ├── auth.py                         # Gerenciador de autenticação OAuth 2.0 PKCE com servidor local
│   ├── api.py                          # Cliente oficial da Google Drive API v3 (Range requests e uploads)
│   ├── cache.py                        # Gerenciador de cache esparso em blocos (16MB) com descarte LRU
│   ├── vfs.py                          # Sistema de arquivos virtual em FUSE (implementação POSIX)
│   ├── watcher.py                      # Fila assíncrona de sincronização e detecção de mudanças remotas
│   ├── doc_launcher.py                 # Lançador de janelas dedicadas de aplicativo para Docs/Sheets
│   ├── diagnostic.py                   # Sistema de telemetria, diagnóstico e abertura de Issues no GitHub
│   └── mcp_server.py                   # Servidor MCP (Model Context Protocol) para IAs locais
├── ui/                                 # Camada de apresentação e interface com o desktop
│   ├── indicator.py                    # AppIndicator para barra superior do Ubuntu (GNOME / KDE)
│   └── webview_window.py               # Janela gráfica de configurações e conexão de contas
├── nautilus/                           # Integração com o explorador de arquivos do GNOME
│   └── google_drive_nautilus.py        # Extensão Python para Nautilus: badges de status e menu de contexto
├── updater/                            # Subsistema de manutenção e atualização contínua
│   └── omaha_updater.py                # Conector e avaliador de releases do Google Omaha v2/v3
├── assets/                             # Recursos visuais oficiais extraídos da distribuição
│   ├── icons/                          # 155 ícones oficiais (Docs, Sheets, Slides, Drive) em PNG 256x256 e SVG
│   └── html/                           # 111 telas oficiais de diálogos (dot.html, onboarding, sobre)
├── packaging/                          # Arquivos de empacotamento para distribuição Linux
│   ├── debian/                         # Estrutura do pacote Debian (.deb)
│   │   └── DEBIAN/
│   │       ├── control                 # Metadados e dependências APT (fuse3, python3-nautilus, etc.)
│   │       └── postinst                # Script pós-instalação (permissões, links simbólicos e Nautilus)
│   ├── systemd/                        # Definição do serviço em segundo plano
│   │   └── google-drive.service        # Unidade systemd --user para inicialização automática no boot
│   └── desktop/                        # Integração com o menu de aplicativos XDG
│       └── google-drive.desktop        # Arquivo de inicialização e ações rápidas no menu
├── tests/                              # Suíte de verificação automatizada
│   └── simulation_suite.py             # Simulador de integração ponta a ponta (7 subsistemas)
├── install.sh                          # Script de instalação de um clique para Ubuntu
├── uninstall.sh                        # Script de remoção completa do sistema
├── gdrive-cli                          # Utilitário de linha de comando para terminal
├── gdrive-doc-launcher                 # Binário executável do lançador de documentos PWA
├── main.py                             # Ponto de entrada do serviço/daemon
├── README.md                           # Documentação pública principal do repositório
├── REA.md                              # Estudo aprofundado de engenharia reversa do cliente Windows
├── DOCUMENTATION.md                    # Este documento: especificação e auditoria do projeto
└── LICENSE                             # Licença MIT
```

---

## 3. Arquitetura de Dados em Tempo de Execução

Quando instalado e em execução no Ubuntu, o cliente gerencia seus dados distribuídos nos seguintes locais padronizados pelo XDG:

### A. Diretório de Configurações e Estado (`~/.config/google-drive-linux/`)
* **`config.json`:** Armazena preferências do usuário: ponto de montagem, modo de sincronização (`stream` vs `mirror`), limite de banda de upload/download, intervalo de checagem do Omaha e preferências de notificações.
* **`accounts.json`:** Armazena tokens de autenticação (OAuth tokens, refresh tokens e identificadores de perfil). As chaves críticas são protegidas ou delegadas ao Linux Secret Service via `libsecret`.
* **`metadata.db`:** Banco de dados SQLite de alta performance operando em modo **WAL (Write-Ahead Logging)**. Estrutura relacional:
  * `items`: Catálogo completo de arquivos (id, nome, tamanho, md5, mimeType, parent_id, is_directory, is_pinned, sync_status).
  * `operations`: Fila de eventos pendentes de sincronização (`upload_new`, `upload_modify`, `delete`, `rename`, `mkdir`).
  * `cache_blocks`: Índice de blocos baixados localmente para descarte LRU.
  * `accounts`: Lista de contas vinculadas e estatísticas de uso de armazenamento.
* **`logs/google-drive.log`:** Registro rotativo de eventos do daemon com timestamps UTC e níveis de log (`INFO`, `WARNING`, `ERROR`).
* **`gdrive_ipc.sock` & `dfd_mcp.sock`:** Sockets de domínio UNIX para comunicação entre a CLI, extensões e o servidor MCP.

### B. Diretório de Cache de Blocos Esparsos (`~/.cache/google-drive-linux/`)
* Organizado por pastas baseadas no ID do arquivo: `~/.cache/google-drive-linux/<item_id>/`.
* Arquivos parciais em blocos: `chunk_0.bin` (0 a 16MB), `chunk_1.bin` (16 a 32MB), etc.
* Arquivos completos (offline): `full.bin`.

### C. Ponto de Montagem do Usuário (`~/GoogleDrive/`)
* O diretório visível no gerenciador de arquivos (Nautilus), montado através do FUSE.

---

## 4. Motor de Sincronização e Sistema de Arquivos Virtual (FUSE VFS)

O sistema de arquivos virtual implementado no módulo [`core/vfs.py`](file:///c:/Users/PlanetJet/Downloads/REA/google-drive-linux/core/vfs.py) é a peça central do projeto.

```
+-------------------------------------------------------------------+
|               Aplicações de Usuário (VLC, LibreOffice, Terminal)   |
+-------------------------------------------------------------------+
                                  |
                           POSIX Syscalls
                                  v
+-------------------------------------------------------------------+
|                     Linux Kernel (VFS Layer)                      |
+-------------------------------------------------------------------+
                                  |
                             /dev/fuse
                                  v
+-------------------------------------------------------------------+
|               GoogleDriveFUSE (core/vfs.py)                       |
+-------------------------------------------------------------------+
         |                                           |
    Metadata Lookup                             Block Read/Write
         v                                           v
+-------------------+                       +-----------------------+
|  SQLite Database  |                       | CacheManager          |
|  (metadata.db)    |                       | (core/cache.py)       |
+-------------------+                       +-----------------------+
                                                     |
                                            HTTP Range Requests
                                                     v
                                            +-----------------------+
                                            | Google Drive API v3   |
                                            +-----------------------+
```

### A. Ciclo de Leitura Sob Demanda (Streaming por Blocos de 16MB)
1. Quando uma aplicação solicita leitura (`read`) de um trecho de arquivo:
   $$\text{offset} = 33.554.432 \text{ bytes (32 MB)}, \quad \text{size} = 4.096 \text{ bytes}$$
2. O `CacheManager` calcula quais blocos de 16MB cobrem a solicitação:
   $$\text{start\_chunk} = \left\lfloor \frac{\text{offset}}{\text{chunk\_size}} \right\rfloor = \left\lfloor \frac{32\text{MB}}{16\text{MB}} \right\rfloor = 2$$
   $$\text{end\_chunk} = \left\lfloor \frac{\text{offset} + \text{size} - 1}{\text{chunk\_size}} \right\rfloor = 2$$
3. Se o arquivo `chunk_2.bin` já existir em cache local, a leitura é feita do disco local em microssegundos.
4. Se não existir, o `DriveAPI.read_range()` dispara uma requisição HTTP Range:
   ```http
   GET /drive/v3/files/<id>?alt=media HTTP/1.1
   Host: www.googleapis.com
   Authorization: Bearer [TOKEN]
   Range: bytes=33554432-50331647
   ```
5. O bloco de 16MB é salvo em `~/.cache/.../chunk_2.bin` e os 4KB solicitados são entregues imediatamente ao processo solicitante.

### B. Algoritmo de Descarte LRU (Least Recently Used)
Para evitar que o streaming encha o disco do computador, o método `enforce_quota_if_needed()` do `CacheManager`:
1. Monitora o tamanho total da pasta `~/.cache/google-drive-linux/`.
2. Se o tamanho ultrapassar `max_cache_size_gb` (padrão: 10 GB), ele executa a query:
   ```sql
   SELECT b.item_id, b.chunk_index FROM cache_blocks b
   JOIN items i ON b.item_id = i.id
   WHERE i.is_pinned = 0
   ORDER BY b.last_access ASC LIMIT 50;
   ```
3. Remove fisicamente os blocos mais antigos que **não pertençam a arquivos marcados como "Disponíveis Offline" (pinned)**.

### C. Fila de Sincronização Assíncrona de Uploads
1. Quando um novo arquivo é criado ou modificado no VFS:
   * A chamada `create()` ou `write()` grava os dados no cache local e registra uma entrada na tabela `operations` com status `pending`.
2. O thread em segundo plano [`core/watcher.py`](file:///c:/Users/PlanetJet/Downloads/REA/google-drive-linux/core/watcher.py):
   * Recupera as operações pendentes.
   * Executa o protocolo de **Resumable Upload** da Google Drive API v3 (inicia a sessão de upload via `POST /upload/drive/v3/files?uploadType=resumable` e envia o binário via `PUT`).
   * Ao concluir com sucesso, atualiza o item no SQLite para `synced` e remove da fila.

---

## 5. Lançador de Aplicativos Autônomos (Google Docs, Planilhas e Slides)

### A. O Desafio dos Formatos Proprietários da Google
Arquivos nativos do Workspace (`.gdoc`, `.gsheet`, `.gslides`) não contêm bytes binários padrão no Google Drive; são ponteiros JSON na nuvem.

### B. A Solução Implementada (`gdrive-doc-launcher` e `.desktop`)
O módulo [`core/doc_launcher.py`](file:///c:/Users/PlanetJet/Downloads/REA/google-drive-linux/core/doc_launcher.py) gera dinamicamente entradas no padrão de especificação XDG Desktop Entry no VFS.

Quando o Nautilus lista a pasta, o VFS sintetiza o arquivo com a estrutura:
```ini
[Desktop Entry]
Version=1.0
Type=Application
Name=Relatório Estratégico
Comment=Google Doc (Janela de Aplicativo)
Exec=/usr/local/bin/gdrive-doc-launcher 1a2b3c4d doc
Icon=/opt/google-drive-linux/assets/icons/docs.png
Terminal=false
StartupNotify=true
StartupWMClass=google-docs
Categories=Office;WordProcessor;
```

#### Execução:
Ao ser disparado, o `gdrive-doc-launcher`:
1. Localiza um navegador compatível com modo Site-Specific Browser (Chrome, Chromium, Brave ou Edge).
2. Executa o comando:
   ```bash
   google-chrome --app="https://docs.google.com/document/d/1a2b3c4d/edit" --class="google-docs"
   ```
3. O gerenciador de janelas do Ubuntu (Mutter / GNOME Shell) reconhece a classe `google-docs` e associa a janela ao ícone azul oficial do Google Docs no dock, separando-a completamente de qualquer outra janela de navegação web.

---

## 6. Subsistema de Auto-Atualização Oficial (Protocolo Google Omaha)

O módulo [`updater/omaha_updater.py`](file:///c:/Users/PlanetJet/Downloads/REA/google-drive-linux/updater/omaha_updater.py) implementa a comunicação com o mesmo servidor que atualiza o Google Drive mundialmente.

### A. Fluxo de Verificação Periódico
```
[ OmahaUpdater Thread ]
         |
         | (A cada 12 horas ou sob demanda)
         v
+--------------------------------------------------------------------+
|  POST https://update.googleapis.com/service/update2/json           |
|  Payload: AppID={6BBAE539-2232-434A-A4E5-9A33560C6283}, Ver=132   |
+--------------------------------------------------------------------+
                                  |
                                  v
+--------------------------------------------------------------------+
|  Resposta Oficial da Google:                                       |
|  - Status: "ok"                                                    |
|  - Nova Versão: 133.0.2.0                                          |
|  - URL: https://dl.google.com/release2/drive-file-stream/... .crx3|
|  - SHA-256: 1a0bdeee66da12bfe33a2ef6d56193475bb497f47afbce1354... |
+--------------------------------------------------------------------+
                                  |
                         Versão > Versão Atual?
                                  |
                        +---------+---------+
                        |                   |
                       SIM                 NÃO
                        v                   v
            +-----------------------+   +-------------------+
            | Dispara Notificação e|   | Permanece Ocioso  |
            | Extrai Novos Assets   |   +-------------------+
            +-----------------------+
```

### B. Extração Silenciosa de Assets Visuais
Os pacotes `.crx3` distribuídos pela Google contêm uma assinatura CRX seguidos por um arquivo compactado ZIP. O `OmahaUpdater`:
1. Localiza a assinatura mágica `PK\x03\x04` dentro do payload binário.
2. Descompacta e substitui os arquivos da pasta `assets/html/` e `assets/icons/` pelas novas versões emitidas pela Google (traduções, folhas de estilo e janelas de diálogo), mantendo o cliente esteticamente idêntico às versões mais recentes sem necessidade de recompilação.

---

## 7. Captura de Logs, Diagnóstico e Criação Automática de Issues no GitHub

O subsistema desenvolvido no módulo [`core/diagnostic.py`](file:///c:/Users/PlanetJet/Downloads/REA/google-drive-linux/core/diagnostic.py) garante monitoramento proativo de incidentes.

### A. Interceptação Global de Falhas
Na inicialização do daemon ([`main.py`](file:///c:/Users/PlanetJet/Downloads/REA/google-drive-linux/main.py)):
```python
install_global_crash_hook()
```
Isso substitui `sys.excepthook` para capturar qualquer exceção não tratada no interpretador Python.

### B. Sanitização Criteriosa de Dados Pessoais
Antes de qualquer envio, a função `DiagnosticCollector.sanitize()` executa expressões regulares para remover segredos:
* `Bearer [A-Za-z0-9_\-\.]+` $\rightarrow$ `Bearer [REDACTED_TOKEN]`
* `client_secret: "..."` $\rightarrow$ `client_secret: [REDACTED_SECRET]`
* E-mails de usuários são mascarados para preservar a privacidade.

### C. Geração de Relatório e Submissão da Issue
1. Um pacote JSON completo é gravado em `~/.config/google-drive-linux/crash_reports/report_<timestamp>.json`.
2. Se a variável `GITHUB_TOKEN` estiver definida, o cliente faz um `POST` direto para:
   `https://api.github.com/repos/gijogadds/google-drive-linux/issues`
3. Se não houver token, ele formata o relatório em Markdown com codificação URI e abre o navegador na tela de criação de issues com os campos de título, ambiente, stack trace e logs já 100% preenchidos.
4. O usuário recebe um alerta visual imediato via `notify-send`.

### D. O Ciclo Fechado: Da Issue à Cura Automática no Ubuntu (Self-Healing Loop)

Uma das maiores inovações do projeto é o **fechamento automático do ciclo de vida de bugs**. Uma vez que o erro é registrado no GitHub e corrigido pelo desenvolvedor, **o programa instalado na máquina do usuário recebe a atualização e se autocorrige automaticamente**:

```
+-------------------------------------------------------------------------+
| [1. MÁQUINA DO USUÁRIO]                                                 |
| Ocorre um erro inesperado no VFS ou na sincronização.                   |
| -> O hook global captura a exceção, sanitiza logs e tokens.             |
| -> Cria uma Issue em github.com/gijogadds/google-drive-linux.           |
| -> Dispara notificação no desktop informando sobre o incidente.         |
+-------------------------------------------------------------------------+
                                     |
                                     v
+-------------------------------------------------------------------------+
| [2. GITHUB / DESENVOLVEDOR / IA]                                        |
| O desenvolvedor ou IA inspeciona a Issue, identifica o bug,             |
| implementa a correção e sobe o commit para a branch `main` ou           |
| publica uma nova versão (ex: Release v1.0.1).                           |
+-------------------------------------------------------------------------+
                                     |
                                     v
+-------------------------------------------------------------------------+
| [3. MÁQUINA DO USUÁRIO - ATUALIZADOR AUTOMÁTICO (GitHubUpdater)]        |
| O thread de fundo `updater/github_updater.py` consulta a API do GitHub. |
| -> Detecta o novo commit / release publicado.                           |
| -> Baixa o pacote compactado diretamente do repositório.                |
| -> Substitui silenciosamente os arquivos corrigidos em /opt.            |
| -> Executa: systemctl --user restart google-drive.service               |
| -> Exibe notificação no desktop:                                        |
|    "Google Drive Atualizado! A correção foi aplicada com sucesso."      |
+-------------------------------------------------------------------------+
```

O usuário não precisa abrir terminal, reinstalar nem executar comandos manuais: o cliente detecta a correção no repositório, aplica o patch e se restabelece sozinho.

---

## 8. Servidor Integrado de Model Context Protocol (MCP) para Agentes de IA

Para reproduzir a capacidade descoberta no binário oficial `dfd_mcp_server.exe` (`apps/drive/fs/mcp/dfd_mcp_server.cc`), criamos o servidor [`core/mcp_server.py`](file:///c:/Users/PlanetJet/Downloads/REA/google-drive-linux/core/mcp_server.py).

### A. Especificação do Protocolo JSON-RPC 2.0
O servidor implementa as seguintes chamadas de ferramenta:

#### 1. `tools/list`
Retorna as capacidades disponibilizadas para modelos de linguagem (Claude Desktop, Antigravity, etc.):
* `search_drive`: Localiza arquivos por nome parcial ou extensão na base SQLite.
* `get_sync_status`: Retorna o status de conexão, cota utilizada e operações na fila.
* `read_drive_file`: Lê o conteúdo do arquivo através da camada de cache local.

#### 2. `tools/call` (`search_drive`)
* **Requisição:** `{"name": "search_drive", "arguments": {"query": "Contrato"}}`
* **Resposta:** Lista JSON estruturada de itens correspondentes com IDs, MIME types e tamanhos.

---

## 9. Ciclo de Vida do Daemon e Execução no Ubuntu

### A. Estados Operacionais do Daemon
```
[ DESLIGADO ]
      |
      | (systemctl --user start google-drive.service)
      v
[ INICIALIZAÇÃO ] ---> Lê ~/.config/google-drive-linux/config.json
      |                Inicializa SQLite em WAL Mode
      |                Verifica Contas em accounts.json
      v
[ MONTAGEM VFS ]  ---> FUSE monta ~/GoogleDrive em thread dedicada
      |
      +--------------> Inicia SyncWorker (processamento de fila)
      |
      +--------------> Inicia OmahaUpdater (verificador de versão)
      |
      +--------------> Inicia AyatanaAppIndicator (bandeja GTK3)
      v
[ OPERAÇÃO CONTÍNUA ]
      |
      | (SIGINT / SIGTERM / systemctl stop)
      v
[ ENCERRAMENTO LIMPO ]
      |
      +--> Desmonta sistema de arquivos FUSE (fusermount -u)
      +--> Conclui escritas pendentes de cache
      +--> Fecha conexões SQLite
      v
[ DESLIGADO ]
```

---

## 10. Suíte de Testes Automatizados e Auditoria de Qualidade e Segurança

O script [`tests/simulation_suite.py`](file:///c:/Users/PlanetJet/Downloads/REA/google-drive-linux/tests/simulation_suite.py) foi construído para servir como auditoria contínua de regressão.

### A. Matriz de Auditoria e Cobertura de Testes
Para auditar a saúde de todos os subsistemas, execute:
```bash
python3 tests/simulation_suite.py
```

Resultados auditados e validados:
1. **Teste 1 - Integridade do Esquema Relacional:** Valida criação das tabelas `accounts`, `items`, `operations` e `cache_blocks` e tratamento de campos nulos.
2. **Teste 2 - Conformidade POSIX do VFS:** Valida resolução de paths hierárquicos e mapeamento de permissões `0755` e `0644`.
3. **Teste 3 - Emissão de Metadados XDG Desktop:** Verifica integridade dos arquivos `.desktop` e sintaxe de classes de janela para Docs e Sheets.
4. **Teste 4 - Streaming e Alocação de Blocos:** Valida leitura precisa através de fronteiras de blocos de 16MB com dados idênticos ao arquivo original.
5. **Teste 5 - Conectividade com Endpoint Omaha de Produção:** Efetua requisição HTTPS real a `update.googleapis.com` e valida o parsing da resposta da Google.
6. **Teste 6 - Protocolo MCP JSON-RPC 2.0:** Valida retorno das ferramentas e busca na base de metadados.
7. **Teste 7 - Interceptação de Escrita e Enfileiramento Transacional:** Simula criação de arquivo no VFS e valida a inserção da tarefa de upload na fila.

### B. Diretrizes de Auditoria de Segurança
* **Permissões de Arquivos:** Todos os tokens locais em `accounts.json` e bancos `metadata.db` devem ter permissões restritas ao usuário (`chmod 600` / `chmod 700`).
* **Proteção contra DoS Local:** O algoritmo LRU impede esgotamento de espaço em disco em leituras massivas de arquivos de mídia.
* **Segurança de Execução:** Nenhuma chamada ao shell (`shell=True` no subprocess) é utilizada com dados não sanitizados, prevenindo vulnerabilidades de injeção de comandos.
