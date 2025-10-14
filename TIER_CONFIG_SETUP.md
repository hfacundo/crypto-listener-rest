# Tier Config Setup - Filtrado por Usuario

## 📊 Resumen

Este documento explica cómo configurar el **tier filtering** por usuario en crypto-listener-rest. Permite que cada usuario tenga su propio nivel de riesgo, aceptando solo los tiers que quiera.

---

## 🎯 Concepto

**crypto-analyzer-redis** asigna un **tier (1-10)** a cada señal:
- **Tier 1-3**: Excelente, Bueno, Aceptable (alta calidad)
- **Tier 4-6**: Compensación (una métrica alta compensa otra baja)
- **Tier 7**: Balanced Moderate (ambos moderados)
- **Tier 8**: Marginal (ambos bajos pero aceptables)
- **Tier 9**: EV Excepcional (EV alto compensa métricas bajas)
- **Tier 10**: Rechazado

Cada usuario puede configurar **hasta qué tier acepta**:
- **Usuarios agresivos** (hufsa, futures): Aceptan tier 1-9 (todos)
- **Usuarios conservadores** (copy_trading, copy_2): Aceptan solo tier 1-7

---

## 📋 Configuración en Base de Datos

### **Estructura JSON en campo `rules`**

Agregar al JSON de cada usuario en la tabla `rules`:

```json
{
  "tier_config": {
    "enabled": true,
    "max_tier_accepted": 7,
    "description": "Conservative - only accept high quality trades (tier 1-7)"
  }
}
```

### **Campos:**
- **`enabled`** (boolean): Si false, acepta todos los tiers
- **`max_tier_accepted`** (int 1-10): Tier máximo que acepta este usuario
- **`description`** (string): Descripción del perfil de riesgo

---

## 🔧 SQL para Actualizar Usuarios

### **1. HUFSA - Agresivo (acepta todo: tier 1-9)**

```sql
UPDATE rules
SET rules = jsonb_set(
    rules::jsonb,
    '{tier_config}',
    '{
      "enabled": true,
      "max_tier_accepted": 9,
      "description": "Aggressive - accept all viable trades (tier 1-9)"
    }'::jsonb
)
WHERE user_id = 'hufsa' AND strategy = 'archer_dual';
```

### **2. FUTURES - Agresivo (acepta todo: tier 1-9)**

```sql
UPDATE rules
SET rules = jsonb_set(
    rules::jsonb,
    '{tier_config}',
    '{
      "enabled": true,
      "max_tier_accepted": 9,
      "description": "Aggressive - accept all viable trades (tier 1-9)"
    }'::jsonb
)
WHERE user_id = 'futures' AND strategy = 'archer_dual';
```

### **3. COPY_TRADING - Conservador (solo tier 1-7)**

```sql
UPDATE rules
SET rules = jsonb_set(
    rules::jsonb,
    '{tier_config}',
    '{
      "enabled": true,
      "max_tier_accepted": 7,
      "description": "Conservative - only high quality trades (tier 1-7)"
    }'::jsonb
)
WHERE user_id = 'copy_trading' AND strategy = 'archer_dual';
```

### **4. COPY_2 - Conservador (solo tier 1-7)**

```sql
UPDATE rules
SET rules = jsonb_set(
    rules::jsonb,
    '{tier_config}',
    '{
      "enabled": true,
      "max_tier_accepted": 7,
      "description": "Conservative - only high quality trades (tier 1-7)"
    }'::jsonb
)
WHERE user_id = 'copy_2' AND strategy = 'archer_dual';
```

---

## 📊 Comportamiento por Tier

| Tier | Descripción | HUFSA | FUTURES | COPY_TRADING | COPY_2 |
|------|-------------|-------|---------|--------------|--------|
| 1 | EXCELENTE | ✅ | ✅ | ✅ | ✅ |
| 2 | BUENO | ✅ | ✅ | ✅ | ✅ |
| 3 | ACEPTABLE | ✅ | ✅ | ✅ | ✅ |
| 4 | COMPENSACIÓN HIGH SQS | ✅ | ✅ | ✅ | ✅ |
| 5 | COMPENSACIÓN HIGH PROB | ✅ | ✅ | ✅ | ✅ |
| 6 | COMPENSACIÓN MODERATE | ✅ | ✅ | ✅ | ✅ |
| 7 | BALANCED MODERATE | ✅ | ✅ | ✅ | ✅ |
| 8 | MARGINAL | ✅ | ✅ | ❌ | ❌ |
| 9 | EV EXCEPCIONAL | ✅ | ✅ | ❌ | ❌ |

---

## 🧪 Ejemplos de Uso

### **Escenario 1: Trade Tier 6 (Compensación Moderate)**
```
crypto-analyzer-redis envía: tier=6, SQS=45, Prob=59%
↓
HUFSA: max_tier=9 → ✅ ACEPTA
FUTURES: max_tier=9 → ✅ ACEPTA
COPY_TRADING: max_tier=7 → ✅ ACEPTA
COPY_2: max_tier=7 → ✅ ACEPTA
```

### **Escenario 2: Trade Tier 8 (Marginal)**
```
crypto-analyzer-redis envía: tier=8, SQS=50, Prob=53%
↓
HUFSA: max_tier=9 → ✅ ACEPTA
FUTURES: max_tier=9 → ✅ ACEPTA
COPY_TRADING: max_tier=7 → ❌ RECHAZA "tier 8 > max_tier_accepted 7"
COPY_2: max_tier=7 → ❌ RECHAZA "tier 8 > max_tier_accepted 7"
```

### **Escenario 3: Trade Tier 9 (EV Excepcional)**
```
crypto-analyzer-redis envía: tier=9, SQS=47, Prob=55%, EV=0.27
↓
HUFSA: max_tier=9 → ✅ ACEPTA
FUTURES: max_tier=9 → ✅ ACEPTA
COPY_TRADING: max_tier=7 → ❌ RECHAZA "tier 9 > max_tier_accepted 7"
COPY_2: max_tier=7 → ❌ RECHAZA "tier 9 > max_tier_accepted 7"
```

---

## 🔍 Logs de Rechazo

Cuando un usuario rechaza por tier, verás:

```
[copy_trading] Trade REJECTED: TIER_REJECTED: tier 8 exceeds max_tier_accepted 7 (too aggressive for this user)
🎯 copy_trading - TIER REJECTED: tier 8 > max_tier_accepted 7
```

---

## ⚙️ Desactivar Tier Filtering

Para desactivar el filtrado de tier (aceptar todos):

```sql
UPDATE rules
SET rules = jsonb_set(
    rules::jsonb,
    '{tier_config}',
    '{
      "enabled": false,
      "max_tier_accepted": 10,
      "description": "Disabled - accept all tiers"
    }'::jsonb
)
WHERE user_id = 'nombre_usuario' AND strategy = 'archer_dual';
```

---

## 📈 Perfiles de Riesgo Recomendados

### **Ultra Conservador** (solo tier 1-5)
```json
{
  "tier_config": {
    "enabled": true,
    "max_tier_accepted": 5,
    "description": "Ultra conservative - only premium signals"
  }
}
```

### **Conservador** (tier 1-7) ⭐ Recomendado para copy trading
```json
{
  "tier_config": {
    "enabled": true,
    "max_tier_accepted": 7,
    "description": "Conservative - balanced quality trades"
  }
}
```

### **Balanceado** (tier 1-8)
```json
{
  "tier_config": {
    "enabled": true,
    "max_tier_accepted": 8,
    "description": "Balanced - accept most viable trades"
  }
}
```

### **Agresivo** (tier 1-9) ⭐ Recomendado para cuentas propias
```json
{
  "tier_config": {
    "enabled": true,
    "max_tier_accepted": 9,
    "description": "Aggressive - maximize signal volume"
  }
}
```

---

## 🚀 Deployment

1. **Ejecutar SQL** de actualización para cada usuario
2. **Restart** crypto-listener-rest:
   ```bash
   sudo systemctl restart crypto-listener
   ```
3. **Verificar** en logs que tier_config se lee correctamente

---

## 📝 Notas

- **Default behavior**: Si `tier_config` no existe o `enabled=false`, acepta todos los tiers
- **Backward compatible**: Señales sin tier (antiguas) no son afectadas por este filtro
- **Orden de validación**: Tier filtering es la PRIMERA validación (antes de circuit breaker)
- **Granularidad**: Control por usuario, no por símbolo
