#!/bin/bash
# Raspberry Pi Setup Script for Trading Bot

echo "Setting up Trading Bot for Raspberry Pi..."

# 1. Update and install system dependencies if needed
sudo apt-get update
sudo apt-get install -y python3-pip python3-venv

# 2. Create and activate virtual environment
python3 -m venv venv
source venv/bin/activate

# 3. Install Python dependencies
echo "Installing Python requirements..."
pip install -r requirements.txt

# 4. Copy systemd service file
echo "Installing systemd service..."
sudo cp tradingbot.service /etc/systemd/system/

# 5. Reload systemd daemon
sudo systemctl daemon-reload

# 6. Enable service to start on boot
sudo systemctl enable tradingbot.service

echo ""
echo "Setup complete! "
echo "Don't forget to configure your .env file."
echo ""
echo "To start the bot, run:"
echo "  sudo systemctl start tradingbot"
echo "To check the status, run:"
echo "  sudo systemctl status tradingbot"
echo "To view logs, run:"
echo "  journalctl -u tradingbot -f"
