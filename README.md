# Google Drive para Linux (Ubuntu) 🚀

Cliente nativo e de alta performance do **Google Drive para Ubuntu e distribuições Linux**, desenvolvido através de engenharia reversa do cliente oficial do Windows (`Google Drive for Desktop / Drive File Stream`). 

Oferece **paridade total de recursos** em relação ao cliente do Windows, incluindo **streaming de arquivos sob demanda via FUSE**, **janelas de aplicativo dedicadas para Google Docs/Sheets/Slides**, **servidor MCP integrado para IA local**, **sistema de verificação de atualização oficial da Google (Protocolo Omaha)** e **sistema automatizado de diagnóstico e abertura de Issues no GitHub**.

> 📚 **Documentação Técnica Detalhada:**
> * 📘 [**DOCUMENTATION.md**](DOCUMENTATION.md) — Manual técnico completo de arquitetura, subsistemas de sincronização, VFS, telemetria de logs e auditoria.
> * 🔬 [**REA.md**](REA.md) — Estudo aprofundado de engenharia reversa dos binários do Windows (`GoogleDriveSetup.exe` e Dokan Driver).

---

## 📑 Sumário
1. [Engenharia Reversa do Google Drive para Windows](#1-engenharia-reversa-do-google-drive-para-windows)
2. [Arquitetura da Solução para Ubuntu](#2-arquitetura-da-solução-para-ubuntu)
3. [Google Docs, Planilhas e Slides como Aplicativos Nativos](#3-google-docs-planilhas-e-slides-como-aplicativos-nativos)
4. [Auto-Atualização Oficial via Protocolo Google Omaha](#4-auto-atualização-oficial-via-protocolo-google-omaha)
5. [Sistema de Coleta de Logs e Abertura Automática de Issues](#5-sistema-de-coleta-de-logs-e-abertura-automática-de-issues)
6. [Servidor MCP Embutido para Agentes de IA](#6-servidor-mcp-embutido-para-agentes-de-ia)
7. [Resultado da Simulação e Verificação de Subsistemas](#7-resultado-da-simulação-e-verificação-de-subsistemas)
8. [Estrutura do Repositório](#8-estrutura-do-repositório)
9. [Guia de Instalação no Ubuntu](#9-guia-de-instalação-no-ubuntu)
10. [Como Usar (CLI e Interface)](#10-como-usar-cli-e-interface)
11. [Guia de Desinstalação](#11-guia-de-desinstalação)

---

## 1. Engenharia Reversa do Google Drive para Windows

A partir da análise do instalador `GoogleDriveSetup.exe` (versão 132.0.0.0, ~292 MB) e da pasta de binários instalados em `C:\Program Files\Google\Drive File Stream`:

### A. Anatomia do Instalador (`GoogleDriveSetup.exe`)
* **Formato:** Binário PE32+ (x64) compilado em C++ interno da Google (Abseil, Protobuf, gflags).
* **Armazenamento de Cargas Úteis (Payload):** O instalador armazena os arquivos da aplicação na seção `.rsrc` sob o recurso `RT_RCDATA` (ID 10) utilizando arquivos ZIP padrão:
  * **ID 101 (129 MB):** Binários completos x86_64 (`GoogleDriveFS.exe`, DLLs, assets HTML).
  * **ID 104 (127 MB):** Binários completos para arquitetura ARM64.
  * **ID 102 (4.1 MB):** Desinstalador oficial `uninstall.exe`.
  * **ID 103 (5.3 MB):** Instalador do Google Update (`UpdaterSetup.exe` / Omaha).
  * **ID 231 / 232 (142 KB):** Drivers de sistema de arquivos `googledrivefs31931.sys` (x64 e ARM64).
  * **ID 251 (1.5 MB):** Bootstrapper do runtime Microsoft Edge WebView2.

### B. Por Que o Binário do Windows Não Roda no Linux (Mesmo com Wine)?
* **O Bloqueio do Driver de Kernel (.sys):** O Google Drive no Windows utiliza o framework **Dokan (Dokany)**. O arquivo `googledrivefs*.sys` é um driver de filtro do kernel do Windows NT que intercepta as chamadas de I/O na unidade virtual (ex: `G:\`). Como o Wine não emula drivers de kernel `.sys`, o cliente falha imediatamente ao tentar inicializar o disco virtual.
* **Interface WebView2:** Os diálogos e menus do Google Drive oficial são escritos em HTML/CSS/JS (pasta `html/`) e renderizados pelo motor Microsoft Edge WebView2 (`WebView2Loader.dll`), que possui instabilidade sob camadas de compatibilidade.

---

## 2. Arquitetura da Solução para Ubuntu

Para alcançar paridade e estabilidade superiores no Ubuntu, a solução substitui cada componente específico do Windows por seu equivalente nativo em Linux:

| Recurso | Windows (Drive File Stream) | Ubuntu (Nossa Solução) |
| :--- | :--- | :--- |
| **Sistema de Arquivos Virtual** | Driver de Kernel Dokan (`googledrivefs.sys`) | **FUSE (`libfuse3` / modo de usuário)** |
| **Camada de Streaming** | `dokancc31931.dll` | **Motor de Sparse Chunks (16MB)** |
| **Autenticação** | DPAPI / Credential Manager | **OAuth 2.0 PKCE** via Navegador Padrão |
| **Bandeja do Sistema** | Windows System Tray | **AyatanaAppIndicator** (GNOME e KDE) |
| **Gerenciador de Arquivos** | Shell Extension (`drivefsext.dll`) | **Extensão Python para Nautilus** com badges |
| **Banco de Metadados** | `metadata_sqlite_db` (SQLite) | **SQLite local** (`metadata.db`) em WAL mode |
| **Diagnóstico de Falhas** | Crashpad / `diagnostic_tool.exe` | **Módulo de Diagnóstico e Issues do GitHub** |

---

## 3. Google Docs, Planilhas e Slides como Aplicativos Nativos

No Windows, atalhos do Google Docs frequentemente abrem em uma aba normal do navegador com barras de ferramentas. No Ubuntu, nossa solução eleva a experiência ao transformar os documentos em **verdadeiros aplicativos de desktop autônomos**:

* **Janela Dedicada (Modo SSB / PWA):** Ao clicar em um arquivo `.desktop` gerado pelo FUSE, o script `gdrive-doc-launcher` executa o navegador Chromium/Chrome/Brave em modo `--app="https://docs.google.com/..."`.
* **Sem Distrações:** A janela abre sem barra de endereços, sem favoritos e sem abas de navegação.
* **Ícones Oficiais no Dock do Ubuntu:** Foram extraídos e convertidos ícones oficiais em alta resolução (256x256 PNG) para cada serviço:
  * 📄 **Google Docs:** `docs.png` com classe de janela `google-docs`.
  * 📊 **Google Sheets:** `sheets.png` com classe de janela `google-sheets`.
  * 📑 **Google Slides:** `slides.png` com classe de janela `google-slides`.
* **Funcionalidade Integral:** Suporte a atalhos de teclado (`Ctrl+Z`, `Ctrl+S`, `Ctrl+P`), edição colaborativa em tempo real, comentários e histórico de revisões.

---

## 4. Auto-Atualização Oficial via Protocolo Google Omaha

O cliente mantém-se atualizado conectando-se diretamente ao servidor de produção da Google que gerencia as atualizações mundiais do Google Drive:

* **Endpoint de Produção:** `https://update.googleapis.com/service/update2/json`
* **AppID Oficial do Google Drive:** `{6BBAE539-2232-434A-A4E5-9A33560C6283}`
* **Fluxo:**
  1. O módulo `updater/omaha_updater.py` consulta o endpoint enviando a versão atual instalada.
  2. O servidor da Google retorna a versão estável mais recente (ex: `133.0.2.0`), a URL direta do CDN oficial (`dl.google.com`) e o hash criptográfico SHA-256.
  3. O cliente valida o hash, extrai as novas telas de interface HTML/CSS e notifica o usuário via bandeja do sistema para aplicação rápida.

---

## 5. Sistema de Coleta de Logs e Abertura Automática de Issues

Para garantir suporte contínuo e resolução imediata de problemas, o projeto conta com um subsistema completo de telemetria e diagnóstico inspirado no `Crashpad` da Google:

1. **Captura Global de Exceções (`install_global_crash_hook`):**
   * Qualquer falha não tratada em threads de sincronização ou no VFS é imediatamente capturada.
2. **Sanitização de Dados Privados:**
   * Tokens OAuth, chaves de API, senhas e identificadores pessoais são mascarados automaticamente (`[REDACTED_TOKEN]`).
3. **Notificação Nativa no Desktop:**
   * Utiliza `notify-send` na área de trabalho do Ubuntu informando que um diagnóstico foi gerado.
4. **Criação Automática de Issue no GitHub:**
   * Gera uma Issue diretamente no repositório com título descritivo, informações completas do sistema (Distribuição, Kernel, Python, Interface Gráfica), estado das filas e stack trace formatado.
   * Pode ser acionado manualmente a qualquer momento pelo comando:
     ```bash
     gdrive-cli report
     ```
   * Ou através do menu da bandeja: **"🐛 Relatar Problema / Diagnóstico..."**.

---

## 6. Servidor MCP Embutido para Agentes de IA

Durante a análise dos binários oficiais, identificamos o arquivo `dfd_mcp_server.exe` (`apps/drive/fs/mcp/dfd_mcp_server.cc`). Implementamos esse mesmo padrão no Linux através do protocolo **Model Context Protocol (MCP)**:

* **Comando:** `gdrive-cli mcp`
* **Ferramentas Expostas para IA:**
  * `search_drive(query)`: Busca arquivos no Google Drive por nome ou metadados.
  * `get_sync_status()`: Retorna o status de sincronização e uso de cota.
  * `read_drive_file(item_id)`: Lê o conteúdo de arquivos locais/remotos para contexto de modelos LLM.
* **Compatibilidade:** Funciona nativamente com Claude Desktop, Antigravity e agentes baseados em MCP.

---

## 7. Resultado da Simulação e Verificação de Subsistemas

Todos os componentes foram submetidos a uma suíte de testes de integração e simulação (`tests/simulation_suite.py`):

| Subsistema Testado | Cenário Simulado | Resultado |
| :--- | :--- | :--- |
| **Banco de Metadados** | Ingestão de contas, hierarquia de diretórios e fila de operações | ✅ Aprovado (100%) |
| **FUSE VFS (POSIX)** | Permissões de pasta (0755), `readdir`, injeção `.desktop` | ✅ Aprovado (100%) |
| **Lançador PWA** | Geração dinâmica de atalhos e classes `StartupWMClass` | ✅ Aprovado (100%) |
| **Cache em Blocos** | Streaming sob demanda de arquivo de 35MB cruzando blocos de 16MB | ✅ Aprovado (100%) |
| **Google Omaha** | Consulta real HTTPS ao servidor da Google com validação de versão | ✅ Aprovado (100%) |
| **Servidor MCP** | Despacho JSON-RPC 2.0 e busca de arquivos via IA | ✅ Aprovado (100%) |
| **Fila de Upload** | Criação de arquivo local interceptada e enfileirada no banco | ✅ Aprovado (100%) |

---

## 8. Estrutura do Repositório

```
google-drive-linux/
├── core/
│   ├── config.py             # Configurações globais e diretórios
│   ├── db.py                 # Banco SQLite de metadados e fila de operações
│   ├── auth.py               # Autenticação OAuth 2.0 PKCE com login no navegador
│   ├── api.py                # Cliente da API v3 do Google Drive
│   ├── cache.py              # Gerenciador de cache esparso em blocos e algoritmo LRU
│   ├── vfs.py                # Sistema de arquivos virtual em modo de usuário (FUSE)
│   ├── watcher.py            # Fila de sincronização e detecção de alterações
│   ├── doc_launcher.py       # Lançador de janelas dedicadas de aplicativo para Docs/Sheets
│   ├── diagnostic.py         # Coletor de diagnóstico e integrador de Issues no GitHub
│   └── mcp_server.py         # Servidor MCP para agentes de IA locais
├── ui/
│   ├── indicator.py          # Indicador da bandeja do GNOME/KDE (AppIndicator)
│   └── webview_window.py     # Janela de preferências e gerenciador de contas
├── nautilus/
│   └── google_drive_nautilus.py # Extensão de badges e menus para o Nautilus
├── updater/
│   └── omaha_updater.py      # Monitor e extrator de atualizações oficiais da Google
├── assets/
│   ├── icons/                # Ícones oficiais de alta resolução (Docs, Sheets, Slides, Drive)
│   └── html/                 # Telas de interface oficiais extraídas do instalador
├── packaging/
│   ├── debian/               # Arquivos de controle do pacote Debian (.deb)
│   ├── systemd/              # Serviço systemd --user para autostart silencioso
│   └── desktop/              # Atalhos e integrações de menu .desktop
├── tests/
│   └── simulation_suite.py   # Suíte completa de verificação automatizada
├── install.sh                # Instalador automatizado de 1 clique para Ubuntu
├── uninstall.sh              # Desinstalador limpo
├── gdrive-cli                # Utilitário de linha de comando para terminal
├── gdrive-doc-launcher       # Executável do lançador de documentos autônomos
└── main.py                   # Ponto de entrada do serviço/daemon
```

---

## 9. Guia de Instalação no Ubuntu

Compatível com **Ubuntu 20.04 LTS, 22.04 LTS, 24.04 LTS** e derivados (Linux Mint, Pop!_OS, Debian).

### Passo 1: Clonar o Repositório
```bash
git clone https://github.com/gijogadds/google-drive-linux.git
cd google-drive-linux
```

### Passo 2: Executar o Instalador Automatizado
```bash
sudo chmod +x install.sh
sudo ./install.sh
```

O script executará automaticamente:
1. Instalação das dependências necessárias via APT (`python3-gi`, `fuse3`, `python3-fusepy`, `python3-nautilus`, `gir1.2-ayatanaappindicator3-0.1`, `xclip`).
2. Cópia dos arquivos da aplicação para `/opt/google-drive-linux/`.
3. Registro dos binários `gdrive-cli` e `gdrive-doc-launcher` no `/usr/local/bin/`.
4. Instalação da extensão de badges para o gerenciador de arquivos (Nautilus).
5. Configuração do serviço em segundo plano no `systemd` do usuário.

---

## 10. Como Usar (CLI e Interface)

### 1. Conectar sua Conta Google
No terminal ou através do menu de aplicativos:
```bash
gdrive-cli login
```
*Seu navegador padrão será aberto com a tela oficial de login da Google para concessão de acesso.*

### 2. Iniciar a Sincronização em Segundo Plano
```bash
systemctl --user enable --now google-drive.service
```
*O Google Drive será montado em `~/GoogleDrive` e o ícone de sincronização aparecerá na barra superior do Ubuntu.*

### 3. Comandos Úteis do Terminal (`gdrive-cli`)
* **Verificar status da conta e armazenamento:**
  ```bash
  gdrive-cli status
  ```
* **Verificar atualizações oficiais da Google:**
  ```bash
  gdrive-cli update
  ```
* **Gerar relatório de diagnóstico e abrir Issue no GitHub:**
  ```bash
  gdrive-cli report
  ```
* **Iniciar servidor MCP para assistentes de IA:**
  ```bash
  gdrive-cli mcp
  ```

---

## 11. Guia de Desinstalação

Caso queira remover completamente a aplicação do sistema:

```bash
cd google-drive-linux
sudo chmod +x uninstall.sh
sudo ./uninstall.sh
```

O desinstalador remove:
* O serviço do `systemd` e para as instâncias ativas.
* Todos os arquivos em `/opt/google-drive-linux` e links em `/usr/local/bin/`.
* A extensão do Nautilus e atalhos de menu.
* Reinicia o Nautilus para remover os emblemas dos arquivos.

---

## 📄 Licença
Distribuído sob a licença MIT. Consulte `LICENSE` para mais informações.
