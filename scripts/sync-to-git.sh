#!/usr/bin/env bash

# Exit immediately on error
set -e

# Source and destination directories
SRC_DIR="/etc/nixos"
DEST_DIR="$HOME/Downloads/nixos"

echo "📂 Copying $SRC_DIR to $DEST_DIR..."

# Remove existing destination directory
if [ -d "$DEST_DIR" ]; then
    rm -rf "$DEST_DIR"
fi

# Use sudo to copy since /etc/nixos is only readable by root
sudo cp -r "$SRC_DIR" "$DEST_DIR"

# Change ownership to current user so subsequent operations don't need sudo
sudo chown -R "$(id -u):$(id -g)" "$DEST_DIR"

echo "🔓 Unlocking permissions and optimizing for Git..."

# Set all directories to 755 (drwxr-xr-x)
find "$DEST_DIR" -type d -exec chmod 755 {} +

# Set all files to 644 (-rw-r--r--)
find "$DEST_DIR" -type f -exec chmod 644 {} +

echo "✅ Done! You can now perform Git operations in $DEST_DIR."

