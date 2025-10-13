# Guía de Migración: Lambda → crypto-listener-rest

Esta guía te ayudará a migrar de `crypto-listener` (Lambda + SNS) a `crypto-listener-rest` (EC2 + REST API).

## 📋 Resumen de Cambios

| Componente | Antes (Lambda) | Después (REST) |
|------------|----------------|----------------|
| **Arquitectura** | SNS → Lambda | HTTP POST → EC2 Service |
| **Costo** | $32-50/mes (NAT Gateway) | **$0** |
| **Latencia** | 100-500ms | <10ms |
| **Procesamiento** | Asíncrono (fire-and-forget) | Síncrono (con respuesta) |
| **Retry** | Automático (SNS) | No (por diseño - trades time-sensitive) |
| **Logs** | CloudWatch | journalctl |
| **DB Variable** | `DATABASE_URL` | `DATABASE_URL_CRYPTO_TRADER` (mismo que crypto-analyzer-redis) |

## 🚀 Pasos de Migración

### 1. Preparar crypto-listener-rest en EC2

#### 1.1. Subir código a GitHub
```bash
cd /mnt/d/Development/python/crypto-listener-rest
git init
git add .
git commit -m "Initial commit: crypto-listener-rest"
git remote add origin YOUR_GITHUB_REPO
git push -u origin main
```

#### 1.2. Clonar en EC2
```bash
ssh ubuntu@YOUR_EC2_IP

cd ~
git clone YOUR_GITHUB_REPO crypto-listener-rest
cd crypto-listener-rest
```

#### 1.3. Configurar variables de entorno

**Agregar a ~/.bashrc:**
```bash
nano ~/.bashrc

# Agregar al final (o usar el método automático del ENVIRONMENT_SETUP.md):

# Database (mismo nombre que crypto-analyzer-redis)
export DATABASE_URL_CRYPTO_TRADER="postgresql://app_user:YOUR_PASSWORD@localhost:5432/crypto_trader"

# Redis
export REDIS_HOST="localhost"
export REDIS_PORT="6379"
export REDIS_DB="0"

# Environment
export DEPLOYMENT_ENV="main"

# Binance API Keys (copiar las mismas del template.yaml)
export BINANCE_FUTURES_API_KEY_COPY="..."
export BINANCE_FUTURES_API_SECRET_COPY="..."
export BINANCE_FUTURES_API_KEY_HUFSA="..."
export BINANCE_FUTURES_API_SECRET_HUFSA="..."
export BINANCE_FUTURES_API_KEY_COPY_2="..."
export BINANCE_FUTURES_API_SECRET_COPY_2="..."
export BINANCE_FUTURES_API_KEY_FUTURES="..."
export BINANCE_FUTURES_API_SECRET_FUTURES="..."
```

**Recargar:**
```bash
source ~/.bashrc
```

**Verificar:**
```bash
echo $DATABASE_URL_CRYPTO_TRADER
```

#### 1.4. Instalar servicio
```bash
cd ~/crypto-listener-rest

# Crear venv
python3 -m venv venv
source venv/bin/activate

# Instalar dependencias
pip install -r requirements.txt

# Copiar service file
sudo cp crypto-listener.service /etc/systemd/system/

# Editar service con password real
sudo nano /etc/systemd/system/crypto-listener.service
# Buscar DATABASE_URL_CRYPTO_TRADER y reemplazar YOUR_PASSWORD

# Recargar systemd
sudo systemctl daemon-reload
```

#### 1.5. Probar manualmente primero
```bash
cd ~/crypto-listener-rest
source venv/bin/activate
python main.py
```

En otra terminal:
```bash
curl http://localhost:8000/health
```

Si funciona, detener con Ctrl+C y continuar.

#### 1.6. Iniciar como servicio
```bash
sudo systemctl start crypto-listener
sudo systemctl enable crypto-listener
sudo systemctl status crypto-listener
```

#### 1.7. Verificar logs
```bash
sudo journalctl -u crypto-listener -f
```

#### 1.8. Ejecutar pruebas
```bash
cd ~/crypto-listener-rest
source venv/bin/activate
python test_integration.py
```

---

### 2. Actualizar crypto-analyzer-redis

crypto-analyzer-redis ya ha sido actualizado para usar REST API en lugar de SNS:

✅ **Cambios realizados:**
- `app/utils/message_rest.py` - Nuevo módulo REST (reemplaza message_sns.py)
- `app/scheduler.py` - Actualizado para importar y usar message_rest
- Logs cambiados de "SNS SENT" a "HTTP POST sent"

#### 2.1. Pull cambios desde GitHub (si actualizaste el repo)
```bash
ssh ubuntu@YOUR_EC2_IP

cd ~/crypto-analyzer-redis
git pull origin main
```

#### 2.2. Reiniciar crypto-analyzer-redis
```bash
sudo systemctl restart crypto-analyzer-redis
sudo journalctl -u crypto-analyzer-redis -f
```

#### 2.3. Verificar logs
Busca en los logs:
- ✅ "HTTP POST sent" (nuevo)
- ❌ "SNS SENT" (viejo - no debería aparecer)

---

### 3. Prueba End-to-End

#### 3.1. Verificar que ambos servicios están corriendo
```bash
sudo systemctl status crypto-listener
sudo systemctl status crypto-analyzer-redis
```

#### 3.2. Monitorear logs en tiempo real

Terminal 1 - crypto-analyzer-redis:
```bash
sudo journalctl -u crypto-analyzer-redis -f | grep "HTTP POST"
```

Terminal 2 - crypto-listener-rest:
```bash
sudo journalctl -u crypto-listener -f
```

#### 3.3. Esperar una señal

Cuando crypto-analyzer-redis detecte una señal, deberías ver:

**En crypto-analyzer-redis:**
```
📊 REST PIPELINE: BTCUSDT LONG 75.0% → checking signal_filter
✅ REST PIPELINE: BTCUSDT passed signal_filter → checking trade_validator
🚀 HTTP POST sent: BTCUSDT LONG 75.0% → delivered to crypto-listener-rest
```

**En crypto-listener-rest:**
```
📩 Trade request received: BTCUSDT LONG @ 45000.0
✅ HTTP POST sent: BTCUSDT LONG - 4/4 users successful in 0.523s
   ✅ User_1: trade_created
   ✅ User_3: trade_created
   ✅ User_2: trade_created
   ✅ User_4: trade_created
```

---

### 4. Monitoreo Post-Migración (7 días)

#### 4.1. Verificar daily
```bash
# Verificar servicios
sudo systemctl status crypto-listener crypto-analyzer-redis

# Ver últimas señales procesadas
sudo journalctl -u crypto-listener --since "1 hour ago" | grep "HTTP POST sent"

# Ver estadísticas
curl http://localhost:8000/stats | python3 -m json.tool
```

#### 4.2. Comparar con período Lambda
- Número de trades ejecutados
- Latencia promedio
- Errores de conexión
- Costo AWS (debería ver reducción de $32-50/mes)

---

### 5. Cleanup (Después de validar 7 días)

**SOLO después de confirmar que todo funciona correctamente:**

#### 5.1. Deshabilitar Lambda
```bash
aws lambda update-function-configuration \
  --function-name CryptoListenerFunction \
  --environment Variables={DEPLOYMENT_ENV=disabled}
```

O simplemente eliminar:
```bash
# Desde crypto-listener/
sam delete
```

#### 5.2. Eliminar NAT Gateway (ahorra $32-50/mes)
```bash
# 1. Buscar NAT Gateway ID
aws ec2 describe-nat-gateways --region eu-central-1

# 2. Eliminar (⚠️ asegúrate de que no lo usen otros servicios)
aws ec2 delete-nat-gateway --nat-gateway-id nat-XXXXX --region eu-central-1
```

#### 5.3. Eliminar subscripción SNS (opcional)
```bash
aws sns list-subscriptions

aws sns unsubscribe --subscription-arn SUBSCRIPTION_ARN
```

**Nota:** Puedes mantener el SNS topic si lo usas para otras cosas.

---

## 🔧 Troubleshooting

### Problema: crypto-listener-rest no inicia

```bash
# Ver error
sudo journalctl -u crypto-listener -n 50

# Verificar variables de entorno
sudo systemctl show crypto-listener | grep Environment

# Test manual
cd ~/crypto-listener-rest
source venv/bin/activate
python main.py  # Ver error directamente
```

### Problema: Database connection error

```bash
# Verificar PostgreSQL
sudo systemctl status postgresql

# Test de conexión
psql "$DATABASE_URL_CRYPTO_TRADER"

# Verificar password en service file
sudo nano /etc/systemd/system/crypto-listener.service
```

### Problema: crypto-analyzer-redis no puede conectar a API

```bash
# Verificar que crypto-listener-rest está corriendo
curl http://localhost:8000/health

# Verificar puerto
sudo netstat -tulpn | grep 8000

# Ver logs de conexión
sudo journalctl -u crypto-analyzer-redis -f | grep "HTTP POST\|ConnectionError"
```

### Problema: Trades no se ejecutan

```bash
# Ver respuesta completa de API
sudo journalctl -u crypto-listener -n 100 | grep "Trade\|user_id"

# Test manual
curl -X POST http://localhost:8000/execute-trade \
  -H "Content-Type: application/json" \
  -d '{"symbol":"BTCUSDT","entry":45000.0,"stop":44500.0,"target":46000.0,"trade":"LONG","rr":2.0,"probability":75.0}'
```

---

## 📊 Checklist de Migración

- [ ] crypto-listener-rest clonado en EC2
- [ ] Variables de entorno configuradas en ~/.bashrc
- [ ] Service instalado y corriendo
- [ ] Test de integración pasado (test_integration.py)
- [ ] crypto-analyzer-redis actualizado y reiniciado
- [ ] Logs muestran "HTTP POST sent" (no "SNS SENT")
- [ ] Primera señal procesada correctamente end-to-end
- [ ] Monitoreado por 7 días sin errores
- [ ] Lambda deshabilitada/eliminada
- [ ] NAT Gateway eliminado
- [ ] Costo AWS reducido confirmado

---

## 🎉 Beneficios Conseguidos

Después de completar la migración:

✅ **$32-50/mes ahorrados** (eliminación de NAT Gateway)
✅ **<10ms latencia** (vs 100-500ms con Lambda)
✅ **Respuesta síncrona** (sabes inmediatamente si el trade fue ejecutado)
✅ **Logs más fáciles** (journalctl vs CloudWatch)
✅ **Sin cold starts** (servicio siempre activo)
✅ **Consistencia de variables** (DATABASE_URL_CRYPTO_TRADER compartida con crypto-analyzer-redis)
✅ **Simplicidad** (menos componentes AWS, todo en EC2)

---

## 📞 Soporte

Si encuentras problemas durante la migración:

1. Revisa los logs: `sudo journalctl -u crypto-listener -f`
2. Ejecuta test de integración: `python test_integration.py`
3. Verifica variables de entorno: Ver ENVIRONMENT_SETUP.md
4. Test manual de la API: `curl http://localhost:8000/health`

**Rollback plan:** Si algo falla, la Lambda original sigue funcionando. Solo necesitas volver a habilitar la subscripción SNS.
