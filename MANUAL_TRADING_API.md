# Manual Trading API - Endpoints de Control

Esta documentación describe los endpoints para control manual de posiciones abiertas.

## 📋 Tabla de Contenidos

1. [Cerrar Posición](#1-cerrar-posición)
2. [Establecer Stop Loss](#2-establecer-stop-loss)
3. [Establecer Take Profit](#3-establecer-take-profit)
4. [Ajustar Stop Loss y Take Profit](#4-ajustar-stop-loss-y-take-profit)

---

## 1. Cerrar Posición

Cierra completamente una posición abierta y cancela todas las órdenes pendientes (SL/TP).

### Endpoint
```
POST /close-position
```

### Request Body
```json
{
  "user_id": "copy_trading",
  "symbol": "BTCUSDT"
}
```

### Response (Success)
```json
{
  "success": true,
  "message": "Position closed successfully",
  "user_id": "copy_trading",
  "symbol": "BTCUSDT",
  "order_id": 123456789
}
```

### Response (Error)
```json
{
  "success": false,
  "error": "No open position to close",
  "user_id": "copy_trading",
  "symbol": "BTCUSDT"
}
```

### Ejemplo cURL
```bash
curl -X POST http://localhost:8000/close-position \
  -H "Content-Type: application/json" \
  -d '{
    "user_id": "copy_trading",
    "symbol": "BTCUSDT"
  }'
```

---

## 2. Establecer Stop Loss

Actualiza únicamente el Stop Loss de una posición abierta. Valida que el nuevo SL sea más seguro que el anterior (tighten-only).

### Endpoint
```
POST /set-stop-loss
```

### Request Body
```json
{
  "user_id": "copy_trading",
  "symbol": "BTCUSDT",
  "stop_loss": 44000.0
}
```

### Validaciones Automáticas
- **Para LONG**: `stop_loss < mark_price` (el SL debe estar debajo del precio actual)
- **Para SHORT**: `stop_loss > mark_price` (el SL debe estar arriba del precio actual)
- **Tighten-only**: El nuevo SL debe ser mejor que el anterior (más cercano al entry o BE)

### Response (Success)
```json
{
  "success": true,
  "message": "Stop loss updated successfully",
  "user_id": "copy_trading",
  "symbol": "BTCUSDT",
  "direction": "BUY",
  "stop_loss": 44000.0,
  "mark_price": 45000.0,
  "previous_stop": 43000.0,
  "algo_order_id": "987654321"
}
```

### Response (Error - Validación)
```json
{
  "success": false,
  "error": "Invalid SL for LONG (expected stop_loss < mark_price)",
  "user_id": "copy_trading",
  "symbol": "BTCUSDT",
  "mark_price": 45000.0,
  "requested_stop": 46000.0
}
```

### Ejemplo cURL
```bash
curl -X POST http://localhost:8000/set-stop-loss \
  -H "Content-Type: application/json" \
  -d '{
    "user_id": "copy_trading",
    "symbol": "BTCUSDT",
    "stop_loss": 44000.0
  }'
```

---

## 3. Establecer Take Profit

Actualiza únicamente el Take Profit de una posición abierta. Mantiene el Stop Loss existente intacto.

### Endpoint
```
POST /set-take-profit
```

### Request Body
```json
{
  "user_id": "copy_trading",
  "symbol": "BTCUSDT",
  "take_profit": 47000.0
}
```

### Validaciones Automáticas
- **Para LONG**: `take_profit > mark_price` (el TP debe estar arriba del precio actual)
- **Para SHORT**: `take_profit < mark_price` (el TP debe estar debajo del precio actual)
- Mantiene el Stop Loss existente sin modificarlo

### Response (Success)
```json
{
  "success": true,
  "message": "Take profit updated successfully",
  "user_id": "copy_trading",
  "symbol": "BTCUSDT",
  "direction": "BUY",
  "take_profit": 47000.0,
  "mark_price": 45000.0,
  "stop_loss": 44000.0,
  "algo_order_id": "456789123"
}
```

### Response (Error - Validación)
```json
{
  "success": false,
  "error": "Invalid TP for LONG (expected take_profit > mark_price)",
  "user_id": "copy_trading",
  "symbol": "BTCUSDT",
  "mark_price": 45000.0,
  "requested_tp": 44000.0
}
```

### Ejemplo cURL
```bash
curl -X POST http://localhost:8000/set-take-profit \
  -H "Content-Type: application/json" \
  -d '{
    "user_id": "copy_trading",
    "symbol": "BTCUSDT",
    "take_profit": 47000.0
  }'
```

---

## 4. Ajustar Stop Loss y Take Profit

Actualiza tanto el Stop Loss como el Take Profit simultáneamente.

### Endpoint
```
POST /adjust-sl-tp
```

### Request Body
```json
{
  "user_id": "copy_trading",
  "symbol": "BTCUSDT",
  "stop_loss": 44000.0,
  "take_profit": 47000.0
}
```

### Validaciones Automáticas
- Validaciones de SL (según dirección)
- Validaciones de TP (según dirección)
- Cancela las órdenes anteriores y crea nuevas

### Response (Success)
```json
{
  "success": true,
  "message": "Stop loss and take profit updated successfully",
  "user_id": "copy_trading",
  "symbol": "BTCUSDT",
  "direction": "BUY",
  "stop_loss": 44000.0,
  "take_profit": 47000.0,
  "mark_price": 45000.0
}
```

### Ejemplo cURL
```bash
curl -X POST http://localhost:8000/adjust-sl-tp \
  -H "Content-Type: application/json" \
  -d '{
    "user_id": "copy_trading",
    "symbol": "BTCUSDT",
    "stop_loss": 44000.0,
    "take_profit": 47000.0
  }'
```

---

## 🔒 Validaciones Generales

Todos los endpoints validan:

1. **User ID válido**: Debe ser uno de los usuarios configurados (copy_trading, futures, hufsa, copy_2)
2. **Símbolo válido**: Debe existir en Binance Futures
3. **Posición abierta**: Debe haber una posición activa para ese símbolo
4. **Precios válidos**: Los precios deben cumplir con:
   - Tick size de Binance
   - Rangos min/max permitidos
   - Lógica de dirección (LONG vs SHORT)

## ⚠️ Errores Comunes

### 400 Bad Request
```json
{
  "detail": "Invalid user_id. Must be one of: copy_trading, futures, hufsa, copy_2"
}
```

### 404 Not Found
```json
{
  "success": false,
  "error": "No open position to adjust",
  "user_id": "copy_trading",
  "symbol": "BTCUSDT"
}
```

### 422 Validation Error
```json
{
  "detail": [
    {
      "loc": ["body", "stop_loss"],
      "msg": "field required",
      "type": "value_error.missing"
    }
  ]
}
```

---

## 📊 Ejemplos de Uso

### Caso 1: Mover Stop Loss a Break Even
```bash
# Obtener precio de entrada (manualmente o desde BD)
ENTRY_PRICE=45000

# Mover SL a BE
curl -X POST http://localhost:8000/set-stop-loss \
  -H "Content-Type: application/json" \
  -d "{
    \"user_id\": \"copy_trading\",
    \"symbol\": \"BTCUSDT\",
    \"stop_loss\": $ENTRY_PRICE
  }"
```

### Caso 2: Trailing Stop Manual
```bash
# Obtener mark price actual
MARK_PRICE=$(curl -s "https://fapi.binance.com/fapi/v1/premiumIndex?symbol=BTCUSDT" | jq -r '.markPrice')

# Calcular nuevo SL (2% por debajo del mark)
NEW_SL=$(echo "$MARK_PRICE * 0.98" | bc)

# Actualizar SL
curl -X POST http://localhost:8000/set-stop-loss \
  -H "Content-Type: application/json" \
  -d "{
    \"user_id\": \"copy_trading\",
    \"symbol\": \"BTCUSDT\",
    \"stop_loss\": $NEW_SL
  }"
```

### Caso 3: Cerrar Todas las Posiciones de un Usuario
```bash
SYMBOLS=("BTCUSDT" "ETHUSDT" "SOLUSDT")

for symbol in "${SYMBOLS[@]}"; do
  echo "Closing $symbol..."
  curl -X POST http://localhost:8000/close-position \
    -H "Content-Type: application/json" \
    -d "{
      \"user_id\": \"copy_trading\",
      \"symbol\": \"$symbol\"
    }"
  sleep 1
done
```

---

## 🔐 Seguridad

- Los endpoints NO requieren autenticación en esta versión (solo local/VPC)
- Si se expone públicamente, agregar autenticación con API keys
- Validar que el user_id pertenezca al usuario autenticado

## 📝 Notas Importantes

1. **Tighten-only para SL**: El endpoint `set-stop-loss` solo permite hacer el SL más seguro, no aflojarlo
2. **Algo Orders**: Todas las órdenes SL/TP usan el nuevo Algo Order API de Binance
3. **Mark Price**: Las validaciones usan mark price, no last price, para evitar wicks
4. **Idempotencia**: Los endpoints son idempotentes (llamar múltiples veces con los mismos parámetros produce el mismo resultado)

---

## 🧪 Testing

Usa el script de prueba incluido:

```bash
python test_manual_trading_endpoints.py
```

O prueba manualmente con Postman/Insomnia importando la colección incluida.
