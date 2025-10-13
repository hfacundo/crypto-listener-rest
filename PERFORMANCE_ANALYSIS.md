# Análisis de Performance del Sistema de Trading

## Distribución Real de API Calls

### Por Servicio (Análisis Detallado)

#### 1. crypto-data-redis
**Frecuencia**: Continua (cada minuto)
**Símbolos**: 50 activos
**Llamadas por minuto**:
```
- futures_klines (1m): 50 calls/min (1 por símbolo)
- Weight: 50/min
```

**Total por hora**: 3,000 calls (50 weight/min × 60 min)

---

#### 2. crypto-analyzer-redis
**Frecuencia**: Continua (cada 30 segundos)
**Símbolos**: 50 activos
**Llamadas por minuto**:
```
- futures_mark_price: 100 calls/min (50 × 2 calls/min)
- futures_order_book: 100 calls/min (50 × 2 calls/min) [si no usa cache]
```

**Con cache actual (binance_cache.py)**:
- mark_price: ~100 calls/min (cache hit ~80% → 20 API calls/min)
- orderbook: ~100 calls/min (cache hit ~80% → 20 API calls/min)

**Total por hora**: ~2,400 calls (~40/min × 60 min)

---

#### 3. crypto-listener-rest
**Frecuencia**: Esporádica (solo cuando hay trade signal)
**Llamadas por trade**:
```
ANTES de optimización:
- create_order flow: ~36 calls por trade
  - futures_exchange_info: 2
  - futures_order_book: 1
  - futures_mark_price: 10
  - futures_klines: 1
  - futures_position_information: 7 (críticas)
  - futures_get_open_orders: 3 (críticas)
  - futures_create_order: 7 (críticas)
  - futures_cancel_order: 4 (críticas)
  - futures_change_leverage: 1 (crítica)

DESPUÉS de optimización (con cache):
- create_order flow: ~20 calls por trade
  - Cacheables eliminadas: 16 calls
  - Críticas (mantienen): 20 calls
```

**Frecuencia de trades**:
```
Escenario bajo: 2-5 trades/hora
- 5 trades × 20 calls = 100 calls/hora = 1.67 calls/min

Escenario medio: 10-15 trades/hora
- 15 trades × 20 calls = 300 calls/hora = 5 calls/min

Escenario alto: 30-50 trades/hora
- 50 trades × 20 calls = 1,000 calls/hora = 16.67 calls/min
```

---

#### 4. crypto-guardian
**Frecuencia**: Cada 60 segundos (cuando hay posiciones)
**Llamadas**: 0 (lee desde PostgreSQL/Redis)

---

#### 5. crypto-guardian-cleanup
**Frecuencia**: Post-guardian action (esporádico)
**Llamadas por verificación**:
```
- futures_position_information: 1
- futures_account_trades: 1
- futures_get_open_orders: 1
```

**Total**: ~3 calls por verificación
**Frecuencia**: 1-5 verificaciones/hora = 3-15 calls/hora = 0.05-0.25 calls/min

---

## 📊 Tabla Comparativa de API Calls

| Servicio | Calls/Min (Actual) | Calls/Hora | % del Total | Tipo |
|----------|-------------------|------------|-------------|------|
| **crypto-data-redis** | 50 | 3,000 | 43% | Continuo |
| **crypto-analyzer-redis** | 40 | 2,400 | 34% | Continuo |
| **crypto-listener-rest** | 1.67-16.67 | 100-1,000 | 1-14% | Esporádico |
| **crypto-guardian-cleanup** | 0.05-0.25 | 3-15 | <1% | Esporádico |
| **TOTAL** | **91.72-106.92** | **5,503-6,415** | 100% | - |

---

## 🔥 Insight Clave: ¿Dónde está el bottleneck?

### Conclusión Sorprendente:

**crypto-analyzer-redis usa MÁS API calls que crypto-listener-rest**

```
Análisis:
- crypto-analyzer-redis: ~2,400 calls/hora (continuo)
- crypto-listener-rest: ~100-1,000 calls/hora (esporádico)

Razón:
- crypto-analyzer-redis: Polling cada 30s para 50 símbolos = continuo
- crypto-listener-rest: Solo ejecuta cuando hay trade signal = esporádico
```

**Implicación**: Optimizar crypto-analyzer-redis tiene MAYOR impacto.

---

## 💡 Impacto de WebSocket Mark Price

### Sin WebSocket (Actual):
```
crypto-analyzer-redis:
- futures_mark_price: 100 calls/min (50 símbolos × 2/min)
- Weight: ~100/min

crypto-listener-rest:
- futures_mark_price: 10 calls/trade (con cache hit ~80%)
- Weight: ~0.3-3/min (depende de frecuencia de trades)

Total mark_price calls: ~100-103 calls/min
```

### Con WebSocket Mark Price:
```
crypto-analyzer-redis:
- futures_mark_price: 0 calls/min (WebSocket)
- Weight: 0/min

crypto-listener-rest:
- futures_mark_price: 0 calls (lee cache de WebSocket)
- Weight: 0/min

Total mark_price calls: 0 calls/min
```

**Ahorro: 100-103 calls/min → 6,000-6,180 calls/hora**

---

## 🎯 Estrategia de Throttling Recomendada

### Opción A: Throttling a 5 segundos ⭐⭐⭐ (RECOMENDADO)

**Configuración**:
```python
# WebSocket Manager
save_interval = 5  # Guardar cada 5 segundos

# Writes a Redis:
50 símbolos / 5 segundos = 10 writes/segundo
= 600 writes/minuto
= 36,000 writes/hora
```

**Comparación con sistema actual**:
```
Sistema actual (REST polling):
- 100 API calls/min a Binance
- 100 writes/min a Redis (después de API call)

Sistema WebSocket (throttling 5s):
- 0 API calls/min a Binance
- 600 writes/min a Redis

Ratio: 6x más writes a Redis, pero:
- Redis puede handle 100,000+ writes/seg
- 600/min = 10/seg = 0.01% de capacidad
```

**✅ Redis NO es bottleneck**

---

### Opción B: Throttling a 10 segundos ⭐⭐ (Conservador)

**Configuración**:
```python
save_interval = 10  # Guardar cada 10 segundos
```

**Writes a Redis**:
```
50 símbolos / 10 segundos = 5 writes/segundo
= 300 writes/minuto
= 18,000 writes/hora
```

**Trade-off**:
- Pros: Menos writes a Redis (50% vs Opción A)
- Cons: Data puede tener hasta 10s de age
- ¿Es aceptable?: SÍ, para trading con drift tolerance de 0.5-1%

---

### Opción C: Hybrid In-Memory + Redis (10s sync) ⭐ (Best of both)

**Configuración**:
```python
# In-memory: Updates cada segundo
# Redis sync: Cada 10 segundos

memory_updates = 50/segundo
redis_writes = 5/segundo (batch cada 10s)
```

**Ventajas**:
- Servicios en mismo proceso (crypto-analyzer-redis): access instantáneo (<1ms)
- Servicios externos (crypto-listener-rest): leen Redis cada 10s (suficiente)
- Menor carga en Redis

**Desventajas**:
- Más complejo
- Requiere que crypto-analyzer-redis y WebSocket manager estén en mismo proceso

---

## 📊 Benchmarks de Performance

### Redis Write Performance

```bash
# Test: 1 millón de setex operations
redis-benchmark -t set -n 1000000 -d 100

Results típicos:
- Throughput: 80,000-120,000 ops/seg
- Latency p50: 0.3-0.5ms
- Latency p99: 1-2ms

Tu uso:
- Opción A (5s): 10 ops/seg = 0.01% capacidad
- Opción B (10s): 5 ops/seg = 0.006% capacidad
```

**✅ Conclusión: Redis writes NO son un problema**

---

### Network Latency

```
REST API call a Binance:
- Latency: 50-200ms (depende de región)
- Weight: 1-10 por call

WebSocket update:
- Latency: <10ms (push desde Binance)
- Weight: 0

Redis read/write:
- Local: 0.3-0.5ms
- Network (mismo datacenter): 1-2ms
- Network (cross-region): 5-20ms
```

---

## 🎯 Recomendación Final

### Para tu caso específico:

**Implementar: Throttling a 5 segundos con batching**

```python
class BinanceWebSocketManager:
    def __init__(self, redis_client):
        self.redis_client = redis_client
        self.pending_updates = {}
        self.batch_interval = 5  # 5 segundos

        # Flush thread
        import threading
        self.flush_thread = threading.Thread(
            target=self._flush_batch_loop,
            daemon=True
        )
        self.flush_thread.start()

    def start_mark_price_stream(self, symbol: str):
        def handle_message(msg):
            if msg['e'] == 'markPriceUpdate':
                # Acumular en memoria (no escribir inmediatamente)
                self.pending_updates[symbol] = {
                    'mark_price': float(msg['p']),
                    'timestamp': msg['E'] / 1000,
                    'source': 'websocket'
                }

        self.twm.start_mark_price_socket(
            callback=handle_message,
            symbol=symbol
        )

    def _flush_batch_loop(self):
        while True:
            time.sleep(self.batch_interval)

            if not self.pending_updates:
                continue

            # Batch write usando pipeline (1 network round-trip)
            pipeline = self.redis_client.pipeline()

            for symbol, data in self.pending_updates.items():
                cache_key = f"websocket:mark_price:{symbol.lower()}"
                pipeline.setex(cache_key, 30, json.dumps(data))

            results = pipeline.execute()

            print(f"✅ Flushed {len(self.pending_updates)} prices "
                  f"(1 batch, {len(results)} operations)")

            self.pending_updates.clear()
```

**Razones**:
1. ✅ Updates cada segundo en memoria (disponibles inmediatamente)
2. ✅ Escritura a Redis cada 5 segundos (batch de 50 símbolos)
3. ✅ Solo 1 network round-trip cada 5 segundos (Redis pipeline)
4. ✅ Data age máximo: 5 segundos (aceptable para trading)
5. ✅ Elimina 100 API calls/min a Binance

---

## 📈 Impacto Esperado en Rate Limit

### Antes (Sistema Actual):
```
Total API calls: ~5,500-6,500/hora
Weight estimado: ~5,500-6,500/hora (asumiendo weight promedio 1)

Por minuto: ~92-108 calls/min
Rate limit Binance: 2,400/min
Utilización: 3.8-4.5%
```

### Después (Con WebSocket Mark Price):
```
Total API calls: ~3,100-4,100/hora (elimina ~2,400/hora)
Weight estimado: ~3,100-4,100/hora

Por minuto: ~52-68 calls/min
Rate limit Binance: 2,400/min
Utilización: 2.2-2.8%
```

**Reducción: ~40% de API calls totales**

---

## ⚡ Optimizaciones Adicionales (Futuro)

### Si quieres reducir aún más:

1. **WebSocket Orderbook** (depth20@100ms)
   - Elimina: ~100 calls/min adicionales
   - Reducción total: ~60% de API calls

2. **WebSocket User Data Stream**
   - Elimina polling de órdenes en crypto-listener-rest
   - Elimina: ~5-10 calls/min
   - Beneficio adicional: Detección instantánea de ejecución

3. **Increase Polling Interval en crypto-data-redis**
   - Cambiar klines de 1min a 5min (si tu estrategia lo permite)
   - Reduce: 50 calls/min → 10 calls/min

---

## 🎯 Conclusión

### ¿Es eficiente el sistema actual?

**SÍ**, considerando:
- Utilización de rate limit: 3.8-4.5% (muy bajo)
- Margen disponible: 95-96%
- Bottleneck: Ninguno

### ¿Vale la pena WebSocket Mark Price?

**SÍ**, porque:
- ✅ Reduce 40% de API calls totales
- ✅ Latencia <10ms vs 50-200ms
- ✅ Zero weight
- ✅ Redis writes: solo 10/seg (0.01% capacidad)
- ✅ Throttling a 5s es suficiente para trading

### ¿Necesitas optimizar más allá de esto?

**NO es urgente**, pero puedes considerar:
- WebSocket orderbook si quieres reducir a ~1% rate limit usage
- User Data Stream si quieres detección instantánea de órdenes

---

**Última actualización:** 2025-01-13
