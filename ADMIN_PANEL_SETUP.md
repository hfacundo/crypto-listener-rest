# 🎛️ Crypto Trading Admin Panel - Setup Guide

## 📋 Tabla de Contenidos

1. [Overview](#overview)
2. [Características](#características)
3. [Setup Local (Testing)](#setup-local-testing)
4. [Deploy a EC2](#deploy-a-ec2)
5. [Acceso desde Internet](#acceso-desde-internet)
6. [Seguridad y Autenticación](#seguridad-y-autenticación)
7. [Uso del Panel](#uso-del-panel)

---

## 🎯 Overview

Panel de administración web para controlar **crypto-listener-rest** remotamente:

- ✅ Acceso desde browser (desktop/móvil)
- ✅ Control de trading por usuario
- ✅ Modificación de configuración en tiempo real
- ✅ Emergency stop global
- ✅ Multi-usuario con autenticación
- ✅ Auto-refresh cada 30 segundos

---

## ✨ Características

### 🚨 Emergency Controls
- **Stop All Trading**: Detiene todos los usuarios inmediatamente
- **Resume All Trading**: Reactiva todos los usuarios

### 👤 Control por Usuario
- **Pause/Resume**: Activar o pausar usuario individual
- **Tier Config**: Modificar `max_tier_accepted` y habilitar/deshabilitar filtrado
- **Circuit Breaker**: Configurar max_losses, window, cooldown

### 📊 Monitoreo
- Estado en tiempo real de todos los usuarios
- Visualización de configuración actual
- Auto-refresh automático

---

## 🧪 Setup Local (Testing)

### 1. Instalar Dependencias

```bash
# Navegar al proyecto
cd /mnt/d/Development/python/crypto-listener-rest

# Instalar dependencias (si no están instaladas)
pip install fastapi uvicorn psycopg2-binary
```

### 2. Configurar Base de Datos

El panel usa la misma base de datos que crypto-listener-rest:

```bash
# Verificar variable de entorno
echo $DATABASE_URL_CRYPTO_TRADER

# Si no está configurada:
export DATABASE_URL_CRYPTO_TRADER="postgresql://postgres:superhufsa@localhost:5433/crypto_trader"
```

### 3. Probar Localmente

```bash
# Ejecutar admin panel
python admin_api.py

# Output esperado:
# 🎛️  Starting Crypto Admin Panel...
# 📊 Dashboard: http://localhost:8080
# 📖 API Docs: http://localhost:8080/docs
```

### 4. Acceder al Dashboard

Abre tu browser:
```
http://localhost:8080
```

**Credenciales por defecto:**
- **Usuario admin**: `admin` / `crypto2025!`
- **Usuario viewer**: `viewer` / `view2025!`

⚠️ **IMPORTANTE**: Cambia estas credenciales antes de deploy (ver sección de seguridad)

---

## 🚀 Deploy a EC2

### Opción 1: Ejecutar Directamente

```bash
# Conectar a EC2
ssh ec2-user@tu-ec2-ip

# Navegar al proyecto
cd /path/to/crypto-listener-rest

# Copiar archivos desde local a EC2
scp admin_api.py ec2-user@tu-ec2-ip:/path/to/crypto-listener-rest/
scp -r static/ ec2-user@tu-ec2-ip:/path/to/crypto-listener-rest/

# En EC2, instalar dependencias
pip3 install fastapi uvicorn psycopg2-binary

# Configurar variable de entorno
export DATABASE_URL_CRYPTO_TRADER="postgresql://postgres:superhufsa@localhost:5433/crypto_trader"

# Ejecutar en background
nohup python3 admin_api.py > admin_panel.log 2>&1 &

# Verificar que está corriendo
tail -f admin_panel.log
```

### Opción 2: Crear Servicio Systemd (Recomendado)

```bash
# Crear archivo de servicio
sudo nano /etc/systemd/system/crypto-admin-panel.service
```

**Contenido del archivo:**

```ini
[Unit]
Description=Crypto Trading Admin Panel
After=network.target

[Service]
Type=simple
User=ec2-user
WorkingDirectory=/path/to/crypto-listener-rest
Environment="DATABASE_URL_CRYPTO_TRADER=postgresql://postgres:superhufsa@localhost:5433/crypto_trader"
ExecStart=/usr/bin/python3 /path/to/crypto-listener-rest/admin_api.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

**Activar servicio:**

```bash
# Recargar systemd
sudo systemctl daemon-reload

# Habilitar inicio automático
sudo systemctl enable crypto-admin-panel

# Iniciar servicio
sudo systemctl start crypto-admin-panel

# Verificar estado
sudo systemctl status crypto-admin-panel

# Ver logs
sudo journalctl -u crypto-admin-panel -f
```

---

## 🌐 Acceso desde Internet

### Opción A: Cloudflare Tunnel (⭐ Recomendado)

**Ventajas:**
- ✅ HTTPS automático (certificado SSL gratis)
- ✅ NO necesitas abrir puertos en EC2
- ✅ Protección DDoS incluida
- ✅ Gratis

**Pasos:**

#### 1. Instalar Cloudflared en EC2

```bash
# Descargar cloudflared
wget https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64.deb
sudo dpkg -i cloudflared-linux-amd64.deb

# Verificar instalación
cloudflared --version
```

#### 2. Autenticar con Cloudflare

```bash
# Autenticar (abrirá browser)
cloudflared tunnel login
```

Esto abrirá tu browser para autorizar. Si estás en SSH, copia la URL y ábrela en tu browser local.

#### 3. Crear Tunnel

```bash
# Crear tunnel
cloudflared tunnel create crypto-admin

# Output:
# Tunnel credentials written to: /home/ec2-user/.cloudflared/<TUNNEL-ID>.json
# Created tunnel crypto-admin with id <TUNNEL-ID>
```

#### 4. Configurar DNS

```bash
# Asociar dominio (si tienes uno en Cloudflare)
cloudflared tunnel route dns crypto-admin admin.tudominio.com

# O usar dominio gratis de Cloudflare (*.trycloudflare.com)
# No necesitas este paso, usarás URL temporal
```

#### 5. Crear Archivo de Configuración

```bash
# Crear directorio de configuración
mkdir -p ~/.cloudflared

# Crear config.yml
nano ~/.cloudflared/config.yml
```

**Contenido (sin dominio propio):**

```yaml
tunnel: <TUNNEL-ID>  # Reemplazar con tu ID
credentials-file: /home/ec2-user/.cloudflared/<TUNNEL-ID>.json

ingress:
  - service: http://localhost:8080
```

**Contenido (con dominio propio):**

```yaml
tunnel: <TUNNEL-ID>
credentials-file: /home/ec2-user/.cloudflared/<TUNNEL-ID>.json

ingress:
  - hostname: admin.tudominio.com
    service: http://localhost:8080
  - service: http_status:404
```

#### 6. Iniciar Tunnel

**Modo temporal (para probar):**

```bash
cloudflared tunnel --url http://localhost:8080
```

Esto te dará una URL temporal como: `https://random-words.trycloudflare.com`

**Modo permanente (con servicio):**

```bash
# Instalar como servicio
sudo cloudflared service install

# Iniciar servicio
sudo systemctl start cloudflared
sudo systemctl enable cloudflared

# Ver logs
sudo journalctl -u cloudflared -f
```

#### 7. Acceder al Dashboard

Abre la URL en tu browser (desde cualquier lugar):

```
https://random-words.trycloudflare.com
```

O si usaste dominio propio:

```
https://admin.tudominio.com
```

---

### Opción B: Security Group + IP Whitelisting

**Ventajas:**
- ✅ Muy simple
- ✅ No requiere herramientas extra

**Contras:**
- ❌ Solo HTTP (sin SSL)
- ❌ Tu IP debe ser fija o cambiarla cada vez

**Pasos:**

#### 1. Obtener tu IP Pública

```bash
# Desde tu casa, ejecuta:
curl ifconfig.me

# Output: 123.45.67.89
```

#### 2. Abrir Puerto en Security Group

```bash
# Obtener Security Group ID de tu EC2
aws ec2 describe-instances --instance-ids i-tu-instance-id \
  --query 'Reservations[0].Instances[0].SecurityGroups[0].GroupId' \
  --output text

# Output: sg-xxxxxxxxxxxxx

# Agregar regla para tu IP
aws ec2 authorize-security-group-ingress \
  --group-id sg-xxxxxxxxxxxxx \
  --protocol tcp \
  --port 8080 \
  --cidr TU_IP/32
```

O desde **AWS Console**:

1. Ve a EC2 → Security Groups
2. Selecciona el Security Group de tu instancia
3. Inbound Rules → Edit
4. Add Rule:
   - **Type**: Custom TCP
   - **Port**: 8080
   - **Source**: My IP (o ingresa tu IP manualmente)
5. Save

#### 3. Acceder al Dashboard

```
http://tu-ec2-ip:8080
```

⚠️ **IMPORTANTE**: Esto es HTTP sin encriptación. Para producción, agrega HTTPS con Let's Encrypt o usa Cloudflare.

---

### Opción C: Tailscale VPN (🔒 Máxima Seguridad)

**Ventajas:**
- ✅ Súper seguro (VPN encriptada)
- ✅ NO abres puertos públicos
- ✅ Funciona desde celular/casa/cualquier lugar
- ✅ Gratis para uso personal

**Pasos:**

#### 1. Instalar Tailscale en EC2

```bash
# Instalar
curl -fsSL https://tailscale.com/install.sh | sh

# Iniciar y autenticar
sudo tailscale up

# Esto te dará una URL para autorizar en tu browser
```

#### 2. Instalar Tailscale en tus Dispositivos

- **PC/Mac**: https://tailscale.com/download
- **iPhone/Android**: Busca "Tailscale" en App Store/Play Store

#### 3. Acceder al Dashboard

Una vez conectado a Tailscale, usa la IP interna:

```
# Ver IP de Tailscale en EC2
tailscale ip

# Output: 100.x.x.x

# Acceder desde cualquier dispositivo en tu Tailscale:
http://100.x.x.x:8080
```

---

## 🔐 Seguridad y Autenticación

### Cambiar Credenciales por Defecto

**⚠️ CRÍTICO**: Cambia las credenciales antes de hacer deploy.

Edita `admin_api.py` línea ~37:

```python
AUTHORIZED_USERS = {
    "admin": "TU_PASSWORD_SEGURO_AQUI",
    "viewer": "OTRO_PASSWORD_AQUI"
}
```

**Recomendaciones:**
- Usa passwords largos (min 16 caracteres)
- Mezcla letras, números, símbolos
- Generador: https://www.lastpass.com/password-generator

### Agregar Más Usuarios

```python
AUTHORIZED_USERS = {
    "admin": "password123",      # Permisos completos
    "viewer": "viewer123",       # Solo lectura (a futuro)
    "partner": "partner456",     # Tu socio/colaborador
}
```

### Permisos por Usuario

Actualmente todos tienen los mismos permisos. Para implementar roles:

```python
# En admin_api.py, modificar verify_credentials:
def verify_credentials(credentials: HTTPBasicCredentials = Depends(security)) -> dict:
    # ... validación ...
    return {
        "username": username,
        "role": "admin" if username == "admin" else "viewer"
    }

# En endpoints críticos:
@app.post("/api/emergency/stop-all")
async def emergency_stop_all(user: dict = Depends(verify_credentials)):
    if user["role"] != "admin":
        raise HTTPException(status_code=403, detail="Admin only")
    # ...
```

---

## 📱 Uso del Panel

### Desde Desktop

1. Abre browser (Chrome/Firefox/Safari)
2. Navega a tu URL (Cloudflare, IP pública, o Tailscale)
3. Ingresa credenciales cuando aparezca el prompt
4. ✅ Dashboard cargado

### Desde Móvil

1. Abre browser en tu celular
2. Navega a la misma URL
3. Ingresa credenciales
4. ✅ Dashboard responsivo automático

El diseño es 100% responsivo - se adapta automáticamente a pantallas pequeñas.

---

## 🎮 Funcionalidades

### 🚨 Emergency Stop

**Uso:** Detener todo el trading inmediatamente.

1. Click en botón rojo "⛔ STOP ALL TRADING"
2. Confirmar en el diálogo
3. ✅ Todos los usuarios pausados

**Cuándo usar:**
- Volatilidad extrema del mercado
- Problemas técnicos
- Noticias importantes (Fed, regulación, etc.)

### 👤 Pausar Usuario Individual

**Uso:** Pausar solo un usuario específico.

1. En la tarjeta del usuario, click "⏸️ Pause"
2. ✅ Usuario pausado (badge cambia a rojo "PAUSED")

**Cuándo usar:**
- Usuario con muchas pérdidas
- Probar configuración nueva en otros usuarios
- Mantenimiento específico

### 🎯 Modificar Tier Config

**Uso:** Cambiar tier máximo aceptado por usuario.

1. Click en "🎯 Tier Config"
2. Modal se abre
3. Modificar:
   - ✅/❌ Enable Tier Filtering
   - Max Tier Accepted (1-10)
4. Click "Save"

**Ejemplo:**
- **Conservador**: Max Tier = 7 (solo alta calidad)
- **Moderado**: Max Tier = 8
- **Agresivo**: Max Tier = 9 (todos los viables)

### 🔄 Modificar Circuit Breaker

**Uso:** Configurar protección contra pérdidas consecutivas.

1. Click en "🔄 Circuit Breaker"
2. Modal se abre
3. Modificar:
   - ✅/❌ Enable Circuit Breaker
   - Max Losses: Cuántas pérdidas seguidas
   - Window Minutes: En qué ventana de tiempo
   - Cooldown Minutes: Cuánto tiempo pausar
4. Click "Save"

**Ejemplo:**
- Max Losses: 3
- Window: 60 minutos
- Cooldown: 120 minutos
- → Si pierde 3 trades en 1 hora, pausar por 2 horas

---

## 🔄 Auto-Refresh

El dashboard se actualiza automáticamente cada **30 segundos**.

También puedes refrescar manualmente:
- Click en el botón circular "↻" (esquina inferior derecha)

---

## 🐛 Troubleshooting

### Error: "Authentication failed"

**Causa:** Credenciales incorrectas

**Solución:**
1. Verifica usuario/password en `admin_api.py`
2. Limpia caché del browser (localStorage)
3. Logout y vuelve a login

### Error: "Database error"

**Causa:** No puede conectar a PostgreSQL

**Solución:**
```bash
# Verificar que PostgreSQL está corriendo
sudo systemctl status postgresql

# Verificar variable de entorno
echo $DATABASE_URL_CRYPTO_TRADER

# Verificar conexión manual
psql $DATABASE_URL_CRYPTO_TRADER -c "SELECT 1"
```

### Panel no carga (Cloudflare Tunnel)

**Causa:** Tunnel no está corriendo

**Solución:**
```bash
# Ver status del tunnel
sudo systemctl status cloudflared

# Ver logs
sudo journalctl -u cloudflared -f

# Reiniciar tunnel
sudo systemctl restart cloudflared
```

### No puedo acceder desde celular

**Causa:** Firewall o IP whitelisting

**Solución:**
- **Cloudflare Tunnel**: Debería funcionar desde cualquier lugar
- **Security Group**: Agrega la IP de tu celular
- **Tailscale**: Instala Tailscale en tu celular

---

## 📊 Logs y Monitoreo

### Ver Logs del Admin Panel

```bash
# Si usas systemd:
sudo journalctl -u crypto-admin-panel -f

# Si usas nohup:
tail -f admin_panel.log

# Filtrar errores:
sudo journalctl -u crypto-admin-panel | grep ERROR
```

### Verificar que Está Respondiendo

```bash
# Health check
curl http://localhost:8080/health

# Output esperado:
# {"status":"healthy","database":"connected"}
```

---

## 🚀 Next Steps (Futuro)

### Features a Agregar:

1. **Ver Trades Recientes**
   - Endpoint: GET /api/trades/recent
   - Mostrar últimos 20 trades recomendados
   - Filtrar por usuario, símbolo, fecha

2. **Estadísticas en Tiempo Real**
   - P&L del día por usuario
   - Win rate
   - Trades activos vs cerrados

3. **Gráficos**
   - Chart.js o Recharts
   - P&L histórico
   - Trades por hora

4. **Notificaciones Push**
   - Alertas cuando se activa circuit breaker
   - Notificaciones de trades grandes

5. **Roles y Permisos**
   - Admin: control total
   - Viewer: solo lectura
   - User: ver solo sus propios trades

---

## 📞 Soporte

Si tienes problemas:
1. Revisa los logs
2. Verifica que PostgreSQL está corriendo
3. Verifica que el puerto 8080 no está bloqueado
4. Prueba localmente primero antes de deploy

---

## 🎉 Listo!

Ahora tienes un panel de control completo para gestionar tu sistema de trading desde cualquier lugar.

**Disfruta el control total de tu sistema! 🚀**
