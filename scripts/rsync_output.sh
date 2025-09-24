#!/usr/bin/env bash
set -euo pipefail

# This script syncs the local output folder to the remote web directory.

USER="labothap"
PASS="B49ees32"
DEST_DIR="/home/labothap/public_html/times_pypsa/"
SRC_DIR="/home/sylvain/svn/TIMES_PyPSA/output/"
HOST="labothap.squoilin.eu"

# Ensure sshpass exists
if ! command -v sshpass >/dev/null 2>&1; then
  echo "sshpass is not installed. Attempting to install..." >&2
  if command -v apt-get >/dev/null 2>&1; then
    sudo apt-get update -y
    sudo apt-get install -y sshpass
  else
    echo "Cannot auto-install sshpass on this system. Please install it and retry." >&2
    exit 1
  fi
fi

if [[ ! -d "$SRC_DIR" ]]; then
  echo "Source directory not found: $SRC_DIR" >&2
  exit 1
fi

echo "Syncing $SRC_DIR to $USER@$HOST:$DEST_DIR ..."
sshpass -p "$PASS" \
  rsync -avz --delete \
    -e "ssh -o StrictHostKeyChecking=no" \
    "$SRC_DIR" "$USER@$HOST:$DEST_DIR"

echo "Sync completed successfully."


