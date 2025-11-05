# Scripts para consultar Daily Loss Pause desde EC2

## 📋 Scripts disponibles

### 1. `check_pause_status_ec2.sh` (Completo)
Script detallado que muestra:
- Estado de la pausa (activa/inactiva)
- Cuándo comenzó y cuándo termina
- P&L diario actual
- Lista de todos los trades cerrados hoy

**Uso:**
```bash
# Conectarse a EC2
ssh tu-servidor-ec2

# Ir al directorio del proyecto
cd /path/to/crypto-listener-rest/scripts

# Dar permisos de ejecución
chmod +x check_pause_status_ec2.sh

# Ejecutar para usuario hufsa (default)
./check_pause_status_ec2.sh

# O para otro usuario
./check_pause_status_ec2.sh copy_trading
```

---

### 2. `quick_check_ec2.sh` (Rápido)
Versión simplificada que muestra solo lo esencial.

**Uso:**
```bash
chmod +x quick_check_ec2.sh
./quick_check_ec2.sh
```

---

## 🔧 Comandos manuales (sin scripts)

### Ver pausa en Redis
```bash
redis-cli GET "user_risk:hufsa:archer_dual:daily_loss_pause"
```

### Ver P&L de hoy
```bash
psql $DATABASE_URL_CRYPTO_TRADER -c "
SELECT
    COUNT(*) as trades,
    ROUND(COALESCE(SUM(pnl_pct), 0)::numeric, 2) as daily_pnl_pct,
    ROUND(COALESCE(SUM(pnl_usdt), 0)::numeric, 2) as daily_pnl_usdt
FROM trade_history
WHERE user_id = 'hufsa'
  AND strategy = 'archer_dual'
  AND exit_time >= DATE_TRUNC('day', NOW() AT TIME ZONE 'UTC')
  AND exit_reason IN ('target_hit', 'stop_hit', 'timeout', 'manual_close');
"
```

### Ver trades cerrados hoy
```bash
psql $DATABASE_URL_CRYPTO_TRADER -c "
SELECT symbol, direction, exit_reason,
       ROUND(pnl_pct::numeric, 2) as pnl_pct,
       TO_CHAR(exit_time, 'HH24:MI:SS') as time
FROM trade_history
WHERE user_id = 'hufsa'
  AND strategy = 'archer_dual'
  AND exit_time >= DATE_TRUNC('day', NOW() AT TIME ZONE 'UTC')
ORDER BY exit_time DESC;
"
```

---

## 🗑️ Eliminar pausa manualmente (CUIDADO)

Si quieres **forzar** la eliminación de la pausa antes de tiempo:

```bash
# Ver cuánto falta
redis-cli GET "user_risk:hufsa:archer_dual:daily_loss_pause"

# Eliminar la pausa (solo si estás seguro)
redis-cli DEL "user_risk:hufsa:archer_dual:daily_loss_pause"
```

⚠️ **ADVERTENCIA**: Esto desactiva la protección. Solo hazlo si:
- Estás seguro de que fue un falso positivo
- Entiendes los riesgos
- Quieres permitir trading antes del tiempo de pausa

---

## 📊 Modificar configuración de límites

La configuración está en: `app/utils/db/local_rules.py`

Para **hufsa**, busca la sección `daily_loss_limits`:

```python
"daily_loss_limits": {
    "enabled": True,
    "max_daily_loss_pct": 6.0,      # Cambiar este valor
    "pause_duration_hours": 12,      # Cambiar duración de pausa
    "reset_time_utc": "00:00"
}
```

Después de modificar, **reiniciar el servicio**:
```bash
sudo systemctl restart crypto-listener
```

---

## 🔍 Entender el cálculo

El sistema suma el `pnl_pct` de **todos los trades cerrados desde medianoche UTC**:

```
Ejemplo:
- Trade 1: -2.5% (STOP HIT)
- Trade 2: +1.0% (TARGET HIT)
- Trade 3: -3.0% (STOP HIT)
- Trade 4: -1.8% (STOP HIT)
----------------------------
Total: -6.3% → PAUSA ACTIVADA (límite: -6%)
```

Cuando el total supera `-6%`, se activa una pausa de 12 horas.
