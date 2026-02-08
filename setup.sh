#!/bin/bash
# PoolSense — Raspberry Pi Setup Script
# Run this on a fresh Raspberry Pi OS Lite installation
# Usage: curl -sL https://raw.githubusercontent.com/kallgit-codex/poolsense/main/setup.sh | bash

set -e
echo "========================================="
echo "  PoolSense — Setup Script"
echo "========================================="

# Update system
echo "[1/6] Updating system..."
sudo apt update && sudo apt upgrade -y

# Install dependencies
echo "[2/6] Installing dependencies..."
sudo apt install -y python3-pip python3-smbus i2c-tools git

# Enable I2C and 1-Wire interfaces
echo "[3/6] Enabling I2C and 1-Wire..."
sudo raspi-config nonint do_i2c 0
echo "dtoverlay=w1-gpio" | sudo tee -a /boot/config.txt

# Install Python packages
echo "[4/6] Installing Python packages..."
pip3 install flask smbus2 requests --break-system-packages

# Clone PoolSense repo
echo "[5/6] Downloading PoolSense..."
cd /home/pi
git clone https://github.com/kallgit-codex/poolsense.git || true
cd poolsense

# Create systemd service for auto-start
echo "[6/6] Setting up auto-start service..."
sudo tee /etc/systemd/system/poolsense.service > /dev/null << 'EOF'
[Unit]
Description=PoolSense Leak Detection System
After=network.target

[Service]
ExecStart=/usr/bin/python3 /home/pi/poolsense/poolsense.py
WorkingDirectory=/home/pi/poolsense
User=pi
Restart=always
RestartSec=5
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable poolsense
sudo systemctl start poolsense

echo ""
echo "========================================="
echo "  PoolSense installed successfully!"
echo "  Dashboard: http://poolsense.local:8080"
echo "  "
echo "  Reboot to enable I2C + 1-Wire:"
echo "  sudo reboot"
echo "========================================="
