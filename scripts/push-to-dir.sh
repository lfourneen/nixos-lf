#!/usr/bin/env bash

# Exit immediately on error
set -e

# Script location and repo root (parent of scripts/)
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(dirname "$SCRIPT_DIR")"
DEST_DIR="/etc/nixos"

echo "📂 Cleaning old config and copying new files to $DEST_DIR..."

# Delete every existing file/dir (incl. dotfiles) as root.
# Do not use "$DEST_DIR/*" here: the glob expands in the caller's shell before
# sudo, and /etc/nixos is left 700/root-owned, so it silently matches nothing.
sudo find "$DEST_DIR" -mindepth 1 -delete

# Lock down /etc/nixos itself before writing into it
sudo chmod 700 "$DEST_DIR"

# Copy config files from repo into /etc/nixos (requires sudo since dest is root-owned)
sudo cp -r "$REPO_DIR/home" "$DEST_DIR/"
sudo cp -r "$REPO_DIR/overlays" "$DEST_DIR/"
sudo cp -r "$REPO_DIR/system" "$DEST_DIR/"
sudo cp "$REPO_DIR/flake.nix" "$DEST_DIR/"
sudo cp "$REPO_DIR/flake.lock" "$DEST_DIR/"
sudo cp "$REPO_DIR/.sops.yaml" "$DEST_DIR/"

echo "🔒 Setting secure permissions..."

# Set all directories to 700 (drwx------)
sudo find "$DEST_DIR" -type d -exec chmod 700 {} +
# Set all files to 600 (-rw-------)
sudo find "$DEST_DIR" -type f -exec chmod 600 {} +

echo "✅ Done! Config pushed to $DEST_DIR with permissions (dirs 700 / files 600)."

