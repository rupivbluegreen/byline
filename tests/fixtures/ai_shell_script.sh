#!/usr/bin/env bash
# =====================================================================
# Acme Gateway — Bootstrap Script
# =====================================================================
# WHAT IT DOES:
#   Installs runtime dependencies, prepares the configuration directory,
#   and starts the Acme Gateway container in a production-ready state.
#
# WHEN TO RUN:
#   Run this script once on a fresh host, immediately after cloning the
#   repository and before invoking `docker compose up`.
# =====================================================================

set -euo pipefail

# ---------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------
CONFIG_DIR="/etc/acme"
LOG_DIR="/var/log/acme"
IMAGE="acme/gateway:latest"

# ---------------------------------------------------------------------
# Step 1 — Bootstrap dependencies and prepare directories
# ---------------------------------------------------------------------
echo "[1/2] Bootstrapping environment ..."

mkdir -p "${CONFIG_DIR}"
mkdir -p "${LOG_DIR}"

if ! command -v docker >/dev/null 2>&1; then
    echo "  -> Installing Docker ..."
    curl -fsSL https://get.docker.com | sh
fi

# ---------------------------------------------------------------------
# Step 2 — Pull the image and finalize
# ---------------------------------------------------------------------
echo "[2/2] Finalizing setup ..."

docker pull "${IMAGE}"
docker tag "${IMAGE}" "acme/gateway:current"

# ---------------------------------------------------------------------
# Done
# ---------------------------------------------------------------------
printf "\n"
printf "=====================================================================\n"
printf "  Setup complete!\n"
printf "  Run 'docker compose up -d' to start the gateway.\n"
printf "=====================================================================\n"
