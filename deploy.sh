#!/bin/bash
#
# Deployment script for crypto-listener-rest
# This script sets up the service on an EC2 instance
#

set -e  # Exit on error

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}crypto-listener-rest Deployment Script${NC}"
echo -e "${GREEN}========================================${NC}"

# Configuration
SERVICE_NAME="crypto-listener"
INSTALL_DIR="/home/ubuntu/crypto-listener-rest"
VENV_DIR="$INSTALL_DIR/venv"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"

# Check if running as correct user
if [ "$EUID" -eq 0 ]; then
   echo -e "${RED}Error: Do not run this script as root. Run as ubuntu user.${NC}"
   echo "Use: ./deploy.sh"
   exit 1
fi

echo -e "${YELLOW}Step 1: Checking Python version...${NC}"
python3 --version
if [ $? -ne 0 ]; then
    echo -e "${RED}Error: Python 3 is not installed${NC}"
    exit 1
fi

echo -e "${YELLOW}Step 2: Installing system dependencies...${NC}"
sudo apt-get update
sudo apt-get install -y python3-pip python3-venv

echo -e "${YELLOW}Step 3: Creating installation directory...${NC}"
sudo mkdir -p $INSTALL_DIR
sudo chown ubuntu:ubuntu $INSTALL_DIR

echo -e "${YELLOW}Step 4: Copying files to $INSTALL_DIR...${NC}"
# Copy files from current directory to install directory
rsync -av --exclude 'venv' --exclude '__pycache__' --exclude '.git' \
    $(pwd)/ $INSTALL_DIR/

echo -e "${YELLOW}Step 5: Creating Python virtual environment...${NC}"
if [ ! -d "$VENV_DIR" ]; then
    python3 -m venv $VENV_DIR
else
    echo "Virtual environment already exists"
fi

echo -e "${YELLOW}Step 6: Installing Python dependencies...${NC}"
$VENV_DIR/bin/pip install --upgrade pip
$VENV_DIR/bin/pip install -r $INSTALL_DIR/requirements.txt

echo -e "${YELLOW}Step 7: Setting up environment variables...${NC}"
echo -e "${YELLOW}⚠️  Important: Edit the service file to add your DATABASE_URL_CRYPTO_TRADER${NC}"
echo -e "${YELLOW}   sudo nano /etc/systemd/system/${SERVICE_NAME}.service${NC}"
echo ""
read -p "Press Enter to continue after noting this..."

echo -e "${YELLOW}Step 8: Installing systemd service...${NC}"
sudo cp $INSTALL_DIR/crypto-listener.service $SERVICE_FILE
sudo systemctl daemon-reload

echo -e "${YELLOW}Step 9: Testing database connection...${NC}"
echo "Testing if PostgreSQL is accessible..."
export DATABASE_URL_CRYPTO_TRADER="postgresql://app_user:YOUR_PASSWORD@localhost:5432/crypto_trader"
$VENV_DIR/bin/python3 -c "
from sqlalchemy import create_engine, text
import os
try:
    engine = create_engine(os.environ['DATABASE_URL'])
    with engine.connect() as conn:
        result = conn.execute(text('SELECT 1'))
        print('✅ Database connection successful')
except Exception as e:
    print(f'❌ Database connection failed: {e}')
    print('Please update DATABASE_URL in the service file')
" || echo -e "${RED}Database test failed - please configure DATABASE_URL in service file${NC}"

echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}Deployment completed!${NC}"
echo -e "${GREEN}========================================${NC}"
echo ""
echo -e "${YELLOW}Next steps:${NC}"
echo ""
echo "1. Edit the service file to configure DATABASE_URL:"
echo "   ${GREEN}sudo nano $SERVICE_FILE${NC}"
echo ""
echo "2. Start the service:"
echo "   ${GREEN}sudo systemctl start ${SERVICE_NAME}${NC}"
echo ""
echo "3. Enable auto-start on boot:"
echo "   ${GREEN}sudo systemctl enable ${SERVICE_NAME}${NC}"
echo ""
echo "4. Check service status:"
echo "   ${GREEN}sudo systemctl status ${SERVICE_NAME}${NC}"
echo ""
echo "5. View logs:"
echo "   ${GREEN}sudo journalctl -u ${SERVICE_NAME} -f${NC}"
echo ""
echo "6. Test the API:"
echo "   ${GREEN}curl http://localhost:8000/health${NC}"
echo ""
echo -e "${YELLOW}Optional - Update crypto-analyzer to call this API:${NC}"
echo '   response = requests.post('
echo '       "http://localhost:8000/execute-trade",'
echo '       json=trade_data,'
echo '       timeout=2'
echo '   )'
echo ""
