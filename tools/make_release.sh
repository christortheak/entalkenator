#!/bin/bash
# Builds the download: dist/enTalkenator-<version>.zip, unzipping to an "enTalkenator" folder.
# Only committed files go in (git archive), minus the paths marked export-ignore in
# .gitattributes. Commit first; uncommitted changes are left out.
set -euo pipefail
cd "$(dirname "$0")/.."
VERSION="$(cat VERSION)"
if [ -n "$(git status --porcelain)" ]; then
    echo "warning: uncommitted changes aren't included" >&2
fi
mkdir -p dist
OUT="dist/enTalkenator-$VERSION.zip"
git archive --format=zip --prefix=enTalkenator/ -o "$OUT" HEAD
echo "$OUT ($(du -h "$OUT" | cut -f1), $(unzip -l "$OUT" | tail -1 | awk '{print $2}') files)"
