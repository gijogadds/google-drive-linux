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

echo -e "${BLUE}[1/6] Instalando dependências do sistema via APT...${NC}"
apt-get update -qq
apt-get install -y \
    python3 \
    python3-pip \
    python3-gi \
    python3-gi-cairo \
    gir1.2-gtk-3.0 \
    gir1.2-ayatanaappindicator3-0.1 \
    fuse3 \
    python3-fusepy \
    python3-nautilus \
    libsecret-1-0 \
    xclip

# Garantir permissões do FUSE
modprobe fuse || true
if [ -f /etc/fuse.conf ]; then
    sed -i 's/#user_allow_other/user_allow_other/' /etc/fuse.conf || true
fi

echo -e "${BLUE}[2/6] Copiando arquivos do aplicativo para ${INSTALL_DIR}...${NC}"
mkdir -p "${INSTALL_DIR}"
cp -r "${SCRIPT_DIR}/core" "${INSTALL_DIR}/"
cp -r "${SCRIPT_DIR}/ui" "${INSTALL_DIR}/"
cp -r "${SCRIPT_DIR}/nautilus" "${INSTALL_DIR}/"
cp -r "${SCRIPT_DIR}/updater" "${INSTALL_DIR}/"
cp -r "${SCRIPT_DIR}/assets" "${INSTALL_DIR}/"
cp "${SCRIPT_DIR}/main.py" "${INSTALL_DIR}/"
cp "${SCRIPT_DIR}/gdrive-cli" "${INSTALL_DIR}/"
cp "${SCRIPT_DIR}/gdrive-doc-launcher" "${INSTALL_DIR}/"

chmod +x "${INSTALL_DIR}/main.py"
chmod +x "${INSTALL_DIR}/gdrive-cli"
chmod +x "${INSTALL_DIR}/gdrive-doc-launcher"

echo -e "${BLUE}[3/6] Criando atalhos no PATH do sistema (/usr/local/bin)...${NC}"
ln -sf "${INSTALL_DIR}/gdrive-cli" /usr/local/bin/gdrive-cli
ln -sf "${INSTALL_DIR}/gdrive-doc-launcher" /usr/local/bin/gdrive-doc-launcher

echo -e "${BLUE}[4/6] Instalando extensão do Nautilus (Gerenciador de Arquivos)...${NC}"
NAUTILUS_EXT_DIR="/usr/share/nautilus-python/extensions"
mkdir -p "${NAUTILUS_EXT_DIR}"
cp "${INSTALL_DIR}/nautilus/google_drive_nautilus.py" "${NAUTILUS_EXT_DIR}/"

echo -e "${BLUE}[5/6] Instalando atalho de aplicativo na Área de Trabalho e Menu...${NC}"
cp "${SCRIPT_DIR}/packaging/desktop/google-drive.desktop" /usr/share/applications/

echo -e "${BLUE}[6/6] Configurando serviço de inicialização automática no systemd...${NC}"
USER_SYSTEMD_DIR="${USER_HOME}/.config/systemd/user"
su - "${TARGET_USER}" -c "mkdir -p ${USER_SYSTEMD_DIR}"
cp "${SCRIPT_DIR}/packaging/systemd/google-drive.service" "${USER_SYSTEMD_DIR}/"
chown -R "${TARGET_USER}:${TARGET_USER}" "${USER_SYSTEMD_DIR}"

su - "${TARGET_USER}" -c "systemctl --user daemon-reload || true"

# Reiniciar Nautilus para carregar os badges
su - "${TARGET_USER}" -c "nautilus -q || true"

echo -e "${GREEN}==================================================================${NC}"
echo -e "${GREEN}        INSTALAÇÃO CONCLUÍDA COM SUCESSO!                        ${NC}"
echo -e "${GREEN}==================================================================${NC}"
echo -e "Como iniciar:"
echo -e "  1. No terminal ou menu, execute: ${YELLOW}gdrive-cli login${NC} para autenticar com sua conta Google."
echo -e "  2. O Google Drive será montado em: ${YELLOW}${USER_HOME}/GoogleDrive${NC}"
echo -e "  3. Para ativar a inicialização automática no login:"
echo -e "     ${YELLOW}systemctl --user enable --now google-drive.service${NC}"
echo -e "=================================================================="
