#!/usr/bin/env bash
# Upload the committed server, the app build, the ONNX bundle and (optionally) the tunnel configuration to the VM,
# then run setup.sh there. Run from any folder of this repository (Git Bash on Windows works):
#   deploy/azure-vm/push.sh <vm-address> <onnx-bundle-dir> <app-dist-dir> [<tunnel-config-dir>]
# With SHADOW_GATE_BUNDLE=<onnx-bundle-dir>, the gate of that bundle also judges every view without deciding, to be
# compared with the gate in use at /usage (shadow mode); without it, shadow mode is turned off.
# The tunnel folder holds config.yml and the tunnel credentials (<id>.json), and optionally access.env with the
# Cloudflare Access team domain and AUD tag and github-deploy.pub; it never goes into git.
set -euo pipefail

VM=${1:?vm address}
BUNDLE=${2:?onnx bundle folder}
DIST=${3:?app dist folder}
TUNNEL=${4:-}
KEY=${SSH_KEY:-$HOME/.ssh/mammotriage_vm}
USER_AT_VM=${VM_USER:-azureuser}@$VM
HERE=$(cd "$(dirname "$0")" && pwd)
REPO=$(git -C "$HERE" rev-parse --show-toplevel)

grep -q '"format": "onnx"' "$BUNDLE/manifest.json" || { echo "$BUNDLE is not an ONNX bundle (export_onnx.py)"; exit 1; }
SHADOW=${SHADOW_GATE_BUNDLE:-}
if [ -n "$SHADOW" ]; then
  grep -q '"format": "onnx"' "$SHADOW/manifest.json" || { echo "$SHADOW is not an ONNX bundle"; exit 1; }
fi
[ -f "$DIST/index.html" ] || { echo "$DIST has no index.html: build the app first"; exit 1; }
# A build without VITE_DEFAULT_SERVER_URL would allow connections to any HTTPS server (CSP) and point to localhost.
grep -q "connect-src 'self' https://" "$DIST/index.html"   || { echo "$DIST was built without VITE_DEFAULT_SERVER_URL=https://<domain>/mcp: rebuild"; exit 1; }
if find "$DIST" -iname '*.png' -path '*e2e*' | grep -q .; then echo "$DIST contains test images: rebuild"; exit 1; fi

STAGE=$(mktemp -d)
trap 'rm -rf "$STAGE"' EXIT
git -C "$REPO" archive --format=tar.gz --prefix=ai-mammogram-triage/ -o "$STAGE/ai-mammogram-triage.tar.gz" HEAD
tar -C "$DIST" -czf "$STAGE/app-dist.tar.gz" .
tar -C "$BUNDLE" -czf "$STAGE/bundle.tar.gz" .
[ -z "$SHADOW" ] || tar -C "$SHADOW" -czf "$STAGE/shadow-bundle.tar.gz" .
cp "$HERE/setup.sh" "$HERE/mammo-triage.service" "$HERE/nginx-mammotriage.conf" "$HERE/constraints.txt" \
  "$HERE/deploy-entry.sh" "$STAGE/"
if [ -n "$TUNNEL" ]; then
  mkdir "$STAGE/cloudflared"
  cp "$TUNNEL/config.yml" "$TUNNEL"/*.json "$STAGE/cloudflared/"
  # Optional Cloudflare Access settings (team domain and AUD tag) for the server, see ADR 0006.
  [ ! -f "$TUNNEL/access.env" ] || cp "$TUNNEL/access.env" "$STAGE/access.env"
  # Optional public half of the GitHub Actions deploy key (README.md, "Automatic deployment").
  [ ! -f "$TUNNEL/github-deploy.pub" ] || cp "$TUNNEL/github-deploy.pub" "$STAGE/github-deploy.pub"
fi

SSH=(ssh -i "$KEY" -o IdentitiesOnly=yes)
"${SSH[@]}" "$USER_AT_VM" 'rm -rf /tmp/mammo-deploy && mkdir -m 700 /tmp/mammo-deploy'
scp -q -i "$KEY" -o IdentitiesOnly=yes -r "$STAGE"/. "$USER_AT_VM:/tmp/mammo-deploy/"
# The upload holds the weights and the tunnel credentials: removed from /tmp as soon as setup.sh finishes.
"${SSH[@]}" "$USER_AT_VM" 'sudo bash /tmp/mammo-deploy/setup.sh; status=$?; sudo rm -rf /tmp/mammo-deploy; exit $status'
