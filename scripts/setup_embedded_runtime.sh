#!/usr/bin/env bash
# Script para baixar e preparar o runtime embutido (Chromium Engine com suporte Offline)
set -e

DEST_DIR="${1:-/opt/google-drive-linux/runtime}"
mkdir -p "${DEST_DIR}"

echo "[Google Workspace Runner] Verificando runtime embutido em: ${DEST_DIR}"
if [ -f "${DEST_DIR}/electron" ]; then
    echo "[Google Workspace Runner] Runtime já está presente e pronto!"
    exit 0
fi

TEMP_DIR=$(mktemp -d /tmp/gdrive_runtime_XXXXXX)
trap 'rm -rf "${TEMP_DIR}"' EXIT

echo "[Google Workspace Runner] Baixando motor Chromium autocontido..."
cd "${TEMP_DIR}"
npm init -y >/dev/null 2>&1
npm install electron@latest >/dev/null 2>&1
node node_modules/electron/install.js >/dev/null 2>&1

echo "[Google Workspace Runner] Instalando runtime em ${DEST_DIR}..."
cp -r node_modules/electron/dist/* "${DEST_DIR}/"

if [ -f "${DEST_DIR}/chrome-sandbox" ] && [ "$(id -u)" = "0" ]; then
    chown root:root "${DEST_DIR}/chrome-sandbox" || true
    chmod 4755 "${DEST_DIR}/chrome-sandbox" || true
fi

echo "[Google Workspace Runner] Concluído com sucesso! Motor instalado."
