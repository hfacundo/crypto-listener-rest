# Schedule Configuration Guide

## 📅 Configuración de Horarios de Trading

Esta guía explica cómo configurar horarios permitidos para trading por usuario.

---

## ✅ Fix Aplicado (2025-12-02)

Se corrigió el bug en `app/utils/binance/utils.py:269-284` para soportar tanto:
- **PostgreSQL jsonb** (devuelve dict directamente)
- **String JSON** (legacy, por compatibilidad)

---

## 🔧 Estructura en Base de Datos

### Tabla: `user_rules`
### Columna: `rules_config` (tipo: `jsonb`)

```json
{
  "schedule": {
    "enabled": true,
    "timezone": "UTC",
    "Monday": [["09:00", "17:00"]],
    "Tuesday": [["09:00", "17:00"]],
    "Wednesday": [["09:00", "17:00"]],
    "Thursday": [["09:00", "17:00"]],
    "Friday": [["09:00", "17:00"]],
    "Saturday": [],
    "Sunday": []
  }
}
```

---

## 📋 Campos del Schedule

### `enabled` (boolean) - **REQUERIDO**
- `true`: Activa la validación de horarios
- `false`: Desactiva (permite operar 24/7)

### `timezone` (string) - **INFORMATIVO**
- Actualmente **no se usa** en el código (hardcoded a UTC)
- Se mantiene para futura compatibilidad

### Días de la semana (array de arrays) - **REQUERIDO SI enabled=true**
- **Nombres válidos**: `Monday`, `Tuesday`, `Wednesday`, `Thursday`, `Friday`, `Saturday`, `Sunday`
- **Formato exacto**: Primera letra mayúscula, resto minúsculas
- **Array vacío `[]`**: No se permite operar ese día
- **Array con horarios**: `[["HH:MM", "HH:MM"], ...]`

---

## 📖 Ejemplos de Configuración

### Ejemplo 1: Lunes a Viernes 9am-5pm UTC
```json
{
  "schedule": {
    "enabled": true,
    "timezone": "UTC",
    "Monday": [["09:00", "17:00"]],
    "Tuesday": [["09:00", "17:00"]],
    "Wednesday": [["09:00", "17:00"]],
    "Thursday": [["09:00", "17:00"]],
    "Friday": [["09:00", "17:00"]],
    "Saturday": [],
    "Sunday": []
  }
}
```

### Ejemplo 2: Múltiples Ventanas (con descanso al mediodía)
```json
{
  "schedule": {
    "enabled": true,
    "timezone": "UTC",
    "Monday": [
      ["08:00", "12:00"],
      ["14:00", "20:00"]
    ],
    "Tuesday": [
      ["08:00", "12:00"],
      ["14:00", "20:00"]
    ],
    "Wednesday": [
      ["08:00", "12:00"],
      ["14:00", "20:00"]
    ],
    "Thursday": [
      ["08:00", "12:00"],
      ["14:00", "20:00"]
    ],
    "Friday": [
      ["08:00", "12:00"],
      ["14:00", "18:00"]
    ],
    "Saturday": [],
    "Sunday": []
  }
}
```

### Ejemplo 3: Solo Horario de NY (8:30am-3pm ET = 13:30-20:00 UTC)
```json
{
  "schedule": {
    "enabled": true,
    "timezone": "UTC",
    "Monday": [["13:30", "20:00"]],
    "Tuesday": [["13:30", "20:00"]],
    "Wednesday": [["13:30", "20:00"]],
    "Thursday": [["13:30", "20:00"]],
    "Friday": [["13:30", "20:00"]],
    "Saturday": [],
    "Sunday": []
  }
}
```

### Ejemplo 4: 24/7 Excepto Fines de Semana
```json
{
  "schedule": {
    "enabled": true,
    "timezone": "UTC",
    "Monday": [["00:00", "23:59"]],
    "Tuesday": [["00:00", "23:59"]],
    "Wednesday": [["00:00", "23:59"]],
    "Thursday": [["00:00", "23:59"]],
    "Friday": [["00:00", "23:59"]],
    "Saturday": [],
    "Sunday": []
  }
}
```

### Ejemplo 5: Horario de México (10am-4pm CDMX = 16:00-22:00 UTC)
```json
{
  "schedule": {
    "enabled": true,
    "timezone": "UTC",
    "Monday": [["16:00", "22:00"]],
    "Tuesday": [["16:00", "22:00"]],
    "Wednesday": [["16:00", "22:00"]],
    "Thursday": [["16:00", "22:00"]],
    "Friday": [["16:00", "22:00"]],
    "Saturday": [],
    "Sunday": []
  }
}
```

---

## 🌍 Conversión de Zona Horaria a UTC

**IMPORTANTE**: Todos los horarios se evalúan en UTC. Debes convertir tus horarios locales.

### Ciudad de México (UTC-6)
- **10:00 AM CDMX** → **16:00 UTC** (sumar 6 horas)
- **4:00 PM CDMX** → **22:00 UTC** (sumar 6 horas)

### Nueva York (UTC-5 en invierno / UTC-4 en verano)
- **8:30 AM ET** → **13:30 UTC** (sumar 5 horas en invierno)
- **3:00 PM ET** → **20:00 UTC** (sumar 5 horas en invierno)

### Londres (UTC+0 en invierno / UTC+1 en verano)
- **9:00 AM GMT** → **09:00 UTC** (sin cambio en invierno)
- **5:00 PM GMT** → **17:00 UTC** (sin cambio en invierno)

---

## 💾 Query SQL para Actualizar Schedule

### Para usuario específico:
```sql
UPDATE user_rules
SET rules_config = jsonb_set(
    rules_config,
    '{schedule}',
    '{
      "enabled": true,
      "timezone": "UTC",
      "Monday": [["16:00", "22:00"]],
      "Tuesday": [["16:00", "22:00"]],
      "Wednesday": [["16:00", "22:00"]],
      "Thursday": [["16:00", "22:00"]],
      "Friday": [["16:00", "22:00"]],
      "Saturday": [],
      "Sunday": []
    }'::jsonb
)
WHERE user_id = 'hufsa' AND strategy = 'archer_model';
```

### Habilitar schedule:
```sql
UPDATE user_rules
SET rules_config = jsonb_set(rules_config, '{schedule,enabled}', 'true')
WHERE user_id = 'hufsa' AND strategy = 'archer_model';
```

### Deshabilitar schedule:
```sql
UPDATE user_rules
SET rules_config = jsonb_set(rules_config, '{schedule,enabled}', 'false')
WHERE user_id = 'hufsa' AND strategy = 'archer_model';
```

---

## 🧪 Cómo Probar

### 1. Verificar configuración actual:
```sql
SELECT user_id, rules_config->'schedule'
FROM user_rules
WHERE user_id = 'hufsa' AND strategy = 'archer_model';
```

### 2. Enviar un trade DENTRO del horario permitido:
- **Resultado esperado**:
  ```
  ✅ Permitido: dentro del rango 16:00-22:00
  [hufsa] ALL VALIDATIONS PASSED
  [hufsa] Trade exitoso
  ```

### 3. Enviar un trade FUERA del horario permitido:
- **Resultado esperado**:
  ```
  ⛔ Operación rechazada: fuera del horario permitido en Monday (UTC)
  [hufsa] Trade REJECTED: SCHEDULE: Outside trading hours (Monday 23:15:42 UTC)
  ```

### 4. Enviar un trade en día NO permitido (ejemplo: sábado):
- **Resultado esperado**:
  ```
  ⛔ Operación rechazada: no permitido en Saturday
  [hufsa] Trade REJECTED: SCHEDULE: Outside trading hours (Saturday 14:30:00 UTC)
  ```

---

## 📊 Flujo de Validación

```
1. Request de trade llega a /execute-trade
         ↓
2. main.py:179 → validator.validate_trade()
         ↓
3. user_risk_validator.py:187 → Verifica if schedule.enabled == true
         ↓
4. user_risk_validator.py:188 → Llama _check_schedule()
         ↓
5. utils.py:269 → is_trade_allowed_by_schedule_utc()
         ↓
6. Evalúa día y hora actual en UTC contra schedule
         ↓
7. Retorna True (permitido) o False (rechazado)
```

---

## 🛡️ Comportamiento de Seguridad

Si ocurre un error durante la validación del schedule:
- **El sistema permite el trade** (fail-safe)
- **Se registra el error** en logs
- **Razón**: Evitar que un bug bloquee todas las operaciones

```python
except Exception as e:
    print(f"❌ Error al interpretar schedule: {e}")
    return True  # Por seguridad, asumimos que se permite
```

---

## 📝 Notas Importantes

1. **Formato de tiempo**: Debe ser `"HH:MM"` en formato 24 horas
2. **Validación inclusiva**: `start <= current_time <= end`
3. **No hay validación de rangos traslapados**: Puedes tener múltiples ventanas que se solapan
4. **Días faltantes**: Si un día no está en el schedule, se asume `[]` (no permitido)
5. **Formato case-sensitive**: `"Monday"` ✅ | `"monday"` ❌ | `"MONDAY"` ❌

---

## 🔗 Archivos Relacionados

- `app/utils/binance/utils.py:269-297` - Función principal de validación
- `app/utils/user_risk_validator.py:185-192` - Integración con validador
- `app/utils/user_risk_validator.py:597-609` - Helper _check_schedule()
- `main.py:35` - Import (aunque no se usa directamente en main)
