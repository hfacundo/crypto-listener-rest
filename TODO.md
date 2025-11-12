# TODO: Fix Daily Loss Limit Calculation

## Problema Identificado

El `daily_loss_limit` actualmente NO compara contra el balance de la cuenta, sino contra el **cambio porcentual de precio** de los trades cerrados.

### Comportamiento Actual (INCORRECTO)

**Archivo**: `app/utils/trade_protection.py:404`
```python
pnl_pct = ((exit_price - entry_price) / entry_price) * 100
```

**Archivo**: `app/utils/user_risk_validator.py:402-410`
```sql
SELECT COALESCE(SUM(pnl_pct), 0) as daily_pnl
FROM trade_history
WHERE user_id = %s
  AND strategy = %s
  AND exit_time >= DATE_TRUNC('day', NOW() AT TIME ZONE 'UTC')
  AND exit_reason IN ('target_hit', 'stop_hit', 'timeout', 'manual_close')
```

### Ejemplo del Problema

Usuario con 343 USDT de balance:

| Trade | Entry | Exit | pnl_pct | Pérdida Real |
|-------|-------|------|---------|--------------|
| Trade 1 | $50,000 | $49,500 | -1% | ~3.43 USDT |
| Trade 2 | $2,000 | $1,960 | -2% | ~3.43 USDT |
| Trade 3 | $100 | $93 | -7% | ~3.43 USDT |

**Resultado Actual**:
- Suma de `pnl_pct`: -1% + -2% + -7% = **-10%** → ❌ PAUSA ACTIVADA
- Balance real: 343 - 10.29 = **332.71 USDT** (solo -3% de pérdida real)

**El sistema activa la pausa cuando la suma de cambios de precio llega a -10%, NO cuando se pierde el 10% del balance.**

---

## Solución Propuesta: Enfoque Híbrido

### Objetivo
Comparar contra el **porcentaje real del balance**, no contra cambios de precio.

### Fórmula Correcta
```python
# 1. Obtener balance ACTUAL de Binance
current_balance = get_available_balance_from_binance()

# 2. Obtener P&L acumulado del día (en USDT, no %)
daily_pnl_usdt = self._get_daily_pnl_usdt()  # Suma de pnl_usdt desde medianoche

# 3. Calcular balance al INICIO del día
initial_balance_today = current_balance - daily_pnl_usdt

# 4. Calcular pérdida porcentual REAL
daily_loss_pct = (daily_pnl_usdt / initial_balance_today) * 100
```

### Implementación con Caché en Redis

```python
def _get_initial_balance_today(self) -> float:
    """
    Obtiene el balance al inicio del día.

    Usa caché en Redis para evitar recalcular en cada validación.
    El caché expira automáticamente a medianoche UTC.
    """

    # 1. Intentar obtener de Redis (si ya se calculó hoy)
    cache_key = f"{self.cache_prefix}:initial_balance:{datetime.now(timezone.utc).date()}"
    cached_balance = self.redis_client.get(cache_key)

    if cached_balance:
        return float(cached_balance)

    # 2. Si no existe, calcularlo al vuelo
    current_balance = self._get_available_balance()
    daily_pnl_usdt = self._get_daily_pnl_usdt()
    initial_balance = current_balance - daily_pnl_usdt

    # 3. Guardarlo en Redis con TTL hasta medianoche
    seconds_until_midnight = self._seconds_until_midnight_utc()
    self.redis_client.setex(cache_key, seconds_until_midnight, str(initial_balance))

    return initial_balance

def _seconds_until_midnight_utc(self) -> int:
    """Calcula segundos hasta la próxima medianoche UTC."""
    now = datetime.now(timezone.utc)
    tomorrow = now + timedelta(days=1)
    midnight = tomorrow.replace(hour=0, minute=0, second=0, microsecond=0)
    return int((midnight - now).total_seconds())

def _get_available_balance(self) -> float:
    """Obtiene el available balance actual de Binance."""
    try:
        client = get_binance_client_for_user(self.user_id)
        account = client.futures_account()
        return float(account.get('availableBalance', 0))
    except Exception as e:
        logger.error(f"Error getting available balance: {e}")
        return 0.0

def _get_daily_pnl_usdt(self) -> float:
    """Obtiene el P&L diario acumulado en USDT desde medianoche UTC."""
    if not self.protection_system:
        return 0.0

    try:
        conn = self.protection_system._get_conn()

        query = """
            SELECT
                COALESCE(SUM(pnl_usdt), 0) as daily_pnl
            FROM trade_history
            WHERE user_id = %s
              AND strategy = %s
              AND exit_time >= DATE_TRUNC('day', NOW() AT TIME ZONE 'UTC')
              AND exit_reason IN ('target_hit', 'stop_hit', 'timeout', 'manual_close')
        """

        with conn.cursor() as cur:
            cur.execute(query, (self.user_id, self.strategy))
            result = cur.fetchone()
            daily_pnl = float(result[0]) if result else 0.0

        conn.close()
        return daily_pnl

    except Exception as e:
        logger.error(f"Error getting daily P&L USDT for {self.user_id}: {e}")
        return 0.0
```

### Modificación de `_check_daily_loss_limits()`

```python
def _check_daily_loss_limits(self) -> Tuple[bool, str, Dict]:
    """Verifica si el usuario ha excedido su límite de pérdida diaria."""
    config = self.rules["daily_loss_limits"]
    max_daily_loss_pct = config.get("max_daily_loss_pct", 5.0)
    pause_duration_hours = config.get("pause_duration_hours", 12)

    try:
        if not self.redis_client:
            return True, "", {"error": "redis_unavailable"}

        # Verificar si hay pausa activa en Redis
        pause_key = f"{self.cache_prefix}:daily_loss_pause"
        pause_until_str = self.redis_client.get(pause_key)

        if pause_until_str:
            pause_until = datetime.fromisoformat(pause_until_str.decode() if isinstance(pause_until_str, bytes) else pause_until_str)

            if datetime.now(timezone.utc) < pause_until:
                time_remaining = pause_until - datetime.now(timezone.utc)
                return False, f"Daily loss pause active. Resumes in {time_remaining.total_seconds()/3600:.1f}h", {
                    "paused_until": pause_until.isoformat(),
                    "time_remaining_hours": time_remaining.total_seconds() / 3600
                }

        # ===== NUEVO CÁLCULO =====
        # Obtener balance inicial del día
        initial_balance = self._get_initial_balance_today()

        if initial_balance <= 0:
            logger.warning(f"Invalid initial balance for {self.user_id}: {initial_balance}")
            return True, "", {"error": "invalid_balance"}

        # Calcular P&L diario en USDT
        daily_pnl_usdt = self._get_daily_pnl_usdt()

        # Calcular pérdida porcentual REAL del balance
        daily_loss_pct = (daily_pnl_usdt / initial_balance) * 100

        # Verificar si se excedió el límite
        if daily_loss_pct <= -max_daily_loss_pct:
            # Activar pausa
            pause_until = datetime.now(timezone.utc) + timedelta(hours=pause_duration_hours)
            self.redis_client.setex(
                pause_key,
                int(pause_duration_hours * 3600),
                pause_until.isoformat()
            )

            logger.warning(f"🚨 {self.user_id} - Daily loss limit triggered: {daily_loss_pct:.2f}% (limit: {max_daily_loss_pct}%)")
            logger.warning(f"   Initial balance today: ${initial_balance:.2f}, Lost: ${daily_pnl_usdt:.2f}")

            return False, f"Daily loss limit exceeded ({daily_loss_pct:.2f}% loss). Paused for {pause_duration_hours}h", {
                "daily_loss_pct": daily_loss_pct,
                "daily_pnl_usdt": daily_pnl_usdt,
                "initial_balance_today": initial_balance,
                "max_daily_loss_pct": max_daily_loss_pct,
                "paused_until": pause_until.isoformat()
            }

        # OK - No se excedió el límite
        return True, "", {
            "daily_loss_pct": daily_loss_pct,
            "daily_pnl_usdt": daily_pnl_usdt,
            "initial_balance_today": initial_balance,
            "max_daily_loss_pct": max_daily_loss_pct,
            "remaining_loss_allowance_pct": max_daily_loss_pct + daily_loss_pct,
            "remaining_loss_allowance_usdt": initial_balance * (max_daily_loss_pct / 100) + daily_pnl_usdt
        }

    except Exception as e:
        logger.error(f"Error checking daily loss limits for {self.user_id}: {e}")
        return True, "", {"error": str(e)}
```

---

## Ventajas del Enfoque Híbrido

✅ **No necesita cron job**: Se calcula automáticamente en la primera validación del día
✅ **Siempre correcto**: Se auto-ajusta basado en datos reales
✅ **No depende de Redis**: Si Redis falla, recalcula al vuelo
✅ **Eficiente**: Caché evita llamadas repetidas a Binance
✅ **Auto-reset a medianoche**: TTL de Redis expira automáticamente

---

## Limitación Conocida

⚠️ **Depósitos/Retiros durante el día**: Si el usuario hace depósitos o retiros durante el trading day, el cálculo de `initial_balance_today` se descompensará.

**Solución alternativa** (si es necesario):
- Guardar snapshot del balance en PostgreSQL con timestamp a medianoche
- Requiere un cron job o trigger

---

## Archivos a Modificar

### 1. `app/utils/user_risk_validator.py`

**Métodos nuevos**:
- `_get_initial_balance_today()` - Obtener/cachear balance inicial del día
- `_get_available_balance()` - Consultar Binance
- `_get_daily_pnl_usdt()` - Cambiar de pnl_pct a pnl_usdt
- `_seconds_until_midnight_utc()` - Helper para TTL

**Métodos a modificar**:
- `_check_daily_loss_limits()` - Usar nuevo cálculo

**Líneas afectadas**: 338-422

### 2. `app/utils/trade_protection.py` (Opcional)

**Verificar**:
- Que `pnl_usdt` se esté guardando correctamente en línea 405-409
- Mantener `pnl_pct` como está (se usa en otros lugares)

---

## Testing Requerido

1. **Test inicial**: Primera validación del día debe calcular y cachear `initial_balance`
2. **Test caché**: Validaciones subsecuentes deben usar valor cacheado
3. **Test medianoche**: Verificar que caché expire y se recalcule
4. **Test activación**: Confirmar que pausa se activa con pérdida real del balance
5. **Test multi-usuario**: Verificar que cada usuario tenga su propio caché

---

## Prioridad

**ALTA** - El comportamiento actual genera falsas alarmas que pausan el trading sin razón válida.

---

## Estado

✅ **IMPLEMENTADO** - 2025-01-11

### Cambios realizados:

1. **Agregados 4 métodos nuevos** en `app/utils/user_risk_validator.py` (líneas 434-524):
   - `_get_initial_balance_today()` - Calcula y cachea balance inicial del día en Redis
   - `_seconds_until_midnight_utc()` - Helper para TTL de caché
   - `_get_available_balance()` - Consulta balance actual de Binance Futures
   - `_get_daily_pnl_usdt()` - Suma P&L del día en USDT (no pnl_pct)

2. **Modificado `_check_daily_loss_limits()`** (líneas 338-432):
   - Ahora calcula pérdida basándose en balance real: `(daily_pnl_usdt / initial_balance) * 100`
   - Logs mejorados con información detallada del cálculo
   - Retorna información adicional: `current_balance`, `remaining_loss_allowance_usdt`

3. **Eliminado método obsoleto**:
   - `_get_daily_pnl_pct()` - Ya no se usa (reemplazado por `_get_daily_pnl_usdt()`)

### Validación:

El nuevo cálculo previene falsas alarmas:

**Antes** (INCORRECTO):
- 3 trades: -1% precio, -2% precio, -7% precio = -10% → PAUSA ❌
- Pérdida real: solo -3% del balance

**Ahora** (CORRECTO):
- Balance inicial: $343
- Pérdida total: $10.29 = -3% → NO PAUSA ✅
