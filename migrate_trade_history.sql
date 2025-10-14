-- ============================================================================
-- MIGRACIÓN: trade_history - Separar user_id y strategy
-- ============================================================================
-- Este script migra la tabla trade_history de strategy_name combinado
-- a user_id y strategy separados, y agrega campos para order_ids de Binance.
--
-- IMPORTANTE: Ejecutar en servidor de producción después de desplegar el código
-- ============================================================================

BEGIN;

-- 1. Agregar nuevas columnas si no existen
ALTER TABLE trade_history
ADD COLUMN IF NOT EXISTS user_id VARCHAR(50),
ADD COLUMN IF NOT EXISTS strategy VARCHAR(50),
ADD COLUMN IF NOT EXISTS order_id BIGINT,
ADD COLUMN IF NOT EXISTS sl_order_id BIGINT,
ADD COLUMN IF NOT EXISTS tp_order_id BIGINT;

-- 2. Migrar datos existentes: separar strategy_name en user_id + strategy
--    Formato actual: "hufsa_archer_dual", "copy_trading_archer_dual"
--    Nuevo formato: user_id="hufsa", strategy="archer_dual"
UPDATE trade_history
SET
    user_id = SPLIT_PART(strategy_name, '_', 1),
    strategy = REGEXP_REPLACE(strategy_name, '^[^_]+_', '')
WHERE strategy_name IS NOT NULL
  AND (user_id IS NULL OR strategy IS NULL);

-- 3. Verificar que no haya NULLs después de la migración
SELECT
    COUNT(*) as total_rows,
    COUNT(user_id) as user_id_count,
    COUNT(strategy) as strategy_count,
    COUNT(*) - COUNT(user_id) as null_user_ids,
    COUNT(*) - COUNT(strategy) as null_strategies
FROM trade_history;

-- 4. Si todo está bien, hacer las columnas NOT NULL
ALTER TABLE trade_history
ALTER COLUMN user_id SET NOT NULL,
ALTER COLUMN strategy SET NOT NULL;

-- 5. Eliminar columna strategy_name antigua (opcional - comentar si quieres mantener backup)
-- ALTER TABLE trade_history DROP COLUMN IF EXISTS strategy_name;

-- 6. Crear índices nuevos
CREATE INDEX IF NOT EXISTS idx_trade_history_user_strategy
    ON trade_history(user_id, strategy, entry_time DESC);

CREATE INDEX IF NOT EXISTS idx_trade_history_order_id
    ON trade_history(order_id);

-- 7. Eliminar índice antiguo si existe
DROP INDEX IF EXISTS idx_trade_history_strategy;

-- 8. Verificar resultado final
SELECT
    user_id,
    strategy,
    COUNT(*) as trade_count,
    MIN(entry_time) as first_trade,
    MAX(entry_time) as last_trade
FROM trade_history
GROUP BY user_id, strategy
ORDER BY user_id, strategy;

COMMIT;

-- ============================================================================
-- VERIFICACIONES POST-MIGRACIÓN
-- ============================================================================

-- Verificar que todos los trades tienen user_id y strategy
SELECT
    CASE
        WHEN user_id IS NULL THEN '❌ NULL user_id'
        WHEN strategy IS NULL THEN '❌ NULL strategy'
        ELSE '✅ OK'
    END as status,
    COUNT(*) as count
FROM trade_history
GROUP BY 1;

-- Ver distribución por usuario y estrategia
SELECT
    user_id,
    strategy,
    COUNT(*) as total_trades,
    SUM(CASE WHEN exit_reason = 'active' THEN 1 ELSE 0 END) as active_trades,
    SUM(CASE WHEN exit_reason = 'target_hit' THEN 1 ELSE 0 END) as targets_hit,
    SUM(CASE WHEN exit_reason = 'stop_hit' THEN 1 ELSE 0 END) as stops_hit
FROM trade_history
GROUP BY user_id, strategy
ORDER BY user_id, strategy;

-- Verificar order_ids (serán NULL para trades antiguos, OK para nuevos)
SELECT
    CASE
        WHEN order_id IS NOT NULL THEN 'Con order_id'
        ELSE 'Sin order_id (trade antiguo)'
    END as has_order_id,
    COUNT(*) as count,
    MIN(entry_time) as oldest_trade,
    MAX(entry_time) as newest_trade
FROM trade_history
GROUP BY 1;
