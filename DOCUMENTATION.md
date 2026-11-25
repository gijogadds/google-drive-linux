# Documentação Técnica Abrangente, Arquitetura e Operação

Este documento constitui o **manual técnico definitivo de arquitetura, funcionamento interno, subsistemas de sincronização híbrida, delta sync incremental, resolução de conflitos, regras `.gitignore`, interface GTK3, protocolo de atualização, telemetria de falhas, suíte de testes e procedimentos de auditoria** do **Google Drive para Linux (Ubuntu)**.

---

## 📑 Índice
1. [Visão Geral e Filosofia de Engenharia](#1-visão-geral-e-filosofia-de-engenharia)
2. [Estrutura Completa de Diretórios e Responsabilidade dos Arquivos](#2-estrutura-completa-de-diretórios-e-responsabilidade-dos-arquivos)
3. [Arquitetura de Dados em Tempo de Execução](#3-arquitetura-de-dados-em-tempo-de-execução)
4. [Motor de Sincronização Híbrida, Delta Sync e Sistema de Arquivos Virtual (FUSE VFS)](#4-motor-de-sincronização-híbrida-delta-sync-e-sistema-de-arquivos-virtual-fuse-vfs)
5. [Regras de Exclusão (`.gitignore`), Matriz de Conflitos e Offline Docs](#5-regras-de-exclusão-gitignore-matriz-de-conflitos-e-offline-docs)
6. [Interface Gráfica Nativa GTK3 e Telas Avançadas](#6-interface-gráfica-nativa-gtk3-e-telas-avançadas)
7. [Lançador de Aplicativos Autônomos (Google Docs, Planilhas e Slides)](#7-lançador-de-aplicativos-autônomos-google-docs-planilhas-e-slides)
8. [Subsistema de Atualização Dual: Canal 1 (Google Omaha) e Canal 2 (GitHub)](#8-subsistema-de-atualização-dual-canal-1-google-omaha-e-canal-2-github)
9. [Captura de Logs, Diagnóstico e Criação Automática de Issues no GitHub](#9-captura-de-logs-diagnóstico-e-criação-automática-de-issues-no-github)
10. [Servidor Integrado de Model Context Protocol (MCP) para Agentes de IA](#10-servidor-integrado-de-model-context-protocol-mcp-para-agentes-de-ia)
11. [Ciclo de Vida do Daemon, Multi-Conta e OAuth Rate Limit](#11-ciclo-de-vida-do-daemon-multi-conta-e-oauth-rate-limit)
12. [Suíte de Testes Automatizados e Auditoria de Qualidade e Segurança](#12-suíte-de-testes-automatizados-e-auditoria-de-qualidade-e-segurança)

---

## 1. Visão Geral e Filosofia de Engenharia

O **Google Drive para Linux** foi concebido para oferecer uma experiência nativa e superior no Ubuntu, combinando a engenharia reversa do cliente oficial do Windows (`GoogleDriveFS.exe`) com inovações arquiteturais modernas:

* **Arquitetura Híbrida (FUSE Stream + Pastas Locais Reais):** Suporta simultaneamente **Zero-Disk Streaming** via FUSE (`libfuse3`) e **sincronização física seletiva** de qualquer diretório local real (`LocalSyncWorker`) nos modos `TWO_WAY`, `UPLOAD_ONLY` e `DOWNLOAD_ONLY`.
* **Eficiência de Rede via Delta Sync (`changes.list`):** Após a indexação inicial, apenas alterações incrementais são buscadas via `changes_page_token` persistido no SQLite, eliminando varreduras completas recorrentes.
* **Segurança de Dados em Primeiro Lugar:** Exclusões locais causadas por deleção remota sempre movem arquivos para a **Lixeira do SO** (`gio trash` / XDG Trash); exclusões na nuvem vão para a **Lixeira do Google Drive** (`PATCH {"trashed": true}`); e HDs externos desconectados ativam automaticamente proteção contra exclusão (`missing_file_behavior = "UNSYNC"`).
* **Isolamento em Espaço de Usuário (User Space Safety):** Opera 100% em espaço de usuário via FUSE e GTK3 nativo, sem módulos de kernel proprietários.

---

## 2. Estrutura Completa de Diretórios e Responsabilidade dos Arquivos

```
google-drive-linux/
├── .github/
│   ├── ISSUE_TEMPLATE/                 # Templates de bug_report e feature_request
│   └── workflows/ci.yml                # Pipeline de CI/CD
├── core/
│   ├── config.py                       # Configurações globais, multi-conta, proxy, políticas e HD externo
│   ├── db.py                           # SQLite WAL: items, operations, cache_blocks, accounts, sync_tokens, conflicts, activity_log, local_sync_rules
│   ├── auth.py                         # OAuth 2.0 PKCE multi-conta e refresh automático de tokens
│   ├── api.py                          # Drive API v3: changes.list, resumable upload 8 MB (HTTP 308), trash, export, shared drives, proxy e backoff+jitter
│   ├── cache.py                        # Cache esparso em blocos de 16 MB, coerência por MD5, pin recursivo offline e LRU com throttle
│   ├── vfs.py                          # FUSE POSIX: streaming, escrita sem duplicação, /Compartilhados comigo, statfs() real e .gitignore
│   ├── watcher.py                      # SyncWorker: delta sync incremental, ThreadPoolExecutor paralelo, mkdir real e hidratação de pins
│   ├── local_sync.py                   # LocalSyncWorker: sincronização seletiva de pastas reais com File Matching MD5 e guarda de raiz ausente
│   ├── ignore.py                       # IgnoreRulesEngine compatível com .gitignore (*, ?, **, /, !negação)
│   ├── conflicts.py                    # ConflictResolver (matriz 4 casos), MD5 streaming, cópia de conflito e move_to_os_trash
│   ├── docs_converter.py               # Exportação offline de Google Docs/Sheets/Slides (LINK / OPEN_DOCUMENT / MS_OFFICE)
│   ├── doc_launcher.py                 # Lançador PWA autônomo para Docs/Sheets/Slides com conta pré-selecionada
│   ├── diagnostic.py                   # Telemetria, sanitização de segredos e abertura de Issues no GitHub
│   ├── version.py                      # Controle de versão semântica
│   └── mcp_server.py                   # Servidor JSON-RPC 2.0 MCP para agentes de IA
├── ui/
│   ├── indicator.py                    # AppIndicator para barra superior (GNOME / KDE)
│   ├── webview_window.py               # Janela principal GTK3, busca dupla, popover multi-conta e SettingsDialog
│   ├── advanced_views.py               # Telas GTK3: LocalSyncPage, IgnoreRulesPage (com testador ao vivo) e ConflictsPage (1 clique)
│   └── install_wizard.py               # Assistente inicial de instalação
├── nautilus/
│   └── google_drive_nautilus.py        # Extensão Python para Nautilus (badges e menu de contexto)
├── updater/
│   ├── omaha_updater.py                # Canal 1: Atualizações oficiais Google Omaha v2/v3
│   └── github_updater.py               # Canal 2: Auto-cura (self-healing) via GitHub main
├── scripts/
│   ├── audit_and_test.py               # Auditoria de sintaxe + 15 testes automatizados + auditoria do ambiente real
│   └── setup_embedded_runtime.sh       # Preparação de runtime embarcado
├── tests/
│   └── simulation_suite.py             # Suíte de simulação de 7 subsistemas
├── assets/                             # Ícones oficiais (256x256 PNG / SVG) e telas HTML
├── packaging/                          # Pacote .deb, systemd --user e .desktop
├── install.sh / uninstall.sh           # Instalador e desinstalador
├── gdrive-cli                          # CLI completa (status, local-sync, ignore, conflicts, activity, audit, update, upgrade, report, mcp)
├── gdrive-doc-launcher                 # Binário do lançador PWA
└── main.py                             # Orquestrador multi-conta, IPC UNIX socket e daemon principal
```

---

## 3. Arquitetura de Dados em Tempo de Execução

### A. Diretório de Configurações e Estado (`~/.config/google-drive-linux/`)
* **`config.json`:** Configurações globais e por conta: `sync_mode`, `mount_point`, `account_mounts`, `account_names`, `cache_dir`, `docs_export_mode`, `missing_file_behavior`, `max_sync_workers`, `conflict_policies`, `proxy_config`, `autostart`.
* **`ignore_rules.txt`:** Regras de exclusão em sintaxe `.gitignore` recarregadas automaticamente por `mtime` (sem reiniciar o daemon).
* **`accounts.json`:** Metadados de autenticação OAuth por conta (tokens e refresh tokens).
* **`metadata.db` (SQLite em modo WAL):**
  * `items`: Catálogo completo (`id`, `account_id`, `parent_id`, `name`, `mime_type`, `size`, `md5_checksum`, `modified_time`, `is_directory`, `is_trashed`, `is_pinned`, `is_shared`, `drive_id`, `is_starred`, `local_cache_path`, `sync_status`). `upsert_items_batch` preserva `is_pinned` via `MAX(items.is_pinned, excluded.is_pinned)`.
  * `operations`: Fila transacional de I/O (`upload_new`, `upload_modify`, `delete`, `rename`, `mkdir`).
  * `cache_blocks`: Índice de blocos de 16 MB para política de descarte LRU.
  * `accounts`: Contas conectadas, avatar, nome e cota real (`storage_total`, `storage_used`).
  * `sync_tokens`: Persistência de `changes_page_token` e `initial_scan_complete` por conta para Delta Sync.
  * `conflicts`: Registro de conflitos pendentes e resolvidos (`conflict_type`, `local_path`, `cloud_name`, `local_md5`, `cloud_md5`, `resolution`, `created_at`, `resolved_at`).
  * `activity_log`: Feed estruturado de atividades limitado às 2.000 entradas mais recentes.
  * `local_sync_rules`: Regras de sincronização seletiva de pastas locais (`account_id`, `local_path`, `cloud_folder_id`, `sync_mode`, `export_mode`, `enabled`, `last_item_count`).
* **`logs/google-drive.log`:** Logs rotativos do daemon.
* **`gdrive_ipc.sock` & `dfd_mcp.sock`:** Sockets UNIX para IPC de janela única e servidor MCP.

### B. Diretório de Cache (`~/.cache/google-drive-linux/` ou HD externo)
* Blocos esparsos `chunk_<n>.bin` (16 MB), arquivos completos `full.bin` e exportações offline de Docs.
* Coerência de cache garantida por [`CacheManager.invalidate_if_changed()`](core/cache.py): quando um delta remoto traz novo `md5Checksum`, os blocos antigos em disco são invalidados automaticamente.

---

## 4. Motor de Sincronização Híbrida, Delta Sync e Sistema de Arquivos Virtual (FUSE VFS)

### A. FUSE VFS ([`core/vfs.py`](core/vfs.py))
* **Leitura Sob Demanda:** Blocos de 16 MB via HTTP `Range` requests, descartados por LRU com throttle de 30s (`enforce_quota_if_needed`) preservando itens `is_pinned = 1`.
* **Escrita Segura sem Duplicação:** `flush()` distingue `upload_new` (ID sintético `local_*`) de `upload_modify` (ID real), preserva o nome original (`db_item["name"]`) e limpa `write_buffer` para que `release()` não duplique uploads.
* **Pasta Virtual `/Compartilhados comigo`:** Exposta dinamicamente na raiz de cada montagem FUSE listando os itens com `is_shared = 1` da conta.
* **Cota Real no Sistema (`statfs()`):** Consulta `storage_total` e `storage_used` do SQLite da conta para que `df -h` e o Nautilus exibam a capacidade real da nuvem (ex: 5 TB).
* **Filtro `.gitignore`:** `create()` e `mkdir()` consultam `IgnoreRulesEngine` para evitar enfileirar arquivos temporários/locks.

### B. Delta Sync Incremental e Pool Paralelo ([`core/watcher.py`](core/watcher.py) & [`core/api.py`](core/api.py))
1. No primeiro acesso sem token, `SyncWorker` obtém `startPageToken` (`get_start_page_token`), executa o scan inicial + `list_shared_with_me` e grava o token em `sync_tokens`.
2. Nos ciclos seguintes, chama apenas `list_changes(page_token)`, aplicando criações, edições, invalidações de cache MD5 e exclusões (`mark_item_trashed`) com custo mínimo de API.
3. A fila de operações executa em paralelo via `ThreadPoolExecutor(max_workers=config["max_sync_workers"])` (padrão: 4).
4. **Resumable Upload em Blocos de 8 MB (`_upload_resumable_chunks`):** Envia arquivos grandes com cabeçalho `Content-Range` e trata HTTP `308 Resume Incomplete`, permitindo uploads robustos sem estourar memória.
5. **Backoff Exponencial com `Retry-After` + Jitter:** Trata automaticamente `403` (rate limit), `429` e `5xx` sem bloquear o VFS.

### C. Sincronização Seletiva de Pastas Locais ([`core/local_sync.py`](core/local_sync.py))
* Executa uma instância dedicada de `LocalSyncWorker` por conta em paralelo ao FUSE.
* **File Matching por MD5:** Antes de fazer upload ou download, compara `compute_file_md5(local_path)` com o `md5_checksum` da nuvem, evitando transferências redundantes.
* **Guarda de Raiz Ausente / HD Desconectado:** Se a pasta local desaparecer ou ficar subitamente vazia quando antes possuía arquivos (`last_item_count > 0`) e `missing_file_behavior == "UNSYNC"`, o worker aborta deleções remotas e protege 100% dos dados na nuvem.

---

## 5. Regras de Exclusão (`.gitignore`), Matriz de Conflitos e Offline Docs

### A. Motor `.gitignore` ([`core/ignore.py`](core/ignore.py))
* Suporta comentários `#`, globs `*` e `?`, recursão `**`, regras somente de diretório com `/` final, caminhos ancorados e negação `!padrão`.
* Recarrega automaticamente quando `~/.config/google-drive-linux/ignore_rules.txt` é editado pela UI, CLI ou editor externo.

### B. Matriz de Conflitos de 4 Casos ([`core/conflicts.py`](core/conflicts.py))
| Cenário | Padrão | Opções disponíveis |
| :--- | :--- | :--- |
| `LOCAL_EDIT_CLOUD_EDIT` | `KEEP_BOTH` | `KEEP_BOTH`, `KEEP_LOCAL`, `KEEP_CLOUD`, `ALWAYS_ASK` |
| `LOCAL_EDIT_CLOUD_DELETE` | `KEEP_LOCAL` | `KEEP_LOCAL`, `KEEP_CLOUD`, `ALWAYS_ASK` |
| `LOCAL_EDIT_UNSYNC` | `KEEP_LOCAL` | `KEEP_LOCAL`, `KEEP_UNSYNC`, `ALWAYS_ASK` |
| `LOCAL_DELETE_CLOUD_EDIT` | `KEEP_CLOUD` | `KEEP_CLOUD`, `KEEP_LOCAL`, `ALWAYS_ASK` |

* Quando um conflito requer atenção (`ALWAYS_ASK`), ele é salvo em `conflicts` e exibido na aba **⚠️ Conflitos** com badge de contagem.

### C. Exportação Offline de Google Docs ([`core/docs_converter.py`](core/docs_converter.py))
* `LINK`: gera atalhos `.gdoc` / `.gsheet` / `.gslides`.
* `OPEN_DOCUMENT`: exporta via Drive API (`files.export`) para `.odt` / `.ods` / `.odp` (LibreOffice).
* `MS_OFFICE`: exporta para `.docx` / `.xlsx` / `.pptx`.

---

## 6. Interface Gráfica Nativa GTK3 e Telas Avançadas

Implementada em [`ui/webview_window.py`](ui/webview_window.py) e [`ui/advanced_views.py`](ui/advanced_views.py):
* **Abas Principais:** Início (cartões de status), Atividade de Sincronização, Notificações e Busca Dupla (Computador + Nuvem).
* **📁 Pastas Locais (`LocalSyncPage`):** CRUD visual completo de regras em `local_sync_rules` com switch de ativação, seletor de pasta no Drive, modo de sincronização e formato de Docs offline.
* **🚫 Regras de Exclusão (`IgnoreRulesPage`):** Editor monoespaçado de `ignore_rules.txt`, botões de presets rápidos e testador em tempo real (`test_ignore_rules_in_memory`) que avalia o texto do editor antes de salvar.
* **⚠️ Central de Conflitos (`ConflictsPage`):** Resolução em 1 clique via `apply_conflict_resolution` (`KEEP_LOCAL`, `KEEP_CLOUD` com envio da cópia local à Lixeira do SO, ou `KEEP_BOTH` com cópia de conflito) + edição direta da matriz de políticas e da proteção de HD externo.
* **⚙️ Preferências (`SettingsDialog`):** Layout com rolagem (`Gtk.ScrolledWindow`) cobrindo contas/nomes no Nautilus, modo Stream/Mirror, diretórios de montagem/cache, formato padrão de Docs offline, comportamento de HD desconectado e threads paralelas.

---

## 7. Lançador de Aplicativos Autônomos (Google Docs, Planilhas e Slides)

O módulo [`core/doc_launcher.py`](core/doc_launcher.py) e o executável `gdrive-doc-launcher` lançam documentos do Google Workspace em modo `--app` (PWA) com `StartupWMClass` dedicado (`google-docs`, `google-sheets`, `google-slides`), ícones oficiais 256x256 e seleção automática da conta ativa (`authuser`/e-mail).

---

## 8. Subsistema de Atualização Dual: Canal 1 (Google Omaha) e Canal 2 (GitHub)

* **Canal 1 ([`updater/omaha_updater.py`](updater/omaha_updater.py)):** Consulta `https://update.googleapis.com/service/update2/json` (AppID `{6BBAE539-2232-434A-A4E5-9A33560C6283}`), valida SHA-256 e extrai novos assets HTML/ícones.
* **Canal 2 ([`updater/github_updater.py`](updater/github_updater.py)):** Monitora `gijogadds/google-drive-linux`, baixa automaticamente novos commits de `main`, aplica em `/opt/google-drive-linux` e reinicia `google-drive.service`.

---

## 9. Captura de Logs, Diagnóstico e Criação Automática de Issues no GitHub

O módulo [`core/diagnostic.py`](core/diagnostic.py) instala `install_global_crash_hook()`, sanitiza automaticamente tokens `Bearer`, `client_secret` e e-mails, salva relatórios em `~/.config/google-drive-linux/crash_reports/` e abre Issues no GitHub (automático em crash ou sob demanda via Engrenagem ⚙️, Bandeja ou `gdrive-cli report -m "..."`).

---

## 10. Servidor Integrado de Model Context Protocol (MCP) para Agentes de IA

O módulo [`core/mcp_server.py`](core/mcp_server.py) implementa JSON-RPC 2.0 via `gdrive-cli mcp`, expondo `search_drive`, `get_sync_status` e `read_drive_file` para clientes MCP (Claude Desktop, Antigravity, etc.).

---

## 11. Ciclo de Vida do Daemon, Multi-Conta e OAuth Rate Limit

1. `main.py` inicializa o IPC UNIX socket, SQLite WAL, `AuthManager` e cria um runtime isolado (`account_runtimes[acc_id]`) contendo `DriveAPI`, `CacheManager`, `SyncWorker`, `LocalSyncWorker` e thread FUSE dedicada para cada conta ativa.
2. No encerramento (`SIGINT`/`SIGTERM`), para `LocalSyncWorker` e `SyncWorker`, desmonta cada ponto FUSE (`fusermount -uz`) e fecha o banco limpo.
3. **Nota sobre Cota OAuth (`HTTP 403 Rate Limit`):** Quando utilizado com credenciais OAuth públicas compartilhadas (ex: projeto comunitário `202264815644`), o Google pode responder temporariamente com `403 Queries per minute`. O cliente trata isso automaticamente com backoff exponencial + jitter sem derrubar o FUSE; caso o usuário deseje cota 100% dedicada, basta criar um app OAuth Desktop gratuito no Google Cloud Console e informar `client_id`/`client_secret` no `config.json`.

---

## 12. Suíte de Testes Automatizados e Auditoria de Qualidade e Segurança

### A. Auditoria Completa (`scripts/audit_and_test.py` / `gdrive-cli audit`)
Executa 3 etapas em sequência:
1. **Auditoria de Sintaxe Python:** Compila todos os `28/28` arquivos `.py` do repositório.
2. **Bateria de 15 Testes Unitários e de Integração:**
   * `TestIgnoreRulesEngine`: globs, negação `!`, regras de diretório `/` e recursão `**`.
   * `TestConflictResolverAndTrash`: matriz de 4 casos, resolução `KEEP_BOTH` em upload e `move_to_os_trash`.
   * `TestDocsConverter`: perfis `LINK`/`OPEN_DOCUMENT`/`MS_OFFICE` e chamada `export_google_doc`.
   * `TestSyncEngineAndVFS`: Delta Sync `changes.list`, `LocalSyncWorker` + guarda de raiz ausente, coerência de cache MD5 + `/Compartilhados comigo`, `mkdir` real + `statfs()` de cota real, parsing RFC3339 e `flush()` sem duplicação.
   * `TestAdvancedUILogic`: testador `.gitignore` em memória e resolução de conflitos em 1 clique (`KEEP_LOCAL`, `KEEP_CLOUD`, `KEEP_BOTH`).
3. **Auditoria do Ambiente Real:** Status de `google-drive.service`, montagens FUSE ativas, `PRAGMA integrity_check` do SQLite e tamanho dos logs.

### B. Diretrizes de Segurança
* Tokens e banco de metadados restritos ao usuário (`~/.config/google-drive-linux/`).
* Proteção contra esgotamento de disco via cache LRU e proteção contra perda de dados via Lixeira do SO + Lixeira do Drive + `missing_file_behavior = "UNSYNC"`.
* Zero uso de `shell=True` com entrada não sanitizada.
