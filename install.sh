#!/usr/bin/env bash
# ==============================================================================
# Google Drive para Linux (Ubuntu) - Script de Instalação Automática
# Compatível com Ubuntu 20.04 LTS, 22.04 LTS, 24.04 LTS e derivados (Mint, Pop!_OS)
# ==============================================================================

set -e

GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

echo -e "${BLUE}==================================================================${NC}"
echo -e "${GREEN}       Instalador do Google Drive para Linux (Ubuntu)           ${NC}"
echo -e "${BLUE}==================================================================${NC}"

# 1. Verificar privilégios de superusuário
if [ "$EUID" -ne 0 ]; then
    echo -e "${RED}[Erro] Este script precisa ser executado com permissões de administrador (sudo).${NC}"
    echo "Uso: sudo ./install.sh"
    exit 1
fi

TARGET_USER="${SUDO_USER:-$USER}"
USER_HOME=$(eval echo "~$TARGET_USER")
INSTALL_DIR="/opt/google-drive-linux"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo -e "${BLUE}[1/6] Instalando dependências do sistema via APT (Ubuntu 20.04 / 22.04 / 24.04 / 26.04+)...${NC}"
apt-get update -qq

# Instalação dos pacotes essenciais de sistema
apt-get install -y \
    python3 \
    python3-pip \
    python3-gi \
    python3-gi-cairo \
    gir1.2-gtk-3.0 \
    gir1.2-ayatanaappindicator3-0.1 \
    fuse3 \
    python3-nautilus \
    libsecret-1-0 \
    xclip || true

# WebKit2 GTK (4.1 para Ubuntu 22.04 / 24.04 / 26.04+, ou 4.0 para Ubuntu 20.04)
apt-get install -y gir1.2-webkit2-4.1 || apt-get install -y gir1.2-webkit2-4.0 || true

# Garantir bibliotecas compartilhadas do FUSE (libfuse2t64 / libfuse2 / libfuse3) para o fusepy
apt-get install -y libfuse2t64 || apt-get install -y libfuse2 || true

# Suporte ao FUSE Python (compatível com PEP 668 no Ubuntu 24.04 e 26.04)
if ! python3 -c "import fuse" 2>/dev/null; then
    apt-get install -y python3-fusepy || pip3 install --break-system-packages fusepy || pip3 install fusepy || true
fi

# Garantir permissões do FUSE e suporte a montagem em HDs externos (/run/media/...) no AppArmor (Ubuntu 24.04/26.04+)
modprobe fuse || true
if [ -f /etc/fuse.conf ]; then
    sed -i 's/#user_allow_other/user_allow_other/' /etc/fuse.conf || true
fi
if [ -f /etc/apparmor.d/fusermount3 ]; then
    mkdir -p /etc/apparmor.d/local
    cat << 'EOF' > /etc/apparmor.d/local/fusermount3
mount fstype=@{fuse_types} options=(nosuid,nodev) options in (ro,rw,noatime,dirsync,nodiratime,noexec,sync) -> /run/media/**/,
umount /run/media/**/,
EOF
    apparmor_parser -r /etc/apparmor.d/fusermount3 2>/dev/null || true
fi

echo -e "${BLUE}[2/6] Copiando arquivos do aplicativo para ${INSTALL_DIR}...${NC}"
mkdir -p "${INSTALL_DIR}"
cp -r "${SCRIPT_DIR}/core" "${INSTALL_DIR}/"
cp -r "${SCRIPT_DIR}/ui" "${INSTALL_DIR}/"
cp -r "${SCRIPT_DIR}/nautilus" "${INSTALL_DIR}/"
cp -r "${SCRIPT_DIR}/updater" "${INSTALL_DIR}/"
cp -r "${SCRIPT_DIR}/assets" "${INSTALL_DIR}/"
cp -r "${SCRIPT_DIR}/packaging" "${INSTALL_DIR}/"
cp "${SCRIPT_DIR}/main.py" "${INSTALL_DIR}/"
cp "${SCRIPT_DIR}/gdrive-cli" "${INSTALL_DIR}/"
cp "${SCRIPT_DIR}/gdrive-doc-launcher" "${INSTALL_DIR}/"

chmod +x "${INSTALL_DIR}/main.py"
chmod +x "${INSTALL_DIR}/gdrive-cli"
chmod +x "${INSTALL_DIR}/gdrive-doc-launcher"
chown -R "${TARGET_USER}:${TARGET_USER}" "${INSTALL_DIR}"

# Registrar commit atual para evitar re-download desnecessário pelo GitHubUpdater
if command -v git >/dev/null 2>&1 && [ -d "${SCRIPT_DIR}/.git" ]; then
    CURRENT_SHA=$(git -C "${SCRIPT_DIR}" rev-parse HEAD 2>/dev/null || true)
    if [ -n "${CURRENT_SHA}" ]; then
        mkdir -p "${USER_HOME}/.config/google-drive-linux"
        echo "${CURRENT_SHA}" > "${USER_HOME}/.config/google-drive-linux/last_commit.sha"
        chown -R "${TARGET_USER}:${TARGET_USER}" "${USER_HOME}/.config/google-drive-linux"
    fi
fi

echo -e "${BLUE}[3/6] Criando atalhos no PATH do sistema (/usr/local/bin)...${NC}"
ln -sf "${INSTALL_DIR}/gdrive-cli" /usr/local/bin/gdrive-cli
ln -sf "${INSTALL_DIR}/gdrive-doc-launcher" /usr/local/bin/gdrive-doc-launcher

echo -e "${BLUE}[4/6] Instalando extensão do Nautilus (Gerenciador de Arquivos)...${NC}"
NAUTILUS_EXT_DIR="/usr/share/nautilus-python/extensions"
mkdir -p "${NAUTILUS_EXT_DIR}"
cp "${INSTALL_DIR}/nautilus/google_drive_nautilus.py" "${NAUTILUS_EXT_DIR}/"

echo -e "${BLUE}[5/6] Instalando atalhos e ícones de aplicativo na Área de Trabalho e Menu...${NC}"
cp "${SCRIPT_DIR}/packaging/desktop/google-drive.desktop" /usr/share/applications/
mkdir -p /usr/share/icons/hicolor/256x256/apps/
cp "${INSTALL_DIR}/assets/icons/drive_fs.png" /usr/share/icons/hicolor/256x256/apps/google-drive.png
cp "${INSTALL_DIR}/assets/icons/docs.png" /usr/share/icons/hicolor/256x256/apps/google-docs.png
cp "${INSTALL_DIR}/assets/icons/sheets.png" /usr/share/icons/hicolor/256x256/apps/google-sheets.png
cp "${INSTALL_DIR}/assets/icons/slides.png" /usr/share/icons/hicolor/256x256/apps/google-slides.png
gtk-update-icon-cache /usr/share/icons/hicolor || true

echo -e "${BLUE}[6/6] Abrindo janela de configuração da pasta de montagem e ativando serviço...${NC}"
USER_SYSTEMD_DIR="${USER_HOME}/.config/systemd/user"
USER_WANTS_DIR="${USER_SYSTEMD_DIR}/default.target.wants"
TARGET_UID=$(id -u "${TARGET_USER}")
mkdir -p "${USER_WANTS_DIR}"
cp "${SCRIPT_DIR}/packaging/systemd/google-drive.service" "${USER_SYSTEMD_DIR}/"
ln -sf "${USER_SYSTEMD_DIR}/google-drive.service" "${USER_WANTS_DIR}/google-drive.service"
chown -R "${TARGET_USER}:${TARGET_USER}" "${USER_HOME}/.config/systemd"

# Abrir janela gráfica opcional para escolha do ponto de montagem (com pré-definição padrão)
if [ -n "${DISPLAY:-}" ] || [ -n "${WAYLAND_DISPLAY:-}" ] || [ -d "/run/user/${TARGET_UID}" ]; then
    sudo -u "${TARGET_USER}" \
        DISPLAY="${DISPLAY:-:0}" \
        WAYLAND_DISPLAY="${WAYLAND_DISPLAY:-wayland-0}" \
        XDG_RUNTIME_DIR="/run/user/${TARGET_UID}" \
        DBUS_SESSION_BUS_ADDRESS="unix:path=/run/user/${TARGET_UID}/bus" \
        python3 "${INSTALL_DIR}/ui/install_wizard.py" || true
fi

ACTIVE_MOUNT_POINT=$(sudo -u "${TARGET_USER}" python3 -c "import sys; sys.path.insert(0, '${INSTALL_DIR}'); from core.config import get_mount_point; print(get_mount_point())" 2>/dev/null || echo "${USER_HOME}/GoogleDrive")

if [ -d "/run/user/${TARGET_UID}" ]; then
    sudo -u "${TARGET_USER}" XDG_RUNTIME_DIR="/run/user/${TARGET_UID}" DBUS_SESSION_BUS_ADDRESS="unix:path=/run/user/${TARGET_UID}/bus" systemctl --user daemon-reload || true
    sudo -u "${TARGET_USER}" XDG_RUNTIME_DIR="/run/user/${TARGET_UID}" DBUS_SESSION_BUS_ADDRESS="unix:path=/run/user/${TARGET_UID}/bus" systemctl --user enable --now google-drive.service || true
    sudo -u "${TARGET_USER}" XDG_RUNTIME_DIR="/run/user/${TARGET_UID}" DBUS_SESSION_BUS_ADDRESS="unix:path=/run/user/${TARGET_UID}/bus" systemctl --user restart google-drive.service || true
else
    su - "${TARGET_USER}" -c "systemctl --user daemon-reload || true"
    su - "${TARGET_USER}" -c "systemctl --user enable google-drive.service || true"
fi

# Reiniciar Nautilus para carregar os badges
su - "${TARGET_USER}" -c "nautilus -q || true"

echo -e "${GREEN}==================================================================${NC}"
echo -e "${GREEN}        INSTALAÇÃO CONCLUÍDA COM SUCESSO!                        ${NC}"
echo -e "${GREEN}==================================================================${NC}"
echo -e "Como iniciar:"
echo -e "  1. No terminal ou menu, execute: ${YELLOW}gdrive-cli login${NC} para autenticar com sua conta Google."
echo -e "  2. O Google Drive está montado em: ${YELLOW}${ACTIVE_MOUNT_POINT}${NC}"
echo -e "  3. Inicialização automática no login: ${GREEN}ATIVADA POR PADRÃO${NC} (google-drive.service)"
echo -e "=================================================================="
