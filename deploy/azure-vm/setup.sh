#!/usr/bin/env bash
# Provision a small Ubuntu 24.04 VM (1 GB RAM, e.g. Azure B2ats v2) with the ONNX server, the app and nginx.
# Runs as root on the VM, from the folder uploaded by push.sh:  sudo bash setup.sh
# Idempotent: running it again upgrades the server, the app and the bundle and keeps the existing API key.
# Nothing listens publicly: nginx and the server bind to 127.0.0.1 and traffic arrives through cloudflared.
set -euo pipefail

HERE=$(cd "$(dirname "$0")" && pwd)
DOMAIN=${DOMAIN:-app.mammotriage.com}
APP=/opt/mammo-triage
WEB=/var/www/mammotriage
ENV_FILE=/etc/mammo-triage/env
DEPLOY_STATE=/var/lib/mammo-deploy
# GitHub Actions reaches SSH through the tunnel at this name (README.md, "Automatic deployment").
SSH_HOSTNAME=${SSH_HOSTNAME:-ssh.${DOMAIN#*.}}
export DEBIAN_FRONTEND=noninteractive

echo "== 1. Swap (2 GB safety margin for a 1 GB VM)"
if ! swapon --show=NAME --noheadings | grep -qx /swapfile; then
  fallocate -l 2G /swapfile
  chmod 600 /swapfile
  mkswap /swapfile >/dev/null
  swapon /swapfile
  grep -q '^/swapfile ' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi
echo 'vm.swappiness=10' > /etc/sysctl.d/90-mammo-triage.conf
sysctl -q --system

echo "== 2. System packages, automatic security updates and cloudflared (Cloudflare's apt repository)"
apt-get update -q
apt-get upgrade -yq
apt-get install -yq python3-venv nginx unattended-upgrades curl
if [ ! -f /usr/share/keyrings/cloudflare-main.gpg ]; then
  curl -fsSL https://pkg.cloudflare.com/cloudflare-main.gpg -o /usr/share/keyrings/cloudflare-main.gpg
  echo 'deb [signed-by=/usr/share/keyrings/cloudflare-main.gpg] https://pkg.cloudflare.com/cloudflared any main' \
    > /etc/apt/sources.list.d/cloudflared.list
  apt-get update -q
fi
apt-get install -yq cloudflared

echo "== 3. Server in a virtual environment (no PyTorch; OpenCV without a display)"
id mammo >/dev/null 2>&1 || useradd --system --home-dir "$APP" --shell /usr/sbin/nologin mammo
install -d -m 755 "$APP"
[ -x "$APP/venv/bin/python" ] || python3 -m venv "$APP/venv"
PIP="$APP/venv/bin/pip"
"$PIP" install -q --upgrade pip
# rapidocr-onnxruntime asks for opencv-python, which needs libGL; its dependencies are installed by hand with
# opencv-python-headless instead, and RapidOCR itself without dependencies.
"$PIP" install -q -c "$HERE/constraints.txt" --upgrade "$HERE/ai-mammogram-triage.tar.gz" onnxruntime \
  opencv-python-headless pyclipper shapely six PyYAML tqdm
"$PIP" install -q -c "$HERE/constraints.txt" --no-deps rapidocr-onnxruntime
# The version installed here is the rollback point of the GitHub deployments (deploy-entry.sh). Their commit records
# are cleared, so the next automatic deployment installs main again whatever changed since.
install -d -m 755 "$DEPLOY_STATE"
rm -rf "$DEPLOY_STATE/server-wheel.new" "$DEPLOY_STATE"/*.commit "$DEPLOY_STATE/app-assets.txt"
"$PIP" wheel -q --no-deps -w "$DEPLOY_STATE/server-wheel.new" "$HERE/ai-mammogram-triage.tar.gz"
rm -rf "$DEPLOY_STATE/server-wheel" && mv "$DEPLOY_STATE/server-wheel.new" "$DEPLOY_STATE/server-wheel"

echo "== 4. Model bundle (private: VinDr data use agreement) readable only by the service"
rm -rf "$APP/models.new" && install -d -m 750 -o root -g mammo "$APP/models.new"
tar -xzf "$HERE/bundle.tar.gz" -C "$APP/models.new"
chown -R root:mammo "$APP/models.new" && find "$APP/models.new" -type f -exec chmod 640 {} +
rm -rf "$APP/models" && mv "$APP/models.new" "$APP/models"

echo "== 5. Configuration (the API key is generated here once and never printed)"
install -d -m 700 /etc/mammo-triage
if [ ! -f "$ENV_FILE" ]; then
  # Subshell: the restrictive umask must not leak into the files written later (nginx site, app).
  ( umask 077
  KEY=$("$APP/venv/bin/mammo-triage" new-key)
  cat > "$ENV_FILE" <<EOF
MAMMO_MODELS_DIR=$APP/models
MAMMO_API_KEY=$KEY
MAMMO_HOST=127.0.0.1
MAMMO_PORT=8765
MAMMO_ALLOWED_ORIGINS=https://$DOMAIN
MAMMO_ALLOWED_HOSTS=$DOMAIN,127.0.0.1:*,localhost:*
MAMMO_TRUST_PROXY_HEADERS=true
EOF
  )
fi
# Cloudflare Access (optional, see ADR 0006): access.env holds MAMMO_ACCESS_TEAM_DOMAIN and MAMMO_ACCESS_AUD.
# Its lines replace the previous ones, so running setup.sh again updates them; the API key is kept.
if [ -f "$HERE/access.env" ]; then
  ACCESS=$(tr -d '\r' < "$HERE/access.env" | grep -E '^MAMMO_ACCESS_(TEAM_DOMAIN|AUD)=[A-Za-z0-9.-]+$' || true)
  # Checked before touching the env file, so a bad access.env never removes working values.
  if [ "$(grep -c '^MAMMO_ACCESS_TEAM_DOMAIN=' <<< "$ACCESS")" != 1 ] \
     || [ "$(grep -c '^MAMMO_ACCESS_AUD=' <<< "$ACCESS")" != 1 ]; then
    echo "access.env needs exactly one MAMMO_ACCESS_TEAM_DOMAIN and one MAMMO_ACCESS_AUD"; exit 1
  fi
  sed -i '/^MAMMO_ACCESS_\(TEAM_DOMAIN\|AUD\)=/d' "$ENV_FILE"
  echo "$ACCESS" >> "$ENV_FILE"
  unset ACCESS
fi
chmod 600 "$ENV_FILE"

echo "== 6. Service"
install -m 644 "$HERE/mammo-triage.service" /etc/systemd/system/mammo-triage.service
systemctl daemon-reload
systemctl enable -q mammo-triage
systemctl restart mammo-triage

echo "== 7. App (static build) and nginx on 127.0.0.1:8080"
rm -rf "$WEB.new" && install -d -m 755 "$WEB.new"
tar -xzf "$HERE/app-dist.tar.gz" -C "$WEB.new"
rm -rf "$WEB" && mv "$WEB.new" "$WEB"
sed "s/__DOMAIN__/$DOMAIN/g" "$HERE/nginx-mammotriage.conf" > /etc/nginx/sites-available/mammotriage
ln -sf /etc/nginx/sites-available/mammotriage /etc/nginx/sites-enabled/mammotriage
rm -f /etc/nginx/sites-enabled/default
nginx -t -q
systemctl reload nginx || systemctl restart nginx

echo "== 8. Tunnel (only when its configuration was uploaded: see README.md, step 5)"
if [ -f "$HERE/cloudflared/config.yml" ]; then
  install -d -m 755 /etc/cloudflared
  install -m 644 "$HERE/cloudflared/config.yml" /etc/cloudflared/config.yml
  install -m 600 "$HERE"/cloudflared/*.json /etc/cloudflared/
  [ -f /etc/systemd/system/cloudflared.service ] || cloudflared service install
  systemctl restart cloudflared
fi

echo "== 9. Deploy account for GitHub Actions (only when github-deploy.pub was uploaded)"
if [ -f "$HERE/github-deploy.pub" ]; then
  KEY_LINE=$(tr -d '\r' < "$HERE/github-deploy.pub" | grep -m1 -E '^ssh-ed25519 [A-Za-z0-9+/]+=*( [A-Za-z0-9._@-]+)?$' || true)
  [ -n "$KEY_LINE" ] || { echo "github-deploy.pub must hold one ed25519 public key"; exit 1; }
  sed "s/__DOMAIN__/$DOMAIN/g" "$HERE/deploy-entry.sh" > /usr/local/sbin/mammo-deploy.new
  chown root:root /usr/local/sbin/mammo-deploy.new && chmod 755 /usr/local/sbin/mammo-deploy.new
  mv /usr/local/sbin/mammo-deploy.new /usr/local/sbin/mammo-deploy
  # No password ('*', not locked: sshd refuses locked accounts), a shell for the forced command, nothing else.
  id mammo-deploy >/dev/null 2>&1 || useradd --create-home --shell /bin/sh mammo-deploy
  usermod -p '*' mammo-deploy
  SUDOERS=$(mktemp)
  cat > "$SUDOERS" <<'EOF'
# The GitHub Actions deploy key may run deploy-entry.sh as root and nothing else; it reads its operation from
# SSH_ORIGINAL_COMMAND.
Defaults:mammo-deploy env_keep += "SSH_ORIGINAL_COMMAND"
mammo-deploy ALL=(root) NOPASSWD: /usr/local/sbin/mammo-deploy
EOF
  visudo -cqf "$SUDOERS"
  install -m 440 -o root -g root "$SUDOERS" /etc/sudoers.d/mammo-deploy
  rm -f "$SUDOERS"
  # Owned by root, so the account cannot change which key gets in or what it runs.
  install -d -m 755 -o root -g root /home/mammo-deploy/.ssh
  echo "restrict,command=\"sudo -n /usr/local/sbin/mammo-deploy\" $KEY_LINE" > /home/mammo-deploy/.ssh/authorized_keys
  chown root:root /home/mammo-deploy/.ssh/authorized_keys && chmod 644 /home/mammo-deploy/.ssh/authorized_keys
  echo "DEPLOY_KNOWN_HOSTS (GitHub environment secret):"
  awk -v host="$SSH_HOSTNAME" '{print host, $1, $2}' /etc/ssh/ssh_host_ed25519_key.pub
fi

echo "== 10. Health check"
for _ in $(seq 1 30); do
  if curl -fsS http://127.0.0.1:8765/health >/dev/null 2>&1; then break; fi
  sleep 1
done
curl -fsS http://127.0.0.1:8765/health && echo
curl -fsS -o /dev/null -w "app via nginx: HTTP %{http_code}\n" -H "Host: $DOMAIN" http://127.0.0.1:8080/
free -m
