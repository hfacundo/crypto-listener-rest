#!/bin/bash
#
# Script para verificar variables de entorno de crypto-listener-rest
#

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo "🔍 Verificando variables de entorno para crypto-listener-rest..."
echo ""

check_var() {
    local var_name=$1
    local var_value=${!var_name}

    if [ -z "$var_value" ]; then
        echo -e "${RED}❌ $var_name: NO DEFINIDA${NC}"
        return 1
    else
        # Ocultar valores sensibles
        if [[ $var_name == *"SECRET"* ]] || [[ $var_name == *"PASSWORD"* ]] || [[ $var_name == *"DATABASE_URL"* ]]; then
            echo -e "${GREEN}✅ $var_name: [HIDDEN]${NC}"
        else
            echo -e "${GREEN}✅ $var_name: $var_value${NC}"
        fi
        return 0
    fi
}

failed=0

echo "📊 Database:"
check_var "DATABASE_URL_CRYPTO_TRADER" || ((failed++))
echo ""

echo "📦 Redis:"
check_var "REDIS_HOST" || ((failed++))
check_var "REDIS_PORT" || ((failed++))
check_var "REDIS_DB" || ((failed++))
echo ""

echo "🌍 Environment:"
check_var "DEPLOYMENT_ENV" || ((failed++))
echo ""

echo "🔑 Binance API Keys (User 1 - COPY):"
check_var "BINANCE_FUTURES_API_KEY_COPY" || ((failed++))
check_var "BINANCE_FUTURES_API_SECRET_COPY" || ((failed++))
echo ""

echo "🔑 Binance API Keys (User 3 - HUFSA):"
check_var "BINANCE_FUTURES_API_KEY_HUFSA" || ((failed++))
check_var "BINANCE_FUTURES_API_SECRET_HUFSA" || ((failed++))
echo ""

echo "🔑 Binance API Keys (User 2 - COPY_2):"
check_var "BINANCE_FUTURES_API_KEY_COPY_2" || ((failed++))
check_var "BINANCE_FUTURES_API_SECRET_COPY_2" || ((failed++))
echo ""

echo "🔑 Binance API Keys (User 4 - FUTURES):"
check_var "BINANCE_FUTURES_API_KEY_FUTURES" || ((failed++))
check_var "BINANCE_FUTURES_API_SECRET_FUTURES" || ((failed++))
echo ""

echo "════════════════════════════════════════"
if [ $failed -eq 0 ]; then
    echo -e "${GREEN}🎉 Todas las variables están configuradas correctamente!${NC}"
    echo ""
    echo "Próximo paso:"
    echo "  cd ~/crypto-listener-rest"
    echo "  python test_integration.py"
    exit 0
else
    echo -e "${RED}⚠️  Faltan $failed variable(s)${NC}"
    echo ""
    echo "Para configurar las variables:"
    echo "  nano ~/.bashrc"
    echo ""
    echo "Después de editar:"
    echo "  source ~/.bashrc"
    echo "  ./check_env.sh"
    exit 1
fi
