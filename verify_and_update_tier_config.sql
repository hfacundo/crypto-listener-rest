-- =====================================================================
-- Tier Config Setup - SEGURO con Verificación
-- =====================================================================
-- Este script verifica que NO se eliminan campos importantes antes de
-- agregar tier_config al JSON de cada usuario.
--
-- IMPORTANTE: jsonb_set AGREGA el campo sin eliminar los existentes
-- =====================================================================

-- =====================================================================
-- PASO 1: Verificar estructura actual COMPLETA
-- =====================================================================
\echo '═══════════════════════════════════════════════════════════════'
\echo 'PASO 1: Verificación de estructura actual'
\echo '═══════════════════════════════════════════════════════════════'

SELECT
    user_id,
    strategy,
    jsonb_pretty(rules_config) as current_rules
FROM user_rules
WHERE strategy = 'archer_dual'
ORDER BY user_id;

-- =====================================================================
-- PASO 2: Verificar campos críticos que NO deben perderse
-- =====================================================================
\echo ''
\echo '═══════════════════════════════════════════════════════════════'
\echo 'PASO 2: Verificación de campos críticos existentes'
\echo '═══════════════════════════════════════════════════════════════'

SELECT
    user_id,
    CASE WHEN rules_config ? 'enabled' THEN '✅' ELSE '❌' END as has_enabled,
    CASE WHEN rules_config ? 'min_rr' THEN '✅' ELSE '❌' END as has_min_rr,
    CASE WHEN rules_config ? 'risk_pct' THEN '✅' ELSE '❌' END as has_risk_pct,
    CASE WHEN rules_config ? 'max_leverage' THEN '✅' ELSE '❌' END as has_max_leverage,
    CASE WHEN rules_config ? 'use_guardian' THEN '✅' ELSE '❌' END as has_use_guardian,
    CASE WHEN rules_config ? 'sqs_config' THEN '✅' ELSE '❌' END as has_sqs_config,
    CASE WHEN rules_config ? 'circuit_breaker' THEN '✅' ELSE '❌' END as has_circuit_breaker,
    CASE WHEN rules_config ? 'tier_config' THEN '✅ (ya existe)' ELSE '❌ (no existe - OK)' END as has_tier_config
FROM user_rules
WHERE strategy = 'archer_dual'
ORDER BY user_id;

-- =====================================================================
-- PASO 3: Crear BACKUP temporal antes de modificar
-- =====================================================================
\echo ''
\echo '═══════════════════════════════════════════════════════════════'
\echo 'PASO 3: Creando backup temporal'
\echo '═══════════════════════════════════════════════════════════════'

CREATE TEMP TABLE user_rules_backup AS
SELECT * FROM user_rules WHERE strategy = 'archer_dual';

SELECT 'Backup creado: ' || count(*) || ' registros' as backup_status
FROM user_rules_backup;

-- =====================================================================
-- PASO 4: Aplicar cambios (jsonb_set AGREGA sin eliminar)
-- =====================================================================
\echo ''
\echo '═══════════════════════════════════════════════════════════════'
\echo 'PASO 4: Aplicando tier_config (jsonb_set preserva campos existentes)'
\echo '═══════════════════════════════════════════════════════════════'

-- HUFSA - Agresivo (tier 1-9)
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

-- FUTURES - Agresivo (tier 1-9)
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

-- COPY_TRADING - Conservador (tier 1-7)
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

-- COPY_2 - Conservador (tier 1-7)
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
-- PASO 5: Verificar que NO se perdieron campos críticos
-- =====================================================================
\echo ''
\echo '═══════════════════════════════════════════════════════════════'
\echo 'PASO 5: Verificación POST-UPDATE - Campos críticos intactos?'
\echo '═══════════════════════════════════════════════════════════════'

SELECT
    user_id,
    CASE WHEN rules_config ? 'enabled' THEN '✅' ELSE '❌ PERDIDO!' END as has_enabled,
    CASE WHEN rules_config ? 'min_rr' THEN '✅' ELSE '❌ PERDIDO!' END as has_min_rr,
    CASE WHEN rules_config ? 'risk_pct' THEN '✅' ELSE '❌ PERDIDO!' END as has_risk_pct,
    CASE WHEN rules_config ? 'max_leverage' THEN '✅' ELSE '❌ PERDIDO!' END as has_max_leverage,
    CASE WHEN rules_config ? 'use_guardian' THEN '✅' ELSE '❌ PERDIDO!' END as has_use_guardian,
    CASE WHEN rules_config ? 'sqs_config' THEN '✅' ELSE '❌ PERDIDO!' END as has_sqs_config,
    CASE WHEN rules_config ? 'circuit_breaker' THEN '✅' ELSE '❌ PERDIDO!' END as has_circuit_breaker,
    CASE WHEN rules_config ? 'tier_config' THEN '✅ AGREGADO' ELSE '❌ FALLÓ!' END as has_tier_config
FROM user_rules
WHERE strategy = 'archer_dual'
ORDER BY user_id;

-- =====================================================================
-- PASO 6: Mostrar tier_config agregado
-- =====================================================================
\echo ''
\echo '═══════════════════════════════════════════════════════════════'
\echo 'PASO 6: Configuración tier_config agregada'
\echo '═══════════════════════════════════════════════════════════════'

SELECT
    user_id,
    rules_config->'tier_config'->>'enabled' as tier_enabled,
    rules_config->'tier_config'->>'max_tier_accepted' as max_tier,
    rules_config->'tier_config'->>'description' as description
FROM user_rules
WHERE strategy = 'archer_dual'
ORDER BY user_id;

-- =====================================================================
-- PASO 7: Comparar número de keys ANTES vs DESPUÉS
-- =====================================================================
\echo ''
\echo '═══════════════════════════════════════════════════════════════'
\echo 'PASO 7: Conteo de keys JSON (debería aumentar en 1)'
\echo '═══════════════════════════════════════════════════════════════'

SELECT
    r.user_id,
    jsonb_object_keys(b.rules_config) as keys_count_before,
    jsonb_object_keys(r.rules_config) as keys_count_after
FROM user_rules r
JOIN user_rules_backup b ON r.user_id = b.user_id AND r.strategy = b.strategy
WHERE r.strategy = 'archer_dual'
ORDER BY r.user_id;

\echo ''
\echo '═══════════════════════════════════════════════════════════════'
\echo '✅ VERIFICACIÓN COMPLETA'
\echo '═══════════════════════════════════════════════════════════════'
\echo ''
\echo 'Si ves ❌ PERDIDO en PASO 5, ejecuta el ROLLBACK abajo'
\echo 'Si todo muestra ✅, los cambios fueron exitosos'
\echo ''

-- =====================================================================
-- ROLLBACK (Solo si algo salió mal)
-- =====================================================================
-- Para revertir cambios, descomentar y ejecutar:
--
-- BEGIN;
-- UPDATE user_rules r
-- SET rules_config = b.rules_config
-- FROM user_rules_backup b
-- WHERE r.user_id = b.user_id
--   AND r.strategy = b.strategy
--   AND r.strategy = 'archer_dual';
-- COMMIT;
--
-- SELECT 'Rollback completado' as status;
