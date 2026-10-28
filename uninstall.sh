#!/usr/bin/env bash
# ==============================================================================
# Google Drive para Linux (Ubuntu) - Script de Desinstalação
# ==============================================================================

set -e

if [ "$EUID" -ne 0 ]; then
    echo "[Erro] Execute com sudo: sudo ./uninstall.sh"
    exit 1
fi

TARGET_USER="${SUDO_USER:-$USER}"
USER_HOME=$(eval echo "~$TARGET_USER")

echo "[1/4] Parando serviço systemd..."
su - "${TARGET_USER}" -c "systemctl --user stop google-drive.service || true"
su - "${TARGET_USER}" -c "systemctl --user disable google-drive.service || true"
rm -f "${USER_HOME}/.config/systemd/user/google-drive.service"

echo "[2/4] Removendo arquivos de instalação em /opt..."
rm -rf /opt/google-drive-linux
rm -f /usr/local/bin/gdrive-cli
rm -f /usr/local/bin/gdrive-doc-launcher

echo "[3/4] Removendo extensão do Nautilus e atalho do menu..."
rm -f /usr/share/nautilus-python/extensions/google_drive_nautilus.py
rm -f /usr/share/applications/google-drive.desktop

echo "[4/4] Reiniciando Nautilus..."
su - "${TARGET_USER}" -c "nautilus -q || true"

echo "Google Drive para Linux desinstalado com sucesso."
