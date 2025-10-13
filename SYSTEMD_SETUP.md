# systemd Setup Guide - crypto-listener-rest

## Índice
1. [Qué es systemd y por qué usarlo](#qué-es-systemd)
2. [Prerequisitos](#prerequisitos)
3. [Configuración paso a paso](#configuración-paso-a-paso)
4. [Comandos de gestión](#comandos-de-gestión)
5. [Troubleshooting](#troubleshooting)
6. [Monitoreo y logs](#monitoreo-y-logs)

---

## Qué es systemd

**systemd** es el sistema de gestión de servicios estándar en Linux moderno que:
- ✅ Inicia servicios automáticamente al arrancar el servidor
- ✅ Reinicia servicios automáticamente si fallan
- ✅ Gestiona dependencias entre servicios
- ✅ Centraliza logs con `journalctl`
- ✅ Controla recursos (CPU, memoria)

### Ventajas para crypto-listener-rest

| Sin systemd | Con systemd |
|-------------|-------------|
| ❌ Si crashea, queda muerto | ✅ Se reinicia automáticamente en 10s |
| ❌ Después de reinicio EC2, no inicia | ✅ Inicia automáticamente |
| ❌ Logs en archivos dispersos | ✅ Logs centralizados con journalctl |
| ❌ Conexión SSH cerrada = servicio muerto | ✅ Corre como daemon independiente |

---

## Prerequisitos

### 1. Verificar que systemd está instalado
```bash
systemctl --version
```

Deberías ver algo como:
```
systemd 245 (245.4-4ubuntu3)
+PAM +AUDIT +SELINUX +IMA +APPARMOR +SMACK +SYSVINIT +UTMP +LIBCRYPTSETUP
```

### 2. Verificar rutas del proyecto
```bash
# Ruta completa del proyecto
pwd
# Ejemplo: /home/ubuntu/crypto-listener-rest

# Ruta de Python
which python3
# Ejemplo: /usr/bin/python3

# Verificar que uvicorn está instalado
python3 -m uvicorn --version
```

### 3. Usuario que ejecutará el servicio
```bash
# Usuario actual (recomendado usar este)
whoami
# Ejemplo: ubuntu, ec2-user, admin, etc.
```

---

## Configuración Paso a Paso

### Paso 1: Crear archivo de servicio

```bash
sudo nano /etc/systemd/system/crypto-listener.service
```

Contenido del archivo (ajusta las rutas según tu instalación):

```ini
[Unit]
Description=Crypto Listener REST API - Trade Execution Service
After=network.target postgresql.service redis-server.service
Wants=postgresql.service redis-server.service

[Service]
Type=simple
User=ubuntu
Group=ubuntu
WorkingDirectory=/home/ubuntu/crypto-listener-rest

# Variables de entorno (opcional, si no usas .bashrc)
Environment="PATH=/usr/local/bin:/usr/bin:/bin"
Environment="PYTHONUNBUFFERED=1"

# Comando de inicio
ExecStart=/usr/bin/python3 -m uvicorn main:app --host 0.0.0.0 --port 8000

# Reinicio automático
Restart=always
RestartSec=10

# Límites de recursos (opcional pero recomendado)
MemoryLimit=1G
CPUQuota=80%

# Logging
StandardOutput=journal
StandardError=journal
SyslogIdentifier=crypto-listener

# Seguridad (opcional)
NoNewPrivileges=true
PrivateTmp=true

[Install]
WantedBy=multi-user.target
```

### Paso 2: Ajustar permisos
```bash
sudo chmod 644 /etc/systemd/system/crypto-listener.service
```

### Paso 3: Recargar systemd
```bash
sudo systemctl daemon-reload
```

### Paso 4: Habilitar inicio automático
```bash
sudo systemctl enable crypto-listener
```

Verás:
```
Created symlink /etc/systemd/system/multi-user.target.wants/crypto-listener.service → /etc/systemd/system/crypto-listener.service.
```

### Paso 5: Iniciar el servicio
```bash
sudo systemctl start crypto-listener
```

### Paso 6: Verificar estado
```bash
sudo systemctl status crypto-listener
```

Salida esperada:
```
● crypto-listener.service - Crypto Listener REST API - Trade Execution Service
     Loaded: loaded (/etc/systemd/system/crypto-listener.service; enabled; vendor preset: enabled)
     Active: active (running) since Mon 2025-01-13 10:30:00 UTC; 5s ago
   Main PID: 12345 (python3)
      Tasks: 5 (limit: 4915)
     Memory: 150.2M
     CGroup: /system.slice/crypto-listener.service
             └─12345 /usr/bin/python3 -m uvicorn main:app --host 0.0.0.0 --port 8000

Jan 13 10:30:00 ip-172-31-0-1 systemd[1]: Started Crypto Listener REST API.
Jan 13 10:30:01 ip-172-31-0-1 crypto-listener[12345]: INFO:     Started server process [12345]
Jan 13 10:30:01 ip-172-31-0-1 crypto-listener[12345]: INFO:     Uvicorn running on http://0.0.0.0:8000
```

---

## Comandos de Gestión

### Comandos Básicos
```bash
# Ver estado del servicio
sudo systemctl status crypto-listener

# Iniciar
sudo systemctl start crypto-listener

# Detener
sudo systemctl stop crypto-listener

# Reiniciar
sudo systemctl restart crypto-listener

# Recargar configuración (si cambias variables de entorno)
sudo systemctl reload crypto-listener

# Ver si está habilitado para arranque automático
sudo systemctl is-enabled crypto-listener

# Habilitar arranque automático
sudo systemctl enable crypto-listener

# Deshabilitar arranque automático
sudo systemctl disable crypto-listener
```

### Después de Modificar el Archivo de Servicio
```bash
# 1. Editar el archivo
sudo nano /etc/systemd/system/crypto-listener.service

# 2. Recargar systemd
sudo systemctl daemon-reload

# 3. Reiniciar el servicio
sudo systemctl restart crypto-listener

# 4. Verificar
sudo systemctl status crypto-listener
```

---

## Monitoreo y Logs

### Ver Logs en Tiempo Real
```bash
# Logs en tiempo real (como tail -f)
sudo journalctl -u crypto-listener -f

# Logs con colores
sudo journalctl -u crypto-listener -f --output=cat
```

### Ver Logs Históricos
```bash
# Últimas 100 líneas
sudo journalctl -u crypto-listener -n 100

# Últimas 2 horas
sudo journalctl -u crypto-listener --since "2 hours ago"

# Entre fechas específicas
sudo journalctl -u crypto-listener --since "2025-01-13 10:00:00" --until "2025-01-13 12:00:00"

# Hoy
sudo journalctl -u crypto-listener --since today

# Ayer
sudo journalctl -u crypto-listener --since yesterday --until today
```

### Filtrar por Nivel de Log
```bash
# Solo errores
sudo journalctl -u crypto-listener -p err

# Warnings y superiores
sudo journalctl -u crypto-listener -p warning

# Info y superiores (default)
sudo journalctl -u crypto-listener -p info
```

### Ver Información del Servicio
```bash
# PID del proceso
sudo systemctl show crypto-listener --property=MainPID

# Uso de memoria
sudo systemctl show crypto-listener --property=MemoryCurrent

# Tiempo de actividad
sudo systemctl show crypto-listener --property=ActiveEnterTimestamp

# Número de reinicios
sudo systemctl show crypto-listener --property=NRestarts
```

---

## Troubleshooting

### Problema 1: El servicio no inicia

**Verificar sintaxis del archivo:**
```bash
sudo systemd-analyze verify /etc/systemd/system/crypto-listener.service
```

**Ver logs de error:**
```bash
sudo journalctl -u crypto-listener -n 50 --no-pager
```

**Causas comunes:**
- ❌ Ruta incorrecta en `WorkingDirectory`
- ❌ Ruta incorrecta en `ExecStart`
- ❌ Usuario no tiene permisos en el directorio
- ❌ Variables de entorno no definidas

**Solución:**
```bash
# Verificar permisos del directorio
ls -la /home/ubuntu/crypto-listener-rest

# Probar comando manualmente
cd /home/ubuntu/crypto-listener-rest
/usr/bin/python3 -m uvicorn main:app --host 0.0.0.0 --port 8000
```

### Problema 2: El servicio se reinicia constantemente

**Ver cuántas veces se ha reiniciado:**
```bash
sudo systemctl show crypto-listener --property=NRestarts
```

**Ver logs del último crash:**
```bash
sudo journalctl -u crypto-listener --since "10 minutes ago" -p err
```

**Causas comunes:**
- ❌ Falta alguna variable de entorno
- ❌ PostgreSQL o Redis no están disponibles
- ❌ Puerto 8000 ya está en uso

**Solución temporal (para debug):**
```bash
# Cambiar Restart=always a Restart=on-failure
sudo nano /etc/systemd/system/crypto-listener.service
sudo systemctl daemon-reload
sudo systemctl restart crypto-listener
```

### Problema 3: Variables de entorno no se cargan

**Opción A: Agregar al archivo de servicio**
```ini
[Service]
Environment="DATABASE_URL_CRYPTO_TRADER=postgresql://..."
Environment="REDIS_HOST=localhost"
Environment="REDIS_PORT=6379"
# ... todas las variables
```

**Opción B: Usar archivo .env**
```ini
[Service]
EnvironmentFile=/home/ubuntu/crypto-listener-rest/.env
```

Crear `/home/ubuntu/crypto-listener-rest/.env`:
```bash
DATABASE_URL_CRYPTO_TRADER=postgresql://...
REDIS_HOST=localhost
REDIS_PORT=6379
# ... todas las variables
```

### Problema 4: Puerto 8000 ya está en uso

**Verificar qué está usando el puerto:**
```bash
sudo lsof -i :8000
sudo netstat -tulpn | grep 8000
```

**Soluciones:**
- Cambiar a otro puerto en `ExecStart`: `--port 8001`
- Matar el proceso que usa el puerto: `sudo kill -9 <PID>`

### Problema 5: Servicio no se detiene correctamente

**Ver por qué no se detiene:**
```bash
sudo journalctl -u crypto-listener --since "5 minutes ago"
```

**Forzar detención:**
```bash
# Detención normal
sudo systemctl stop crypto-listener

# Si no funciona, forzar con SIGKILL
sudo systemctl kill -s SIGKILL crypto-listener
```

---

## Testing del Servicio

### Test 1: Verificar que inicia correctamente
```bash
sudo systemctl restart crypto-listener
sleep 5
sudo systemctl status crypto-listener | grep "Active:"
# Debe decir: Active: active (running)
```

### Test 2: Verificar que responde
```bash
curl http://localhost:8000/health
# Debe retornar JSON con status: "healthy"
```

### Test 3: Verificar reinicio automático
```bash
# Obtener PID actual
PID=$(sudo systemctl show crypto-listener --property=MainPID --value)
echo "PID antes: $PID"

# Matar el proceso
sudo kill -9 $PID

# Esperar 15 segundos (RestartSec=10 + tiempo de inicio)
sleep 15

# Verificar que se reinició
NEW_PID=$(sudo systemctl show crypto-listener --property=MainPID --value)
echo "PID después: $NEW_PID"

# Verificar que cambió
if [ "$PID" != "$NEW_PID" ]; then
    echo "✅ Reinicio automático funciona"
else
    echo "❌ No se reinició"
fi
```

### Test 4: Verificar inicio automático después de reboot
```bash
# Verificar que está habilitado
sudo systemctl is-enabled crypto-listener
# Debe decir: enabled

# Ver qué servicios arrancan en boot
sudo systemctl list-unit-files | grep crypto-listener
# Debe decir: crypto-listener.service enabled
```

---

## Configuración Avanzada (Opcional)

### Limitar Uso de Recursos

```ini
[Service]
# Límite de memoria (detiene servicio si excede)
MemoryMax=2G

# Límite de CPU (80% de un core)
CPUQuota=80%

# Límite de archivos abiertos
LimitNOFILE=10000

# Timeout de inicio (30s)
TimeoutStartSec=30

# Timeout de detención (15s)
TimeoutStopSec=15
```

### Configurar Email en Caso de Fallo

Requiere configurar `sendmail` o similar:

```ini
[Service]
OnFailure=email-alert@%i.service
```

### Health Check Periódico

Crear script `/home/ubuntu/crypto-listener-rest/healthcheck.sh`:
```bash
#!/bin/bash
curl -f http://localhost:8000/health || exit 1
```

En el servicio:
```ini
[Service]
ExecStartPost=/bin/sleep 5
ExecStartPost=/home/ubuntu/crypto-listener-rest/healthcheck.sh
```

---

## Comparación: screen vs systemd

| Tarea | Con screen | Con systemd |
|-------|-----------|-------------|
| Iniciar servicio | `screen -S crypto-listener` + comando | `sudo systemctl start crypto-listener` |
| Ver logs | Reconectar a screen | `sudo journalctl -u crypto-listener -f` |
| Detener servicio | Reconectar + Ctrl+C | `sudo systemctl stop crypto-listener` |
| Reiniciar servicio | Detener + iniciar manual | `sudo systemctl restart crypto-listener` |
| Si crashea | Muerto hasta intervención manual | Se reinicia automático en 10s |
| Después de reboot EC2 | No inicia (manual) | Inicia automáticamente |
| Ver estado | `screen -ls` + verificar | `sudo systemctl status crypto-listener` |

---

## Migración de screen a systemd

### Paso 1: Detener servicio en screen
```bash
# Listar sesiones screen
screen -ls

# Conectar a la sesión
screen -r crypto-listener

# Detener el servicio (Ctrl+C)

# Salir de screen
exit

# Matar la sesión screen
screen -X -S crypto-listener quit
```

### Paso 2: Configurar systemd (ver sección anterior)

### Paso 3: Iniciar con systemd
```bash
sudo systemctl start crypto-listener
sudo systemctl status crypto-listener
```

### Paso 4: Verificar que funciona
```bash
curl http://localhost:8000/health
```

---

## Checklist de Producción

Antes de considerar el servicio "listo para producción":

- [ ] systemd configurado y servicio iniciando correctamente
- [ ] Reinicio automático probado (kill manual del proceso)
- [ ] Inicio automático después de reboot probado
- [ ] Logs visibles con `journalctl`
- [ ] Health check respondiendo
- [ ] Variables de entorno cargadas correctamente
- [ ] PostgreSQL y Redis como dependencias configuradas
- [ ] Límites de recursos configurados (memoria, CPU)
- [ ] Documentación de troubleshooting revisada
- [ ] Monitoreo configurado (opcional: CloudWatch, Prometheus, etc.)

---

## Referencias

- [systemd Documentation](https://www.freedesktop.org/software/systemd/man/)
- [systemd for Administrators](https://www.freedesktop.org/wiki/Software/systemd/)
- [journalctl Manual](https://www.freedesktop.org/software/systemd/man/journalctl.html)
- [FastAPI Deployment Guide](https://fastapi.tiangolo.com/deployment/)

---

**Última actualización:** 2025-01-13
**Autor:** crypto-listener-rest migration team
