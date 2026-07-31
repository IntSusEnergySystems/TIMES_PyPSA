#!/usr/bin/env bash
set -euo pipefail

# Sync the local output folder to the remote web directory.
# Credentials and paths live in rsync_output.config.sh (see .example).

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_FILE="${SCRIPT_DIR}/rsync_output.config.sh"

if [[ ! -f "$CONFIG_FILE" ]]; then
  echo "Missing config: $CONFIG_FILE" >&2
  echo "Copy scripts/rsync_output.config.example.sh to rsync_output.config.sh and edit it." >&2
  exit 1
fi

# shellcheck source=/dev/null
source "$CONFIG_FILE"

: "${USER:?USER must be set in $CONFIG_FILE}"
: "${PASS:?PASS must be set in $CONFIG_FILE}"
: "${HOST:?HOST must be set in $CONFIG_FILE}"
: "${DEST_DIR:?DEST_DIR must be set in $CONFIG_FILE}"
: "${SRC_DIR:?SRC_DIR must be set in $CONFIG_FILE}"

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
