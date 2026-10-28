# Relatório Técnico de Engenharia Reversa e Arquitetura (REA.md)

Este documento reúne o **estudo completo de engenharia reversa, análise estática e dinâmica de binários, mapeamento de diretórios, inspeção de protocolos de rede e arquitetura de dados** realizado sobre o instalador oficial do Google Drive para Windows e sua pasta instalada, servindo como especificação fundacional para o desenvolvimento do cliente nativo no Ubuntu.

---

## 🔬 Sumário da Investigação
1. [Metodologia e Alvos Analisados](#1-metodologia-e-alvos-analisados)
2. [Engenharia Reversa do Instalador (`GoogleDriveSetup.exe`)](#2-engenharia-reversa-do-instalador-googledrivesetupexe)
3. [Análise dos Binários Instalados (`C:\Program Files\Google\Drive File Stream`)](#3-análise-dos-binários-instalados)
4. [Engenharia Reversa do Driver de Kernel Dokan (`googledrivefs.sys`)](#4-engenharia-reversa-do-driver-de-kernel-dokan)
5. [Descoberta do Servidor MCP Oficial da Google (`dfd_mcp_server.exe`)](#5-descoberta-do-servidor-mcp-oficial-da-google)
6. [Inspeção dos Dados Locais e Bancos SQLite (`%LocalAppData%\Google\DriveFS`)](#6-inspeção-dos-dados-locais-e-bancos-sqlite)
7. [Engenharia Reversa do Protocolo de Atualização Google Omaha](#7-engenharia-reversa-do-protocolo-de-atualização-google-omaha)
8. [Matriz de Tradução Tecnológica: Windows NT para Linux (Ubuntu)](#8-matriz-de-tradução-tecnológica-windows-nt-para-linux-ubuntu)
9. [Conclusão e Decisões de Projeto](#9-conclusão-e-decisões-de-projeto)

---

## 1. Metodologia e Alvos Analisados

A investigação teve como objetivo dissecar o funcionamento interno do cliente de desktop oficial da Google (**Google Drive for Desktop**, antigamente denominado **Drive File Stream**), identificando:
* O mecanismo de entrega e descompactação de binários;
* As chamadas de sistema (syscalls) e drivers de sistema de arquivos;
* A arquitetura de interface gráfica e dependências de runtime;
* A estrutura de bancos de dados locais e fila de sincronização;
* O protocolo de telemetria e auto-atualização em produção.

### Alvos Primários Inspecionados:
1. **Instalador Oficial:** `C:\Users\PlanetJet\Downloads\REA\Google Drive\GoogleDriveSetup.exe` (292.804.760 bytes).
2. **Pasta de Instalação dos Binários:** `C:\Program Files\Google\Drive File Stream\`.
3. **Pasta de Dados e Cache do Usuário:** `C:\Users\PlanetJet\AppData\Local\Google\DriveFS\`.

---

## 2. Engenharia Reversa do Instalador (`GoogleDriveSetup.exe`)

### A. Estrutura de Cabeçalho PE e Seções
* **Formato:** PE32+ (Executable 64-bit para AMD64/x86_64).
* **Machine ID:** `0x8664` (IMAGE_FILE_MACHINE_AMD64).
* **Tamanho Total:** 279,24 MB (292.804.760 bytes).
* **Manifesto XML (`RT_MANIFEST` / ID 24):**
  ```xml
  <requestedExecutionLevel level="requireAdministrator" uiAccess="false"/>
  ```
  *Conclusão da Análise:* O instalador exige obrigatoriamente elevação UAC para instalar um serviço no Service Control Manager (SCM) do Windows e carregar o driver de kernel de sistema de arquivos.

* **Mapeamento de Seções do PE:**
  | Seção | Tamanho Virtual | Tamanho Bruto (Raw) | Raw Pointer | Descrição Técnica |
  | :--- | :--- | :--- | :--- | :--- |
  | `.text` | 4.332.470 B | 4.332.544 B | `1.024` | Código de máquina do instalador C++ |
  | `.rdata` | 7.121.240 B | 7.121.408 B | `4.333.568` | Constantes, RTTI, IAT (Import Address Table) |
  | `.data` | 123.664 B | 85.504 B | `11.454.976` | Dados globais graváveis |
  | `.pdata` | 192.840 B | 193.024 B | `11.540.480` | Tabelas de Exception Handling x64 |
  | `flags_he` | 4.275 B | 4.608 B | `11.750.912` | Metadados do framework de flags da Google (gflags/Abseil) |
  | `protodes` | 73.160 B | 73.216 B | `11.756.544` | Descritores compilados do Google Protocol Buffers |
  | **`.rsrc`** | **280.922.568 B** | **280.922.624 B** | `11.829.760` | **Tabela de recursos (ocupa 96% do executável)** |
  | `.reloc` | 41.896 B | 41.984 B | `292.752.384` | Tabela de relocação de endereços base |
  | *Overlay* | 10.392 B | 10.392 B | `292.794.368` | Assinatura digital Authenticode (PKCS#7 SignedData) |

### B. Descoberta das Cargas Úteis Ocultas em `RT_RCDATA` (ID 10)
Ao analisar a árvore hierárquica da seção `.rsrc`, identificou-se que a Google não utiliza formatos proprietários opacos nem criptografia nos arquivos embutidos. O instalador embute arquivos em formato padrão **ZIP (`PK\x03\x04`)**:

```
.rsrc / RT_RCDATA (Tipo 10)
├── ID 101 (129,02 MB) -> ZIP padrão com 619 arquivos (Binários x64 + WebView2 HTML)
├── ID 104 (127,01 MB) -> ZIP padrão com 619 arquivos (Binários ARM64 para Windows on ARM)
├── ID 102 (4,11 MB)   -> ZIP com 1 arquivo: uninstall.exe
├── ID 103 (5,36 MB)   -> ZIP com 1 arquivo: UpdaterSetup.exe (Google Omaha)
├── ID 231 (142 KB)    -> ZIP com 1 arquivo: googledrivefs31931.sys (Driver Kernel x64)
├── ID 232 (142 KB)    -> ZIP com 1 arquivo: googledrivefs31931.sys (Driver Kernel ARM64)
└── ID 251 (1,50 MB)   -> ZIP com 1 arquivo: webview2_evergreen_bootstrap.exe
```

### C. Mapeamento dos Fontes Internos da Google
A extração de strings e símbolos de depuração da seção `.rdata` revelou a estrutura da árvore de código-fonte C++ interna da Google:
* `apps/drive/cello/common/absl_status_utils.cc` (Núcleo do motor de sincronização "Cello")
* `apps/drive/cello/platform/posix_util.cc` (Camada de abstração POSIX já existente internamente!)
* `apps/drive/cello/platform/windows/win_util.cc` (Abstração Windows)
* `apps/drive/fs/distribution/win/installer/driver_install.cc` (Instalação do driver Dokan)
* `apps/drive/fs/distribution/win/installer/install.cc` (Lógica de descompactação e setup)
* `apps/drive/fs/distribution/win/installer/omaha.cc` (Integração com Google Update)
* `apps/drive/fs/distribution/win/installer/webview2.cc` (Verificação do motor Edge)
* `apps/drive/fs/ui/zip_util.cc` (Descompactador de recursos RCDATA)

---

## 3. Análise dos Binários Instalados

Na pasta `C:\Program Files\Google\Drive File Stream`:

### A. O Script de Inicialização (`launch.bat`)
```bat
set COMMAND="reg.exe query HKLM\Software\Microsoft\Windows\CurrentVersion\Uninstall\{6BBAE539-2232-434A-A4E5-9A33560C6283} /v InstallLocation"
```
*Identificação:* O GUID oficial do Google Drive para Desktop no registro do Windows é:
`{6BBAE539-2232-434A-A4E5-9A33560C6283}`.
O script busca a versão numérica mais recente instalada em subpastas (ex: `132.0.0.0`, `131.0.2.0`) e executa `GoogleDriveFS.exe`.

### B. O Executável Central (`GoogleDriveFS.exe`)
* **Tamanho:** 106.216.088 bytes (~101 MB).
* **Dependências de Importação (27 DLLs):**
  * `KERNEL32`, `USER32`, `ADVAPI32`, `SHELL32`, `SHLWAPI` (Win32 Base)
  * `CRYPT32`, `Secur32`, `bcrypt` (Criptografia e Windows Credential Manager / DPAPI)
  * `WINHTTP`, `WS2_32`, `IPHLPAPI` (Comunicação de rede e DNS)
  * `WebView2Loader.dll` (Runtime Microsoft Edge Chromium para renderizar o frontend)
  * `dokancc31931.dll` (**Ponto Crítico: Bridge de I/O de Sistema de Arquivos**)

### C. Integração com o Google Chrome (`com.google.drive.nativeproxy.json`)
O cliente registra um Native Messaging Host no Chrome apontando para `switchblade_host.exe`:
```json
{
  "name": "com.google.drive.nativeproxy",
  "description": "Google Drive Client Native Messaging Proxy",
  "path": "C:\\Program Files\\Google\\Drive File Stream\\132.0.0.0\\switchblade_host.exe",
  "type": "stdio",
  "allowed_origins": [
    "chrome-extension://lmjegmlicamnimmfhcmpkclmigmmcbeh/"
  ]
}
```
*Extensão Alvo:* `lmjegmlicamnimmfhcmpkclmigmmcbeh` é a extensão oficial do **Google Docs Offline**. O Google Drive Desktop comunica-se via `stdio` para permitir salvamento local de documentos quando o computador está sem internet.

---

## 4. Engenharia Reversa do Driver de Kernel Dokan (`googledrivefs.sys`)

### O Mecanismo de Virtual Drive no Windows
O Google Drive não cria pastas físicas normais; ele apresenta uma **unidade de disco virtual (ex: `G:\`)** sob demanda.

Inspecionamos os símbolos exportados pela DLL `dokancc31931.dll`:
```
Export Directory RVA: 0x7df60 (51 funções exportadas)
- dokan::FileSystem::Mount(std::wstring, StartupOptions)
- dokan::FileSystem::Unmount()
- dokan::FileSystem::DispatchIo()
- dokan::FileSystem::ReceiveIo()
- dokan::Device::ControlAsync(...)
- dokan::FileCallbacks
- dokan::VolumeCallbacks
```

#### Fluxo Operacional de uma Leitura:
1. Um programa (ex: Word, VLC) tenta abrir `G:\Meu Drive\Arquivo.pdf`.
2. O subsistema de I/O do Windows envia um pacote IRP (`IRP_MJ_CREATE` / `IRP_MJ_READ`) para o driver de kernel `googledrivefs31931.sys`.
3. O driver `googledrivefs31931.sys` repassa a solicitação para espaço de usuário via IOCTL para a DLL `dokancc31931.dll`.
4. `dokancc31931.dll` dispara a callback C++ dentro de `GoogleDriveFS.exe`.
5. `GoogleDriveFS.exe` verifica se o arquivo está em cache local (`%LocalAppData%\Google\DriveFS`).
   * Se já estiver em cache, lê do disco físico.
   * Se for apenas online, faz um HTTP Range Request para a Google Drive API em blocos de 16MB, salva o bloco e devolve os bytes ao driver.

> ⚠️ **Por que isso inviabiliza o Wine no Linux?**
> O Wine / Proton traduz chamadas Win32 de espaço de usuário para POSIX, mas **não emula drivers de sistema de arquivos NT em modo de kernel (`.sys`)**. Por isso, o instalador do Windows falha silenciosamente ou quebra ao tentar iniciar o driver Dokan no Linux.

---

## 5. Descoberta do Servidor MCP Oficial da Google (`dfd_mcp_server.exe`)

Durante a análise dos executáveis da pasta `132.0.0.0`, foi encontrado o binário `dfd_mcp_server.exe` (23.058.584 bytes). Executando o binário com `--help`:
```
Flags from apps/drive/fs/mcp/dfd_mcp_server.cc:
  --socket_path (Path to the IPC socket. If empty, uses default.); default: "";
```

**Descoberta:** A Google embutiu um servidor nativo do **Model Context Protocol (MCP)** diretamente dentro do cliente do Google Drive Desktop. Esse servidor escuta em um socket IPC local para permitir que agentes de IA consultem metadados e arquivos do Drive sob demanda.

*Decisão de Projeto:* Implementamos esse mesmo servidor em Python no nosso cliente Linux (`core/mcp_server.py`), permitindo que agentes locais (Claude Desktop, Antigravity, Ollama) interajam diretamente com o Google Drive no Ubuntu via `gdrive-cli mcp`.

---

## 6. Inspeção dos Dados Locais e Bancos SQLite (`%LocalAppData%\Google\DriveFS`)

Ao inspecionar a pasta de perfil do usuário:
* Diretório: `%LocalAppData%\Google\DriveFS\106843294024698086633\` (ID único da Conta Google).
* Banco de Dados Central: `metadata_sqlite_db` (formato SQLite 3, modo Write-Ahead Logging `WAL`).

### Tabelas Mapeadas:
```sql
SELECT name FROM sqlite_master WHERE type='table';
-- Tabelas identificadas:
['stable_parents', 'operations', 'properties', 'item_properties', 
 'deleted_items', 'stable_ids', 'local_stable_ids', 'workspaces', 
 'item_workspaces', 'shortcut_details', 'label_taxonomies', 
 'activity_items', 'items', 'folder_features', 'queries', 'teamdrive_query']
```

#### Principais Tabelas:
1. **`items`:** Contém todos os arquivos e pastas da árvore do Drive (IDs, nomes, tamanhos, hashes MD5, datas de modificação, permissões e status de cache).
2. **`operations`:** A fila transacional de upload/download do cliente. Toda alteração feita localmente pelo usuário gera uma linha com status `pending`, processada assincronamente pelo worker.

*Decisão de Projeto:* O nosso módulo `core/db.py` foi desenhado espelhando exatamente a mesma estrutura relacional em SQLite com suporte a WAL mode.

---

## 7. Engenharia Reversa do Protocolo de Atualização Google Omaha

O Google Drive não usa atualizações manuais; ele utiliza o protocolo **Google Omaha (Google Update v2/v3)**.

### A. Endpoint de Produção e Estrutura de Requisição
* **URL:** `https://update.googleapis.com/service/update2/json`
* **Protocolo:** JSON com prefixo de proteção anti-XSSI `)]}'\n`.
* **Corpo da Requisição (Omaha v2 JSON):**
  ```json
  {
    "request": {
      "@os": "win",
      "@updater": "Omaha",
      "acceptformat": "crx3,file",
      "os": {
        "platform": "win",
        "version": "10.0.22631",
        "arch": "x64"
      },
      "app": [
        {
          "appid": "{6BBAE539-2232-434A-A4E5-9A33560C6283}",
          "version": "130.0.0.0",
          "updatecheck": {}
        }
      ]
    }
  }
  ```

### B. Resposta Real de Produção Retornada pelo Servidor da Google:
```json
{
  "gupdate": {
    "server": "prod",
    "protocol": "2.0",
    "app": [
      {
        "appid": "{6BBAE539-2232-434A-A4E5-9A33560C6283}",
        "cohort": "1:fj3/nix:nj9@0.05,nj3@0.1",
        "cohortname": "Stable-25-Percent",
        "status": "ok",
        "updatecheck": {
          "status": "ok",
          "codebase": "https://dl.google.com/release2/drive-file-stream/acywfex4pxyg7mdusp2clyezrxqq_133.0.2.0/-6bbae539-2232-434a-a4e5-9a33560c6283-_133.0.2.0_all_adljrx4hqohl565x7vvjo2sficlq.crx3",
          "hash_sha256": "1a0bdeee66da12bfe33a2ef6d56193475bb497f47afbce1354956ad642cdd96e",
          "size": 270202906
        }
      }
    ]
  }
}
```

### C. Como o Atualizador Foi Desenvolvido para o Ubuntu:
O nosso módulo `updater/omaha_updater.py` conecta-se a esse mesmo endpoint oficial da Google:
1. Compara a versão local com a versão mais recente em rollout (`133.0.2.0`).
2. Valida o hash SHA-256 oficial informado pela Google.
3. Extrai as telas HTML/CSS e os ícones mais recentes do arquivo `.crx3` (que internamente é um pacote ZIP assinado) e aplica as novas telas automaticamente na pasta de assets.

---

## 8. Matriz de Tradução Tecnológica: Windows NT para Linux (Ubuntu)

Com todas as peças mapeadas, construímos a equivalência arquitetural para o Ubuntu:

```
[ ARQUITETURA NO WINDOWS ]                      [ ARQUITETURA IMPLEMENTADA NO UBUNTU ]
==========================                      ======================================
GoogleDriveFS.exe (C++)                          -> Python 3 Daemon Assíncrono (main.py)
googledrivefs.sys (Kernel Driver Dokan)          -> FUSE (libfuse3 / fusepy em espaço de usuário)
dokancc31931.dll (IOCTL Bridge)                  -> VFS Engine nativa POSIX (core/vfs.py)
Edge WebView2 + HTML Dialogs                     -> WebKit2GTK + Diálogos Oficiais (assets/html)
Atalhos .gdoc abrindo abas de browser            -> Standalone PWA Window (--app) com ícones 256x256
metadata_sqlite_db (SQLite WAL)                  -> SQLite WAL (~/.config/google-drive-linux/metadata.db)
dfd_mcp_server.exe (apps/drive/fs/mcp/)          -> Servidor MCP Local JSON-RPC 2.0 (core/mcp_server.py)
Windows Explorer Overlay (drivefsext.dll)        -> Extensão Python do Nautilus (badges de status)
Omaha Updater (update.googleapis.com)            -> Monitor Omaha v2/v3 Integrado (updater/omaha_updater.py)
Crashpad Handler (telemetria de crashes)         -> DiagnosticCollector & GitHub Issues API (core/diagnostic.py)
Windows Service Control Manager                  -> systemd --user (google-drive.service)
```

---

## 9. Conclusão e Decisões de Projeto

A engenharia reversa demonstrou que a Google nunca lançou um cliente oficial para Linux não por limitações técnicas de protocolo (já que a Google Drive API e o protocolo Omaha são agnósticos de plataforma), mas pela dependência histórica de drivers proprietários de kernel no Windows (Dokan) e no macOS (FileProvider/macFUSE).

Ao substituir o driver de kernel Dokan pelo subsistema **FUSE (`libfuse3`)**, o cliente desenvolvido para Ubuntu oferece:
1. **Maior Estabilidade:** Impossibilidade de travamentos do sistema operacional (Kernel Panic / BSOD), pois roda 100% em modo de usuário.
2. **Streaming Transparente:** Arquivos ocupam 0 bytes e são carregados sob demanda em blocos esparsos de 16MB.
3. **Experiência Nativa de Programa:** Google Docs e Planilhas abrem em janelas dedicadas sem abas de navegador.
4. **Auto-Suficiência:** Verificação periódica do status de lançamento oficial na CDN da Google e abertura automática de Issues para rastreabilidade de falhas.
