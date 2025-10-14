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
    rules_config->'tier_config' as current_tier_config
FROM user_rules
WHERE strategy = 'archer_dual'
ORDER BY user_id;

-- =====================================================================
-- HUFSA - Agresivo (acepta tier 1-9)
-- =====================================================================
UPDATE user_rules
SET rules_config = jsonb_set(
    rules_config,
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
UPDATE user_rules
SET rules_config = jsonb_set(
    rules_config,
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
UPDATE user_rules
SET rules_config = jsonb_set(
    rules_config,
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
UPDATE user_rules
SET rules_config = jsonb_set(
    rules_config,
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
    rules_config->'tier_config'->>'enabled' as tier_filtering_enabled,
    rules_config->'tier_config'->>'max_tier_accepted' as max_tier,
    rules_config->'tier_config'->>'description' as description
FROM user_rules
WHERE strategy = 'archer_dual'
ORDER BY user_id;

-- =====================================================================
-- ROLLBACK (Si necesitas revertir cambios)
-- =====================================================================
-- UPDATE user_rules
-- SET rules_config = rules_config - 'tier_config'
-- WHERE strategy = 'archer_dual';
