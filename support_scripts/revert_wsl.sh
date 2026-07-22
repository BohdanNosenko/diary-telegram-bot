#!/usr/bin/env bash
set -e

echo "[+] Reverting WSL Nix & Home Manager bootstrap workarounds..."

# Restore pure Nix config
echo "trusted-users = root wsl" | sudo tee /etc/nix/nix.custom.conf >/dev/null
sudo systemctl restart nix-daemon
echo "[+] Restored Nix HTTP/2 capabilities."

# Remove hardcoded HTTP/1.1 and TLS 1.2
sudo rm -f /root/.curlrc
rm -f ~/.curlrc ~/.npmrc
git config --global --unset http.version || true
git config --global --unset http.sslVersion || true
git config --global --unset http.postBuffer || true
echo "[+] Restored Git, Curl, and NPM to defaults."

# Restore optimal packet size
sudo ip link set dev eth0 mtu 1500 || true
echo "[+] Restored eth0 MTU to 1500."

echo "[+] All throttling removed! The system is back to full speed."
