#!/usr/bin/env bash

# Exit on error
set -e

echo "=== Fixing WSL MTU Issue ==="
# Lower MTU to fix SSL RECORD_LAYER_FAILURE during large downloads
sudo ip link set eth0 mtu 1350
echo "MTU fixed."

echo "=== Installing Docker System-wide ==="
# Add Docker's official GPG key
sudo apt-get update
sudo apt-get install -y ca-certificates curl
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc

# Add the repository to Apt sources
echo "deb [arch=amd64 signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/debian bookworm stable" | \
  sudo tee /etc/apt/sources.list.d/docker.list > /dev/null

sudo apt-get update

# Install Docker packages
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin

# Add current user to the docker group so you don't need sudo to run docker
sudo usermod -aG docker $USER

echo "=== Setup Complete ==="
echo "IMPORTANT: You may need to start the Docker daemon manually if systemd is not enabled in your WSL."
echo "To start it, run: sudo service docker start"
echo "Note: You will need to close and reopen your terminal (or run 'newgrp docker') for the docker group changes to take effect."
