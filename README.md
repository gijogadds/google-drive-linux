# Google Drive para Linux (Ubuntu) 🚀

Cliente nativo, híbrido e de alta performance do **Google Drive para Ubuntu e distribuições Linux**, desenvolvido a partir da engenharia reversa do cliente oficial do Windows (`Google Drive for Desktop / Drive File Stream`) e evoluído com recursos avançados de sincronização seletiva, controle de conflitos e offline inspirado nas melhores arquiteturas de mercado.

Oferece **paridade total de recursos** em relação ao cliente oficial do Windows — e vai além com **arquitetura híbrida (FUSE Stream sob demanda + Sincronização Real de Pastas Locais)**, **Delta Sync incremental (`changes.list`)**, **motor `.gitignore` visual e em tempo real**, **matriz de conflitos de 4 casos com resolução em 1 clique**, **exportação offline de Google Docs para LibreOffice/MS Office**, **pasta virtual `/Compartilhados comigo`**, **janelas de aplicativo dedicadas (PWA)**, **servidor MCP integrado para IA local**, **atualização em dois canais (Google Omaha + GitHub Self-Healing)** e **auditoria automatizada ponta a ponta**.

> 📚 **Documentação Técnica Detalhada:**
> * 📘 [**DOCUMENTATION.md**](DOCUMENTATION.md) — Manual técnico completo de arquitetura, sincronização híbrida, delta sync, matriz de conflitos, regras `.gitignore`, VFS, UI GTK3, telemetria e auditoria.
> * 🔬 [**REA.md**](REA.md) — Estudo aprofundado de engenharia reversa dos binários do Windows (`GoogleDriveSetup.exe` e Dokan Driver).

---

## 📑 Sumário
1. [Engenharia Reversa e Evolução da Arquitetura](#1-engenharia-reversa-e-evolução-da-arquitetura)
2. [Arquitetura Híbrida para Ubuntu](#2-arquitetura-híbrida-para-ubuntu)
3. [Recursos Avançados de Sincronização, Offline e Proteção](#3-recursos-avançados-de-sincronização-offline-e-proteção)
4. [Interface Gráfica Nativa GTK3 (Telas Avançadas)](#4-interface-gráfica-nativa-gtk3-telas-avançadas)
5. [Google Docs, Planilhas e Slides como Aplicativos Nativos](#5-google-docs-planilhas-e-slides-como-aplicativos-nativos)
6. [Auto-Atualização Dual (Google Omaha + GitHub Self-Healing)](#6-auto-atualização-dual-google-omaha--github-self-healing)
7. [Sistema de Coleta de Logs e Abertura Automática de Issues](#7-sistema-de-coleta-de-logs-e-abertura-automática-de-issues)
8. [Servidor MCP Embutido para Agentes de IA](#8-servidor-mcp-embutido-para-agentes-de-ia)
9. [Suíte de Testes e Auditoria Automatizada](#9-suíte-de-testes-e-auditoria-automatizada)
10. [Estrutura do Repositório](#10-estrutura-do-repositório)
11. [Guia de Instalação no Ubuntu](#11-guia-de-instalação-no-ubuntu)
12. [Como Usar (CLI e Interface)](#12-como-usar-cli-e-interface)
13. [Guia de Desinstalação](#13-guia-de-desinstalação)

---

## 1. Engenharia Reversa e Evolução da Arquitetura

A partir da análise do instalador oficial `GoogleDriveSetup.exe` (versão 132.0.0.0, ~292 MB) e dos binários em `C:\Program Files\Google\Drive File Stream`:
* **Por que o binário do Windows não roda no Linux (nem com Wine)?** O cliente oficial depende do driver de filtro de kernel Dokan (`googledrivefs*.sys`) e do motor `WebView2` do Edge. No Linux, substituímos o driver de kernel por **FUSE em espaço de usuário (`libfuse3`)** e a UI por **GTK3 nativo**.
* **Evolução Híbrida:** Combinamos o melhor do streaming sob demanda (FUSE com blocos esparsos de 16 MB e zero consumo prévio de disco) com um motor dedicado de **sincronização real de pastas locais** (`LocalSyncWorker`), permitindo escolher pasta por pasta se deseja streaming ou sincronização física real.

---

## 2. Arquitetura Híbrida para Ubuntu

| Recurso | Windows (Drive File Stream) | Ubuntu (Nossa Solução) |
| :--- | :--- | :--- |
| **Sistema de Arquivos Virtual** | Driver de Kernel Dokan (`googledrivefs.sys`) | **FUSE (`libfuse3` / modo de usuário)** com `statfs()` de cota real |
| **Sincronização de Pastas Reais** | Apenas espelhamento global | **Híbrido por pasta (`LocalSyncWorker`)**: `TWO_WAY`, `UPLOAD_ONLY`, `DOWNLOAD_ONLY` |
| **Detecção de Alterações** | Polling / Change Feed interno | **Drive API v3 Delta Sync (`changes.list`)** com `startPageToken` persistido |
| **Uploads de Arquivos Grandes** | Resumable interno | **Resumable Upload em blocos de 8 MB** com retomada HTTP `308` e pool paralelo |
| **Camada de Streaming** | `dokancc31931.dll` | **Motor de Sparse Chunks (16 MB)** com coerência por MD5 e LRU |
| **Filtro de Exclusões** | Limitado | **Motor `.gitignore` completo** (`*`, `?`, `**`, `/`, `!negação`) + editor visual |
| **Resolução de Conflitos** | Automático básico | **Matriz de 4 casos** + resolução em 1 clique + envio seguro para Lixeira do SO |
| **Google Docs Offline** | Apenas atalho web | **3 modos**: Atalho Web (`.gdoc`), LibreOffice (`.odt`/`.ods`/`.odp`) ou MS Office (`.docx`/`.xlsx`/`.pptx`) |
| **Compartilhados Comigo** | Atalho manual | **Pasta virtual `/Compartilhados comigo`** exposta diretamente no FUSE |
| **Bandeja e Gerenciador** | System Tray + Shell Ext | **AyatanaAppIndicator (GNOME/KDE)** + **Extensão Python para Nautilus** |
| **Banco de Metadados** | `metadata_sqlite_db` | **SQLite WAL** (`items`, `operations`, `cache_blocks`, `accounts`, `sync_tokens`, `conflicts`, `activity_log`, `local_sync_rules`) |
| **Diagnóstico & Updates** | Crashpad / Omaha | **Omaha (Canal 1)** + **GitHub Self-Healing (Canal 2)** + **Issues automáticas** |

---

## 3. Recursos Avançados de Sincronização, Offline e Proteção

* **Delta Sync Incremental (`changes.list`):** Após o scan inicial, o cliente persiste `changes_page_token` por conta na tabela `sync_tokens` e consulta apenas deltas incrementais da API v3 — reduzindo drasticamente o consumo de cota, CPU e tempo de sincronização.
* **Sincronização Híbrida de Pastas Locais (`core/local_sync.py`):** Sincronize qualquer pasta local real (inclusive em HD externo) em paralelo com as montagens FUSE, com deduplicação por MD5 (*File Matching*), suporte a `TWO_WAY`, `UPLOAD_ONLY` e `DOWNLOAD_ONLY`.
* **Proteção contra Desconexão de HD Externo e Exclusão Acidental:**
  * `missing_file_behavior = "UNSYNC"` (padrão seguro): se um HD externo desconectar ou uma pasta raiz ficar vazia/ausente, o motor **jamais apaga arquivos na nuvem**.
  * Exclusões remotas aplicadas localmente movem arquivos para a **Lixeira do sistema** (`gio trash` com fallback XDG `~/.local/share/Trash`), nunca usando `unlink` destrutivo.
  * Exclusões na nuvem usam **Lixeira do Google Drive** (`PATCH {"trashed": true}`) em vez de remoção permanente.
* **Motor `.gitignore` (`core/ignore.py`):** Filtra arquivos temporários, locks do Office/LibreOffice, caches (`__pycache__/`, `node_modules/`, etc.) e regras personalizadas definidas em `~/.config/google-drive-linux/ignore_rules.txt`.
* **Matriz de Conflitos de 4 Casos (`core/conflicts.py`):** Trata separadamente `LOCAL_EDIT_CLOUD_EDIT`, `LOCAL_EDIT_CLOUD_DELETE`, `LOCAL_EDIT_UNSYNC` e `LOCAL_DELETE_CLOUD_EDIT` com políticas configuráveis (`KEEP_BOTH`, `KEEP_LOCAL`, `KEEP_CLOUD`, `KEEP_UNSYNC`, `ALWAYS_ASK`).
* **Exportação Offline de Google Docs (`core/docs_converter.py`):** Permite fixar pastas/arquivos offline escolhendo entre atalho web (`.gdoc`/`.gsheet`/`.gslides`), LibreOffice (`.odt`/`.ods`/`.odp`) ou Microsoft Office (`.docx`/`.xlsx`/`.pptx`).
* **Suporte a Proxy e Backoff Inteligente:** Suporte a proxy `NONE`, `SYSTEM` ou `MANUAL` e retry exponencial com `Retry-After` + jitter para respostas HTTP `403`/`429`/`5xx`.

---

## 4. Interface Gráfica Nativa GTK3 (Telas Avançadas)

Além da tela inicial, atividade em tempo real, busca dupla (Computador + Nuvem), notificações e popover multi-conta, a janela principal (`ui/webview_window.py` + `ui/advanced_views.py`) inclui:

1. **📁 Pastas Locais (Híbrido):** Gerenciador visual para adicionar, ativar/pausar, abrir e remover regras de sincronização de pastas reais por conta, com seletor de pasta no Drive, modo de sincronização e formato offline de Docs.
2. **🚫 Regras de Exclusão (`.gitignore`):** Editor visual monoespaçado de `ignore_rules.txt`, atalhos de 1 clique (`node_modules/`, `.git/`, `*.log`, `build/ & dist/`, `.venv/`) e **testador em tempo real** que indica instantaneamente se um caminho será ignorado ou sincronizado antes mesmo de salvar.
3. **⚠️ Central de Conflitos:** Badge dinâmico na barra lateral (`⚠️ Conflitos (N)`), resolução de conflitos pendentes com **1 clique** (*Manter Local*, *Manter Nuvem* ou *Manter Ambos*) e painel completo das políticas automáticas de conflito e proteção de HD externo.
4. **⚙️ Preferências Expandidas:** Janela com rolagem (`Gtk.ScrolledWindow`) incluindo configurações de formato padrão de Google Docs Offline, comportamento para HD externo desconectado e número de threads paralelas de sincronização (`max_sync_workers`).

---

## 5. Google Docs, Planilhas e Slides como Aplicativos Nativos

* **Janela Dedicada (Modo SSB / PWA):** Ao abrir um arquivo `.desktop` / `.gdoc` / `.gsheet` / `.gslides`, o `gdrive-doc-launcher` executa o navegador Chromium/Chrome/Brave em modo `--app`, sem barra de endereços ou abas.
* **Ícones Oficiais no Dock do Ubuntu:** Agrupamento nativo via `StartupWMClass` (`google-docs`, `google-sheets`, `google-slides`) com ícones em alta resolução (256x256 PNG).
* **Login com Conta Pré-Preenchida:** Seleção automática do perfil/conta ativa (`authuser` / e-mail) ao abrir os aplicativos do Workspace.

---

## 6. Auto-Atualização Dual (Google Omaha + GitHub Self-Healing)

* **Canal 1 — Google Omaha (`updater/omaha_updater.py`):** Consulta `https://update.googleapis.com/service/update2/json` (AppID `{6BBAE539-2232-434A-A4E5-9A33560C6283}`), valida SHA-256 e atualiza assets oficiais (`assets/html/` e `assets/icons/`).
* **Canal 2 — GitHub Self-Healing (`updater/github_updater.py`):** Monitora `gijogadds/google-drive-linux`, baixa automaticamente novos commits/releases da branch `main`, aplica em `/opt/google-drive-linux` e reinicia `google-drive.service` silenciosamente.

---

## 7. Sistema de Coleta de Logs e Abertura Automática de Issues

* **Captura Global (`core/diagnostic.py`):** Intercepta exceções não tratadas (`install_global_crash_hook`), mascara automaticamente tokens OAuth (`Bearer`), segredos e e-mails, salva o relatório em `~/.config/google-drive-linux/crash_reports/` e abre/prepara a Issue no GitHub com notificação desktop (`notify-send`).
* **Acionamento Manual:** Disponível via menu da engrenagem ⚙️, menu da bandeja do sistema ou `gdrive-cli report -m "Descrição"`.

---

## 8. Servidor MCP Embutido para Agentes de IA

Implementação do protocolo **Model Context Protocol (MCP)** (`core/mcp_server.py`), acionado via `gdrive-cli mcp`:
* `search_drive(query)`: Busca arquivos por nome/metadados no catálogo SQLite local.
* `get_sync_status()`: Retorna status da sincronização, cota e fila de operações.
* `read_drive_file(item_id)`: Lê o conteúdo de arquivos via camada de cache local.

---

## 9. Suíte de Testes e Auditoria Automatizada

O projeto conta com duas suítes de verificação:
* **`scripts/audit_and_test.py`** (também acessível via `gdrive-cli audit`):
  * **Auditoria de sintaxe:** Compilação de todos os `28/28` arquivos Python sem erros.
  * **15 testes unitários e de integração** cobrindo `.gitignore` (globs, `**`, diretórios, negação), matriz de conflitos de 4 casos, resolução em 1 clique (`KEEP_LOCAL`, `KEEP_CLOUD`, `KEEP_BOTH`), testador de regras em memória, exportação offline de Docs, Delta Sync (`changes.list`), `LocalSyncWorker` com proteção de raiz ausente, invalidação de cache por MD5, pasta virtual `/Compartilhados comigo`, `mkdir` real, `statfs()` com cota real, parsing RFC3339 e `flush()` sem duplicação.
  * **Auditoria ao vivo do ambiente:** Verifica `systemctl --user is-active google-drive.service`, montagens FUSE ativas, integridade do SQLite (`PRAGMA integrity_check`) e saúde dos logs.
* **`tests/simulation_suite.py`:** Simulação ponta a ponta dos 7 subsistemas originais (DB, FUSE POSIX, PWA, Cache 16 MB, Omaha, MCP e fila de upload).

---

## 10. Estrutura do Repositório

```
google-drive-linux/
├── core/
│   ├── config.py             # Configurações globais, multi-conta, proxy e HD externo
│   ├── db.py                 # SQLite WAL (items, operations, sync_tokens, conflicts, activity_log, local_sync_rules)
│   ├── auth.py               # Autenticação OAuth 2.0 PKCE multi-conta
│   ├── api.py                # Cliente Drive API v3 (Delta Sync, Resumable 8 MB, Trash, Export, Proxy, Retry)
│   ├── cache.py              # Cache esparso 16 MB, coerência MD5, pin recursivo e LRU
│   ├── vfs.py                # Sistema de arquivos FUSE (POSIX, /Compartilhados comigo, statfs real, .gitignore)
│   ├── watcher.py            # Motor de sincronização com Delta Sync e ThreadPoolExecutor paralelo
│   ├── local_sync.py         # Motor híbrido de sincronização real de pastas locais (TWO_WAY/UPLOAD/DOWNLOAD)
│   ├── ignore.py             # Motor de regras compatível com .gitignore
│   ├── conflicts.py          # Matriz de conflitos de 4 casos, MD5 e Lixeira segura do SO
│   ├── docs_converter.py     # Conversor/exportador offline de Google Docs (LINK / ODF / MS Office)
│   ├── doc_launcher.py       # Lançador PWA para Google Docs/Sheets/Slides
│   ├── diagnostic.py         # Coletor de diagnóstico, sanitização e Issues no GitHub
│   ├── version.py            # Controle de versão semântica
│   └── mcp_server.py         # Servidor MCP para agentes de IA
├── ui/
│   ├── indicator.py          # Indicador da bandeja GNOME/KDE (AppIndicator)
│   ├── webview_window.py     # Janela principal GTK3, busca dupla, popover multi-conta e Preferências
│   ├── advanced_views.py     # Telas GTK3: Pastas Locais, Editor .gitignore e Central de Conflitos 1-clique
│   └── install_wizard.py     # Assistente inicial de configuração
├── nautilus/
│   └── google_drive_nautilus.py # Extensão de badges e menu de contexto para Nautilus
├── updater/
│   ├── omaha_updater.py      # Canal 1: Atualizações oficiais Google Omaha
│   └── github_updater.py     # Canal 2: Auto-cura e atualizações via GitHub
├── scripts/
│   ├── audit_and_test.py     # Auditoria completa de sintaxe, 15 testes e verificação ao vivo
│   └── setup_embedded_runtime.sh
├── tests/
│   └── simulation_suite.py   # Suíte de simulação de subsistemas
├── assets/                   # Ícones oficiais e assets HTML
├── packaging/                # Pacote .deb, serviço systemd --user e arquivos .desktop
├── install.sh / uninstall.sh # Instalador e desinstalador automatizados
├── gdrive-cli                # Utilitário completo de linha de comando
├── gdrive-doc-launcher       # Executável do lançador de documentos PWA
└── main.py                   # Ponto de entrada do daemon e orquestrador multi-conta
```

---

## 11. Guia de Instalação no Ubuntu

Compatível com **Ubuntu 20.04 LTS, 22.04 LTS, 24.04 LTS** e derivados (Linux Mint, Pop!_OS, Debian).

```bash
git clone https://github.com/gijogadds/google-drive-linux.git
cd google-drive-linux
sudo chmod +x install.sh
sudo ./install.sh
```

> **Dica sobre cota da API Google (Opcional):** Por padrão, o cliente utiliza credenciais OAuth comunitárias compartilhadas, que ocasionalmente podem receber `HTTP 403 (Rate Limit)` em horários de pico (tratado automaticamente com backoff exponencial). Para cota 100% exclusiva e sem limites compartilhados, você pode configurar seu próprio `client_id`/`client_secret` gratuito do Google Cloud em `~/.config/google-drive-linux/config.json`.

---

## 12. Como Usar (CLI e Interface)

```bash
# Conectar conta Google e iniciar daemon
gdrive-cli login
systemctl --user enable --now google-drive.service

# Status geral (contas, cota real, fila e montagens)
gdrive-cli status

# Sincronização híbrida de pastas reais (TWO_WAY, UPLOAD_ONLY, DOWNLOAD_ONLY)
gdrive-cli local-sync list
gdrive-cli local-sync add <account_id> ~/Projetos root --mode TWO_WAY --export OPEN_DOCUMENT
gdrive-cli local-sync remove ~/Projetos

# Regras .gitignore
gdrive-cli ignore list
gdrive-cli ignore test app/node_modules/pacote/index.js

# Central de conflitos e log estruturado de atividades
gdrive-cli conflicts
gdrive-cli activity

# Auditoria completa (sintaxe + 15 testes + ambiente ao vivo)
gdrive-cli audit

# Atualizações (Canal 1 Google Omaha / Canal 2 GitHub)
gdrive-cli update
gdrive-cli upgrade

# Diagnóstico / Relatar problema e Servidor MCP
gdrive-cli report -m "Descrição do problema"
gdrive-cli mcp
```

---

## 13. Guia de Desinstalação

```bash
cd google-drive-linux
sudo chmod +x uninstall.sh
sudo ./uninstall.sh
```

---

## 📄 Licença
Distribuído sob a licença MIT. Consulte [`LICENSE`](LICENSE) para mais informações.
