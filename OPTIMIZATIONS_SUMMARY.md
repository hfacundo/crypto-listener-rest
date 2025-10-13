# Optimizaciones de API Calls Implementadas

## Resumen Ejecutivo

Se implementaron optimizaciones que reducen **51% de las llamadas a Binance API** en crypto-listener-rest, pasando de ~41 llamadas a ~20 llamadas por ciclo completo de trading.

**Fecha**: 2025-01-13
**Impacto**: -21 API calls por trade (-51%)
**Rate Limit**: De ~1-2% a ~0.5% de uso

---

## Cambios Implementados

### 1. ✅ Módulo BinanceCacheClient (NUEVO)

**Archivo**: `app/utils/binance/binance_cache_client.py`

**Propósito**: Cliente read-only para reutilizar cache generado por crypto-analyzer-redis.

**Funcionalidades**:
- `get_mark_price()` - Lee mark_price desde Redis con fallback a API
- `get_orderbook_data()` - Lee orderbook desde Redis con fallback a API
- `get_klines_from_redis()` - Lee klines de crypto-data-redis
- `get_exchange_info_cached()` - Cache local de exchange_info (TTL 1h)
- `get_leverage_bracket_cached()` - Cache local de leverage brackets (TTL 1h)

**Stats tracking**:
```python
cache_client.get_cache_stats()
# Returns:
# {
#   'cache_hit_rate': 85.2,
#   'cache_hits': 120,
#   'cache_misses': 21,
#   'fallback_api_calls': 5
# }
```

---

### 2. ✅ Actualización de utils.py

**Archivo**: `app/utils/binance/utils.py`

**Cambios**:

#### get_mark_price()
```python
# ANTES: API call directo
mark_price = float(client.futures_mark_price(symbol=symbol)["markPrice"])

# DESPUÉS: Cache con fallback
cache_client = get_binance_cache_client()
mark_price = cache_client.get_mark_price(symbol, client=client, max_age=30)
```

**Impacto**: -10 API calls (mark_price usado en múltiples lugares)

#### get_symbol_filters()
```python
# ANTES: API call directo
exchange_info = client.futures_exchange_info()

# DESPUÉS: Cache local de 1 hora
exchange_info = get_exchange_info_cached(client)
```

**Impacto**: -2 API calls (exchange_info casi no cambia)

#### get_max_allowed_leverage()
```python
# ANTES: API call directo
brackets = client.futures_leverage_bracket(symbol=symbol)

# DESPUÉS: Cache local de 1 hora
brackets = get_leverage_bracket_cached(symbol, client)
```

**Impacto**: -1 API call (leverage brackets muy estáticos)

---

### 3. ✅ Actualización de dynamic_rules.py

**Archivo**: `app/utils/binance/dynamic_rules.py`

**Cambios**:

#### adjust_base_depth_and_depth_pct_for_symbol()
```python
# ANTES: API call para klines
klines = client.futures_klines(symbol=symbol, interval="1m", limit=60)

# DESPUÉS: Redis (crypto-data-redis)
cache_client = get_binance_cache_client()
klines = cache_client.get_klines_from_redis(symbol, interval="1m", limit=60)

# Fallback a API si Redis no disponible
if not klines:
    klines = client.futures_klines(symbol=symbol, interval="1m", limit=60)
```

**Impacto**: -1 API call (klines ya en Redis)

---

### 4. ✅ Actualización de futures.py

**Archivo**: `app/futures.py`

**Cambios**: 6 llamadas a `mark_price` reemplazadas con cache

**Ubicaciones**:
- `create_order()` línea 76: mark_price para validación
- `close_position_and_cancel_orders()` línea 330: mark_price para PostgreSQL
- `adjust_sl_tp_for_open_position()` línea 390: mark_price para validación
- `adjust_stop_only_for_open_position()` línea 485: mark_price para sanity check
- `half_close_and_move_be()` línea 534: mark_price para validación
- `half_close_and_move_be()` línea 569: mark_price para BE calculation

**Cambio de orderbook**:
```python
# ANTES: API call directo
order_book = client.futures_order_book(symbol=symbol, limit=100)

# DESPUÉS: Cache con fallback
orderbook_data = cache_client.get_orderbook_data(symbol, depth_limit=100, client=client, max_age=30)
if orderbook_data:
    order_book = orderbook_data
else:
    order_book = client.futures_order_book(symbol=symbol, limit=100)
```

**Impacto**: -7 API calls (6 mark_price + 1 orderbook)

---

### 5. ✅ Actualización de market_validation.py

**Archivo**: `app/market_validation.py`

**Cambios**:

#### get_fresh_market_data()
```python
# ANTES: API calls directos
mark_data = client.futures_mark_price(symbol=symbol.upper())
orderbook = client.futures_order_book(symbol=symbol.upper(), limit=20)

# DESPUÉS: Cache con fallback
mark_price = get_mark_price(symbol.upper(), client)
orderbook_data = cache_client.get_orderbook_data(symbol.upper(), depth_limit=20, client=client, max_age=30)
```

**Impacto**: -2 API calls (usado en validación de Guardian)

---

## Impacto por Tipo de Llamada

### Llamadas Optimizadas (21 total)

| Tipo de Llamada | Antes | Después | Método de Optimización |
|-----------------|-------|---------|------------------------|
| `futures_mark_price` | 10 | 0-2* | Cache Redis (30s TTL) |
| `futures_order_book` | 2 | 0-1* | Cache Redis (30s TTL) |
| `futures_klines` | 1 | 0* | Usar crypto-data-redis |
| `futures_exchange_info` | 2 | 0-1** | Cache local (3600s TTL) |
| `futures_leverage_bracket` | 1 | 0-1** | Cache local (3600s TTL) |

\* Fallback a API solo si cache miss
\*\* Primera llamada popula cache, siguientes usan cache

### Llamadas NO Optimizadas (20 total)

| Tipo de Llamada | Cantidad | Razón |
|-----------------|----------|-------|
| `futures_create_order` | 7 | Operación de trading crítica |
| `futures_cancel_order` | 4 | Operación de trading crítica |
| `futures_position_information` | 7 | Verificación de posición en tiempo real |
| `futures_change_leverage` | 1 | Configuración pre-trade necesaria |
| `futures_get_open_orders` | 1 | Verificación crítica de órdenes |

**Estas 20 llamadas son NECESARIAS y NO deben cachearse.**

---

## Métricas de Impacto

### Antes de Optimización
```
Trade completo:
- API calls: ~41
- Weight usado: ~240-320 (4 users parallel)
- Rate limit usage: 1-2% del límite (2,400/min)
```

### Después de Optimización
```
Trade completo:
- API calls: ~20 (-51%)
- Weight usado: ~120-160 (-50%)
- Rate limit usage: 0.5-1% del límite
- Cache hit rate esperado: >80%
```

### Beneficios Adicionales
- ✅ **Latencia reducida**: Cache local < 1ms vs API 50-200ms
- ✅ **Resiliencia**: Cache sobrevive a caídas temporales de Binance
- ✅ **Escalabilidad**: Margen de 99% de rate limit disponible
- ✅ **Consistencia**: Múltiples servicios leen mismo cache

---

## Diagrama de Flujo de Cache

```
┌─────────────────────┐
│ crypto-data-redis   │
│ (klines en Redis)   │
└──────────┬──────────┘
           │
           ▼
┌──────────────────────────────┐
│ crypto-analyzer-redis        │
│ - binance_cache (mark_price) │
│ - binance_cache (orderbook)  │
│ Popula Redis cada 30s        │
└──────────┬───────────────────┘
           │
           ▼
┌──────────────────────────────┐
│ crypto-listener-rest         │
│ - binance_cache_client       │
│ Lee desde Redis (read-only)  │
│ Fallback a API si cache miss │
└──────────────────────────────┘
```

---

## Configuración de TTLs

| Cache | TTL | Justificación |
|-------|-----|---------------|
| Mark Price | 30s | Cambia frecuentemente, pero 30s es aceptable |
| Orderbook | 30s | Suficiente para validaciones pre-trade |
| Klines | 60s | crypto-data-redis actualiza cada minuto |
| Exchange Info | 3600s | Casi estático, cambia muy raramente |
| Leverage Bracket | 3600s | Casi estático |

---

## Testing y Validación

### Cómo verificar que funciona

```bash
# En crypto-listener-rest, agregar logging
import logging
logging.basicConfig(level=logging.DEBUG)

# Ejecutar trade y ver logs:
# ✅ Mark price cache HIT: BTCUSDT (age: 15.2s)
# ✅ Orderbook cache HIT: BTCUSDT (age: 10.5s)
# ✅ Klines cache HIT: BTCUSDT 1m (60 candles)
```

### Verificar stats del cache

```python
from app.utils.binance.binance_cache_client import get_binance_cache_client

cache_client = get_binance_cache_client()
stats = cache_client.get_cache_stats()
print(stats)

# Expected output:
# {
#   'cache_hit_rate': 85.2,  # >80% es excelente
#   'cache_hits': 120,
#   'cache_misses': 21,
#   'fallback_api_calls': 5
# }
```

---

## Próximos Pasos (Opcional)

### Optimización Fase 2 (Futuro)

Si quieres optimizar aún más:

1. **WebSocket para Mark Price** (ver WEBSOCKET_GUIDE.md)
   - Elimina TODAS las llamadas a `futures_mark_price`
   - Latencia < 10ms vs 50-200ms REST
   - Zero weight

2. **Batch Position Calls Cache**
   - Cachear `futures_position_information()` (sin symbol)
   - TTL 60s
   - Elimina 4 llamadas adicionales

3. **User Data Stream WebSocket**
   - Elimina polling de órdenes
   - Detección instantánea de ejecución
   - Elimina ~5 llamadas más

**Total potencial**: Hasta 65-70% reducción de API calls

---

## Troubleshooting

### Cache miss rate muy alto (>30%)

**Causas posibles**:
1. crypto-analyzer-redis no está corriendo (no popula cache)
2. Redis no está disponible
3. TTLs muy cortos

**Solución**:
```bash
# Verificar que crypto-analyzer-redis está corriendo
ps aux | grep crypto-analyzer

# Verificar Redis
redis-cli ping

# Ver keys en Redis
redis-cli keys "binance_cache:*"
```

### Fallback API calls frecuentes

**Causas posibles**:
1. Cache vacío (primer uso)
2. Símbolo no monitoreado por crypto-analyzer-redis
3. Redis desconectado

**Solución**:
```python
# Logs mostrarán:
# ⚠️ Mark price cache MISS: NEWCOIN - fallback to API

# Agregar símbolo a crypto-analyzer-redis si es necesario
```

---

## Referencias

- Análisis completo de API calls: Ver análisis detallado en conversación
- WebSocket integration: `WEBSOCKET_GUIDE.md`
- systemd setup: `SYSTEMD_SETUP.md`

---

**Última actualización:** 2025-01-13
**Implementado por:** crypto-listener-rest optimization team
