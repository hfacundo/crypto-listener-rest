# Environment Variables Setup Guide

## Variables de Entorno Requeridas

Estas variables deben ser agregadas a tu `~/.bashrc` en el servidor EC2.

### 1. Base de Datos PostgreSQL

```bash
# IMPORTANTE: Usa el mismo nombre que crypto-analyzer-redis
export DATABASE_URL_CRYPTO_TRADER="postgresql://app_user:YOUR_PASSWORD@localhost:5432/crypto_trader"
```

**Nota**: Reemplaza `YOUR_PASSWORD` con tu password real.

### 2. Redis Configuration

```bash
export REDIS_HOST="localhost"
export REDIS_PORT="6379"
export REDIS_DB="0"
# export REDIS_PASSWORD=""  # Opcional, solo si tienes password
```

### 3. Deployment Environment

```bash
export DEPLOYMENT_ENV="main"
```

### 4. Binance API Keys

```bash
# User 1 - COPY_TRADING
export BINANCE_FUTURES_API_KEY_COPY="qh1PdpDQeb35GU0uOW3KW9dgR0jwxviO2fCioVNJqqvGz4eq1rmykxpkVO8vL5XI"
export BINANCE_FUTURES_API_SECRET_COPY="BDOz5jI7KJqtrY6Uh3MDH9w1MRaduL0LgMlzzM95HbnF3E89tTpan6Fnrnkf0jHJ"

# User 3 - HUFSA
export BINANCE_FUTURES_API_KEY_HUFSA="i1sYMFZhAQxm7jxCwidTz6X6kJShQ4xWovia47f2hvTTpQmc6nMOUkAMUyhvnwAp"
export BINANCE_FUTURES_API_SECRET_HUFSA="aDD799IsGlWj496hZMLd46OVcrRF4OwVv012QZcG5kYaS1QdpVeTdbk00WVtipz7"

# User 2 - COPY_2
export BINANCE_FUTURES_API_KEY_COPY_2="D6kUNq3KZHshG6eogeEJ16M0bkdX7saFLWNEKiVzbaEThaoGevHfMeLYOQIvo7Uj"
export BINANCE_FUTURES_API_SECRET_COPY_2="nXNJDigTLNLQZRxr6gj5coBGblBSaMKuWH6ADjIRdeMknWNRRSovLxr4GGYKvdDg"

# User 4 - FUTURES
export BINANCE_FUTURES_API_KEY_FUTURES="BmkwNIpR8putPUsnFYsuH1acfkvLDTwUEvDb4JWhm2Ftue9lZHBIiUTO10jzX05U"
export BINANCE_FUTURES_API_SECRET_FUTURES="N8I5dnZ8RkZ5Mqyc3j9bpauFPHy5QSGtILNnNPUOqxeFMAEuZSjQUi4kkyz0UrDO"
```

### 5. Optional - S3 Bucket (si lo necesitas)

```bash
export S3_BUCKET_NAME="aws-sam-cli-managed-default-samclisourcebucket-573uv63tjbkp"
```

---

## Pasos para Configurar

### Método 1: Agregar manualmente a .bashrc

1. Edita tu archivo .bashrc:
```bash
nano ~/.bashrc
```

2. Agrega todas las variables al final del archivo (copia el bloque completo de arriba)

3. Guarda y cierra (Ctrl+X, Y, Enter)

4. Recarga el archivo:
```bash
source ~/.bashrc
```

5. Verifica que se cargaron:
```bash
echo $DATABASE_URL_CRYPTO_TRADER
echo $REDIS_HOST
echo $DEPLOYMENT_ENV
```

### Método 2: Usar script automático

Ejecuta este comando para agregar todas las variables:

```bash
cat >> ~/.bashrc << 'EOF'

# ============================================
# crypto-listener-rest Environment Variables
# ============================================

# Database (shared with crypto-analyzer-redis)
export DATABASE_URL_CRYPTO_TRADER="postgresql://app_user:YOUR_PASSWORD@localhost:5432/crypto_trader"

# Redis
export REDIS_HOST="localhost"
export REDIS_PORT="6379"
export REDIS_DB="0"

# Environment
export DEPLOYMENT_ENV="main"

# Binance API Keys
export BINANCE_FUTURES_API_KEY_COPY="qh1PdpDQeb35GU0uOW3KW9dgR0jwxviO2fCioVNJqqvGz4eq1rmykxpkVO8vL5XI"
export BINANCE_FUTURES_API_SECRET_COPY="BDOz5jI7KJqtrY6Uh3MDH9w1MRaduL0LgMlzzM95HbnF3E89tTpan6Fnrnkf0jHJ"
export BINANCE_FUTURES_API_KEY_HUFSA="i1sYMFZhAQxm7jxCwidTz6X6kJShQ4xWovia47f2hvTTpQmc6nMOUkAMUyhvnwAp"
export BINANCE_FUTURES_API_SECRET_HUFSA="aDD799IsGlWj496hZMLd46OVcrRF4OwVv012QZcG5kYaS1QdpVeTdbk00WVtipz7"
export BINANCE_FUTURES_API_KEY_COPY_2="D6kUNq3KZHshG6eogeEJ16M0bkdX7saFLWNEKiVzbaEThaoGevHfMeLYOQIvo7Uj"
export BINANCE_FUTURES_API_SECRET_COPY_2="nXNJDigTLNLQZRxr6gj5coBGblBSaMKuWH6ADjIRdeMknWNRRSovLxr4GGYKvdDg"
export BINANCE_FUTURES_API_KEY_FUTURES="BmkwNIpR8putPUsnFYsuH1acfkvLDTwUEvDb4JWhm2Ftue9lZHBIiUTO10jzX05U"
export BINANCE_FUTURES_API_SECRET_FUTURES="N8I5dnZ8RkZ5Mqyc3j9bpauFPHy5QSGtILNnNPUOqxeFMAEuZSjQUi4kkyz0UrDO"

EOF
```

**IMPORTANTE**: Después de ejecutar este comando, debes editar `~/.bashrc` y reemplazar `YOUR_PASSWORD` con tu password real.

```bash
nano ~/.bashrc
# Busca la línea DATABASE_URL_CRYPTO_TRADER y reemplaza YOUR_PASSWORD
```

Luego recarga:
```bash
source ~/.bashrc
```

---

## Verificación

Ejecuta este script para verificar que todas las variables estén configuradas:

```bash
#!/bin/bash

echo "🔍 Verificando variables de entorno..."
echo ""

check_var() {
    local var_name=$1
    local var_value=${!var_name}

    if [ -z "$var_value" ]; then
        echo "❌ $var_name: NO DEFINIDA"
        return 1
    else
        # Ocultar valores sensibles
        if [[ $var_name == *"SECRET"* ]] || [[ $var_name == *"PASSWORD"* ]] || [[ $var_name == *"DATABASE_URL"* ]]; then
            echo "✅ $var_name: [HIDDEN]"
        else
            echo "✅ $var_name: $var_value"
        fi
        return 0
    fi
}

failed=0

# Database
check_var "DATABASE_URL_CRYPTO_TRADER" || ((failed++))

# Redis
check_var "REDIS_HOST" || ((failed++))
check_var "REDIS_PORT" || ((failed++))
check_var "REDIS_DB" || ((failed++))

# Environment
check_var "DEPLOYMENT_ENV" || ((failed++))

# Binance Keys
check_var "BINANCE_FUTURES_API_KEY_COPY" || ((failed++))
check_var "BINANCE_FUTURES_API_SECRET_COPY" || ((failed++))
check_var "BINANCE_FUTURES_API_KEY_HUFSA" || ((failed++))
check_var "BINANCE_FUTURES_API_SECRET_HUFSA" || ((failed++))
check_var "BINANCE_FUTURES_API_KEY_COPY_2" || ((failed++))
check_var "BINANCE_FUTURES_API_SECRET_COPY_2" || ((failed++))
check_var "BINANCE_FUTURES_API_KEY_FUTURES" || ((failed++))
check_var "BINANCE_FUTURES_API_SECRET_FUTURES" || ((failed++))

echo ""
if [ $failed -eq 0 ]; then
    echo "🎉 Todas las variables están configuradas correctamente!"
else
    echo "⚠️  Faltan $failed variable(s). Por favor configúralas en ~/.bashrc"
    exit 1
fi
```

Guarda este script como `check_env.sh`, dale permisos de ejecución y ejecútalo:

```bash
chmod +x check_env.sh
./check_env.sh
```

---

## Notas Importantes

1. **DATABASE_URL_CRYPTO_TRADER**: Este nombre es el MISMO que usa crypto-analyzer-redis, lo que facilita compartir configuración
2. **Security**: Considera mover las API keys a AWS Secrets Manager o un servicio de secrets más seguro en producción
3. **Backup**: Haz un backup de tu .bashrc antes de modificarlo: `cp ~/.bashrc ~/.bashrc.backup`
4. **Session**: Las variables solo están disponibles en nuevas sesiones de terminal. Usa `source ~/.bashrc` para cargarlas en la sesión actual

---

## Troubleshooting

### Variables no se cargan después de source ~/.bashrc

```bash
# Verifica sintaxis del .bashrc
bash -n ~/.bashrc

# Si hay errores, restaura el backup
cp ~/.bashrc.backup ~/.bashrc
```

### Database connection error

```bash
# Verifica que PostgreSQL esté corriendo
sudo systemctl status postgresql

# Test de conexión
psql "$DATABASE_URL_CRYPTO_TRADER"
```

### Redis connection error

```bash
# Verifica que Redis esté corriendo
sudo systemctl status redis

# Test de conexión
redis-cli -h $REDIS_HOST -p $REDIS_PORT ping
```
