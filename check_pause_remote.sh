#!/bin/bash
# Script para consultar el estado de pausa desde el servidor remoto
# Ejecutar desde el servidor donde corre crypto-listener-rest

USER_ID="${1:-hufsa}"
STRATEGY="${2:-archer_model}"

echo "=========================================="
echo "Checking Daily Loss Pause for $USER_ID"
echo "=========================================="
echo ""

# 1. Verificar pausa en Redis
echo "📍 Redis Pause Status:"
redis-cli GET "user_risk:${USER_ID}:${STRATEGY}:daily_loss_pause"
echo ""

# 2. Calcular P&L diario desde PostgreSQL
echo "📊 Today's P&L (since midnight UTC):"
psql $DATABASE_URL_CRYPTO_TRADER -c "
SELECT
    COUNT(*) as total_trades,
    COALESCE(SUM(pnl_pct), 0) as daily_pnl_pct,
    COALESCE(SUM(pnl_usdt), 0) as daily_pnl_usdt,
    SUM(CASE WHEN pnl_pct > 0 THEN 1 ELSE 0 END) as wins,
    SUM(CASE WHEN pnl_pct < 0 THEN 1 ELSE 0 END) as losses
FROM trade_history
WHERE user_id = '${USER_ID}'
  AND strategy = '${STRATEGY}'
  AND exit_time >= DATE_TRUNC('day', NOW() AT TIME ZONE 'UTC')
  AND exit_reason IN ('target_hit', 'stop_hit', 'timeout', 'manual_close');
"

echo ""
echo "📋 Today's Closed Trades:"
psql $DATABASE_URL_CRYPTO_TRADER -c "
SELECT
    symbol,
    direction,
    exit_reason,
    ROUND(pnl_pct::numeric, 2) as pnl_pct,
    ROUND(pnl_usdt::numeric, 2) as pnl_usdt,
    TO_CHAR(exit_time, 'HH24:MI:SS') as exit_time
FROM trade_history
WHERE user_id = '${USER_ID}'
  AND strategy = '${STRATEGY}'
  AND exit_time >= DATE_TRUNC('day', NOW() AT TIME ZONE 'UTC')
  AND exit_reason IN ('target_hit', 'stop_hit', 'timeout', 'manual_close')
ORDER BY exit_time DESC;
"

echo ""
echo "=========================================="
