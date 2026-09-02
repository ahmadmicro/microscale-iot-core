#!/usr/bin/env bash
#
# Microscale IoT cloud — deployment script.
#
# Automates docs/cloud-hosting.md end to end: secrets, bring-up, TLS, client
# onboarding, backups. Every command is safe to re-run:
#   - `init` REFUSES to overwrite an existing .env (replacing JWT_SECRET would
#     invalidate every credential already issued to clients),
#   - nothing here deletes a data volume.
#
#   ./deploy.sh init [--domain D] [--email E] [--local]
#   ./deploy.sh up                              build + start, wait until healthy
#   ./deploy.sh tls                             issue the cert, switch nginx to HTTPS
#   ./deploy.sh renew                           renew the cert + reload nginx (cron)
#   ./deploy.sh site NAME --spec F [--theme F]  onboard a client, print credentials
#   ./deploy.sh status                          containers, health, sites, secret id
#   ./deploy.sh logs [SERVICE]                  tail logs
#   ./deploy.sh backup                          pg_dump history into ./backups
#   ./deploy.sh restore FILE [DB]               load a dump into a new database
#   ./deploy.sh restart [SERVICE] | down | help
#
set -euo pipefail

INVOKED_FROM="$PWD"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"
ENV_FILE="$HERE/.env"
BACKUP_DIR="$HERE/backups"
BACKUP_KEEP="${BACKUP_KEEP:-14}"
HTTPS_TEMPLATES="./nginx/templates-https"

cd "$HERE"

# ---------------------------------------------------------------- output ----
if [ -t 1 ]; then BOLD=$(printf '\033[1m'); DIM=$(printf '\033[2m'); OFF=$(printf '\033[0m')
else BOLD=; DIM=; OFF=; fi
step() { printf '%s==>%s %s\n' "$BOLD" "$OFF" "$*"; }
info() { printf '    %s\n' "$*"; }
note() { printf '%s    %s%s\n' "$DIM" "$*" "$OFF"; }
warn() { printf 'warning: %s\n' "$*" >&2; }
die()  { printf 'error: %s\n' "$*" >&2; exit 1; }

# --------------------------------------------------------------- plumbing ---
need() { command -v "$1" >/dev/null 2>&1 || die "required command not found: $1"; }

# resolve a user-supplied path against the directory they ran the command in
abspath() { case "$1" in /*) printf '%s' "$1" ;; *) printf '%s/%s' "$INVOKED_FROM" "$1" ;; esac; }

if docker compose version >/dev/null 2>&1; then DC=(docker compose)
elif command -v docker-compose >/dev/null 2>&1; then DC=(docker-compose)
else die "docker compose not found — install Docker (https://get.docker.com)"; fi

load_env() {
  [ -f "$ENV_FILE" ] || die "no .env here — run: ./deploy.sh init"
  set -a; . "$ENV_FILE"; set +a          # .env is shell-sourced: keep values unquoted+simple
  [ -n "${DOMAIN:-}" ]     || die "DOMAIN is not set in .env"
  [ -n "${JWT_SECRET:-}" ] || die "JWT_SECRET is not set in .env"
}

tls_on() { [ "${NGINX_TEMPLATE_DIR:-./nginx/templates}" = "$HTTPS_TEMPLATES" ]; }

# the URL clients actually use — TLS mode and non-default ports included
public_base() {
  if tls_on; then
    if [ "${HTTPS_PORT:-443}" = 443 ]; then printf 'https://%s' "$DOMAIN"
    else printf 'https://%s:%s' "$DOMAIN" "$HTTPS_PORT"; fi
  else
    if [ "${HTTP_PORT:-80}" = 80 ]; then printf 'http://%s' "$DOMAIN"
    else printf 'http://%s:%s' "$DOMAIN" "$HTTP_PORT"; fi
  fi
}

health_url() {
  if tls_on; then printf 'https://%s/api/health' "$DOMAIN"
  else printf 'http://localhost:%s/api/health' "${HTTP_PORT:-80}"; fi
}

wait_healthy() {
  local url i; url="$(health_url)"
  step "waiting for $url"
  for i in $(seq 1 60); do
    if curl -fsS --max-time 5 "$url" >/dev/null 2>&1; then info "healthy"; return 0; fi
    sleep 2
  done
  warn "no healthy response after 2 minutes — inspect with: ./deploy.sh logs"
  return 1
}

# Rewrite one key in .env, preserving the file's 0600 permissions (write through
# the existing inode rather than replacing the file).
set_env_var() {
  local key="$1" val="$2" tmp
  tmp="$(mktemp)"
  if grep -q "^${key}=" "$ENV_FILE"; then
    awk -v k="$key" -v v="$val" '{ if (index($0, k "=") == 1) print k "=" v; else print }' \
      "$ENV_FILE" > "$tmp"
  else
    { cat "$ENV_FILE"; printf '%s=%s\n' "$key" "$val"; } > "$tmp"
  fi
  cat "$tmp" > "$ENV_FILE"
  rm -f "$tmp"
}

# base64url without padding — the JWT wire encoding
b64url() { openssl base64 -A | tr '+/' '-_' | tr -d '='; }

# Mint an HS256 JWT with nothing but openssl, so a bare VPS needs no Python to
# bring the stack up. The claim shape must stay identical to
# tools/mint_token.py's `service` role — tests/test_cloud.py asserts it does.
mint_service_token() {
  local secret="$1" days="${2:-3650}" now header payload h p sig
  now="$(date -u +%s)"
  header='{"alg":"HS256","typ":"JWT"}'
  payload="$(printf '{"username":"service.cloud","acl":{"pub":[],"sub":["ms/#"]},"iat":%s,"exp":%s}' \
             "$now" "$((now + days * 86400))")"
  h="$(printf '%s' "$header"  | b64url)"
  p="$(printf '%s' "$payload" | b64url)"
  sig="$(printf '%s' "$h.$p" | openssl dgst -sha256 -hmac "$secret" -binary | b64url)"
  printf '%s.%s.%s' "$h" "$p" "$sig"
}

# Run a repo Python tool: prefer the host interpreter, else borrow the api image
# (it already carries PyJWT + PyYAML) so the box needs no Python at all.
run_py() {
  if python3 -c 'import jwt, yaml' >/dev/null 2>&1; then
    ( cd "$REPO" && python3 "$@" )
  else
    note "no host python with PyJWT/PyYAML — running inside the api image"
    local a mapped=()
    for a in "$@"; do
      case "$a" in
        "$REPO"/*) mapped+=("/repo/${a#"$REPO"/}") ;;
        /*) [ -e "$a" ] && die "the container cannot see $a — keep specs under $REPO"
            mapped+=("$a") ;;
        *) mapped+=("$a") ;;
      esac
    done
    "${DC[@]}" run --rm --no-deps -T -v "$REPO:/repo" -w /repo -e JWT_SECRET api python "${mapped[@]}"
  fi
}

# --------------------------------------------------------------- commands ---
cmd_init() {
  local domain="" email="" local_mode=0
  while [ $# -gt 0 ]; do
    case "$1" in
      --domain) domain="${2:-}"; shift 2 ;;
      --email)  email="${2:-}";  shift 2 ;;
      --local)  local_mode=1;    shift ;;
      *) die "unknown option for init: $1" ;;
    esac
  done
  need openssl
  [ -f "$ENV_FILE" ] && die ".env already exists — refusing to overwrite it.
    It holds JWT_SECRET; regenerating that invalidates every token already issued
    to clients. Edit .env by hand, or move it aside first if you really mean to."

  if [ "$local_mode" = 1 ]; then
    domain="${domain:-localhost}"
  else
    [ -n "$domain" ] || die "pass --domain <host> (or --local to evaluate on this machine)"
  fi

  step "generating secrets"
  local jwt_secret pg_password api_token sink_token
  jwt_secret="$(openssl rand -hex 32)"
  pg_password="$(openssl rand -hex 24)"
  api_token="$(openssl rand -hex 24)"
  sink_token="$(mint_service_token "$jwt_secret")"

  umask 077
  {
    printf '# Microscale IoT cloud — generated by deploy.sh on %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    printf '# SECRETS. Never commit. Back up JWT_SECRET: losing it means reissuing every credential.\n\n'
    printf 'DOMAIN=%s\n' "$domain"
    printf 'ACME_EMAIL=%s\n\n' "$email"
    printf '# signs every credential on the broker and the read API — treat as root\n'
    printf 'JWT_SECRET=%s\n\n' "$jwt_secret"
    printf 'PG_PASSWORD=%s\n\n' "$pg_password"
    printf '# the historian sink reads every site and writes nothing (service role, 10y)\n'
    printf 'SINK_TOKEN=%s\n\n' "$sink_token"
    printf '# static ops credential for the read API (site dashboard JWTs work too)\n'
    printf 'API_TOKEN=%s\n\n' "$api_token"
    printf '# flipped to %s by `deploy.sh tls`\n' "$HTTPS_TEMPLATES"
    printf 'NGINX_TEMPLATE_DIR=./nginx/templates\n'
    if [ "$local_mode" = 1 ]; then
      printf '\n# local evaluation — unprivileged ports, certs kept out of /etc\n'
      printf 'HTTP_PORT=8880\nHTTPS_PORT=8444\nMQTT_PORT=11883\nLETSENCRYPT_DIR=./letsencrypt\n'
    fi
  } > "$ENV_FILE"
  chmod 600 "$ENV_FILE"

  info "wrote $ENV_FILE (mode 600)"
  step "next"
  info "./deploy.sh up"
  [ "$local_mode" = 1 ] || info "./deploy.sh tls        # once DNS points $domain at this box"
  note "copy .env into your secret manager now — it is gitignored, not backed up"
}

cmd_up() {
  load_env
  step "building and starting the stack"
  "${DC[@]}" up -d --build
  wait_healthy
  step "running"
  info "dashboards: $(public_base)/s/<site>/"
  info "onboard a client: ./deploy.sh site <name> --spec <spec.yaml>"
}

cmd_tls() {
  load_env
  need curl
  [ "$DOMAIN" != "localhost" ] || die "TLS needs a real DOMAIN — .env currently says localhost"
  [ -n "${ACME_EMAIL:-}" ] || warn "ACME_EMAIL is empty; certbot will register without a contact address"

  step "checking that Let's Encrypt can reach this box on port 80"
  curl -fsS --max-time 10 "http://$DOMAIN/api/health" >/dev/null 2>&1 \
    || die "http://$DOMAIN/api/health did not answer.
    Point DNS at this box, open port 80, and run ./deploy.sh up first —
    certbot validates over the same path."

  step "issuing the certificate for $DOMAIN"
  "${DC[@]}" --profile certs run --rm certbot

  # verify inside the container: /etc/letsencrypt is often root-owned on the host
  "${DC[@]}" --profile certs run --rm --entrypoint sh certbot \
    -c "test -f /etc/letsencrypt/live/$DOMAIN/fullchain.pem" \
    || die "certbot finished but no certificate for $DOMAIN — see the output above"

  step "switching nginx to HTTPS"
  set_env_var NGINX_TEMPLATE_DIR "$HTTPS_TEMPLATES"
  load_env
  "${DC[@]}" up -d --force-recreate nginx
  wait_healthy
  step "TLS is on"
  info "renewal: add to cron →  0 3 * * *  $HERE/deploy.sh renew"
  warn "build client dashboards with wss:// and https:// URLs — an HTTPS page cannot open ws://"
}

cmd_renew() {
  load_env
  step "renewing certificates"
  "${DC[@]}" --profile certs run --rm certbot renew
  "${DC[@]}" restart nginx
  wait_healthy || true
}

cmd_site() {
  [ $# -ge 1 ] || die "usage: ./deploy.sh site NAME --spec FILE [--theme FILE]"
  load_env
  local site="$1"; shift
  local args=() a
  while [ $# -gt 0 ]; do
    case "$1" in
      --spec|--theme)
        a="$(abspath "${2:?$1 needs a file}")"
        [ -f "$a" ] || die "no such file: $a"
        args+=("$1" "$a"); shift 2 ;;
      *) args+=("$1"); shift ;;
    esac
  done
  step "onboarding $site"
  # JWT_SECRET travels in the environment, never on the command line (ps is public)
  run_py "$REPO/tools/onboard_site.py" "$site" --domain "$DOMAIN" \
         --base-url "$(public_base)" "${args[@]}"
}

cmd_status() {
  load_env
  "${DC[@]}" ps
  echo
  local fp sites
  fp="$(printf '%s' "$JWT_SECRET" | openssl dgst -sha256 | awk '{print substr($NF,1,12)}')"
  sites="$(ls -1 "$HERE/dashboards" 2>/dev/null | tr '\n' ' ')"
  printf 'domain        %s\n' "$DOMAIN"
  printf 'tls           %s\n' "$(tls_on && echo on || echo 'off (run ./deploy.sh tls)')"
  printf 'secret id     %s   %s\n' "$fp" "(sha256 prefix — compare, never share)"
  printf 'sites         %s\n' "${sites:-<none — ./deploy.sh site ...>}"
  printf 'health        ' 
  curl -fsS --max-time 5 "$(health_url)" 2>/dev/null || printf 'UNREACHABLE at %s' "$(health_url)"
  echo
}

cmd_backup() {
  load_env
  mkdir -p "$BACKUP_DIR"
  local out tmp
  out="$BACKUP_DIR/iotcore-$(date -u +%Y%m%dT%H%M%SZ).sql.gz"
  tmp="$out.part"
  step "dumping the history database"
  if "${DC[@]}" exec -T -e PGPASSWORD="$PG_PASSWORD" timescaledb \
       pg_dump -U postgres -d iotcore | gzip > "$tmp"; then
    mv "$tmp" "$out"
    info "$(du -h "$out" | cut -f1)  $out"
  else
    rm -f "$tmp"
    die "pg_dump failed — is the stack up? (./deploy.sh status)"
  fi
  # keep the newest $BACKUP_KEEP
  ls -1t "$BACKUP_DIR"/iotcore-*.sql.gz 2>/dev/null | tail -n +$((BACKUP_KEEP + 1)) \
    | while read -r old; do rm -f "$old"; info "pruned $(basename "$old")"; done
  warn "this backup sits on the same box as the data — copy it off-site"
}

# Restore a `backup` dump. Non-destructive by default: loads into a NEW database and
# leaves the live one untouched — promoting it is a deliberate, separate step.
# TimescaleDB needs its pre/post-restore hooks around the load; without them the
# hypertable catalog comes back inconsistent.
cmd_restore() {
  load_env
  local file="${1:-}" target="${2:-}"
  [ -n "$file" ] || die "usage: ./deploy.sh restore BACKUP.sql.gz [TARGET_DB]"
  file="$(abspath "$file")"
  [ -f "$file" ] || die "no such backup: $file"
  # all-lowercase: postgres folds unquoted identifiers, so a T/Z timestamp would not round-trip
  target="${target:-iotcore_restore_$(date -u +%Y%m%d%H%M%S)}"
  [ "$target" != iotcore ] || die "refusing to overwrite the live database.
    Restore into a new one (the default), verify it, then promote it by renaming."

  local psql=("${DC[@]}" exec -T -e PGPASSWORD="$PG_PASSWORD" timescaledb psql -U postgres -q)
  step "restoring $(basename "$file") into $target"
  "${psql[@]}" -d postgres -c "CREATE DATABASE $target;" >/dev/null
  "${psql[@]}" -d "$target" -c "CREATE EXTENSION IF NOT EXISTS timescaledb;" \
                        -c "SELECT timescaledb_pre_restore();" >/dev/null
  gzip -dc "$file" | "${psql[@]}" -d "$target" >/dev/null
  "${psql[@]}" -d "$target" -c "SELECT timescaledb_post_restore();" >/dev/null

  local rows
  rows="$("${psql[@]}" -d "$target" -tAc 'select count(*) from readings' | tr -d '[:space:]')"
  info "$rows reading rows restored into $target"
  note "promote it when you are satisfied (stop the stack first):"
  note "  ALTER DATABASE iotcore RENAME TO iotcore_old; ALTER DATABASE $target RENAME TO iotcore;"
}

cmd_logs()    { load_env; "${DC[@]}" logs -f --tail=100 "$@"; }
cmd_restart() { load_env; step "restarting ${*:-all services}"; "${DC[@]}" restart "$@"; }

cmd_down() {
  load_env
  step "stopping (data volumes are kept)"
  "${DC[@]}" down
  note "to destroy the history database too: docker compose down -v  — not offered here"
}

cmd_help() { sed -n '3,26p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; }

# ------------------------------------------------------------------ main ----
case "${1:-help}" in
  init)    shift; cmd_init "$@" ;;
  up)      shift; cmd_up "$@" ;;
  tls)     shift; cmd_tls "$@" ;;
  renew)   shift; cmd_renew "$@" ;;
  site)    shift; cmd_site "$@" ;;
  status)  shift; cmd_status "$@" ;;
  logs)    shift; cmd_logs "$@" ;;
  backup)  shift; cmd_backup "$@" ;;
  restore) shift; cmd_restore "$@" ;;
  restart) shift; cmd_restart "$@" ;;
  down)    shift; cmd_down "$@" ;;
  help|-h|--help) cmd_help ;;
  *) die "unknown command: $1  (try ./deploy.sh help)" ;;
esac
