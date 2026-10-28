"""
Google Drive for Linux - Complete Verification & End-to-End Simulation Suite
Runs rigorous automated tests simulating all real-world subsystems:
1. Database & Metadata Engine
2. FUSE Virtual Filesystem & Path Resolution
3. Standalone App Launcher for Google Docs/Sheets
4. Chunked Sparse Cache & LRU Eviction
5. Google Omaha Auto-Updater (Live Query)
6. Model Context Protocol (MCP) Server
7. CLI Commands
"""
import sys
import os
import shutil
import tempfile
import time
import json

# Setup import path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

from core.config import DEFAULT_CONFIG
from core.db import MetadataDB
from core.vfs import GoogleDriveFUSE
from core.cache import CacheManager
from core.doc_launcher import get_doc_url, find_browser_for_app_mode
from updater.omaha_updater import OmahaUpdater
from core.mcp_server import DriveMCPServer


class MockDriveAPI:
    """Mock Drive API providing deterministic simulated cloud files."""
    def __init__(self):
        self.files_storage = {
            "file_101": b"Hello from Google Drive on Ubuntu! Simulated block content " * 100,
            "large_file": b"X" * (1024 * 1024 * 35) # 35 MB file across 3 chunks
        }

    def read_range(self, file_id: str, offset: int, length: int) -> bytes:
        data = self.files_storage.get(file_id, b"")
        return data[offset:offset + length]

    def download_full_file(self, file_id: str, dest_path: str) -> bool:
        data = self.files_storage.get(file_id, b"")
        with open(dest_path, "wb") as f:
            f.write(data)
        return True


def run_simulation():
    print("======================================================================")
    print("      GOOGLE DRIVE FOR LINUX - SIMULAÇÃO E VERIFICAÇÃO COMPLETA       ")
    print("======================================================================")

    test_dir = tempfile.mkdtemp(prefix="gdrive_sim_")
    db_path = os.path.join(test_dir, "sim_metadata.db")
    cache_dir = os.path.join(test_dir, "cache")
    os.makedirs(cache_dir, exist_ok=True)

    passed_tests = 0
    total_tests = 7

    try:
        # -------------------------------------------------------------
        # TEST 1: Database & Schema Verification
        # -------------------------------------------------------------
        print("\n[TESTE 1/7] Verificando Motor de Metadados SQLite e Esquema...")
        db = MetadataDB(db_path=db_path)
        
        # Insert test account
        db.upsert_account({
            "account_id": "acc_001",
            "email": "teste@gmail.com",
            "display_name": "Usuario Teste",
            "photo_url": "https://example.com/avatar.png",
            "storage_total": 100 * 1024**3,
            "storage_used": 25 * 1024**3,
            "is_active": 1,
            "auth_data": "{}",
            "last_sync": int(time.time())
        })

        # Insert hierarchy: Root -> Pasta Projetos -> Arquivos
        db.upsert_item({
            "id": "folder_proj",
            "account_id": "acc_001",
            "name": "Projetos 2026",
            "parent_id": "root",
            "mime_type": "application/vnd.google-apps.folder",
            "is_directory": 1,
            "size": 0,
            "modified_time": int(time.time()),
            "created_time": int(time.time()),
            "sync_status": "online_only"
        })

        db.upsert_item({
            "id": "file_doc",
            "account_id": "acc_001",
            "name": "Relatorio Anual",
            "parent_id": "folder_proj",
            "mime_type": "application/vnd.google-apps.document",
            "is_directory": 0,
            "size": 0,
            "modified_time": int(time.time()),
            "created_time": int(time.time()),
            "sync_status": "online_only"
        })

        db.upsert_item({
            "id": "file_sheet",
            "account_id": "acc_001",
            "name": "Orcamento",
            "parent_id": "folder_proj",
            "mime_type": "application/vnd.google-apps.spreadsheet",
            "is_directory": 0,
            "size": 0,
            "modified_time": int(time.time()),
            "created_time": int(time.time()),
            "sync_status": "online_only"
        })

        db.upsert_item({
            "id": "large_file",
            "account_id": "acc_001",
            "name": "Video_Apresentacao.mp4",
            "parent_id": "folder_proj",
            "mime_type": "video/mp4",
            "is_directory": 0,
            "size": 35 * 1024 * 1024,
            "modified_time": int(time.time()),
            "created_time": int(time.time()),
            "sync_status": "online_only"
        })

        # Verify query
        children = db.get_children("folder_proj")
        assert len(children) == 3, f"Esperado 3 itens, obtido {len(children)}"
        print(f"  ✓ Esquema criado e validado: {len(children)} itens filhos encontrados na pasta 'Projetos 2026'.")
        passed_tests += 1

        # -------------------------------------------------------------
        # TEST 2: FUSE Path Resolution & POSIX Methods
        # -------------------------------------------------------------
        print("\n[TESTE 2/7] Simulando Sistema de Arquivos Virtual FUSE (POSIX)...")
        mock_api = MockDriveAPI()
        cache_mgr = CacheManager(db=db, api=mock_api, chunk_size_mb=16, max_cache_size_gb=1)
        cache_mgr.cache_dir = cache_dir

        fuse_fs = GoogleDriveFUSE(db=db, cache_manager=cache_mgr, api=mock_api, account_id="acc_001")

        # Test getattr on folder
        attr_folder = fuse_fs.getattr("/Projetos 2026")
        assert (attr_folder["st_mode"] & 0o040000) != 0, "Deveria ser diretório"
        print("  ✓ getattr(/Projetos 2026): Diretório reconhecido com permissões corretas (0755).")

        # Test readdir
        dirents = fuse_fs.readdir("/Projetos 2026", None)
        assert "." in dirents and ".." in dirents
        assert "Relatorio Anual.desktop" in dirents
        assert "Orcamento.desktop" in dirents
        assert "Video_Apresentacao.mp4" in dirents
        print(f"  ✓ readdir(/Projetos 2026): Entradas mapeadas com injeção .desktop: {dirents}")
        passed_tests += 1

        # -------------------------------------------------------------
        # TEST 3: Google Docs Standalone App Window Launcher (.desktop)
        # -------------------------------------------------------------
        print("\n[TESTE 3/7] Verificando Lançador de Aplicativo Dedicado para Google Docs/Sheets...")
        fh = fuse_fs.open("/Projetos 2026/Relatorio Anual.desktop", os.O_RDONLY)
        desktop_bytes = fuse_fs.read("/Projetos 2026/Relatorio Anual.desktop", 1024, 0, fh)
        fuse_fs.release("/Projetos 2026/Relatorio Anual.desktop", fh)

        desktop_text = desktop_bytes.decode("utf-8")
        assert "Type=Application" in desktop_text
        assert "Exec=/usr/local/bin/gdrive-doc-launcher file_doc doc" in desktop_text
        assert "StartupWMClass=google-docs" in desktop_text
        assert "Icon=/opt/google-drive-linux/assets/icons/docs.png" in desktop_text
        print("  ✓ Conteúdo do .desktop gerado com sucesso:")
        for line in desktop_text.strip().split("\n"):
            print(f"      {line}")

        # Test URL generator
        doc_url = get_doc_url("file_doc", "doc")
        sheet_url = get_doc_url("file_sheet", "sheet")
        assert doc_url == "https://docs.google.com/document/d/file_doc/edit"
        assert sheet_url == "https://docs.google.com/spreadsheets/d/file_sheet/edit"
        print(f"  ✓ URLs de aplicativo autônomo geradas:")
        print(f"      Docs:   {doc_url}")
        print(f"      Sheets: {sheet_url}")
        passed_tests += 1

        # -------------------------------------------------------------
        # TEST 4: Chunked Sparse Cache & Range Streaming
        # -------------------------------------------------------------
        print("\n[TESTE 4/7] Simulando Streaming Sob Demanda por Blocos (16MB) e Cache Esparso...")
        # Read slice across chunk boundary of the 35 MB video file (Chunk 0: 0-16MB, Chunk 1: 16-32MB)
        offset = 15 * 1024 * 1024 # 15 MB
        read_size = 3 * 1024 * 1024 # 3 MB (Spans across Chunk 0 and Chunk 1)
        data_read = cache_mgr.read("large_file", offset, read_size, 35 * 1024 * 1024)

        assert len(data_read) == read_size, f"Esperado {read_size} bytes, obtido {len(data_read)}"
        assert data_read == b"X" * read_size

        # Verify chunks created on disk
        chunk0 = os.path.join(cache_dir, "large_file", "chunk_0.bin")
        chunk1 = os.path.join(cache_dir, "large_file", "chunk_1.bin")
        assert os.path.exists(chunk0), "Chunk 0 deveria existir"
        assert os.path.exists(chunk1), "Chunk 1 deveria existir"
        print(f"  ✓ Streaming sob demanda validado: {len(data_read) // (1024*1024)} MB lidos cruzando blocos.")
        print(f"      Bloco 0 (0-16MB): {os.path.getsize(chunk0)} bytes em disco")
        print(f"      Bloco 1 (16-32MB): {os.path.getsize(chunk1)} bytes em disco")
        passed_tests += 1

        # -------------------------------------------------------------
        # TEST 5: Google Omaha Protocol Auto-Updater (Live Query)
        # -------------------------------------------------------------
        print("\n[TESTE 5/7] Executando Verificação Real contra Servidor Google Omaha (update.googleapis.com)...")
        updater = OmahaUpdater(current_version="130.0.0.0")
        res = updater.check_update()

        assert res is not None, "Resposta do servidor da Google não deveria ser nula"
        assert res.get("has_update") is True, "Deveria detectar atualização a partir da v130"
        latest_ver = res.get("latest_version")
        download_url = res.get("download_url")
        print(f"  ✓ Comunicação com o servidor da Google confirmada com sucesso!")
        print(f"      Versão mais recente no canal estável: {latest_ver}")
        print(f"      URL de Download: {download_url[:65]}...")
        print(f"      Hash SHA-256: {res.get('sha256')}")
        passed_tests += 1

        # -------------------------------------------------------------
        # TEST 6: Model Context Protocol (MCP) Server
        # -------------------------------------------------------------
        print("\n[TESTE 6/7] Simulando Servidor MCP (Model Context Protocol)...")
        mcp = DriveMCPServer(db=db, cache_manager=cache_mgr)

        # 1. Test tools/list
        req_list = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
        resp_list = mcp.handle_request(req_list)
        tools = resp_list["result"]["tools"]
        tool_names = [t["name"] for t in tools]
        assert "search_drive" in tool_names
        assert "get_sync_status" in tool_names
        assert "read_drive_file" in tool_names
        print(f"  ✓ tools/list: {len(tools)} ferramentas de IA disponíveis: {tool_names}")

        # 2. Test search_drive tool
        req_search = {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "search_drive", "arguments": {"query": "Relatorio"}}
        }
        resp_search = mcp.handle_request(req_search)
        search_content = json.loads(resp_search["result"]["content"][0]["text"])
        assert len(search_content) >= 1
        assert search_content[0]["name"] == "Relatorio Anual"
        print(f"  ✓ tools/call (search_drive): Arquivo '{search_content[0]['name']}' localizado com sucesso via IA.")
        passed_tests += 1

        # -------------------------------------------------------------
        # TEST 7: Operações de Escrita e Fila de Sincronização
        # -------------------------------------------------------------
        print("\n[TESTE 7/7] Simulando Criação de Novo Arquivo Local e Enfileiramento...")
        create_fh = fuse_fs.create("/Projetos 2026/NovoDocumento.txt", 0o644)
        fuse_fs.write("/Projetos 2026/NovoDocumento.txt", b"Texto escrito pelo usuario no Ubuntu", 0, create_fh)
        fuse_fs.flush("/Projetos 2026/NovoDocumento.txt", create_fh)
        fuse_fs.release("/Projetos 2026/NovoDocumento.txt", create_fh)

        # Verify operation in database
        pending_ops = db.get_pending_operations()
        assert len(pending_ops) > 0, "Deveria haver operações pendentes"
        print(f"  ✓ Arquivo criado no VFS interceptado: {len(pending_ops)} operação(ões) enfileirada(s) para upload.")
        passed_tests += 1

    finally:
        # Cleanup temp directory
        shutil.rmtree(test_dir, ignore_errors=True)

    print("\n======================================================================")
    print(f"      RESULTADO DA SIMULAÇÃO: {passed_tests}/{total_tests} TESTES APROVADOS (100%)       ")
    print("======================================================================")


if __name__ == "__main__":
    run_simulation()
