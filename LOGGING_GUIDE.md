# Guía de Migración a Logging con RotatingFileHandler

## Configuración

El sistema de logging está configurado en `app/utils/logger_config.py` con:
- **Tamaño máximo por archivo:** 5MB
- **Archivos de backup:** 1 (total 10MB máximo)
- **Formato:** `YYYY-MM-DD HH:MM:SS | NIVEL | Mensaje`
- **Ubicación:** `logs/crypto-listener.log`

### Archivos generados

```
logs/
├── crypto-listener.log       # Archivo activo (hasta 5MB)
└── crypto-listener.log.1     # Backup (se elimina al rotar nuevamente)
```

## Uso del Logger

### 1. Importar el logger en tu archivo

```python
from app.utils.logger_config import get_logger

logger = get_logger(__name__)  # __name__ identifica el módulo
```

### 2. Niveles de log disponibles

#### `logger.info()` - Información general
Usar para flujo normal del programa, confirmaciones, status updates.

```python
# Antes
print(f"✅ Trade exitoso para {symbol}")
print(f"🔄 Estado actualizado: {status}")

# Después
logger.info(f"✅ Trade exitoso para {symbol}")
logger.info(f"🔄 Estado actualizado: {status}")
```

#### `logger.warning()` - Advertencias no críticas
Usar para situaciones inusuales que no son errores pero merecen atención.

```python
# Antes
print(f"⚠️ No se encontró trade para {symbol}")
print(f"⚠️ Capital bajo: {capital}%")

# Después
logger.warning(f"⚠️ No se encontró trade para {symbol}")
logger.warning(f"⚠️ Capital bajo: {capital}%")
```

#### `logger.error()` - Errores y excepciones
Usar para errores, fallos, excepciones.

```python
# Antes
print(f"❌ Error al conectar con Binance: {e}")
print(f"❌ Trade falló: {reason}")

# Después
logger.error(f"❌ Error al conectar con Binance: {e}")
logger.error(f"❌ Trade falló: {reason}")

# Con traceback completo
try:
    result = risky_operation()
except Exception as e:
    logger.error(f"❌ Error crítico: {e}", exc_info=True)  # exc_info=True incluye traceback
```

#### `logger.debug()` - Debugging detallado
Usar para información técnica de debugging (normalmente deshabilitado en producción).

```python
# Antes
print(f"🔍 DEBUG: Variable x = {x}")
print(f"DEBUG: Checking condition...")

# Después
logger.debug(f"🔍 Variable x = {x}")
logger.debug(f"Checking condition...")
```

## Conversión Automática

Usa el script `convert_prints_to_logger.py` para convertir automáticamente:

```bash
# Ver qué cambios se harían (sin modificar)
python convert_prints_to_logger.py main.py --dry-run

# Aplicar cambios
python convert_prints_to_logger.py main.py
```

El script detecta automáticamente el nivel apropiado:
- **error:** Mensajes con ❌, "error", "failed", "exception"
- **warning:** Mensajes con ⚠️, "warning", "no se encontr"
- **debug:** Mensajes con 🔍, "debug"
- **info:** Todo lo demás

## Ejemplo de Migración Completo

### Antes (usando print)

```python
# app/futures.py

def create_trade(symbol, entry, stop, target, direction, ...):
    print(f"📩 Creating trade for {symbol}")

    try:
        print(f"🔍 Validating parameters...")

        if not validate_params(entry, stop):
            print(f"❌ Invalid parameters for {symbol}")
            return None

        order = binance_client.create_order(...)
        print(f"✅ Order created: {order['orderId']}")
        return order

    except Exception as e:
        print(f"❌ Error creating trade: {e}")
        import traceback
        traceback.print_exc()
        return None
```

### Después (usando logger)

```python
# app/futures.py
from app.utils.logger_config import get_logger

logger = get_logger(__name__)

def create_trade(symbol, entry, stop, target, direction, ...):
    logger.info(f"📩 Creating trade for {symbol}")

    try:
        logger.debug(f"🔍 Validating parameters...")

        if not validate_params(entry, stop):
            logger.error(f"❌ Invalid parameters for {symbol}")
            return None

        order = binance_client.create_order(...)
        logger.info(f"✅ Order created: {order['orderId']}")
        return order

    except Exception as e:
        logger.error(f"❌ Error creating trade: {e}", exc_info=True)
        return None
```

## Archivos Principales a Migrar

Ejecuta el script en estos archivos (en orden):

```bash
# 1. Main entry point
python convert_prints_to_logger.py main.py

# 2. Core modules
python convert_prints_to_logger.py app/futures.py
python convert_prints_to_logger.py app/multi_user_execution.py
python convert_prints_to_logger.py app/market_validation.py
python convert_prints_to_logger.py app/trade_limits.py

# 3. Utils
python convert_prints_to_logger.py app/utils/db/query_executor.py
python convert_prints_to_logger.py app/utils/binance/binance_client.py
python convert_prints_to_logger.py app/utils/trade_protection.py
python convert_prints_to_logger.py app/utils/user_risk_validator.py
```

## Verificar Logs

Después de implementar, verifica que los logs se estén escribiendo:

```bash
# Ver logs en tiempo real
tail -f logs/crypto-listener.log

# Ver últimas 100 líneas
tail -100 logs/crypto-listener.log

# Buscar errores
grep ERROR logs/crypto-listener.log

# Ver tamaño de archivos
ls -lh logs/
```

## Comando nohup (Sin Cambios)

Tu comando nohup actual sigue funcionando igual:

```bash
nohup uvicorn main:app --host 127.0.0.1 --port 8000 > uvicorn.log 2>&1 &
```

**Resultado:**
- `uvicorn.log` → Logs de uvicorn (startup, requests HTTP)
- `logs/crypto-listener.log` → Logs de tu aplicación (rotados a 5MB)

Ambos logs son complementarios:
- **uvicorn.log:** INFO sobre HTTP requests, startup
- **crypto-listener.log:** DEBUG/INFO/ERROR de tu lógica de negocio

## Configuración Avanzada

Si necesitas ajustar el tamaño o cantidad de backups:

```python
# app/utils/logger_config.py

logger = setup_logger(
    name="crypto-listener-rest",
    log_file="logs/crypto-listener.log",
    max_bytes=10 * 1024 * 1024,  # 10MB en lugar de 5MB
    backup_count=3,               # Más backups si lo necesitas
    level=logging.INFO            # o logging.DEBUG para más detalle
)
```

## Troubleshooting

### El directorio logs/ no existe
Se crea automáticamente al iniciar la aplicación.

### Permisos de escritura
```bash
mkdir -p logs
chmod 755 logs
```

### Ver estadísticas de logging
```python
from app.utils.logger_config import get_logger
import logging

logger = get_logger("crypto-listener-rest")

# Cambiar nivel en runtime
logger.setLevel(logging.DEBUG)

# Ver handlers configurados
for handler in logger.handlers:
    print(f"Handler: {handler}")
    print(f"  Level: {handler.level}")
```
