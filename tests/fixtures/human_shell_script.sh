#!/usr/bin/env bash
set -e

SRC="$HOME/work"
DEST="${1:-}"

if [ -z "$DEST" ]; then
    echo "usage: $0 <dest>"
    exit 1
fi

if [ ! -d "$DEST" ]; then
    echo "dest not found: $DEST"
    exit 1
fi

stamp=$(date +%Y%m%d-%H%M)
out="$DEST/work-$stamp.tar.gz"

# skip caches, node_modules, build dirs
tar --exclude='node_modules' --exclude='.cache' --exclude='dist' \
    -czf "$out" -C "$HOME" work

sha256sum "$out" > "$out.sha256"

echo "wrote $out"
