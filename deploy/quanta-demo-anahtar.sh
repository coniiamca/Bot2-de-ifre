#!/usr/bin/env bash
# Binance DEMO hesabının API anahtarını bu sunucuya ekler ve demo işlem servisini başlatır.
#
#   sudo quanta-demo-anahtar          # önerilen: Ed25519 (özel anahtar bu sunucudan hiç çıkmaz)
#   sudo quanta-demo-anahtar --hmac   # demo Ed25519 kabul etmezse: HMAC anahtarı
#
# Hiçbir anahtar ekrana, loga ya da git'e yazılmaz; dosyalar yalnız quanta kullanıcısının
# okuyabileceği şekilde (0400) /etc/quanta/secrets altına kaydedilir.
set -euo pipefail

SECRETS=/etc/quanta/secrets
PRIV="$SECRETS/binance_demo_ed25519.pem"
API="$SECRETS/binance_demo_api_key"
HMAC="$SECRETS/binance_demo_hmac_secret"
CONF=/etc/quanta/trader-demo.yaml
Q=/opt/quanta/.venv/bin/quanta
USER_NAME=quanta
UNIT=quanta-trader-demo

say() { printf '%s\n' "$*"; }
die() { printf 'HATA: %s\n' "$*" >&2; exit 1; }

[[ $EUID -eq 0 ]] || die "sudo ile çalıştır: sudo quanta-demo-anahtar"
[[ -x "$Q" ]] || die "quanta kurulu değil (önce: sudo bash bootstrap.sh --native)"
[[ -f "$CONF" ]] || die "$CONF yok (bootstrap.sh'yi tekrar çalıştır)"

mode=ed25519
[[ "${1:-}" == "--hmac" ]] && mode=hmac

write_secret() {  # path value
  local tmp="$1.tmp"
  (umask 377 && printf '%s' "$2" >"$tmp")
  chown "$USER_NAME:$USER_NAME" "$tmp"
  mv -f "$tmp" "$1"
}

say ""
say "Binance DEMO hesabı (gerçek para yok) için API anahtarı kurulumu"
say "-----------------------------------------------------------------"
if [[ $mode == ed25519 ]]; then
  if [[ ! -f "$PRIV" ]]; then
    sudo -u "$USER_NAME" "$Q" trader keygen --out "$PRIV" >/dev/null
  fi
  say "1) Tarayıcıda https://demo.binance.com adresini aç ve Binance hesabınla giriş yap."
  say "2) Profil menüsü → API Management → Create API → \"Self-generated\" seç."
  say "3) Açık anahtar (public key) alanına AŞAĞIDAKİ bloğu olduğu gibi yapıştır:"
  say ""
  sudo -u "$USER_NAME" "$Q" trader keygen --out "$PRIV"
  say ""
  say "4) İzinler: yalnız vadeli işlem (Futures/trading). Para çekme KAPALI kalsın."
  say "5) Binance'in gösterdiği API Key'i kopyala ve aşağıya yapıştır (ekranda görünmez)."
else
  say "1) https://demo.binance.com → API Management → Create API → \"System generated\"."
  say "2) İzinler: yalnız vadeli işlem. Para çekme KAPALI."
  say "3) API Key ve Secret Key'i sırayla aşağıya yapıştır (ekranda görünmez)."
fi
say ""
read -r -s -p "API Key: " api_key
say ""
[[ ${#api_key} -ge 16 ]] || die "API Key çok kısa görünüyor; tekrar dene."
write_secret "$API" "$api_key"
unset api_key
if [[ $mode == hmac ]]; then
  read -r -s -p "Secret Key: " secret
  say ""
  [[ ${#secret} -ge 16 ]] || die "Secret Key çok kısa görünüyor; tekrar dene."
  write_secret "$HMAC" "$secret"
  unset secret
  sed -i "s|^  type: .*|  type: hmac|; s|^  secret_file: .*|  secret_file: $HMAC|" "$CONF"
else
  sed -i "s|^  type: .*|  type: ed25519|; s|^  secret_file: .*|  secret_file: $PRIV|" "$CONF"
fi

say "Bağlantı deneniyor (emir gönderilmez)…"
if out="$(sudo -u "$USER_NAME" "$Q" trader check --config "$CONF" 2>&1)"; then
  say "Tamam: $out"
  systemctl enable "$UNIT" >/dev/null 2>&1 || true
  systemctl restart "$UNIT"
  say ""
  say "Demo işlem servisi başladı. Durum sayfasındaki \"Demo işlem\" bölümünü izle:"
  say "ilk test çevrimi birkaç dakika içinde, tatbikatlar 10 dakika sonra görünür."
else
  say "Bağlantı başarısız: $out"
  if [[ $mode == ed25519 ]]; then
    say "Demo ortamı Ed25519 anahtarını kabul etmiyorsa HMAC ile dene: sudo quanta-demo-anahtar --hmac"
  fi
  exit 1
fi
