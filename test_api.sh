#!/bin/bash
#
# Simple API test script for crypto-listener-rest
#

API_URL="http://localhost:8000"

echo "Testing crypto-listener-rest API..."
echo ""

# Test 1: Health check
echo "1. Health Check"
echo "   GET $API_URL/health"
curl -s $API_URL/health | python3 -m json.tool
echo ""
echo ""

# Test 2: Stats
echo "2. Statistics"
echo "   GET $API_URL/stats"
curl -s $API_URL/stats | python3 -m json.tool
echo ""
echo ""

# Test 3: Root endpoint
echo "3. Root Endpoint"
echo "   GET $API_URL/"
curl -s $API_URL/ | python3 -m json.tool
echo ""
echo ""

echo "Basic tests completed!"
echo ""
echo "To test trade execution (⚠️  will execute real trades):"
echo ""
echo "curl -X POST $API_URL/execute-trade \\"
echo "  -H 'Content-Type: application/json' \\"
echo "  -d '{"
echo "    \"symbol\": \"BTCUSDT\","
echo "    \"entry\": 45000.0,"
echo "    \"stop\": 44500.0,"
echo "    \"target\": 46000.0,"
echo "    \"trade\": \"LONG\","
echo "    \"rr\": 2.0,"
echo "    \"probability\": 75.0,"
echo "    \"signal_quality_score\": 8.5"
echo "  }'"
echo ""
