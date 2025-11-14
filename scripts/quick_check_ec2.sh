#!/bin/bash
# ========================================
# Quick check - versión simple
# Para ejecutar en EC2
# ========================================

USER_ID="${1:-hufsa}"
STRATEGY="${2:-archer_model}"

echo ""
echo "🔍 Quick Status Check for ${USER_ID}"
echo "========================================"
echo ""

# Redis pause
PAUSE_KEY="user_risk:${USER_ID}:${STRATEGY}:daily_loss_pause"
PAUSE_UNTIL=$(redis-cli GET "$PAUSE_KEY" 2>/dev/null)

if [ -n "$PAUSE_UNTIL" ]; then
    echo "⚠️  PAUSE: Active (ends at $PAUSE_UNTIL)"
else
    echo "✅ PAUSE: None"
fi

# Daily P&L
echo ""
echo "📊 Today's P&L:"
psql "$DATABASE_URL_CRYPTO_TRADER" -t -c "
SELECT
    '   Trades: ' || COUNT(*) ||
    ' | P&L: ' || ROUND(COALESCE(SUM(pnl_pct), 0)::numeric, 2) || '%' ||
    ' | Wins: ' || SUM(CASE WHEN pnl_pct > 0 THEN 1 ELSE 0 END) ||
    ' | Losses: ' || SUM(CASE WHEN pnl_pct < 0 THEN 1 ELSE 0 END)
FROM trade_history
WHERE user_id = '${USER_ID}'
  AND strategy = '${STRATEGY}'
  AND exit_time >= DATE_TRUNC('day', NOW() AT TIME ZONE 'UTC')
  AND exit_reason IN ('target_hit', 'stop_hit', 'timeout', 'manual_close');
"

echo ""
