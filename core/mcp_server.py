"""
Google Drive for Linux - Built-in Model Context Protocol (MCP) Server
Replicates Google's dfd_mcp_server.exe (apps/drive/fs/mcp/dfd_mcp_server.cc) for local AI agents.
"""
import sys
import os
import json
from typing import Dict, Any, List
from core.db import MetadataDB


class DriveMCPServer:
    def __init__(self, db: MetadataDB, cache_manager):
        self.db = db
        self.cache = cache_manager

    def handle_request(self, request_json: Dict[str, Any]) -> Dict[str, Any]:
        """JSON-RPC 2.0 / MCP Protocol Dispatcher."""
        method = request_json.get("method")
        params = request_json.get("params", {})
        req_id = request_json.get("id")

        if method == "tools/list":
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "tools": [
                        {
                            "name": "search_drive",
                            "description": "Busca arquivos no Google Drive por nome ou metadados",
                            "inputSchema": {
                                "type": "object",
                                "properties": {"query": {"type": "string"}},
                                "required": ["query"]
                            }
                        },
                        {
                            "name": "get_sync_status",
                            "description": "Retorna o status atual da sincronização e uso de armazenamento",
                            "inputSchema": {"type": "object"}
                        },
                        {
                            "name": "read_drive_file",
                            "description": "Lê o conteúdo textual de um arquivo no Google Drive",
                            "inputSchema": {
                                "type": "object",
                                "properties": {"item_id": {"type": "string"}},
                                "required": ["item_id"]
                            }
                        }
                    ]
                }
            }

        elif method == "tools/call":
            tool_name = params.get("name")
            arguments = params.get("arguments", {})

            if tool_name == "search_drive":
                q = f"%{arguments.get('query', '')}%"
                with self.db._get_connection() as conn:
                    cursor = conn.cursor()
                    cursor.execute("SELECT id, name, mime_type, size, sync_status FROM items WHERE name LIKE ? LIMIT 15", (q,))
                    results = [dict(r) for r in cursor.fetchall()]
                return {"jsonrpc": "2.0", "id": req_id, "result": {"content": [{"type": "text", "text": json.dumps(results, indent=2)}]}}

            elif tool_name == "get_sync_status":
                accounts = self.db.get_accounts()
                pending = len(self.db.get_pending_operations())
                status = {"connected_accounts": len(accounts), "pending_uploads": pending}
                return {"jsonrpc": "2.0", "id": req_id, "result": {"content": [{"type": "text", "text": json.dumps(status, indent=2)}]}}

            elif tool_name == "read_drive_file":
                item_id = arguments.get("item_id")
                item = self.db.get_item(item_id)
                if not item:
                    return {"jsonrpc": "2.0", "id": req_id, "error": {"code": -32602, "message": "Item não encontrado"}}
                data = self.cache.read(item_id, 0, min(item.get("size", 0), 1024 * 1024), item.get("size", 0))
                try:
                    text_content = data.decode("utf-8")
                except UnicodeDecodeError:
                    text_content = f"<Conteúdo binário - {len(data)} bytes>"
                return {"jsonrpc": "2.0", "id": req_id, "result": {"content": [{"type": "text", "text": text_content}]}}

        return {"jsonrpc": "2.0", "id": req_id, "error": {"code": -32601, "message": "Method not found"}}


def run_stdio_mcp_server(db, cache):
    """Run MCP server in stdio mode."""
    server = DriveMCPServer(db, cache)
    for line in sys.stdin:
        if not line.strip():
            continue
        try:
            req = json.loads(line)
            resp = server.handle_request(req)
            sys.stdout.write(json.dumps(resp) + "\n")
            sys.stdout.flush()
        except Exception as e:
            sys.stderr.write(f"MCP Server error: {e}\n")
