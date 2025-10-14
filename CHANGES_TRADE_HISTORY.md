# Cambios en trade_history - Unificación de Tabla

## 📋 Resumen

Se unificó el almacenamiento de trades en la tabla `trade_history`, separando `user_id` y `strategy` (que estaban combinados en `strategy_name`), y agregando campos `order_id`, `sl_order_id`, `tp_order_id` para tracking completo de órdenes de Binance.

**Problema anterior:**
- Tabla `trades` tenía los campos correctos pero **nunca se usaba** (función `save_trade()` nunca se llamaba)
- Tabla `trade_history` se usaba activamente pero:
  - ❌ NO tenía `order_id` (necesario para tracking de Binance)
  - ❌ Combinaba `user_id` + `strategy` en un solo campo `strategy_name` (ej: "hufsa_archer_dual")
  - ❌ NO tenía columnas separadas para `user_id` y `strategy`

---

## ✅ Cambios Realizados

### 1. **Esquema de `trade_history`** (`app/utils/trade_protection.py:76-130`)

**Nuevos campos agregados:**
```sql
user_id VARCHAR(50) NOT NULL,          -- Separado de strategy_name
strategy VARCHAR(50) NOT NULL,          -- Separado de strategy_name
order_id BIGINT,                        -- Order ID de Binance (entry)
sl_order_id BIGINT,                     -- Order ID de Binance (stop loss)
tp_order_id BIGINT,                     -- Order ID de Binance (take profit)
```

**Migración automática incluida:**
```sql
-- Separa strategy_name existente en user_id + strategy
UPDATE trade_history
SET
    user_id = SPLIT_PART(strategy_name, '_', 1),
    strategy = REGEXP_REPLACE(strategy_name, '^[^_]+_', '')
WHERE user_id IS NULL OR strategy IS NULL;

-- Elimina columna strategy_name antigua
ALTER TABLE trade_history DROP COLUMN IF EXISTS strategy_name;
```

**Nuevos índices:**
```sql
CREATE INDEX idx_trade_history_user_strategy ON trade_history(user_id, strategy, entry_time DESC);
CREATE INDEX idx_trade_history_order_id ON trade_history(order_id);
```

---

### 2. **Función `record_trade()`** (`app/utils/trade_protection.py:240-304`)

**Cambios en signature:**
```python
# ANTES:
def record_trade(self, strategy_name: str, symbol: str, ...)

# AHORA:
def record_trade(
    self,
    user_id: str,           # ✅ Nuevo: separado
    strategy: str,          # ✅ Nuevo: separado
    symbol: str,
    ...
    order_id: int = None,      # ✅ Nuevo: Binance order ID
    sl_order_id: int = None,   # ✅ Nuevo: Binance SL order ID
    tp_order_id: int = None    # ✅ Nuevo: Binance TP order ID
)
```

**Cambios en INSERT:**
```python
# ANTES:
INSERT INTO trade_history (
    strategy_name, symbol, direction, ...
) VALUES (%s, %s, %s, ...)

# AHORA:
INSERT INTO trade_history (
    user_id, strategy, symbol, direction, ...,
    order_id, sl_order_id, tp_order_id
) VALUES (%s, %s, %s, %s, ..., %s, %s, %s)
```

---

### 3. **Funciones actualizadas en `trade_protection.py`**

Todas las siguientes funciones ahora reciben `user_id` y `strategy` separados:

| Función | Línea | Cambio |
|---------|-------|--------|
| `should_block_repetition()` | 173 | `user_id, strategy` en lugar de `strategy_name` |
| `should_activate_circuit_breaker()` | 469 | `user_id, strategy` en lugar de `strategy_name` |
| `get_symbol_stats()` | 584 | `user_id, strategy` en lugar de `strategy_name` |
| `should_block_symbol()` | 671 | `user_id, strategy` en lugar de `strategy_name` |
| `update_trade_exit()` | 312 | `user_id, strategy` en lugar de `strategy_key` |
| `get_symbol_performance_report()` | 757 | `user_id, strategy` en lugar de `strategy_name` |

**Queries actualizados:**
```sql
-- ANTES:
WHERE strategy_name = %s

-- AHORA:
WHERE user_id = %s AND strategy = %s
```

---

### 4. **`user_risk_validator.py`** - Llamadas actualizadas

**`_get_daily_pnl_pct()`** (línea 369):
```python
# ANTES:
WHERE strategy_name = %s
params: (f"{self.user_id}_{self.strategy}",)

# AHORA:
WHERE user_id = %s AND strategy = %s
params: (self.user_id, self.strategy)
```

**`record_trade_opened()`** (línea 587):
```python
# ANTES:
def record_trade_opened(self, symbol, direction, ...)

# AHORA:
def record_trade_opened(
    self,
    symbol, direction, ...,
    order_id: int = None,      # ✅ Nuevo
    sl_order_id: int = None,   # ✅ Nuevo
    tp_order_id: int = None    # ✅ Nuevo
)
```

**Llamadas a `protection_system`** actualizadas:
```python
# Línea 158: Circuit Breaker
self.protection_system.should_activate_circuit_breaker(
    user_id=self.user_id,
    strategy=self.strategy,
    ...
)

# Línea 200: Anti-Repetition
self.protection_system.should_block_repetition(
    user_id=self.user_id,
    strategy=self.strategy,
    ...
)

# Línea 224: Symbol Blacklist
self.protection_system.should_block_symbol(
    user_id=self.user_id,
    strategy=self.strategy,
    ...
)

# Línea 610: Record Trade
self.protection_system.record_trade(
    user_id=self.user_id,
    strategy=self.strategy,
    ...,
    order_id=order_id,
    sl_order_id=sl_order_id,
    tp_order_id=tp_order_id
)
```

---

### 5. **`main.py`** - Pasar order_ids (línea 257-275)

```python
# Extraer order_ids del resultado de Binance
order_id = order.get("order_id")
sl_order_id = order.get("sl_order_id")
tp_order_id = order.get("tp_order_id")

trade_id = validator.record_trade_opened(
    symbol=symbol,
    direction=direction,
    entry_time=datetime.now(timezone.utc),
    entry_price=entry_price,
    stop_price=stop_loss,
    target_price=target_price,
    probability=probability,
    sqs=signal_quality_score,
    rr=rr,
    order_id=order_id,           # ✅ Nuevo
    sl_order_id=sl_order_id,     # ✅ Nuevo
    tp_order_id=tp_order_id      # ✅ Nuevo
)
```

---

## 🚀 Despliegue

### Paso 1: Desplegar código actualizado

```bash
# En servidor EC2
cd /path/to/crypto-listener-rest
git pull origin main

# Reiniciar servicio
sudo systemctl restart crypto-listener
```

### Paso 2: Ejecutar migración SQL

```bash
# Conectar a PostgreSQL
psql $DATABASE_URL_CRYPTO_TRADER -f migrate_trade_history.sql
```

**La migración hace:**
1. ✅ Agrega columnas: `user_id`, `strategy`, `order_id`, `sl_order_id`, `tp_order_id`
2. ✅ Migra datos existentes: separa `strategy_name` en `user_id` + `strategy`
3. ✅ Verifica que no haya NULLs
4. ✅ Hace columnas NOT NULL
5. ✅ Crea índices nuevos
6. ✅ Opcional: elimina columna `strategy_name` antigua

### Paso 3: Verificar

```sql
-- Ver trades por usuario y estrategia
SELECT
    user_id,
    strategy,
    COUNT(*) as total_trades,
    SUM(CASE WHEN exit_reason = 'active' THEN 1 ELSE 0 END) as active,
    SUM(CASE WHEN exit_reason = 'target_hit' THEN 1 ELSE 0 END) as targets
FROM trade_history
GROUP BY user_id, strategy;

-- Verificar order_ids (nuevos trades tendrán order_ids)
SELECT
    CASE WHEN order_id IS NOT NULL THEN 'Con order_id' ELSE 'Sin order_id' END,
    COUNT(*),
    MAX(entry_time) as ultimo_trade
FROM trade_history
GROUP BY 1;
```

---

## 📊 Beneficios

1. ✅ **Tabla única `trade_history`** - toda la información en un solo lugar
2. ✅ **`user_id` y `strategy` separados** - queries más claras y eficientes
3. ✅ **`order_id` incluido** - tracking completo de órdenes de Binance
4. ✅ **Migración automática** - datos existentes se convierten automáticamente
5. ✅ **Compatibilidad hacia atrás** - trades antiguos funcionan (order_id puede ser NULL)

---

## 🗑️ Limpieza (Opcional)

Después de confirmar que todo funciona:

1. **Eliminar tabla `trades` vacía:**
   ```sql
   DROP TABLE IF EXISTS trades;
   ```

2. **Eliminar función `save_trade()` no utilizada:**
   - Archivo: `app/utils/db/query_executor.py`
   - Líneas: 111-158
   - También eliminar constante `TABLE_TRADES` de `app/utils/constants.py`

---

## 📝 Notas

- **Trades antiguos**: Tendrán `order_id`, `sl_order_id`, `tp_order_id` = NULL (aceptable)
- **Trades nuevos**: Tendrán todos los campos completos incluyendo order_ids
- **strategy_state**: Sigue usando `strategy_name` combinado (legacy, OK por ahora)
- **Sin downtime**: El código nuevo funciona con la tabla actualizada inmediatamente

---

**Última actualización:** 2025-10-14
**Versión:** 1.0.0
**Estado:** ✅ Completado y listo para deploy
