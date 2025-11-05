#!/bin/bash
# ========================================
# Script para ejecutar en EC2
# Consulta estado de Daily Loss Pause
# ========================================

USER_ID="${1:-hufsa}"
STRATEGY="${2:-archer_dual}"

echo ""
echo "================================================================================"
echo "📊 DAILY LOSS PAUSE STATUS - ${USER_ID^^}/${STRATEGY^^}"
echo "================================================================================"
echo ""

# 1. Verificar pausa en Redis
PAUSE_KEY="user_risk:${USER_ID}:${STRATEGY}:daily_loss_pause"
PAUSE_UNTIL=$(redis-cli GET "$PAUSE_KEY")

echo "🔍 Redis Pause Key: $PAUSE_KEY"
if [ -n "$PAUSE_UNTIL" ]; then
    echo "🚨 PAUSE STATUS: ACTIVE"
    echo "   Pause ends at: $PAUSE_UNTIL"
    echo ""

    # Calcular tiempo restante usando Python
    python3 << EOF
from datetime import datetime, timezone
try:
    pause_until = datetime.fromisoformat("$PAUSE_UNTIL")
    now = datetime.now(timezone.utc)
    if now < pause_until:
        remaining = pause_until - now
        hours = remaining.total_seconds() / 3600
        minutes = (remaining.total_seconds() % 3600) / 60
        print(f"   ⏱️  Time remaining: {int(hours)}h {int(minutes)}m")

        # Calcular cuándo comenzó (asumiendo 12h de pausa)
        from datetime import timedelta
        pause_started = pause_until - timedelta(hours=12)
        elapsed = now - pause_started
        print(f"   ⏱️  Time elapsed: {elapsed.total_seconds()/3600:.1f}h")
        print(f"   📅 Pause started: {pause_started.strftime('%Y-%m-%d %H:%M:%S')} UTC")
    else:
        print(f"   ✅ Pause expired (ended at {pause_until.strftime('%Y-%m-%d %H:%M:%S')} UTC)")
except:
    print("   ⚠️  Could not parse pause timestamp")
EOF
else
    echo "✅ PAUSE STATUS: NO ACTIVE PAUSE"
fi

echo ""
echo "-------------------------------------------------------------------------------"
echo "📊 TODAY'S P&L (since midnight UTC)"
echo "-------------------------------------------------------------------------------"

# 2. Consultar P&L diario
psql "$DATABASE_URL_CRYPTO_TRADER" -t -A -F'|' << SQL
SELECT
    'Total Trades: ' || COUNT(*) || E'\n' ||
    'Winning Trades: ' || COALESCE(SUM(CASE WHEN pnl_pct > 0 THEN 1 ELSE 0 END), 0) || E'\n' ||
    'Losing Trades: ' || COALESCE(SUM(CASE WHEN pnl_pct < 0 THEN 1 ELSE 0 END), 0) || E'\n' ||
    'Daily P&L: ' || ROUND(COALESCE(SUM(pnl_pct), 0)::numeric, 2) || '% (' ||
    ROUND(COALESCE(SUM(pnl_usdt), 0)::numeric, 2) || ' USDT)'
FROM trade_history
WHERE user_id = '${USER_ID}'
  AND strategy = '${STRATEGY}'
  AND exit_time >= DATE_TRUNC('day', NOW() AT TIME ZONE 'UTC')
  AND exit_reason IN ('target_hit', 'stop_hit', 'timeout', 'manual_close');
SQL

echo ""
echo "-------------------------------------------------------------------------------"
echo "📋 TODAY'S CLOSED TRADES"
echo "-------------------------------------------------------------------------------"
echo ""

# 3. Listar trades cerrados hoy
psql "$DATABASE_URL_CRYPTO_TRADER" << SQL
\x off
\pset border 2
SELECT
    CASE
        WHEN pnl_pct > 0 THEN '✅'
        ELSE '❌'
    END as " ",
    symbol,
    direction as side,
    CASE exit_reason
        WHEN 'target_hit' THEN 'TARGET'
        WHEN 'stop_hit' THEN 'STOP'
        WHEN 'timeout' THEN 'TIMEOUT'
        ELSE exit_reason
    END as exit,
    ROUND(pnl_pct::numeric, 2) || '%' as "P&L %",
    '\$' || ROUND(pnl_usdt::numeric, 2) as "P&L USDT",
    TO_CHAR(exit_time, 'HH24:MI:SS') as time
FROM trade_history
WHERE user_id = '${USER_ID}'
  AND strategy = '${STRATEGY}'
  AND exit_time >= DATE_TRUNC('day', NOW() AT TIME ZONE 'UTC')
  AND exit_reason IN ('target_hit', 'stop_hit', 'timeout', 'manual_close')
ORDER BY exit_time DESC;
SQL

echo ""
echo "================================================================================"
echo ""
