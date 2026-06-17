#!/bin/bash
# ================================================================
# Daily English Article Pusher - One-Click Deploy Script
# ================================================================
# Usage:
#   chmod +x deploy.sh
#   sudo ./deploy.sh
#
# This script will:
#   1. Install system dependencies (Python3, pip, venv)
#   2. Create project directory at /opt/english-article
#   3. Copy all required files from current directory
#   4. Create Python virtual environment and install dependencies
#   5. Handle .env configuration (preserve existing if present)
#   6. Install systemd service and timer
#   7. Enable and start the timer (daily at 08:00)
#
# Run this script on your server via SSH.
# ================================================================

set -e

# --- Configuration ---
INSTALL_DIR="/opt/english-article"
SERVICE_USER="root"
PYTHON="${PYTHON:-python3}"

echo ""
echo "==============================================="
echo "  Daily English Article Pusher - Deploy"
echo "==============================================="
echo ""

# --- Step 1: Check running as root ---
if [ "$EUID" -ne 0 ]; then
  echo "[!] Not running as root. Some steps (systemd install) may require sudo."
fi

# --- Step 2: Check Python ---
echo "[1/7] Checking Python..."
if command -v $PYTHON &> /dev/null; then
  echo "  Python found: $($PYTHON --version)"
else
  echo "  [ERROR] Python3 not found. Installing..."
  apt-get update -qq && apt-get install -y -qq python3 python3-pip python3-venv
fi

# --- Step 3: Check pip ---
if ! command -v pip3 &> /dev/null && ! $PYTHON -m pip --version &> /dev/null; then
  echo "  [!] Installing pip..."
  apt-get install -y -qq python3-pip
fi

# --- Step 4: Create install directory ---
echo "[2/7] Creating install directory: $INSTALL_DIR"
mkdir -p "$INSTALL_DIR"
mkdir -p "$INSTALL_DIR/data"

# --- Step 5: Copy files ---
echo "[3/7] Copying project files..."
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [ -f "$SCRIPT_DIR/daily_english_article.py" ]; then
  cp "$SCRIPT_DIR/daily_english_article.py" "$INSTALL_DIR/"
  chmod +x "$INSTALL_DIR/daily_english_article.py"
  echo "  - daily_english_article.py"
else
  echo "  [ERROR] daily_english_article.py not found in current directory!"
  echo "  Please run this script from the same directory as the script files."
  exit 1
fi

if [ -f "$SCRIPT_DIR/requirements.txt" ]; then
  cp "$SCRIPT_DIR/requirements.txt" "$INSTALL_DIR/"
  echo "  - requirements.txt"
fi

# --- Step 6: Create virtualenv and install dependencies ---
echo "[4/7] Creating Python virtual environment..."
$PYTHON -m venv "$INSTALL_DIR/.venv"
source "$INSTALL_DIR/.venv/bin/activate"

echo "  Installing dependencies..."
pip install --quiet --upgrade pip
pip install --quiet -r "$INSTALL_DIR/requirements.txt"
deactivate

echo "  Done."

# --- Step 7: Configure .env ---
echo "[5/7] Configuring .env..."

if [ -f "$INSTALL_DIR/.env" ]; then
  echo "  .env already exists at $INSTALL_DIR/.env"
  echo "  Edit it if needed: nano $INSTALL_DIR/.env"
else
  if [ -f "$SCRIPT_DIR/.env" ]; then
    cp "$SCRIPT_DIR/.env" "$INSTALL_DIR/.env"
    echo "  .env copied from source directory to $INSTALL_DIR/.env"
  elif [ -f "$SCRIPT_DIR/.env.example" ]; then
    cp "$SCRIPT_DIR/.env.example" "$INSTALL_DIR/.env"
    echo "  .env.example copied to $INSTALL_DIR/.env"
  else
    cat > "$INSTALL_DIR/.env" << 'ENVEOF'
# Push mode: app (personal chat) or webhook (group chat)
FEISHU_PUSH_MODE=app

# --- Webhook mode ---
# FEISHU_WEBHOOK_URL=https://open.feishu.cn/open-apis/bot/v2/hook/xxxxxxxxxxxxxxx
# FEISHU_WEBHOOK_SECRET=

# --- App mode ---
FEISHU_APP_ID=cli_xxxxxxxxxxxxxxxxxxxx
FEISHU_APP_SECRET=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
FEISHU_RECEIVER_ID=ou_xxxxxxxxxxxxxxxxxxxx

# --- Translation ---
TRANSLATION_PROVIDER=libre
TRANSLATION_API_KEY=
TRANSLATION_API_URL=

# --- Data ---
DATA_DIR=./data
ENVEOF
    echo "  Created default .env at $INSTALL_DIR/.env"
  fi
  echo "  >>> IMPORTANT: Edit .env with your credentials:"
  echo "      nano $INSTALL_DIR/.env"
fi

# --- Step 8: Install systemd service ---
echo "[6/7] Installing systemd service..."

SERVICE_NAME="daily-english"
TIMER_NAME="daily-english"

# Service file
cat > "/etc/systemd/system/${SERVICE_NAME}.service" << 'SERVICEEOF'
[Unit]
Description=Daily English Article Pusher - Fetch and push articles to Feishu
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
ExecStart=/opt/english-article/.venv/bin/python /opt/english-article/daily_english_article.py
WorkingDirectory=/opt/english-article
User=root
Group=root
StandardOutput=append:/opt/english-article/logs/run.log
StandardError=append:/opt/english-article/logs/error.log
PrivateTmp=true
SERVICEEOF

# Timer file (runs every day at 8:00 AM)
cat > "/etc/systemd/system/${TIMER_NAME}.timer" << 'TIMEREOF'
[Unit]
Description=Daily English Article Pusher - Daily timer (8:00 AM)
Requires=daily-english.service

[Timer]
OnCalendar=*-*-* 08:00:00
Persistent=true
RandomizedDelaySec=300

[Install]
WantedBy=timers.target
TIMEREOF

# Create logs directory
mkdir -p "$INSTALL_DIR/logs"

# Reload systemd
systemctl daemon-reload

# Enable and start the timer
systemctl enable "${TIMER_NAME}.timer"
systemctl start "${TIMER_NAME}.timer"

echo "  systemd service and timer installed."

# --- Step 9: Test run ---
echo "[7/7] Running a quick test..."
echo ""
echo "  Checking .env configuration..."
echo ""

# Test: check if .env has been filled
if grep -q "cli_xxx\|FEISHU_APP_ID=$" "$INSTALL_DIR/.env" 2>/dev/null; then
  echo "  [!] .env appears to still have placeholder values."
  echo "      Please edit .env first before running:"
  echo "        nano $INSTALL_DIR/.env"
  echo "      Then test with:"
  echo "        systemctl start daily-english.service"
  echo "        journalctl -u daily-english.service -f"
else
  echo "  Running test..."
  systemctl start daily-english.service
  sleep 3
  systemctl status daily-english.service --no-pager -l 2>&1 | head -20
fi

# --- Summary ---
echo ""
echo "==============================================="
echo "  Deployment Complete!"
echo "==============================================="
echo ""
echo "  Install directory: $INSTALL_DIR"
echo ""
echo "  Commands:"
echo "    Edit config:    nano $INSTALL_DIR/.env"
echo "    Test run:       systemctl start daily-english.service"
echo "    View result:    systemctl status daily-english.service"
echo "    View logs:      journalctl -u daily-english.service -f"
echo "    View timer:     systemctl status daily-english.timer"
echo "    Next run time:  systemctl list-timers --all | grep daily-english"
echo "    Stop timer:     systemctl stop daily-english.timer"
echo "    Disable timer:  systemctl disable daily-english.timer"
echo ""
echo "  Logs:"
echo "    Run log:        $INSTALL_DIR/logs/run.log"
echo "    Error log:      $INSTALL_DIR/logs/error.log"
echo ""
echo "  Important:"
echo "    Before the first scheduled run, edit .env with your Feishu credentials!"
echo ""