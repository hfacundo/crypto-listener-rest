# Binance WebSocket Integration Guide

## Índice
1. [¿Por qué usar WebSockets?](#por-qué-usar-websockets)
2. [Comparación: REST vs WebSocket](#comparación-rest-vs-websocket)
3. [WebSockets disponibles en Binance Futures](#websockets-disponibles-en-binance-futures)
4. [Implementación recomendada](#implementación-recomendada)
5. [Ejemplo de código](#ejemplo-de-código)
6. [Integración con crypto-analyzer-redis](#integración-con-crypto-analyzer-redis)
7. [Ventajas y desventajas](#ventajas-y-desventajas)
8. [Rate limits y restricciones](#rate-limits-y-restricciones)

---

## ¿Por qué usar WebSockets?

**WebSockets** permiten recibir datos en **tiempo real** sin hacer polling (llamadas repetidas) a la API REST.

### Problema actual (REST API):
```python
# Cada 30 segundos, hacer una llamada API
while True:
    mark_price = client.futures_mark_price(symbol="BTCUSDT")
    time.sleep(30)
    # Consumes 1 weight cada 30s = ~120 calls/hora
```

### Solución con WebSocket:
```python
# Una sola conexión persistente
# Binance envía updates automáticamente cada vez que cambia el precio
ws = BinanceWebSocket()
ws.subscribe_mark_price("BTCUSDT")
# 0 weight, updates instantáneos
```

---

## Comparación: REST vs WebSocket

| Aspecto | REST API | WebSocket |
|---------|----------|-----------|
| **Latencia** | 50-200ms por call | <10ms (push instantáneo) |
| **Weight usado** | 1-10 weight por call | 0 weight |
| **Freshness** | Depende del polling interval | Tiempo real (cada cambio) |
| **Conexiones** | Una por request | Una persistente |
| **Complejidad** | Simple (request/response) | Media (manejo de conexión) |
| **Ideal para** | Datos estáticos, operaciones | Precios en tiempo real |

---

## WebSockets Disponibles en Binance Futures

### 1. **Mark Price Stream** ⭐ (MÁS ÚTIL)
**Endpoint**: `wss://fstream.binance.com/ws/<symbol>@markPrice@1s`

**Uso**: Recibir mark price actualizado cada segundo.

**Datos recibidos**:
```json
{
  "e": "markPriceUpdate",
  "E": 1672515782136,
  "s": "BTCUSDT",
  "p": "16596.43000000",  // Mark price
  "i": "16596.44000000",  // Index price
  "r": "0.00010000",      // Funding rate
  "T": 1672531200000      // Next funding time
}
```

**Benefit**: ✅ Reemplaza `client.futures_mark_price()` - ahorro de ~10-15 calls/minuto

---

### 2. **Aggregate Trade Stream**
**Endpoint**: `wss://fstream.binance.com/ws/<symbol>@aggTrade`

**Uso**: Trades ejecutados en tiempo real.

**Datos recibidos**:
```json
{
  "e": "aggTrade",
  "E": 1672515782136,
  "s": "BTCUSDT",
  "a": 617613,        // Aggregate trade ID
  "p": "16596.40",    // Price
  "q": "0.100",       // Quantity
  "f": 628328,        // First trade ID
  "l": 628328,        // Last trade ID
  "T": 1672515782136, // Trade time
  "m": true           // Buyer is maker?
}
```

**Benefit**: Real-time trade flow analysis

---

### 3. **Partial Book Depth Stream** ⭐ (ÚTIL)
**Endpoint**: `wss://fstream.binance.com/ws/<symbol>@depth20@100ms`

**Uso**: Orderbook top 20 niveles actualizado cada 100ms.

**Datos recibidos**:
```json
{
  "e": "depthUpdate",
  "E": 1672515782136,
  "T": 1672515782136,
  "s": "BTCUSDT",
  "U": 157,
  "u": 160,
  "pu": 149,
  "b": [              // Bids (top 20)
    ["16596.40", "10.5"],
    ["16596.30", "5.2"]
  ],
  "a": [              // Asks (top 20)
    ["16596.50", "8.1"],
    ["16596.60", "12.3"]
  ]
}
```

**Benefit**: ✅ Reemplaza `client.futures_order_book()` - ahorro de ~5-10 calls/minuto

---

### 4. **User Data Stream** ⭐⭐⭐ (MUY ÚTIL)
**Endpoint**: `wss://fstream.binance.com/ws/<listenKey>`

**Uso**: Eventos de tu cuenta (órdenes, posiciones, balance).

**Eventos recibidos**:
- `ORDER_TRADE_UPDATE`: Orden ejecutada, cancelada, etc.
- `ACCOUNT_UPDATE`: Cambios en balance o posiciones
- `MARGIN_CALL`: Alerta de margin call
- `ACCOUNT_CONFIG_UPDATE`: Cambios en leverage

**Ejemplo ORDER_TRADE_UPDATE**:
```json
{
  "e": "ORDER_TRADE_UPDATE",
  "T": 1672515782136,
  "E": 1672515782136,
  "o": {
    "s": "BTCUSDT",
    "c": "TEST",
    "S": "BUY",
    "o": "MARKET",
    "f": "GTC",
    "q": "0.100",
    "p": "0",
    "ap": "16596.40",  // Average price
    "sp": "0",
    "x": "TRADE",      // Execution type
    "X": "FILLED",     // Order status
    "i": 123456,       // Order ID
    "l": "0.100",      // Last filled qty
    "z": "0.100",      // Cumulative filled qty
    "L": "16596.40",   // Last filled price
    "T": 1672515782136 // Trade time
  }
}
```

**Benefit**: ✅✅✅ **MAJOR** - Elimina necesidad de polling para:
- Verificar si orden se ejecutó
- Detectar cuando posición se cerró por SL/TP
- Monitor de balance en tiempo real

---

### 5. **Kline/Candlestick Stream**
**Endpoint**: `wss://fstream.binance.com/ws/<symbol>@kline_<interval>`

**Uso**: Velas en tiempo real (1m, 5m, 15m, etc.)

**Datos recibidos**:
```json
{
  "e": "kline",
  "E": 1672515782136,
  "s": "BTCUSDT",
  "k": {
    "t": 1672515780000,  // Kline start time
    "T": 1672515839999,  // Kline close time
    "s": "BTCUSDT",
    "i": "1m",           // Interval
    "f": 100,            // First trade ID
    "L": 200,            // Last trade ID
    "o": "16596.00",     // Open
    "c": "16596.40",     // Close
    "h": "16597.00",     // High
    "l": "16595.00",     // Low
    "v": "10.5",         // Volume
    "n": 100,            // Number of trades
    "x": false,          // Is closed?
    "q": "174312.00",    // Quote volume
    "V": "5.2",          // Taker buy volume
    "Q": "86320.80"      // Taker buy quote volume
  }
}
```

**Benefit**: Real-time klines para análisis técnico

---

## Implementación Recomendada

### Arquitectura sugerida

```
crypto-data-redis (NUEVO SERVICIO)
    ↓
WebSocket Manager ←→ Binance WebSocket API
    ↓
Redis (cache en tiempo real)
    ↓
crypto-analyzer-redis (lee desde Redis)
    ↓
crypto-listener-rest (lee desde Redis)
```

### ¿Por qué un servicio separado?

1. **Una sola conexión WebSocket** por stream (Binance limit)
2. **Múltiples consumidores** leen del mismo cache Redis
3. **Resiliencia**: Si crypto-analyzer se reinicia, WebSocket sigue corriendo
4. **Separación de responsabilidades**: WebSocket manager solo maneja conexión

---

## Ejemplo de Código

### Implementación Básica con `python-binance`

```python
# websocket_manager.py

from binance import ThreadedWebsocketManager
import json
import redis

class BinanceWebSocketManager:
    def __init__(self, redis_client):
        self.redis_client = redis_client
        self.twm = ThreadedWebsocketManager()
        self.twm.start()

    def start_mark_price_stream(self, symbol: str):
        """
        Inicia stream de mark price para un símbolo.
        Updates se guardan en Redis automáticamente.
        """
        def handle_message(msg):
            if msg['e'] == 'markPriceUpdate':
                mark_price = float(msg['p'])
                timestamp = msg['E'] / 1000  # Convert to seconds

                # Guardar en Redis con TTL 60s
                cache_key = f"websocket:mark_price:{symbol.lower()}"
                data = {
                    'mark_price': mark_price,
                    'timestamp': timestamp,
                    'source': 'websocket'
                }

                self.redis_client.setex(
                    cache_key,
                    60,
                    json.dumps(data)
                )

                print(f"✅ WS: {symbol} mark_price={mark_price}")

        # Suscribirse al stream
        self.twm.start_mark_price_socket(
            callback=handle_message,
            symbol=symbol
        )

    def start_depth_stream(self, symbol: str, depth: str = '20'):
        """
        Inicia stream de orderbook depth.
        """
        def handle_message(msg):
            if 'b' in msg and 'a' in msg:
                bids = msg['b']
                asks = msg['a']
                timestamp = msg['E'] / 1000

                cache_key = f"websocket:orderbook:{symbol.lower()}:{depth}"
                data = {
                    'bids': bids,
                    'asks': asks,
                    'timestamp': timestamp,
                    'source': 'websocket'
                }

                self.redis_client.setex(
                    cache_key,
                    60,
                    json.dumps(data)
                )

                print(f"✅ WS: {symbol} orderbook updated ({len(bids)} bids, {len(asks)} asks)")

        self.twm.start_depth_socket(
            callback=handle_message,
            symbol=symbol,
            depth=depth
        )

    def start_user_data_stream(self, listen_key: str):
        """
        Inicia stream de datos de usuario (órdenes, posiciones).
        Requiere listen_key de Binance API.
        """
        def handle_message(msg):
            event_type = msg.get('e')

            if event_type == 'ORDER_TRADE_UPDATE':
                order_data = msg['o']
                symbol = order_data['s']
                order_id = order_data['i']
                status = order_data['X']

                # Guardar evento en Redis stream
                stream_key = f"websocket:orders:{symbol.lower()}"
                self.redis_client.xadd(
                    stream_key,
                    {
                        'order_id': order_id,
                        'status': status,
                        'data': json.dumps(order_data)
                    },
                    maxlen=1000  # Keep last 1000 events
                )

                print(f"✅ WS: Order update {symbol} #{order_id} -> {status}")

            elif event_type == 'ACCOUNT_UPDATE':
                # Position/balance update
                positions = msg['a'].get('P', [])
                for pos in positions:
                    symbol = pos['s']
                    position_amt = float(pos['pa'])

                    cache_key = f"websocket:position:{symbol.lower()}"
                    self.redis_client.setex(
                        cache_key,
                        300,  # TTL 5 min
                        json.dumps(pos)
                    )

                    print(f"✅ WS: Position update {symbol} amt={position_amt}")

        self.twm.start_user_socket(
            callback=handle_message,
            listen_key=listen_key
        )

    def stop(self):
        """Detener todas las conexiones WebSocket"""
        self.twm.stop()


# Uso en crypto-data-redis o nuevo servicio
if __name__ == "__main__":
    import redis

    # Conectar a Redis
    redis_client = redis.Redis(host='localhost', port=6379, db=0)

    # Iniciar WebSocket manager
    ws_manager = BinanceWebSocketManager(redis_client)

    # Suscribirse a mark price para múltiples símbolos
    symbols = ['BTCUSDT', 'ETHUSDT', 'SOLUSDT']
    for symbol in symbols:
        ws_manager.start_mark_price_stream(symbol)
        ws_manager.start_depth_stream(symbol, depth='20')

    # Mantener corriendo
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        ws_manager.stop()
```

---

## Integración con crypto-analyzer-redis

### Modificar binance_cache.py

```python
# app/utils/binance/binance_cache.py

def get_mark_price(self, symbol: str, force_fresh: bool = False) -> Optional[float]:
    """
    Obtiene mark price con prioridad a WebSocket.
    Fallback a cache REST si WebSocket no disponible.
    """
    try:
        cache_key = f"{self.cache_prefix}:mark_price:{symbol.lower()}"

        # 1. Intentar WebSocket cache primero
        ws_cache_key = f"websocket:mark_price:{symbol.lower()}"
        ws_data = redis_client.get(ws_cache_key)

        if ws_data:
            data = json.loads(ws_data)
            age = time.time() - data.get('timestamp', 0)

            if age < 5:  # WebSocket data < 5s old
                self.stats['cache_hits'] += 1
                logger.debug(f"✅ Mark price WS HIT: {symbol} (age: {age:.1f}s)")
                return float(data['mark_price'])

        # 2. Fallback a cache REST
        cached_data = redis_client.get(cache_key)
        if cached_data:
            data = json.loads(cached_data)
            age = time.time() - data.get('timestamp', 0)

            if age < self.ttl_config['mark_price']:
                self.stats['cache_hits'] += 1
                logger.debug(f"✅ Mark price REST cache HIT: {symbol} (age: {age:.1f}s)")
                return float(data['mark_price'])

        # 3. Fallback a API
        if not force_fresh and not self._can_make_api_call('mark_price'):
            return None

        self._record_api_call('mark_price')
        client = get_binance_client()
        mark_data = client.futures_mark_price(symbol=symbol.upper())
        mark_price = float(mark_data["markPrice"])

        # Guardar en cache REST
        cache_data = {
            'mark_price': mark_price,
            'timestamp': time.time(),
            'source': 'api'
        }
        redis_client.setex(cache_key, self.ttl_config['mark_price'], json.dumps(cache_data))

        self.stats['api_calls_made'] += 1
        return mark_price

    except Exception as e:
        logger.error(f"Error getting mark price for {symbol}: {e}")
        return None
```

---

## Ventajas y Desventajas

### ✅ Ventajas de WebSocket

1. **Latencia ultra-baja**: Updates en <10ms vs 50-200ms REST
2. **Zero weight**: No consume rate limit
3. **Datos frescos**: Siempre actualizados, no depende de polling
4. **Eficiencia**: Una conexión vs múltiples HTTP requests
5. **Real-time events**: Órdenes, posiciones, balance en tiempo real

### ⚠️ Desventajas

1. **Complejidad**: Requiere manejo de reconexión, heartbeat
2. **Overhead**: Mantener conexión persistente (minimal)
3. **Debugging**: Más difícil de debuggear que REST
4. **Single point of failure**: Si WebSocket cae, necesitas fallback
5. **Servicio adicional**: Requiere proceso separado para manager

---

## Rate Limits y Restricciones

### Binance WebSocket Limits

| Límite | Valor | Notas |
|--------|-------|-------|
| **Conexiones por IP** | 300 | Shared entre todos los servicios en misma IP |
| **Suscripciones por conexión** | 1024 streams | Puedes suscribirte a múltiples símbolos |
| **Max message size** | 10 MB | Muy alto, no es problema |
| **Heartbeat** | 3 min | Debes enviar ping cada 3 min o conexión se cierra |
| **Listen Key TTL** | 60 min | User Data Stream requiere refresh cada hora |

### Recomendaciones

1. **Usar una sola conexión** con múltiples suscripciones
2. **Implementar auto-reconnect** en caso de desconexión
3. **Mantener heartbeat** cada 2 minutos
4. **Refresh listen_key** cada 50 minutos (user data stream)

---

## Implementación Sugerida para tu Pipeline

### Fase 1: WebSocket para Mark Price (Fácil)
```
crypto-data-redis:
  - Agregar WebSocketManager
  - Suscribirse a mark_price para símbolos activos
  - Guardar en Redis con key: websocket:mark_price:{symbol}

crypto-analyzer-redis:
  - Modificar binance_cache.get_mark_price()
  - Priorizar WebSocket cache
  - Fallback a REST API si no disponible
```

**Benefit**: Elimina ~10-15 API calls/min

### Fase 2: WebSocket para Orderbook (Media)
```
crypto-data-redis:
  - Suscribirse a depth20@100ms
  - Guardar en Redis

crypto-analyzer-redis:
  - Modificar binance_cache.get_orderbook_data()
  - Usar WebSocket data
```

**Benefit**: Elimina ~5-10 API calls/min

### Fase 3: WebSocket User Data Stream (Avanzado)
```
crypto-listener-rest:
  - Obtener listen_key al inicio
  - Suscribirse a user data stream
  - Procesar eventos ORDER_TRADE_UPDATE
  - Eliminar polling de futures_get_open_orders()
```

**Benefit**: Elimina polling de órdenes, detección instantánea de ejecución

---

## Conclusión

### ¿Deberías usar WebSockets?

**SÍ** si:
- ✅ Necesitas datos en tiempo real (<1s latency)
- ✅ Estás cerca del rate limit
- ✅ Quieres reducir llamadas API
- ✅ Necesitas detectar eventos de órdenes instantáneamente

**NO** si:
- ❌ Tu polling interval es >1 minuto (REST es suficiente)
- ❌ No tienes overhead de rate limit
- ❌ Prefieres simplicidad sobre performance

### Recomendación para tu caso

**Implementar en 2 fases:**

1. **Fase 1 (Priority)**: Mark Price WebSocket
   - Fácil de implementar
   - Mayor impacto (elimina más llamadas)
   - Bajo riesgo

2. **Fase 2 (Nice to have)**: User Data Stream
   - Requiere más trabajo
   - Elimina polling de órdenes
   - Mejor UX (detección instantánea)

**Orderbook WebSocket**: Opcional, ya tienes cache REST funcionando bien.

---

## Referencias

- [Binance Futures WebSocket Docs](https://binance-docs.github.io/apidocs/futures/en/#websocket-market-streams)
- [python-binance WebSocket Examples](https://python-binance.readthedocs.io/en/latest/websockets.html)
- [Binance WebSocket Limits](https://binance-docs.github.io/apidocs/futures/en/#limits)

---

**Última actualización:** 2025-01-13
