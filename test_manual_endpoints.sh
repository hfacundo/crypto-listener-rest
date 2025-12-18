#!/bin/bash

# Script para probar los endpoints de control manual de trading
# Uso: ./test_manual_endpoints.sh

set -e

BASE_URL="http://localhost:8000"
USER_ID="copy_trading"  # Cambiar según sea necesario
SYMBOL="BTCUSDT"

echo "=========================================="
echo "  Testing Manual Trading Endpoints"
echo "=========================================="
echo ""

# Colores para output
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

# Función para imprimir con color
print_test() {
    echo -e "${YELLOW}[TEST]${NC} $1"
}

print_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

print_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# Función para hacer request y mostrar resultado
make_request() {
    local method=$1
    local endpoint=$2
    local data=$3
    local description=$4

    print_test "$description"
    echo "Endpoint: $method $endpoint"
    echo "Data: $data"
    echo ""

    response=$(curl -s -X "$method" "$BASE_URL$endpoint" \
        -H "Content-Type: application/json" \
        -d "$data")

    echo "Response:"
    echo "$response" | jq '.' 2>/dev/null || echo "$response"
    echo ""

    # Check if success
    if echo "$response" | jq -e '.success == true' >/dev/null 2>&1; then
        print_success "Request successful"
    elif echo "$response" | jq -e '.success == false' >/dev/null 2>&1; then
        print_error "Request failed (expected in some cases)"
    fi
    echo ""
    echo "----------------------------------------"
    echo ""
}

# Test 1: Get current mark price (for reference)
print_test "Getting current mark price for $SYMBOL..."
MARK_PRICE=$(curl -s "https://fapi.binance.com/fapi/v1/premiumIndex?symbol=$SYMBOL" | jq -r '.markPrice')
echo "Current mark price for $SYMBOL: $MARK_PRICE"
echo ""
echo "----------------------------------------"
echo ""

# Test 2: Set Stop Loss
# Calcular SL 2% por debajo del mark price
SL_PRICE=$(echo "$MARK_PRICE * 0.98" | bc)
make_request "POST" "/set-stop-loss" \
    "{\"user_id\": \"$USER_ID\", \"symbol\": \"$SYMBOL\", \"stop_loss\": $SL_PRICE}" \
    "Setting stop loss to $SL_PRICE (2% below mark)"

# Test 3: Set Take Profit
# Calcular TP 4% por arriba del mark price
TP_PRICE=$(echo "$MARK_PRICE * 1.04" | bc)
make_request "POST" "/set-take-profit" \
    "{\"user_id\": \"$USER_ID\", \"symbol\": \"$SYMBOL\", \"take_profit\": $TP_PRICE}" \
    "Setting take profit to $TP_PRICE (4% above mark)"

# Test 4: Adjust both SL and TP
# Calcular nuevos valores
NEW_SL=$(echo "$MARK_PRICE * 0.97" | bc)
NEW_TP=$(echo "$MARK_PRICE * 1.05" | bc)
make_request "POST" "/adjust-sl-tp" \
    "{\"user_id\": \"$USER_ID\", \"symbol\": \"$SYMBOL\", \"stop_loss\": $NEW_SL, \"take_profit\": $NEW_TP}" \
    "Adjusting both SL ($NEW_SL) and TP ($NEW_TP)"

# Test 5: Try invalid SL (should fail)
INVALID_SL=$(echo "$MARK_PRICE * 1.02" | bc)  # SL arriba del mark (inválido para LONG)
make_request "POST" "/set-stop-loss" \
    "{\"user_id\": \"$USER_ID\", \"symbol\": \"$SYMBOL\", \"stop_loss\": $INVALID_SL}" \
    "Testing invalid SL (should fail validation)"

# Test 6: Close position (comentado por defecto para seguridad)
read -p "Do you want to CLOSE the position? (yes/no): " confirm
if [ "$confirm" == "yes" ]; then
    make_request "POST" "/close-position" \
        "{\"user_id\": \"$USER_ID\", \"symbol\": \"$SYMBOL\"}" \
        "Closing position for $SYMBOL"
else
    echo "Skipping position close test"
    echo ""
fi

echo "=========================================="
echo "  Testing Complete"
echo "=========================================="
echo ""
echo "Notes:"
echo "  - Some tests may fail if there's no open position"
echo "  - Invalid SL test should fail (this is expected)"
echo "  - Use 'jq' for better JSON formatting: apt-get install jq"
echo ""
