# Guía de Pruebas en Testnet de Binance Futures

Esta guía explica cómo probar el sistema de trading en el **Testnet de Binance Futures** sin arriesgar dinero real.

## 🎯 ¿Por qué usar Testnet?

- ✅ **Sin riesgo**: Usa fondos virtuales (fake USDT)
- ✅ **API idéntica**: Mismo comportamiento que producción
- ✅ **Pruebas ilimitadas**: Puedes resetear tu balance cuando quieras
- ✅ **Valida cambios críticos**: Como la migración al Algo Order API

## 📋 Paso 1: Crear Cuenta en Testnet

1. Ve a **Binance Futures Testnet**: https://testnet.binancefuture.com/
2. Haz clic en "Register" (arriba derecha)
3. Crea una cuenta con tu email (no necesita verificación)
4. Una vez dentro, tendrás **10,000 USDT virtuales** automáticamente

## 🔑 Paso 2: Generar API Keys de Testnet

1. En el Testnet, ve a tu perfil (arriba derecha)
2. Haz clic en "API Keys"
3. Genera un nuevo API Key:
   - Dale un nombre descriptivo: `crypto-listener-test`
   - **IMPORTANTE**: Habilita permisos de **Trading en Futures**
4. Guarda tu **API Key** y **Secret Key** (no podrás ver el secret después)

### ⚠️ Seguridad de Testnet

Las API Keys de Testnet son diferentes a las de producción:
- **NO** las mezcles con tus keys de producción
- Tienen el prefijo diferente para evitar confusiones
- Solo funcionan en `testnet.binancefuture.com`

## ⚙️ Paso 3: Configurar Variables de Entorno

Crea o edita tu archivo `.env.local` (o `.env` para testing):

```bash
# ========================================
# TESTNET MODE - Activar para pruebas
# ========================================
USE_BINANCE_TESTNET=true

# ========================================
# API Keys de Testnet (NO SON LAS DE PRODUCCIÓN)
# ========================================
# Reemplaza con tus keys de testnet generadas en el paso anterior
BINANCE_FUTURES_API_KEY_COPY=tu_testnet_api_key_aqui
BINANCE_FUTURES_API_SECRET_COPY=tu_testnet_secret_key_aqui

BINANCE_FUTURES_API_KEY_FUTURES=tu_testnet_api_key_aqui
BINANCE_FUTURES_API_SECRET_FUTURES=tu_testnet_secret_key_aqui

BINANCE_FUTURES_API_KEY_HUFSA=tu_testnet_api_key_aqui
BINANCE_FUTURES_API_SECRET_HUFSA=tu_testnet_secret_key_aqui

BINANCE_FUTURES_API_KEY_COPY_2=tu_testnet_api_key_aqui
BINANCE_FUTURES_API_SECRET_COPY_2=tu_testnet_secret_key_aqui

# ========================================
# Otras variables necesarias
# ========================================
DATABASE_URL_CRYPTO_TRADER=postgresql://user:pass@localhost:5432/crypto_trader
REDIS_HOST=localhost
REDIS_PORT=6379
```

### 💡 Tip: Puedes usar la misma API Key para todos los usuarios en Testnet

## 🚀 Paso 4: Ejecutar el Servicio en Modo Testnet

```bash
# 1. Cargar las variables de entorno
export $(cat .env.local | xargs)

# 2. Iniciar el servicio
python main.py

# Deberías ver en los logs:
# ⚠️ TESTNET MODE ENABLED para copy_trading
#    Usando: https://testnet.binancefuture.com
```

## 🧪 Paso 5: Enviar un Trade de Prueba

Puedes usar `curl` o Postman para enviar un trade de prueba:

```bash
curl -X POST http://localhost:8000/execute-trade \
  -H "Content-Type: application/json" \
  -d '{
    "symbol": "BTCUSDT",
    "direction": "BUY",
    "entry_price": 45000.0,
    "stop_loss": 44000.0,
    "target_price": 47000.0,
    "rr": 2.0,
    "probability": 70,
    "strategy": "test_algo_orders",
    "signal_quality_score": 0.8
  }'
```

## 🔍 Paso 6: Verificar los Resultados

### En los Logs del Servicio

Busca estos mensajes que confirman el uso del Algo Order API:

```
✅ Orden MARKET ejecutada: BUY 0.05 BTCUSDT (copy_trading)
📉 Intentando crear STOP LOSS (copy_trading) en 44000.0 (SELL)
✅ Orden STOP_MARKET (CONTRACT_PRICE) creada via Algo API: SELL BTCUSDT (copy_trading) @ 44000.0
🎯 Intentando crear TAKE PROFIT en 47000.0 (SELL)
✅ Orden TAKE_PROFIT_MARKET (copy_trading) (closePosition=True) creada via Algo API: SELL BTCUSDT @ 47000.0
✅ Operación completada con SL y TP creados.
```

### En el Testnet de Binance

1. Ve a https://testnet.binancefuture.com/
2. Haz clic en "Futures" (arriba)
3. Verifica:
   - **Posición abierta** en BTCUSDT
   - **2 órdenes pendientes** (Stop Loss y Take Profit) en la sección de órdenes

### Verificar Algo Orders Manualmente

Puedes consultar las Algo Orders activas usando el endpoint:

```bash
# Reemplaza con tu API key y secret de testnet
curl -X GET "https://testnet.binancefuture.com/fapi/v1/algoOpenOrders?symbol=BTCUSDT&timestamp=..." \
  -H "X-MBX-APIKEY: tu_api_key"
```

## ✅ Checklist de Pruebas

Antes de pasar a producción, verifica:

- [ ] ✅ Orden de entrada (MARKET) se ejecuta correctamente
- [ ] ✅ Stop Loss se crea usando Algo Order API
- [ ] ✅ Take Profit se crea usando Algo Order API
- [ ] ✅ Ambas órdenes aparecen en Binance Testnet
- [ ] ✅ Las órdenes se cancelan correctamente si cierras manualmente la posición
- [ ] ✅ El trailing stop funciona (si lo tienes configurado)
- [ ] ✅ No hay errores `-4120` en los logs

## 🔄 Cambiar entre Testnet y Producción

### Para Testing (Testnet):
```bash
# En .env.local
USE_BINANCE_TESTNET=true
# Usar API keys de testnet
```

### Para Producción:
```bash
# En .env (o .env.production)
USE_BINANCE_TESTNET=false  # O simplemente omitir esta variable
# Usar API keys de producción REALES
```

### ⚠️ CRÍTICO: Nunca mezcles las API Keys

- **API Keys de Testnet**: Solo para `testnet.binancefuture.com`
- **API Keys de Producción**: Solo para `fapi.binance.com`
- Si intentas usar keys de producción en testnet (o viceversa), obtendrás errores de autenticación

## 🧹 Resetear Balance de Testnet

Si necesitas más fondos virtuales:

1. Ve a https://testnet.binancefuture.com/
2. Perfil → API Keys
3. Verás un botón "Reset Balance" o similar
4. Tu balance volverá a 10,000 USDT

## 🐛 Problemas Comunes

### Error: "Signature verification failed"
- ✅ Verifica que estás usando las API keys correctas (testnet vs producción)
- ✅ Verifica que `USE_BINANCE_TESTNET=true` esté configurado

### Error: "Invalid symbol"
- ✅ Asegúrate de que el símbolo existe en testnet (no todos los pares están disponibles)
- ✅ Usa pares populares como BTCUSDT, ETHUSDT, SOLUSDT

### Error: "Insufficient margin"
- ✅ Resetea tu balance en el testnet
- ✅ Reduce el tamaño de la orden de prueba

### No veo las órdenes SL/TP en Binance
- ✅ Busca en la sección "Algo Orders" o "Conditional Orders"
- ✅ No aparecen en "Open Orders" tradicionales

## 📚 Recursos Adicionales

- [Binance Futures Testnet](https://testnet.binancefuture.com/)
- [Documentación Algo Order API](https://developers.binance.com/docs/derivatives/usds-margined-futures/trade/rest-api/New-Algo-Order)
- [python-binance Docs](https://python-binance.readthedocs.io/)

## 🚀 Despliegue a Producción

Una vez que hayas verificado que todo funciona en Testnet:

1. **Cambia `USE_BINANCE_TESTNET=false`** en tu `.env` de producción
2. **Usa tus API Keys REALES de producción**
3. **Despliega** usando tu proceso normal (ej. `./deploy.sh`)
4. **Monitorea** los primeros trades de cerca para confirmar que todo funciona

---

**⚠️ RECUERDA**: Siempre prueba en Testnet antes de desplegar cambios críticos a producción.
