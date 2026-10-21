#!/usr/bin/env bash
# The ONLY program the GitHub Actions deploy key can run on the VM. setup.sh installs it, owned by root, as
# /usr/local/sbin/mammo-deploy and pins the key to it in /home/mammo-deploy/.ssh/authorized_keys:
#   restrict,command="sudo -n /usr/local/sbin/mammo-deploy" ssh-ed25519 AAAA... github-deploy
# so the key opens no shell and can ask only for the operations below, with their arguments checked. Nothing it
# sends can replace this script, the API key, the model bundle, nginx, the service unit or the tunnel: those change
# only with push.sh.
#
#   version app|server   prints the commit deployed last (empty when unknown, e.g. after push.sh)
#   app <commit>         stdin: a .tar.gz of the app build (dist/); replaces the static files, rolls back if nginx
#                        does not serve the new ones
#   server <commit>      stdin: a .tar.gz with the server wheel and constraints.txt; installs it, restarts the service
#                        and, on any failure, puts the previous packages back
#
# What this does NOT protect against: the key exists to put new code on this machine. The server runs as the
# unprivileged, sandboxed "mammo" user, but pip runs as root to install it. Whoever can merge to main, or holds this
# key, can therefore run code on the VM: keep main protected and the key only in the "production" environment.
set -euo pipefail
umask 022

DOMAIN=__DOMAIN__
APP=/opt/mammo-triage
WEB=/var/www/mammotriage
STATE=/var/lib/mammo-deploy
PIP=("$APP/venv/bin/pip")
# pip runs as root on purpose (see above): no warning about it, and no version check on every call.
export PIP_ROOT_USER_ACTION=ignore PIP_DISABLE_PIP_VERSION_CHECK=1
# The app build is a few MB and the wheel well under one; the limits only stop a runaway upload filling the disk.
MAX_UPLOAD_BYTES=$((50 * 1024 * 1024))
MAX_UNPACKED_BYTES=$((200 * 1024 * 1024))

fail() { echo "$*" >&2; exit 1; }

read -r -a COMMAND <<< "${SSH_ORIGINAL_COMMAND:-}"
[ "${#COMMAND[@]}" -le 2 ] || fail "Not allowed."

commit() {
  [[ "${COMMAND[1]:-}" =~ ^[0-9a-f]{40}$ ]] || fail "Expected a full commit SHA."
  echo "${COMMAND[1]}"
}

# Saves stdin to $2 and unpacks it into the folder $1, refusing oversized uploads and anything but files and folders.
receive() {
  local into=$1 archive=$2
  head -c $((MAX_UPLOAD_BYTES + 1)) > "$archive"
  [ "$(stat -c %s "$archive")" -le "$MAX_UPLOAD_BYTES" ] || fail "Upload too large."
  [ "$(gzip -dc "$archive" | head -c $((MAX_UNPACKED_BYTES + 1)) | wc -c)" -le "$MAX_UNPACKED_BYTES" ] \
    || fail "Upload too large once unpacked."
  install -d -m 755 "$into"
  tar --extract --gzip --no-same-owner --no-same-permissions --file "$archive" --directory "$into"
  [ -z "$(find "$into" ! -type f ! -type d -print -quit)" ] || fail "The upload may hold only files and folders."
  chmod -R u=rwX,go=rX "$into"
}

# Once the upload is in, a lost connection (job cancelled, tunnel dropped) must not kill this script halfway through
# an install: from here on it writes to a log, which goes back to the caller at the end if the caller is still there.
detach_output() {
  LOG="$STATE/last-${COMMAND[0]}.log"
  exec 3>&1 >"$LOG" 2>&1
  trap '' HUP PIPE
}

healthy() {
  for _ in $(seq 1 90); do
    curl -fsS -o /dev/null http://127.0.0.1:8765/health 2>/dev/null && return 0
    sleep 1
  done
  return 1
}

install -d -m 755 "$STATE"
# One deployment at a time: the app's and the server's workflows may run together.
exec 9> "$STATE/lock"
flock -w 600 9 || fail "Another deployment is still running."
# Next to the targets (same disk), so the final moves are renames.
SCRATCH=$(mktemp -d -p "$STATE")
LOG=
trap 'status=$?; rm -rf "$SCRATCH"; [ -z "$LOG" ] || cat "$LOG" >&3 2>/dev/null || true; exit $status' EXIT

case "${COMMAND[0]:-}" in
  version)
    case "${COMMAND[1]:-}" in
      app|server) cat "$STATE/${COMMAND[1]}.commit" 2>/dev/null || true ;;
      *) fail "Expected app or server." ;;
    esac
    ;;

  app)
    SHA=$(commit)
    receive "$SCRATCH/web" "$SCRATCH/app.tar.gz"
    detach_output
    [ -f "$SCRATCH/web/index.html" ] || fail "The build has no index.html."
    # A build without VITE_DEFAULT_SERVER_URL would let the app connect to any HTTPS server: exactly this source list.
    grep -qF "connect-src 'self' https://$DOMAIN;" "$SCRATCH/web/index.html" \
      || fail "The build does not connect only to https://$DOMAIN: build it with VITE_DEFAULT_SERVER_URL."
    # index.html is served for any path, so the check fetches a hashed script that only this build has.
    SCRIPT=$(grep -oE 'src="\./assets/[A-Za-z0-9._-]+\.js"' "$SCRATCH/web/index.html" | head -1 | cut -d'"' -f2)
    [ -n "$SCRIPT" ] && [ -f "$SCRATCH/web/$SCRIPT" ] || fail "index.html does not load a script from assets/."
    # A page opened before this deployment loads some chunks later (the server client, at the first analysis), from
    # the previous build: its own assets stay one more release. Older ones are not carried over again.
    ls -1 "$SCRATCH/web/assets" > "$SCRATCH/own-assets.txt"
    if [ -f "$STATE/app-assets.txt" ]; then CARRY=$(cat "$STATE/app-assets.txt"); else CARRY=$(ls -1 "$WEB/assets" 2>/dev/null || true); fi
    while IFS= read -r name; do
      if [[ "$name" =~ ^[A-Za-z0-9._-]+$ ]] && [ -f "$WEB/assets/$name" ] && [ ! -e "$SCRATCH/web/assets/$name" ]; then
        cp -p "$WEB/assets/$name" "$SCRATCH/web/assets/"
      fi
    done <<< "$CARRY"
    rm -rf "$WEB.previous"
    mv "$WEB" "$WEB.previous"
    mv "$SCRATCH/web" "$WEB"
    if ! curl -fsS -o /dev/null -H "Host: $DOMAIN" "http://127.0.0.1:8080/${SCRIPT#./}"; then
      rm -rf "$WEB" && mv "$WEB.previous" "$WEB"
      fail "nginx did not serve the new build: the previous one is back."
    fi
    mv "$SCRATCH/own-assets.txt" "$STATE/app-assets.txt"
    echo "$SHA" > "$STATE/app.commit"
    echo "App $SHA deployed."
    ;;

  server)
    SHA=$(commit)
    receive "$SCRATCH/in" "$SCRATCH/server.tar.gz"
    detach_output
    shopt -s nullglob
    WHEELS=("$SCRATCH"/in/ai_mammogram_triage-*-py3-none-any.whl)
    PREVIOUS=("$STATE"/server-wheel/*.whl)
    [ "${#WHEELS[@]}" = 1 ] || fail "Expected exactly one ai_mammogram_triage wheel."
    [ -f "$SCRATCH/in/constraints.txt" ] || fail "Expected constraints.txt."
    [ "${#PREVIOUS[@]}" = 1 ] || fail "No previous wheel to roll back to: run push.sh once."
    # Every package as it is now, to put back exactly (no dependency resolution) if anything below fails.
    "${PIP[@]}" freeze --exclude ai-mammogram-triage > "$SCRATCH/before.txt"
    # Dependencies first (only those whose version moved), from wheels only: no build code runs as root. Then the
    # package itself, reinstalled even when its version number did not change.
    if "${PIP[@]}" install -q --only-binary :all: -c "$SCRATCH/in/constraints.txt" "${WHEELS[0]}" \
       && "${PIP[@]}" install -q --no-deps --force-reinstall "${WHEELS[0]}" \
       && systemctl restart mammo-triage && healthy; then
      install -d -m 755 "$SCRATCH/wheel" && cp "${WHEELS[0]}" "$SCRATCH/wheel/"
      rm -rf "$STATE/server-wheel.old"
      mv "$STATE/server-wheel" "$STATE/server-wheel.old" && mv "$SCRATCH/wheel" "$STATE/server-wheel"
      rm -rf "$STATE/server-wheel.old"
      echo "$SHA" > "$STATE/server.commit"
      echo "Server $SHA deployed: $(curl -fsS http://127.0.0.1:8765/health)"
    else
      echo "The new server failed (install or /health): putting the previous packages back."
      if "${PIP[@]}" install -q --no-deps --only-binary :all: -r "$SCRATCH/before.txt" \
         && "${PIP[@]}" install -q --no-deps --force-reinstall "${PREVIOUS[0]}" \
         && systemctl restart mammo-triage && healthy; then
        fail "The previous server is back and answers /health."
      fi
      fail "The previous server does not answer /health either: see journalctl -u mammo-triage."
    fi
    ;;

  *)
    fail "Not allowed."
    ;;
esac
