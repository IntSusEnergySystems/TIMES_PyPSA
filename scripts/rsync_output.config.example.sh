#!/usr/bin/env bash
# Copy to rsync_output.config.sh and fill in your values.
# rsync_output.config.sh is gitignored and must not be committed.

USER="your_ssh_user"
PASS="your_ssh_password"
HOST="your.example.com"
DEST_DIR="/home/your_ssh_user/public_html/times_pypsa/"
# Absolute path to the local output directory to sync
SRC_DIR="/path/to/TIMES_PyPSA/output/"
