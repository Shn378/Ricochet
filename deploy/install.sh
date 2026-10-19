#!/usr/bin/env bash
# One-off server setup for Ricochet. Run from the repo:  sudo bash deploy/install.sh
set -euo pipefail
APP="$(cd "$(dirname "$0")/.." && pwd)"
USER_NAME="${SUDO_USER:-$(whoami)}"

echo "== Packages"
apt-get update -y
apt-get install -y python3 git curl

echo "== Swap (helps the small free machine)"
if [ "$(free -m | awk '/Mem:/{print $2}')" -lt 1800 ] && ! swapon --show | grep -q /swapfile; then
  fallocate -l 1G /swapfile && chmod 600 /swapfile && mkswap /swapfile && swapon /swapfile
  grep -q /swapfile /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi

echo "== Config"
if [ ! -f "$APP/config.ini" ]; then
  cp "$APP/config.example.ini" "$APP/config.ini"
  read -r -p "NASA FIRMS map key (Enter to skip): " KEY </dev/tty || KEY=""
  if [ -n "$KEY" ]; then sed -i "s/^map_key *=.*/map_key = $KEY/" "$APP/config.ini"; fi
  chown "$USER_NAME": "$APP/config.ini"; chmod 600 "$APP/config.ini"
fi

echo "== Services"
sed -e "s#@APP@#$APP#g" -e "s#@USER@#$USER_NAME#g" "$APP/deploy/ricochet.service" > /etc/systemd/system/ricochet.service
sed -e "s#@APP@#$APP#g" -e "s#@USER@#$USER_NAME#g" "$APP/deploy/ricochet-update.service" > /etc/systemd/system/ricochet-update.service
cp "$APP/deploy/ricochet-update.timer" /etc/systemd/system/
cat > /etc/sudoers.d/ricochet <<SUDO
$USER_NAME ALL=(root) NOPASSWD: /usr/bin/systemctl restart ricochet
SUDO
chmod 440 /etc/sudoers.d/ricochet
systemctl daemon-reload
systemctl enable --now ricochet ricochet-update.timer

echo "== cloudflared (for the Cloudflare Tunnel)"
if ! command -v cloudflared >/dev/null; then
  mkdir -p --mode=0755 /usr/share/keyrings
  curl -fsSL https://pkg.cloudflare.com/cloudflare-main.gpg -o /usr/share/keyrings/cloudflare-main.gpg
  echo "deb [signed-by=/usr/share/keyrings/cloudflare-main.gpg] https://pkg.cloudflare.com/cloudflared any main" > /etc/apt/sources.list.d/cloudflared.list
  apt-get update -y && apt-get install -y cloudflared
fi

sleep 5
systemctl --no-pager status ricochet | head -5
echo
echo "Ricochet is running. Logs:  journalctl -u ricochet -f"
echo "Next: in Cloudflare Zero Trust create a tunnel pointing at http://localhost:8765 and run the install command it gives you."
