#!/usr/bin/env bash
# quanta — install or update the recorder stack on an existing server with one command.
#
# Supported: Ubuntu 22.04 / 24.04, Debian 12 (amd64 or arm64). Safe to re-run: every step
# checks what is already there, and re-running is also how you update. See
# docs/runbooks/recorder.md ("Sunucuya kurulum") for the walkthrough.
#
#   sudo bash bootstrap.sh                  # install / update (Docker Compose)
#   sudo bash bootstrap.sh --native         # install / update without Docker (systemd)
#   bash bootstrap.sh --check-only          # report only, changes nothing, no root needed
#   sudo bash bootstrap.sh --check-access   # re-run the exchange access check only
#   sudo bash bootstrap.sh --auto-update on # hourly, CI-gated updates with rollback
#
# What it does:
#   1. preflight: OS, CPU/RAM/disk, clock sync, listening ports
#   2. packages: git, curl, chrony (+ ufw with --firewall)
#   3. Docker Engine + compose plugin (skipped if present; a *stopped* Docker is never
#      started without --start-docker) — or, with --native, uv (Python tool) instead
#   4. Tailscale (official installer) and `tailscale up` (prints a login link)
#   5. user `quanta` (uid 10001, the container user) and directories
#   6. repository → /opt/quanta (public: HTTPS; private: generates a read-only deploy key)
#   7. /etc/quanta/recorder.yaml from config/recorder.example.yaml (never overwritten)
#   8. image build (or --native: locked Python env + systemd units), access check for
#      Binance/Bybit/Deribit → <data>/access.json, services up
#   9. `tailscale serve` → status page over HTTPS, reachable from your tailnet only
#
# The chosen mode is remembered in /etc/quanta/compose.env, so a re-run needs no flags.
#
# Nothing is exposed to the internet: the status page listens on 127.0.0.1:8080 and is
# published to the tailnet only (never use `tailscale funnel` for it). No secret is needed.
set -Eeuo pipefail

REPO_SLUG="${QUANTA_REPO:-coniiamca/Bot2-de-ifre}"
# Any git URL (mirror, local path in CI); bypasses the GitHub HTTPS/deploy-key logic.
REPO_URL="${QUANTA_REPO_URL:-}"
BRANCH="${QUANTA_BRANCH:-claude/crypto-trading-research-platform-cy01wr}"
INSTALL_DIR="${QUANTA_INSTALL_DIR:-/opt/quanta}"
ETC_DIR=/etc/quanta
ENV_FILE="$ETC_DIR/compose.env"
CONFIG_FILE="$ETC_DIR/recorder.yaml"
SECRETS_DIR="$ETC_DIR/secrets"
DEPLOY_KEY=/root/.ssh/quanta_deploy_ed25519
GITHUB_KNOWN_HOSTS=/root/.ssh/quanta_github_known_hosts
# GitHub's published SSH host key (docs.github.com → "GitHub's SSH key fingerprints").
GITHUB_HOST_KEY="github.com ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOMqqnkVzrm0SdG6UOoqKLsabgH5C9okWi0dh2l9GKJl"
LOG_FILE=/var/log/quanta-bootstrap.log
UV_CACHE=/var/cache/quanta-uv
UV_PYTHON_DIR=/opt/quanta-python      # only used when the OS has no Python 3.12
UNITS=(quanta-recorder quanta-ui quanta-lake)
LOCK_FILE=/run/quanta-bootstrap.lock
CONTAINER_UID=10001
UI_LOCAL="http://127.0.0.1:8080"
MIN_CPUS=2
MIN_RAM_MB=3800          # "4 GB" machines report slightly less
MIN_DISK_GB=100

MODE=install
DATA_DIR=""
ASSUME_YES=0
WITH_TAILSCALE=""         # 1 | 0 | "" (remembered)
WITH_FIREWALL=0
FORCE_DEPLOY_KEY=0
LITE=0
MIN_FREE_GB=""
NATIVE=""                 # "" = not given (a previous install's choice is remembered)
START_DOCKER=0
AUTO_UPDATE=""            # on | off | "" (remembered)
UNATTENDED=0              # run by quanta-update.timer: never wait for a human
REF=""                    # deploy this commit instead of the branch head

# -- output helpers -------------------------------------------------------------------------
if [[ -t 1 ]]; then
  B=$'\e[1m' G=$'\e[32m' Y=$'\e[33m' R=$'\e[31m' N=$'\e[0m'
else
  B="" G="" Y="" R="" N=""
fi
say()  { printf '%s\n' "$*"; }
step() { printf '\n%s==> %s%s\n' "$B" "$*" "$N"; }
ok()   { printf '  %s✓%s %s\n' "$G" "$N" "$*"; }
warn() { printf '  %s⚠%s %s\n' "$Y" "$N" "$*"; WARNINGS=$((WARNINGS + 1)); }
bad()  { printf '  %s✗%s %s\n' "$R" "$N" "$*"; FAILURES=$((FAILURES + 1)); }
die()  { printf '\n%s✗ %s%s\n' "$R" "$*" "$N" >&2; exit 1; }
WARNINGS=0
FAILURES=0

on_error() {
  printf '\n%s✗ Beklenmeyen hata (satır %s): %s%s\n' "$R" "$1" "$2" "$N" >&2
  [[ -w "$LOG_FILE" ]] && printf '  Ayrıntılar: %s\n' "$LOG_FILE" >&2
  printf '  Betik tekrar çalıştırılabilir; tamamlanan adımlar atlanır.\n' >&2
}
trap 'on_error "$LINENO" "$BASH_COMMAND"' ERR

have_tty() { { : </dev/tty; } 2>/dev/null; }

# ask "question" default(y|n) → returns 0 for yes
ask() {
  local q="$1" def="${2:-n}" ans hint="[e/H]"
  [[ "$def" == y ]] && hint="[E/h]"
  if ((ASSUME_YES)); then [[ "$def" == y ]]; return; fi
  have_tty || die "Etkileşimli onay gerekiyor ama terminal yok: '$q'. --yes ile çalıştır."
  read -r -p "  $q $hint " ans </dev/tty || ans=""
  ans="${ans:-$def}"
  [[ "$ans" =~ ^([eEyY]|evet|yes)$ ]]
}

usage() {
  cat <<EOF
Kullanım: sudo bash bootstrap.sh [seçenekler]

  --check-only        Yalnız ön kontrol; hiçbir şeyi değiştirmez (root gerekmez)
  --check-access      Yalnız borsa erişim kontrolünü yeniden çalıştırır (kurulu sistemde)
  --data-dir DIR      Ham ve işlenmiş verinin host'taki yeri (varsayılan /var/lib/quanta/data;
                      büyük ayrı bir disk varsa onu ver)
  --branch NAME       Kurulacak git branch'i (varsayılan: $BRANCH)
  --repo OWNER/NAME   GitHub deposu (varsayılan: $REPO_SLUG)
  --deploy-key        Depo private: HTTPS'i deneme, doğrudan salt-okunur deploy key kullan
  --native            Docker'sız kurulum: Python ortamı + systemd servisleri (Docker'a hiç
                      dokunmaz). Sunucuda Docker durdurulmuşsa veya başka işler içinse bunu kullan
  --start-docker      Docker kurulu ama durdurulmuşsa yine de başlat (restart politikası olan
                      mevcut konteynerler de başlar!)
  --auto-update on|off  Otomatik güncelleme: saatte bir GitHub'a bakar; yeni sürümü yalnız
                      CI testleri yeşilse kurar, servisler kalkmazsa önceki sürüme döner
  --lite              Küçük/paylaşılan sunucu: daha az sembol kaydeder (config/recorder.lite.yaml;
                      L2 yalnız BTC+ETH, 20 GB disk koruması). Yalnız yeni config'e uygulanır
  --min-free-gb N     Disk koruması: boş alan N GB'ın altına inince kayıt durur (sunucudaki
                      diğer işler için yer bırakır). Yalnız yeni config'e uygulanır
  --firewall          ufw'yi aç: yalnız SSH (+ tailnet) içeri; sunucudaki diğer servisler
                      etkilenebileceği için isteğe bağlı
  --no-tailscale      Tailscale kurulum/serve adımlarını atla
  --yes               Soruları varsayılan cevapla geç (etkileşimsiz)
  -h, --help          Bu yardım
EOF
}

while (($#)); do
  case "$1" in
    --check-only) MODE=check ;;
    --check-access) MODE=access ;;
    --data-dir) DATA_DIR="${2:?--data-dir bir dizin ister}"; shift ;;
    --branch) BRANCH="${2:?--branch bir isim ister}"; shift ;;
    --repo) REPO_SLUG="${2:?--repo OWNER/NAME ister}"; shift ;;
    --deploy-key) FORCE_DEPLOY_KEY=1 ;;
    --native) NATIVE=1 ;;
    --start-docker) START_DOCKER=1 ;;
    --auto-update) AUTO_UPDATE="${2:?--auto-update on|off ister}"; shift ;;
    --unattended) UNATTENDED=1; ASSUME_YES=1 ;;
    --ref) REF="${2:?--ref bir commit ister}"; shift ;;
    --lite) LITE=1 ;;
    --min-free-gb) MIN_FREE_GB="${2:?--min-free-gb bir sayı ister}"; shift ;;
    --firewall) WITH_FIREWALL=1 ;;
    --no-tailscale) WITH_TAILSCALE=0 ;;
    --yes|-y) ASSUME_YES=1 ;;
    -h|--help) usage; exit 0 ;;
    *) usage >&2; die "Bilinmeyen seçenek: $1" ;;
  esac
  shift
done

# A previous install remembers its data directory and mode in the env file.
PREV_MODE=""
if [[ -r "$ENV_FILE" ]]; then
  [[ -n "$DATA_DIR" ]] || DATA_DIR="$(sed -n 's/^QUANTA_DATA=//p' "$ENV_FILE" | tail -n1)"
  PREV_MODE="$(sed -n 's/^QUANTA_MODE=//p' "$ENV_FILE" | tail -n1)"
  PREV_MODE="${PREV_MODE:-docker}"
  [[ -n "$AUTO_UPDATE" ]] || AUTO_UPDATE="$(sed -n 's/^QUANTA_AUTO_UPDATE=//p' "$ENV_FILE" | tail -n1)"
  if [[ -z "$WITH_TAILSCALE" && "$(sed -n 's/^QUANTA_TAILSCALE=//p' "$ENV_FILE" | tail -n1)" == off ]]; then
    WITH_TAILSCALE=0
  fi
fi
WITH_TAILSCALE="${WITH_TAILSCALE:-1}"
AUTO_UPDATE="${AUTO_UPDATE:-off}"
[[ "$AUTO_UPDATE" == on || "$AUTO_UPDATE" == off ]] || die "--auto-update on ya da off olmalı: $AUTO_UPDATE"
if [[ -z "$NATIVE" ]]; then
  if [[ "$PREV_MODE" == native ]]; then NATIVE=1; else NATIVE=0; fi
fi
if [[ -n "$PREV_MODE" && "$MODE" == install ]]; then
  if { ((NATIVE)) && [[ "$PREV_MODE" != native ]]; } || { ((!NATIVE)) && [[ "$PREV_MODE" == native ]]; }; then
    die "Önceki kurulum '$PREV_MODE' modunda. Mod değiştirmek için önce eskisini durdur (runbook: Kaldırma), sonra $ENV_FILE dosyasını sil."
  fi
fi
DATA_DIR="${DATA_DIR:-/var/lib/quanta/data}"
[[ "$DATA_DIR" == /* ]] || die "--data-dir mutlak bir yol olmalı: $DATA_DIR"
[[ -z "$MIN_FREE_GB" || "$MIN_FREE_GB" =~ ^[0-9]+([.][0-9]+)?$ ]] \
  || die "--min-free-gb bir sayı olmalı (GB): $MIN_FREE_GB"

# -- 1. preflight ---------------------------------------------------------------------------
OS_ID="" OS_VERSION="" OS_CODENAME="" OS_NAME=""
detect_os() {
  [[ -r /etc/os-release ]] || return 0
  # shellcheck disable=SC1091
  OS_ID="$(. /etc/os-release && printf '%s' "${ID:-}")"
  # shellcheck disable=SC1091
  OS_VERSION="$(. /etc/os-release && printf '%s' "${VERSION_ID:-}")"
  # shellcheck disable=SC1091
  OS_CODENAME="$(. /etc/os-release && printf '%s' "${VERSION_CODENAME:-}")"
  # shellcheck disable=SC1091
  OS_NAME="$(. /etc/os-release && printf '%s' "${PRETTY_NAME:-unknown}")"
}

docker_active() { systemctl is-active --quiet docker 2>/dev/null; }

existing_parent() {
  local p="$1"
  while [[ ! -e "$p" ]]; do p="$(dirname "$p")"; done
  printf '%s' "$p"
}

preflight() {
  step "1/9 Ön kontrol"
  detect_os
  case "$OS_ID:$OS_VERSION" in
    ubuntu:22.04|ubuntu:24.04|debian:12) ok "İşletim sistemi: $OS_NAME" ;;
    ubuntu:*|debian:*) warn "İşletim sistemi: $OS_NAME (test edilmedi; Ubuntu 22.04/24.04 veya Debian 12 önerilir)" ;;
    *) bad "İşletim sistemi desteklenmiyor: ${OS_NAME:-bilinmiyor} (Ubuntu 22.04/24.04 veya Debian 12 gerekli)" ;;
  esac
  local arch
  arch="$(uname -m)"
  case "$arch" in
    x86_64|aarch64) ok "Mimari: $arch" ;;
    *) bad "Mimari desteklenmiyor: $arch (amd64 veya arm64 gerekli)" ;;
  esac

  local cpus ram_mb disk_gb where
  cpus="$(nproc 2>/dev/null || echo 1)"
  if ((cpus >= MIN_CPUS)); then ok "CPU: $cpus çekirdek"; else warn "CPU: $cpus çekirdek (en az $MIN_CPUS önerilir)"; fi
  ram_mb="$(awk '/^MemTotal:/ {print int($2 / 1024)}' /proc/meminfo)"
  if ((ram_mb >= MIN_RAM_MB)); then ok "RAM: $((ram_mb / 1024)) GB"; else warn "RAM: ${ram_mb} MB (en az 4 GB önerilir)"; fi
  where="$(existing_parent "$DATA_DIR")"
  disk_gb="$(df -Pk "$where" 2>/dev/null | awk 'NR == 2 {print int($4 / 1024 / 1024)}' || true)"
  disk_gb="${disk_gb:-0}"
  if ((disk_gb >= MIN_DISK_GB)); then
    ok "Boş disk ($where): $disk_gb GB"
  else
    warn "Boş disk ($where): $disk_gb GB (en az $MIN_DISK_GB GB önerilir; L2 kaydı günde birkaç GB üretir). Büyük bir disk varsa --data-dir ile göster; paylaşılan/küçük sunucuda --lite --min-free-gb 20 kullan"
  fi
  local floor="${MIN_FREE_GB:-}"
  if [[ -z "$floor" && ! -f "$CONFIG_FILE" ]]; then floor=$((LITE ? 20 : 5)); fi
  if [[ -n "$floor" ]]; then
    local budget=$((disk_gb - ${floor%.*}))
    if ((budget <= 2)); then
      bad "Disk koruması tabanı ${floor} GB, boş alan ${disk_gb} GB: kayıt hemen durur. Yer aç ya da --min-free-gb değerini düşür"
    else
      ok "Disk koruması: boş alan ${floor} GB'a inince kayıt durur → kayda ayrılan alan ≈ ${budget} GB"
    fi
  fi

  if [[ "$(timedatectl show -p NTPSynchronized --value 2>/dev/null || true)" == yes ]]; then
    ok "Saat senkronize (NTP)"
  else
    warn "Saat senkronu doğrulanamadı; kurulum chrony'yi açar (borsa saatiyle fark < 250 ms olmalı)"
  fi

  if ((NATIVE)); then
    ok "Docker kullanılmayacak (--native): servisler systemd ile çalışır; Docker'a dokunulmaz"
    if command -v python3.12 >/dev/null 2>&1; then
      ok "Python 3.12 mevcut"
    else
      say "  · Python 3.12 yok; uv ayrı bir kopyasını $UV_PYTHON_DIR altına indirecek"
    fi
  elif command -v docker >/dev/null 2>&1 && ! docker_active; then
    if ((START_DOCKER)); then
      warn "Docker durdurulmuş; --start-docker verildiği için başlatılacak (restart politikası olan mevcut konteynerler de başlar)"
    else
      bad "Docker kurulu ama durdurulmuş. Başlatılırsa mevcut konteynerler de kendiliğinden başlayabilir. Docker'sız kurulum için --native kullan (önerilen); yine de başlatmak için --start-docker"
    fi
  elif command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
    ok "Docker: $(docker --version | sed 's/,.*//') + compose $(docker compose version --short 2>/dev/null)"
  else
    say "  · Docker (compose eklentisiyle) kurulacak"
  fi
  if command -v tailscale >/dev/null 2>&1; then
    local tsv
    tsv="$(tailscale version 2>/dev/null || true)"
    ok "Tailscale kurulu: ${tsv%%$'\n'*}"
  elif ((WITH_TAILSCALE)); then
    say "  · Tailscale kurulacak"
  fi

  if command -v ss >/dev/null 2>&1; then
    local l8080 public
    l8080="$(ss -ltnpH 'sport = :8080' 2>/dev/null || true)"
    if [[ -n "$l8080" && "$l8080" != *docker-proxy* ]] \
      && ! systemctl is-active --quiet quanta-ui 2>/dev/null; then
      warn "127.0.0.1:8080 başka bir süreç tarafından kullanılıyor; durum sayfası bu portu kullanır: ${l8080//$'\n'/ }"
    fi
    public="$(ss -ltnH 2>/dev/null | awk '{print $4}' | grep -Ev '^(127\.|\[::1\]|\[?::ffff:127\.|100\.)' | sort -u | tr '\n' ' ' || true)"
    [[ -n "$public" ]] && say "  · Dışarıdan dinleyen portlar (bilgi): $public"
  fi
  if [[ -r "$ENV_FILE" ]]; then
    ok "Önceki kurulum bulundu ($ENV_FILE) — güncelleme yapılacak"
  fi
}

# -- 2. packages ----------------------------------------------------------------------------
# NEEDRESTART_MODE=l: needrestart only lists services using outdated libraries; it must not
# restart anything else running on this (existing, shared) server.
apt_get() { DEBIAN_FRONTEND=noninteractive NEEDRESTART_MODE=l apt-get "$@"; }
apt_install() { apt_get install -y -q --no-install-recommends "$@" >/dev/null; }

install_packages() {
  step "2/9 Temel paketler"
  apt_get update -q >/dev/null
  local pkgs=(ca-certificates curl gnupg git openssh-client iproute2 chrony python3)
  ((WITH_FIREWALL)) && pkgs+=(ufw)
  apt_install "${pkgs[@]}"
  ok "${pkgs[*]}"
  systemctl enable --now chrony >/dev/null 2>&1 || systemctl enable --now chronyd >/dev/null 2>&1 || true
  if command -v chronyc >/dev/null 2>&1; then
    local off
    off="$(chronyc tracking 2>/dev/null | awk -F': ' '/^System time/ {print $2}' || true)"
    ok "chrony çalışıyor${off:+ (sistem saati farkı: $off)}"
  fi
  if ((WITH_FIREWALL)); then setup_firewall; fi
}

setup_firewall() {
  local ports p
  ports="$(sshd -T 2>/dev/null | awk '$1 == "port" {print $2}' | sort -u || true)"
  ports="${ports:-22}"
  if ! ufw status | grep -q '^Status: active'; then
    ufw default deny incoming >/dev/null
    ufw default allow outgoing >/dev/null
  fi
  for p in $ports; do ufw allow "$p/tcp" comment 'ssh' >/dev/null; done
  ufw allow in on tailscale0 comment 'tailnet' >/dev/null
  ufw --force enable >/dev/null
  ok "ufw açık: içeri yalnız SSH (port: ${ports//$'\n'/, }) ve tailnet"
}

# -- 3. docker ------------------------------------------------------------------------------
docker_repo() {
  install -m 0755 -d /etc/apt/keyrings
  if [[ ! -s /etc/apt/keyrings/docker.asc ]]; then
    curl -fsSL --max-time 60 "https://download.docker.com/linux/$OS_ID/gpg" -o /etc/apt/keyrings/docker.asc
    chmod a+r /etc/apt/keyrings/docker.asc
  fi
  local list=/etc/apt/sources.list.d/docker.list
  if ! grep -rqs "download.docker.com" /etc/apt/sources.list /etc/apt/sources.list.d/; then
    printf 'deb [arch=%s signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/%s %s stable\n' \
      "$(dpkg --print-architecture)" "$OS_ID" "$OS_CODENAME" >"$list"
  fi
  apt_get update -q >/dev/null
}

install_docker() {
  if ((NATIVE)); then
    install_uv
    return
  fi
  step "3/9 Docker"
  if command -v docker >/dev/null 2>&1 && ! docker_active && ((!START_DOCKER)); then
    die "Docker durdurulmuş; başlatılmayacak. --native ile Docker'sız kur ya da --start-docker ver."
  fi
  if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
    ok "Zaten kurulu: $(docker compose version --short)"
  elif command -v docker >/dev/null 2>&1; then
    docker_repo
    apt_install docker-compose-plugin
    ok "compose eklentisi kuruldu"
  else
    docker_repo
    apt_install docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
    ok "Docker Engine kuruldu: $(docker --version | sed 's/,.*//')"
  fi
  systemctl enable --now docker >/dev/null 2>&1 || true
  docker info >/dev/null 2>&1 || die "Docker servisi çalışmıyor (systemctl status docker)"
}

install_uv() {
  step "3/9 Python aracı (uv) — Docker'sız kurulum"
  if command -v uv >/dev/null 2>&1; then
    ok "uv mevcut: $(uv --version)"
    return
  fi
  curl -LsSf --max-time 120 https://astral.sh/uv/install.sh \
    | env UV_INSTALL_DIR=/usr/local/bin INSTALLER_NO_MODIFY_PATH=1 UV_NO_MODIFY_PATH=1 sh >/dev/null
  command -v uv >/dev/null 2>&1 || die "uv kurulamadı"
  ok "uv kuruldu: $(uv --version)"
}

# -- 4. tailscale ---------------------------------------------------------------------------
# Parse the JSON from a variable (no pipes: a reader that stops early would SIGPIPE the writer
# and trip pipefail). The first "DNSName" in `tailscale status --json` is this node's (Self).
ts_json() { tailscale status --json 2>/dev/null || true; }
ts_state() {
  local j
  j="$(ts_json)"
  sed -n '/"BackendState"/{s/.*"BackendState":[[:space:]]*"\([^"]*\)".*/\1/p;q;}' <<<"$j"
}
ts_dns_name() {
  local j
  j="$(ts_json)"
  sed -n '/"DNSName"/{s/.*"DNSName":[[:space:]]*"\([^"]*\)\.".*/\1/p;q;}' <<<"$j"
}

setup_tailscale() {
  step "4/9 Tailscale"
  if ((!WITH_TAILSCALE)); then say "  · --no-tailscale: atlandı"; return; fi
  if ! command -v tailscale >/dev/null 2>&1; then
    # the official installer runs apt itself: same no-restart rule as apt_get above
    curl -fsSL --max-time 120 https://tailscale.com/install.sh \
      | DEBIAN_FRONTEND=noninteractive NEEDRESTART_MODE=l sh >/dev/null
    ok "Tailscale kuruldu"
  fi
  systemctl enable --now tailscaled >/dev/null 2>&1 || true
  if [[ "$(ts_state)" == Running ]]; then
    ok "Tailnet'e bağlı: $(ts_dns_name)"
    return
  fi
  if ((UNATTENDED)); then
    warn "Tailscale bağlı değil (otomatik güncelleme giriş beklemez). Sonra: sudo tailscale up"
    return
  fi
  say "  Aşağıdaki bağlantıyı tarayıcıda açıp Tailscale hesabınla giriş yap (bu sunucu tailnet'ine eklenecek):"
  if ((ASSUME_YES)) || ! have_tty; then
    # Unattended (e.g. run by an agent in the background): the login link is printed to the
    # output and to $LOG_FILE; give the person 15 minutes to open it, then carry on.
    say "  (15 dk içinde açılmazsa kurulum Tailscale olmadan devam eder)"
    tailscale up --timeout=15m || true
  else
    tailscale up </dev/tty || true
  fi
  if [[ "$(ts_state)" == Running ]]; then
    ok "Tailnet'e bağlı: $(ts_dns_name)"
  else
    warn "Tailscale girişi tamamlanmadı. Sonra: sudo tailscale up  ve betiği tekrar çalıştır"
  fi
}

# -- 5. user & directories ------------------------------------------------------------------
setup_dirs() {
  step "5/9 Kullanıcı ve dizinler"
  if getent passwd "$CONTAINER_UID" >/dev/null; then
    ok "uid $CONTAINER_UID mevcut: $(getent passwd "$CONTAINER_UID" | cut -d: -f1)"
  elif getent passwd quanta >/dev/null; then
    # an unrelated 'quanta' user exists: ownership below is numeric, so just leave it alone
    warn "Başka bir uid ile 'quanta' kullanıcısı var; dosya sahipliği sayısal ($CONTAINER_UID) olacak"
  else
    local grp="$CONTAINER_UID"
    if ! getent group "$CONTAINER_UID" >/dev/null; then
      if getent group quanta >/dev/null; then grp=quanta; else groupadd --gid "$CONTAINER_UID" quanta; fi
    fi
    useradd --uid "$CONTAINER_UID" --gid "$grp" --no-create-home \
      --home-dir /var/lib/quanta --shell /usr/sbin/nologin quanta
    ok "Kullanıcı 'quanta' (uid $CONTAINER_UID, konteyner kullanıcısıyla aynı; giriş yapamaz) oluşturuldu"
  fi
  install -d -m 0755 "$ETC_DIR"
  install -d -m 0700 -o "$CONTAINER_UID" -g "$CONTAINER_UID" "$SECRETS_DIR"
  install -d -m 0750 -o "$CONTAINER_UID" -g "$CONTAINER_UID" "$DATA_DIR"
  # A re-run with an existing data dir keeps its content; only the top-level owner is fixed.
  chown "$CONTAINER_UID:$CONTAINER_UID" "$DATA_DIR"
  ok "$ETC_DIR, $SECRETS_DIR (0700), $DATA_DIR"
}

# -- 6. repository --------------------------------------------------------------------------
ssh_git() {
  GIT_SSH_COMMAND="ssh -i $DEPLOY_KEY -o IdentitiesOnly=yes -o UserKnownHostsFile=$GITHUB_KNOWN_HOSTS -o StrictHostKeyChecking=yes" git "$@"
}

deploy_key_flow() {
  install -d -m 0700 /root/.ssh
  printf '%s\n' "$GITHUB_HOST_KEY" >"$GITHUB_KNOWN_HOSTS"
  if [[ ! -f "$DEPLOY_KEY" ]]; then
    ssh-keygen -q -t ed25519 -N "" -C "quanta-deploy@$(hostname -s)" -f "$DEPLOY_KEY"
  fi
  if ssh_git ls-remote --heads "git@github.com:$REPO_SLUG.git" "$BRANCH" >/dev/null 2>&1; then
    ok "Deploy key GitHub'da tanımlı"
    return
  fi
  say ""
  say "  Depo private görünüyor. Bu sunucu için salt-okunur bir deploy key üretildi."
  say "  GitHub → $REPO_SLUG → Settings → Deploy keys → Add deploy key"
  say "  Title: $(hostname -s)   ·   'Allow write access' KAPALI kalsın   ·   Key:"
  say ""
  say "    $(cat "$DEPLOY_KEY.pub")"
  say ""
  ((ASSUME_YES)) && die "Anahtarı ekledikten sonra betiği tekrar çalıştır."
  have_tty || die "Anahtarı ekledikten sonra betiği tekrar çalıştır."
  local _
  read -r -p "  Anahtarı ekleyince Enter'a bas… " _ </dev/tty || true
  ssh_git ls-remote --heads "git@github.com:$REPO_SLUG.git" "$BRANCH" >/dev/null 2>&1 \
    || die "GitHub anahtarı kabul etmedi. Depo adı ve deploy key doğru mu? (betik tekrar çalıştırılabilir)"
  ok "Deploy key çalışıyor"
}

OLD_HEAD=""
fetch_repo() {
  step "6/9 Kod (${REPO_URL:-$REPO_SLUG} @ $BRANCH)"
  if [[ -d "$INSTALL_DIR/.git" ]]; then OLD_HEAD="$(git -C "$INSTALL_DIR" rev-parse HEAD)"; fi
  # the updater checks out the new commit itself (so the new installer runs) and tells us
  # what was running before, so services are restarted
  OLD_HEAD="${QUANTA_PREV_HEAD:-$OLD_HEAD}"
  local target="${REF:-origin/$BRANCH}"
  local https="https://github.com/$REPO_SLUG.git" ssh="git@github.com:$REPO_SLUG.git"
  if [[ -n "$REPO_URL" ]]; then
    if [[ -d "$INSTALL_DIR/.git" ]]; then
      git -C "$INSTALL_DIR" remote set-url origin "$REPO_URL"
      git -C "$INSTALL_DIR" fetch -q origin "$BRANCH"
    else
      git clone -q --branch "$BRANCH" "$REPO_URL" "$INSTALL_DIR"
    fi
    git -C "$INSTALL_DIR" checkout -q -B "$BRANCH" "$target"
    ok "$(git -C "$INSTALL_DIR" log -1 --format='%h %s' | cut -c1-80)"
    return
  fi
  if [[ -d "$INSTALL_DIR/.git" ]]; then
    local remote
    remote="$(git -C "$INSTALL_DIR" remote get-url origin)"
    if [[ "$remote" == https://* ]] && ((!FORCE_DEPLOY_KEY)) \
      && GIT_TERMINAL_PROMPT=0 git -C "$INSTALL_DIR" fetch -q origin "$BRANCH" 2>/dev/null; then
      :
    else
      # repository became private (or --deploy-key): switch the checkout to the deploy key
      deploy_key_flow
      git -C "$INSTALL_DIR" remote set-url origin "$ssh"
      git -C "$INSTALL_DIR" config core.sshCommand \
        "ssh -i $DEPLOY_KEY -o IdentitiesOnly=yes -o UserKnownHostsFile=$GITHUB_KNOWN_HOSTS -o StrictHostKeyChecking=yes"
      git -C "$INSTALL_DIR" fetch -q origin "$BRANCH"
    fi
    if [[ -n "$(git -C "$INSTALL_DIR" status --porcelain --untracked-files=no)" ]]; then
      die "$INSTALL_DIR içinde yerel değişiklikler var; kurulum dizininde düzenleme yapma (config: $CONFIG_FILE). 'git -C $INSTALL_DIR status' ile bak."
    fi
    git -C "$INSTALL_DIR" checkout -q -B "$BRANCH" "$target"
    ok "Güncel: $(git -C "$INSTALL_DIR" log -1 --format='%h %s' | cut -c1-80)"
    return
  fi
  [[ -e "$INSTALL_DIR" && -n "$(ls -A "$INSTALL_DIR" 2>/dev/null)" ]] \
    && die "$INSTALL_DIR boş değil ve bir git deposu değil; taşı veya QUANTA_INSTALL_DIR ver."
  if ((!FORCE_DEPLOY_KEY)) && GIT_TERMINAL_PROMPT=0 git ls-remote --heads "$https" "$BRANCH" >/dev/null 2>&1; then
    GIT_TERMINAL_PROMPT=0 git clone -q --branch "$BRANCH" "$https" "$INSTALL_DIR"
    ok "Public depo HTTPS ile klonlandı"
  else
    deploy_key_flow
    ssh_git clone -q --branch "$BRANCH" "$ssh" "$INSTALL_DIR"
    git -C "$INSTALL_DIR" config core.sshCommand \
      "ssh -i $DEPLOY_KEY -o IdentitiesOnly=yes -o UserKnownHostsFile=$GITHUB_KNOWN_HOSTS -o StrictHostKeyChecking=yes"
    ok "Private depo deploy key ile klonlandı"
  fi
  if [[ -n "$REF" ]]; then git -C "$INSTALL_DIR" checkout -q -B "$BRANCH" "$REF"; fi
  ok "$(git -C "$INSTALL_DIR" log -1 --format='%h %s' | cut -c1-80)"
}

# -- 7. configuration -----------------------------------------------------------------------
write_config() {
  step "7/9 Yapılandırma"
  if [[ -f "$CONFIG_FILE" ]]; then
    ok "$CONFIG_FILE korunuyor (değişiklikler senin)"
    if ((LITE)) || [[ -n "$MIN_FREE_GB" ]]; then
      warn "--lite / --min-free-gb yalnız yeni config'e uygulanır. Mevcut dosyayı değiştirmek için: sudo nano $CONFIG_FILE (min_free_disk_gb, depth_symbols…), sonra: $(restart_hint)"
    fi
  else
    local template=recorder.example.yaml
    ((LITE)) && template=recorder.lite.yaml
    install -m 0644 "$INSTALL_DIR/config/$template" "$CONFIG_FILE"
    # segment metadata names the recording host; inside a container the hostname is random
    sed -i "/^data_dir:/a host_id: $(hostname -s)" "$CONFIG_FILE"
    if [[ -n "$MIN_FREE_GB" ]]; then
      sed -i "s/^min_free_disk_gb:.*/min_free_disk_gb: $MIN_FREE_GB/" "$CONFIG_FILE"
    fi
    if ((NATIVE)); then
      # no container: host paths, and the metrics endpoint on loopback only
      sed -i "s|^data_dir:.*|data_dir: $DATA_DIR|" "$CONFIG_FILE"
      sed -i '/^metrics:/,/^[^ ]/ s/^  host: .*/  host: 127.0.0.1/' "$CONFIG_FILE"
    fi
    ok "$CONFIG_FILE oluşturuldu ($template; Binance + Bybit + Deribit açık; disk koruması: $(sed -n 's/^min_free_disk_gb: *\([0-9.]*\).*/\1/p' "$CONFIG_FILE") GB)"
  fi
  local new mode=docker
  ((NATIVE)) && mode=native
  local ts=on
  ((WITH_TAILSCALE)) || ts=off
  new="$(printf 'QUANTA_MODE=%s\nQUANTA_CONFIG=%s\nQUANTA_DATA=%s\nQUANTA_SECRETS=%s\nQUANTA_REPO=%s\nQUANTA_BRANCH=%s\nQUANTA_AUTO_UPDATE=%s\nQUANTA_TAILSCALE=%s\n' \
    "$mode" "$CONFIG_FILE" "$DATA_DIR" "$SECRETS_DIR" "$REPO_SLUG" "$BRANCH" "$AUTO_UPDATE" "$ts")"
  printf '# written by deploy/bootstrap.sh — install mode and paths (quanta-compose reads it)\n%s\n' "$new" >"$ENV_FILE"
  if ((NATIVE)); then
    local user
    user="$(getent passwd "$CONTAINER_UID" | cut -d: -f1)"
    cat >/usr/local/bin/quanta <<WRAP
#!/bin/sh
# Run a quanta CLI command as the service user, e.g.:  sudo quanta data volume -d $DATA_DIR
cd / && exec runuser -u $user -- $INSTALL_DIR/.venv/bin/quanta "\$@"
WRAP
    chmod 0755 /usr/local/bin/quanta
    rm -f /usr/local/bin/quanta-compose
    ok "$ENV_FILE, komut: quanta"
    return
  fi
  cat >/usr/local/bin/quanta-compose <<EOF
#!/bin/sh
# docker compose for the quanta stack (written by deploy/bootstrap.sh)
exec docker compose --env-file $ENV_FILE -f $INSTALL_DIR/infra/compose/docker-compose.yml "\$@"
EOF
  cat >/usr/local/bin/quanta <<'EOF'
#!/bin/sh
# Run a quanta CLI command inside the image, e.g.:  sudo quanta data volume -d /var/lib/quanta/data
# (paths are container paths: data /var/lib/quanta/data, config /etc/quanta/recorder.yaml)
exec /usr/local/bin/quanta-compose run --rm --no-deps recorder "$@"
EOF
  chmod 0755 /usr/local/bin/quanta-compose /usr/local/bin/quanta
  ok "$ENV_FILE, komutlar: quanta-compose, quanta"
}

# -- 8. build, access check, start ----------------------------------------------------------
restart_hint() {
  if ((NATIVE)); then
    printf 'sudo systemctl restart quanta-recorder quanta-lake'
  else
    printf 'sudo quanta-compose up -d --force-recreate recorder lake-daily'
  fi
}

ACCESS_RESTRICTED=0
run_access_check() {
  local out rc=0
  out="$(mktemp)"
  say "  Borsalara bağlanılıyor (her biri ~15 sn)…"
  if ((NATIVE)); then
    (cd / && runuser -u "$(getent passwd "$CONTAINER_UID" | cut -d: -f1)" -- \
      "$INSTALL_DIR/.venv/bin/quanta" recorder check-access --config "$CONFIG_FILE" \
      --out "$DATA_DIR/access.json" --samples 10 --ws-seconds 10) >"$out" 2>"$out.err" || rc=$?
  else
    /usr/local/bin/quanta-compose run --rm --no-deps -T recorder recorder check-access \
      --config /etc/quanta/recorder.yaml --out /var/lib/quanta/data/access.json \
      --samples 10 --ws-seconds 10 >"$out" 2>"$out.err" || rc=$?
  fi
  if [[ ! -s "$out" ]]; then
    cat "$out.err" >&2
    rm -f "$out" "$out.err"
    die "Erişim kontrolü çalışmadı (yukarıdaki hataya bak)"
  fi
  local line
  while IFS= read -r line; do
    case "$line" in
      *" OK "*) ok "$line" ;;
      *RESTRICTED*) bad "$line"; ACCESS_RESTRICTED=1 ;;
      *FAILED*) warn "$line" ;;
    esac
  done < <(grep -E ' (OK|RESTRICTED|FAILED) ' "$out.err" || true)
  rm -f "$out" "$out.err"
  if ((ACCESS_RESTRICTED)); then
    say ""
    say "  Bir veya daha fazla borsa bu sunucunun konumuna hizmet vermiyor (HTTP 451/403)."
    say "  O borsa için kayıt yapılamaz. Seçenekler: sunucu bölgesini değiştirmek ya da"
    say "  $CONFIG_FILE içinde o borsayı 'enabled: false' yapıp betiği tekrar çalıştırmak."
  elif ((rc != 0)); then
    say "  Bazı kontroller başarısız (ağ?). Sonra tekrar dene: sudo bash $INSTALL_DIR/deploy/bootstrap.sh --check-access"
  fi
  ok "Sonuç durum sayfasında görünür ($DATA_DIR/access.json)"
}

unit_text() {
  local unit="$1" user="$2"
  local common="User=$user
WorkingDirectory=$DATA_DIR
Restart=always
RestartSec=5
NoNewPrivileges=yes
ProtectSystem=strict
ProtectHome=yes
PrivateTmp=yes"
  printf '# written by deploy/bootstrap.sh --native\n[Unit]\n'
  case "$unit" in
    quanta-recorder)
      printf 'Description=quanta market data recorder\nAfter=network-online.target\nWants=network-online.target\n\n[Service]\n%s\n' "$common"
      printf 'ExecStart=%s/.venv/bin/quanta recorder run --config %s\n' "$INSTALL_DIR" "$CONFIG_FILE"
      printf 'Environment=AWS_SHARED_CREDENTIALS_FILE=%s/aws_credentials\n' "$SECRETS_DIR"
      printf 'ReadWritePaths=%s\nTimeoutStopSec=60\n' "$DATA_DIR"
      ;;
    quanta-ui)
      printf 'Description=quanta status page (127.0.0.1:8080)\nAfter=quanta-recorder.service\n\n[Service]\n%s\n' "$common"
      printf 'ExecStart=%s/.venv/bin/quanta ui --data-dir %s --host 127.0.0.1 --port 8080 --metrics-url http://127.0.0.1:9101/metrics\n' "$INSTALL_DIR" "$DATA_DIR"
      ;;
    quanta-lake)
      printf 'Description=quanta daily lake job (00:20 UTC) and data checks (every 6 h)\nAfter=quanta-recorder.service\n\n[Service]\n%s\n' "$common"
      printf 'ExecStart=%s/.venv/bin/quanta lake schedule --data-dir %s --at 00:20 --config %s\n' "$INSTALL_DIR" "$DATA_DIR" "$CONFIG_FILE"
      printf 'ReadWritePaths=%s\nNice=10\nIOSchedulingClass=idle\n' "$DATA_DIR"
      # bounded by design (~1-2 GB); the cap only protects the rest of a shared server
      printf 'MemoryHigh=5G\nMemoryMax=6G\nOOMScoreAdjust=500\n'
      ;;
  esac
  printf '\n[Install]\nWantedBy=multi-user.target\n'
}

UNITS_CHANGED=0
write_units() {
  local user unit tmp
  user="$(getent passwd "$CONTAINER_UID" | cut -d: -f1)"
  for unit in "${UNITS[@]}"; do
    tmp="$(mktemp)"
    unit_text "$unit" "$user" >"$tmp"
    if ! cmp -s "$tmp" "/etc/systemd/system/$unit.service"; then
      install -m 0644 "$tmp" "/etc/systemd/system/$unit.service"
      UNITS_CHANGED=1
    fi
    rm -f "$tmp"
  done
  if ((UNITS_CHANGED)); then systemctl daemon-reload; fi
  ok "systemd servisleri: ${UNITS[*]}"
}

start_native() {
  step "8/9 Python ortamı, erişim kontrolü, servisler (Docker'sız)"
  say "  Bağımlılıklar kuruluyor (kilit dosyasındaki sürümler; ilk seferde 1–2 dk)…"
  install -d -m 0755 "$UV_CACHE"
  (cd "$INSTALL_DIR" && UV_CACHE_DIR="$UV_CACHE" UV_PYTHON_INSTALL_DIR="$UV_PYTHON_DIR" \
    UV_LINK_MODE=copy uv sync -q --frozen --no-dev --no-editable --compile-bytecode --extra s3 \
    --reinstall-package quanta)   # uv does not rebuild a local package whose version is unchanged
  "$INSTALL_DIR/.venv/bin/quanta" --help >/dev/null || die "Python ortamı çalışmıyor"
  ok "Python ortamı hazır: $INSTALL_DIR/.venv ($("$INSTALL_DIR/.venv/bin/python" --version))"
  write_units
  run_access_check
  if ((ACCESS_RESTRICTED)) && ! ask "Yine de servisler başlatılsın mı?" y; then
    die "Durduruldu. Yapılandırmayı düzeltip betiği tekrar çalıştır."
  fi
  systemctl enable "${UNITS[@]}" >/dev/null 2>&1
  if ((UNITS_CHANGED)) || [[ "$OLD_HEAD" != "$(git -C "$INSTALL_DIR" rev-parse HEAD)" ]]; then
    systemctl restart "${UNITS[@]}"   # new code or units: a brief gap, recorded in-band
  else
    systemctl start "${UNITS[@]}"
  fi
  local _ up=0 u
  for _ in $(seq 1 60); do
    if curl -fsS --max-time 3 "$UI_LOCAL/healthz" >/dev/null 2>&1; then up=1; break; fi
    sleep 2
  done
  ((up)) || die "Durum sayfası 2 dk içinde açılmadı: journalctl -u quanta-ui -n 50"
  for u in "${UNITS[@]}"; do
    systemctl is-active --quiet "$u" || die "'$u' çalışmıyor. Bak: journalctl -u $u -n 50"
  done
  ok "Çalışıyor: ${UNITS[*]} (systemd; sunucu açılışında otomatik başlar)"
}

start_stack() {
  if ((NATIVE)); then
    start_native
    return
  fi
  step "8/9 İmaj, erişim kontrolü, servisler"
  say "  İmaj derleniyor (ilk seferde birkaç dakika)…"
  /usr/local/bin/quanta-compose build --pull -q
  ok "İmaj hazır: quanta:latest"
  run_access_check
  if ((ACCESS_RESTRICTED)) && ! ask "Yine de servisler başlatılsın mı?" y; then
    die "Durduruldu. Yapılandırmayı düzeltip betiği tekrar çalıştır."
  fi
  # containers are recreated only when the image or compose file changed (a config edit
  # needs `quanta-compose up -d --force-recreate recorder`)
  /usr/local/bin/quanta-compose up -d --remove-orphans --quiet-pull
  local _ up=0
  for _ in $(seq 1 60); do
    if curl -fsS --max-time 3 "$UI_LOCAL/healthz" >/dev/null 2>&1; then up=1; break; fi
    sleep 2
  done
  ((up)) || die "Durum sayfası 2 dk içinde açılmadı: sudo quanta-compose logs ui"
  local running s
  running="$(/usr/local/bin/quanta-compose ps --status running --services || true)"
  for s in recorder ui lake-daily; do
    grep -qx "$s" <<<"$running" \
      || die "'$s' çalışmıyor. Bak: sudo quanta-compose ps ; sudo quanta-compose logs $s"
  done
  ok "Çalışıyor: recorder, ui, lake-daily (sunucu açılışında otomatik başlar)"
}

# -- automatic updates -----------------------------------------------------------------------
setup_auto_update() {
  step "Otomatik güncelleme"
  local svc=/etc/systemd/system/quanta-update.service tmr=/etc/systemd/system/quanta-update.timer
  if [[ "$AUTO_UPDATE" == off ]]; then
    if [[ -e "$tmr" ]]; then
      systemctl disable --now quanta-update.timer >/dev/null 2>&1 || true
      rm -f "$svc" "$tmr"
      systemctl daemon-reload
      ok "Kapatıldı"
    else
      say "  · Kapalı. Açmak için: sudo bash $INSTALL_DIR/deploy/bootstrap.sh --auto-update on"
    fi
    return
  fi
  cat >"$svc" <<UNIT
# written by deploy/bootstrap.sh --auto-update on
[Unit]
Description=quanta automatic update (only CI-green commits, rollback on failure)
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
ExecStart=/bin/bash $INSTALL_DIR/deploy/auto-update.sh
TimeoutStartSec=45min
UNIT
  cat >"$tmr" <<UNIT
# written by deploy/bootstrap.sh --auto-update on
[Unit]
Description=quanta automatic update check (hourly)

[Timer]
OnCalendar=hourly
RandomizedDelaySec=15min
Persistent=true

[Install]
WantedBy=timers.target
UNIT
  systemctl daemon-reload
  systemctl enable --now quanta-update.timer >/dev/null 2>&1
  ok "Açık: saatte bir kontrol; yalnız CI'ı yeşil sürümler, başarısızsa geri dönüş (günlük: journalctl -u quanta-update)"
}

# -- 9. tailscale serve ---------------------------------------------------------------------
UI_URL=""
serve_ui() {
  step "9/9 Durum sayfasını tailnet'e açma"
  if ((!WITH_TAILSCALE)) || [[ "$(ts_state)" != Running ]]; then
    warn "Tailscale bağlı değil; sayfa şimdilik yalnız sunucuda: $UI_LOCAL (SSH tüneli: ssh -L 8080:127.0.0.1:8080 sunucu)"
    return
  fi
  local status flat port=443
  status="$(tailscale serve status --json 2>/dev/null || true)"
  flat="$(tr -d ' \n\t' <<<"$status")"
  if [[ "$flat" == *'"Proxy":"http://127.0.0.1:8080"'* ]]; then
    # Web: {"<name>:<port>": {"Handlers": {"/": {"Proxy": "http://127.0.0.1:8080"}}}}
    port="$(sed -n 's|.*:\([0-9][0-9]*\)":{"Handlers":{"/":{"Proxy":"http://127\.0\.0\.1:8080".*|\1|p' <<<"$flat")"
    port="${port:-443}"
    ok "Zaten yapılandırılmış (https:$port)"
  elif ((UNATTENDED)); then
    warn "tailscale serve yapılandırılmamış (otomatik güncelleme bunu kurmaz). Elle: sudo bash $INSTALL_DIR/deploy/bootstrap.sh"
    return
  else
    # something else is already served on this node: leave 443 to it
    if [[ -n "$flat" && "$flat" != "{}" && "$flat" != null ]]; then port=8443; fi
    # Not silenced: if HTTPS is off for the tailnet, tailscale prints a link to enable it and
    # waits for that. Bounded so an unattended run can never hang here.
    say "  Tailnet'te HTTPS kapalıysa aşağıda bir etkinleştirme bağlantısı çıkar; aç (10 dk süre var)."
    if ! timeout --foreground 600 tailscale serve --bg --https="$port" "$UI_LOCAL"; then
      warn "tailscale serve tamamlanmadı. Tailnet'te HTTPS sertifikalarını aç (login.tailscale.com/admin/dns → HTTPS Certificates) ve betiği tekrar çalıştır."
      return
    fi
    ok "tailscale serve: https:$port → $UI_LOCAL (yalnız tailnet; funnel kapalı)"
  fi
  UI_URL="https://$(ts_dns_name)"
  if [[ "$port" != 443 ]]; then UI_URL="$UI_URL:$port"; fi
}

summary() {
  step "Bitti"
  if [[ -n "$UI_URL" ]]; then
    say "  ${B}Durum sayfası:${N} $UI_URL"
    say "    (telefon/bilgisayarda Tailscale uygulaması aynı hesapla açık olmalı; ilk açılış birkaç sn sürebilir)"
  else
    say "  ${B}Durum sayfası:${N} $UI_LOCAL (sunucu üzerinden)"
  fi
  say ""
  say "  Faydalı komutlar:"
  if ((NATIVE)); then
    say "    systemctl status ${UNITS[*]}      servislerin durumu"
    say "    journalctl -u quanta-recorder -f                   kayıt günlüğü"
    say "    sudo quanta data volume -d $DATA_DIR      günlük veri hacmi"
  else
    say "    sudo quanta-compose ps                      servislerin durumu"
    say "    sudo quanta-compose logs -f recorder        kayıt günlüğü"
    say "    sudo quanta data volume -d /var/lib/quanta/data     günlük veri hacmi"
  fi
  say "    sudo bash $INSTALL_DIR/deploy/bootstrap.sh              güncelle (tekrar çalıştır)"
  say "    sudo bash $INSTALL_DIR/deploy/bootstrap.sh --check-access   erişim kontrolünü yenile"
  say "  Yapılandırma: $CONFIG_FILE  → değiştirdikten sonra: $(restart_hint)"
  say "  Veri: $DATA_DIR    Günlük: $LOG_FILE"
  if [[ "$AUTO_UPDATE" == on ]]; then
    say "  Otomatik güncelleme: açık (kapatmak için: sudo bash $INSTALL_DIR/deploy/bootstrap.sh --auto-update off)"
  else
    say "  Otomatik güncelleme: kapalı (açmak için: sudo bash $INSTALL_DIR/deploy/bootstrap.sh --auto-update on)"
  fi
  ((WARNINGS)) && say "  ${Y}$WARNINGS uyarı var; yukarıya bak.${N}"
  return 0
}

# -- main -----------------------------------------------------------------------------------
case "$MODE" in
  check)
    preflight
    say ""
    if ((FAILURES)); then say "${R}Ön kontrol: $FAILURES engel, $WARNINGS uyarı.${N} Hiçbir şey değiştirilmedi."; exit 1; fi
    say "${G}Ön kontrol tamam${N} ($WARNINGS uyarı). Hiçbir şey değiştirilmedi."
    exit 0
    ;;
  access)
    [[ $EUID -eq 0 ]] || die "root gerekli: sudo bash $0 --check-access"
    if ((NATIVE)); then
      [[ -x "$INSTALL_DIR/.venv/bin/quanta" ]] || die "Önce kurulum: sudo bash $0 --native"
    else
      [[ -x /usr/local/bin/quanta-compose ]] || die "Önce kurulum: sudo bash $0"
    fi
    step "Borsa erişim kontrolü"
    run_access_check
    exit 0
    ;;
esac

[[ $EUID -eq 0 ]] || die "root gerekli: sudo bash $0   (yalnız kontrol için: bash $0 --check-only)"
if [[ -z "${QUANTA_LOCK_HELD:-}" ]]; then
  exec 9>"$LOCK_FILE"
  flock -n 9 || die "Başka bir kurulum veya otomatik güncelleme çalışıyor; biraz sonra tekrar dene."
fi
touch "$LOG_FILE" && chmod 0600 "$LOG_FILE"
exec > >(tee -a "$LOG_FILE") 2>&1
say "quanta bootstrap — $(date -u '+%Y-%m-%d %H:%M:%S UTC') — $(hostname)"
preflight
((FAILURES)) && die "Ön kontrol engelleri var; yukarıya bak."
command -v apt-get >/dev/null 2>&1 || die "apt-get bulunamadı (Debian/Ubuntu gerekli)"
install_packages
install_docker
setup_tailscale
setup_dirs
fetch_repo
write_config
start_stack
setup_auto_update
serve_ui
summary
