#!/bin/bash

# =====================================================================
# Start Crypto Admin Panel
# =====================================================================
# Quick start script for the admin panel
# =====================================================================

echo "🎛️  Starting Crypto Trading Admin Panel..."
echo ""

# Check if DATABASE_URL_CRYPTO_TRADER is set
if [ -z "$DATABASE_URL_CRYPTO_TRADER" ]; then
    echo "⚠️  DATABASE_URL_CRYPTO_TRADER not set"
    echo "Setting default: postgresql://postgres:superhufsa@localhost:5433/crypto_trader"
    export DATABASE_URL_CRYPTO_TRADER="postgresql://postgres:superhufsa@localhost:5433/crypto_trader"
fi

# Check dependencies
echo "📦 Checking dependencies..."
python3 -c "import fastapi" 2>/dev/null || {
    echo "❌ FastAPI not installed"
    echo "Installing: pip install fastapi uvicorn psycopg2-binary"
    pip install fastapi uvicorn psycopg2-binary
}

# Check if static folder exists
if [ ! -d "static" ]; then
    echo "❌ static/ folder not found"
    echo "Please ensure static/index.html exists"
    exit 1
fi

# Start the server
echo ""
echo "✅ Starting admin panel..."
echo "📊 Dashboard: http://localhost:8080"
echo "📖 API Docs: http://localhost:8080/docs"
echo ""
echo "Default credentials:"
echo "  Username: admin"
echo "  Password: crypto2025!"
echo ""
echo "⚠️  IMPORTANT: Change credentials in admin_api.py before deploying!"
echo ""
echo "Press Ctrl+C to stop"
echo ""

python3 admin_api.py
