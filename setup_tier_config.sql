-- =====================================================================
-- Tier Config Setup - Configuración por Usuario
-- =====================================================================
-- Este script actualiza la configuración de tier_config para cada usuario
-- en la base de datos crypto_trader.
--
-- IMPORTANTE: Ejecutar en PostgreSQL como usuario con permisos de UPDATE
-- =====================================================================

-- Verificar configuración actual
SELECT
    user_id,
    strategy,
    rules->'tier_config' as current_tier_config
FROM rules
WHERE strategy = 'archer_dual'
ORDER BY user_id;

-- =====================================================================
-- HUFSA - Agresivo (acepta tier 1-9)
-- =====================================================================
UPDATE rules
SET rules = jsonb_set(
    rules::jsonb,
    '{tier_config}',
    '{
      "enabled": true,
      "max_tier_accepted": 9,
      "description": "Aggressive - accept all viable trades (tier 1-9)"
    }'::jsonb
)
WHERE user_id = 'hufsa' AND strategy = 'archer_dual';

-- =====================================================================
-- FUTURES - Agresivo (acepta tier 1-9)
-- =====================================================================
UPDATE rules
SET rules = jsonb_set(
    rules::jsonb,
    '{tier_config}',
    '{
      "enabled": true,
      "max_tier_accepted": 9,
      "description": "Aggressive - accept all viable trades (tier 1-9)"
    }'::jsonb
)
WHERE user_id = 'futures' AND strategy = 'archer_dual';

-- =====================================================================
-- COPY_TRADING - Conservador (acepta tier 1-7)
-- =====================================================================
UPDATE rules
SET rules = jsonb_set(
    rules::jsonb,
    '{tier_config}',
    '{
      "enabled": true,
      "max_tier_accepted": 7,
      "description": "Conservative - only high quality trades (tier 1-7)"
    }'::jsonb
)
WHERE user_id = 'copy_trading' AND strategy = 'archer_dual';

-- =====================================================================
-- COPY_2 - Conservador (acepta tier 1-7)
-- =====================================================================
UPDATE rules
SET rules = jsonb_set(
    rules::jsonb,
    '{tier_config}',
    '{
      "enabled": true,
      "max_tier_accepted": 7,
      "description": "Conservative - only high quality trades (tier 1-7)"
    }'::jsonb
)
WHERE user_id = 'copy_2' AND strategy = 'archer_dual';

-- =====================================================================
-- Verificar cambios aplicados
-- =====================================================================
SELECT
    user_id,
    strategy,
    rules->'tier_config'->>'enabled' as tier_filtering_enabled,
    rules->'tier_config'->>'max_tier_accepted' as max_tier,
    rules->'tier_config'->>'description' as description
FROM rules
WHERE strategy = 'archer_dual'
ORDER BY user_id;

-- =====================================================================
-- ROLLBACK (Si necesitas revertir cambios)
-- =====================================================================
-- UPDATE rules
-- SET rules = rules::jsonb - 'tier_config'
-- WHERE strategy = 'archer_dual';
