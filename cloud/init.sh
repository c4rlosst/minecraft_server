#!/usr/bin/env bash
# Creates .env from .env.example with a random RCON password and panel password.
set -euo pipefail
cd "$(dirname "$0")"

if [ -f .env ]; then echo ".env already exists, leaving it alone."; exit 0; fi
cp .env.example .env
rcon=$(head -c 24 /dev/urandom | base64 | tr -dc 'A-Za-z0-9' | head -c 24)
panel=$(head -c 24 /dev/urandom | base64 | tr -dc 'A-Za-z0-9' | head -c 16)
sed -i "s|^RCON_PASSWORD=.*|RCON_PASSWORD=$rcon|; s|^PANEL_PASSWORD=.*|PANEL_PASSWORD=$panel|" .env
mkdir -p data state backups
echo "Created .env. Panel password: $panel"
echo "Now edit .env: set EULA=TRUE (after reading https://aka.ms/MinecraftEULA), OPS and PUBLIC_ADDRESS."
