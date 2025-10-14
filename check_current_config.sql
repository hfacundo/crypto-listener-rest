-- =====================================================================
-- Solo Verificación - NO modifica nada
-- =====================================================================
-- Este script SOLO muestra la configuración actual sin hacer cambios
-- =====================================================================

\echo '═══════════════════════════════════════════════════════════════'
\echo 'Configuración COMPLETA actual de cada usuario'
\echo '═══════════════════════════════════════════════════════════════'
\echo ''

SELECT
    user_id,
    strategy,
    jsonb_pretty(rules_config) as current_rules
FROM user_rules
WHERE strategy = 'archer_dual'
ORDER BY user_id;

\echo ''
\echo '═══════════════════════════════════════════════════════════════'
\echo 'Campos críticos presentes'
\echo '═══════════════════════════════════════════════════════════════'
\echo ''

SELECT
    user_id,
    CASE WHEN rules_config ? 'enabled' THEN '✅ enabled' ELSE '❌ enabled MISSING' END as enabled,
    CASE WHEN rules_config ? 'min_rr' THEN '✅ min_rr' ELSE '❌ min_rr MISSING' END as min_rr,
    CASE WHEN rules_config ? 'risk_pct' THEN '✅ risk_pct' ELSE '❌ risk_pct MISSING' END as risk_pct,
    CASE WHEN rules_config ? 'max_leverage' THEN '✅ max_leverage' ELSE '❌ max_leverage MISSING' END as max_leverage,
    CASE WHEN rules_config ? 'use_guardian' THEN '✅ use_guardian' ELSE '❌ use_guardian MISSING' END as use_guardian,
    CASE WHEN rules_config ? 'sqs_config' THEN '✅ sqs_config' ELSE '❌ sqs_config MISSING' END as sqs_config,
    CASE WHEN rules_config ? 'circuit_breaker' THEN '✅ circuit_breaker' ELSE '❌ circuit_breaker MISSING' END as circuit_breaker,
    CASE WHEN rules_config ? 'anti_repetition' THEN '✅ anti_repetition' ELSE '❌ anti_repetition MISSING' END as anti_repetition,
    CASE WHEN rules_config ? 'symbol_blacklist' THEN '✅ symbol_blacklist' ELSE '❌ symbol_blacklist MISSING' END as symbol_blacklist,
    CASE WHEN rules_config ? 'tier_config' THEN '⚠️ tier_config YA EXISTE' ELSE '📝 tier_config NO EXISTE (se agregará)' END as tier_config
FROM user_rules
WHERE strategy = 'archer_dual'
ORDER BY user_id;

\echo ''
\echo '═══════════════════════════════════════════════════════════════'
\echo 'Conteo total de keys JSON por usuario'
\echo '═══════════════════════════════════════════════════════════════'
\echo ''

SELECT
    user_id,
    jsonb_object_keys(rules_config) as total_keys
FROM user_rules
WHERE strategy = 'archer_dual'
ORDER BY user_id;

\echo ''
\echo '═══════════════════════════════════════════════════════════════'
\echo 'Estado de tier_config actual (si existe)'
\echo '═══════════════════════════════════════════════════════════════'
\echo ''

SELECT
    user_id,
    COALESCE(rules_config->'tier_config'->>'enabled', 'N/A') as tier_enabled,
    COALESCE(rules_config->'tier_config'->>'max_tier_accepted', 'N/A') as max_tier,
    COALESCE(rules_config->'tier_config'->>'description', 'N/A') as description
FROM user_rules
WHERE strategy = 'archer_dual'
ORDER BY user_id;
