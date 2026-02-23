#!/bin/bash
# =============================================================================
# BeoSound 5c Installer — Shared definitions
# =============================================================================
# Sourced by install.sh and all modules. Provides colors, logging, globals,
# banner, and small helpers.

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

# Logging functions
log_info()    { echo -e "${BLUE}[INFO]${NC} $*"; }
log_success() { echo -e "${GREEN}[OK]${NC} $*"; }
log_warn()    { echo -e "${YELLOW}[WARN]${NC} $*"; }
log_error()   { echo -e "${RED}[ERROR]${NC} $*"; }
log_section() { echo -e "\n${CYAN}=== $* ===${NC}\n"; }

# Display banner
show_banner() {
    echo ""
    echo -e "${CYAN}╔══════════════════════════════════════════════════════════╗${NC}"
    echo -e "${CYAN}║${NC}                                                          ${CYAN}║${NC}"
    echo -e "${CYAN}║${NC}       ${GREEN}____             ____                      _${NC}       ${CYAN}║${NC}"
    echo -e "${CYAN}║${NC}       ${GREEN}| __ )  ___  ___|  __| ___  _   _ _ __   __| |${NC}      ${CYAN}║${NC}"
    echo -e "${CYAN}║${NC}       ${GREEN}|  _ \\ / _ \\/ _ \\|__  |/ _ \\| | | | '_ \\ / _\` |${NC}      ${CYAN}║${NC}"
    echo -e "${CYAN}║${NC}       ${GREEN}| |_) |  __/ (_) |__) | (_) | |_| | | | | (_| |${NC}      ${CYAN}║${NC}"
    echo -e "${CYAN}║${NC}       ${GREEN}|____/ \\___|\\___/|____/\\___/ \\__,_|_| |_|\\__,_|${NC}      ${CYAN}║${NC}"
    echo -e "${CYAN}║${NC}                            ${YELLOW}5 c${NC}                            ${CYAN}║${NC}"
    echo -e "${CYAN}║${NC}                                                          ${CYAN}║${NC}"
    echo -e "${CYAN}║${NC}                  ${BLUE}Installation & Setup${NC}                     ${CYAN}║${NC}"
    echo -e "${CYAN}║${NC}                                                          ${CYAN}║${NC}"
    echo -e "${CYAN}╚══════════════════════════════════════════════════════════╝${NC}"
    echo ""
}

# Globals — set by install.sh before sourcing modules
# INSTALL_USER, INSTALL_DIR, CONFIG_DIR, CONFIG_FILE, SECRETS_FILE,
# PLYMOUTH_THEME_DIR
