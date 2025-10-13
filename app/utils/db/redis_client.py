"""
Redis Client Configuration
===========================
Módulo para configuración y acceso centralizado al cliente de Redis.

Proporciona una instancia compartida (singleton) de Redis client configurado
desde variables de entorno.
"""
import redis
import os
import logging

logger = logging.getLogger(__name__)

# Instancia compartida del cliente Redis
_redis_client = None


def get_redis_client():
    """
    Retorna instancia compartida de Redis client.

    La instancia se crea la primera vez que se llama esta función y se reutiliza
    en llamadas subsecuentes (patrón singleton).

    Variables de entorno:
        REDIS_HOST: Host del servidor Redis (default: localhost)
        REDIS_PORT: Puerto del servidor Redis (default: 6379)
        REDIS_DB: Base de datos de Redis (default: 0)
        REDIS_PASSWORD: Password de Redis (opcional)

    Returns:
        redis.Redis: Cliente de Redis configurado, o None si falla la conexión

    Example:
        >>> client = get_redis_client()
        >>> if client:
        >>>     client.set("key", "value")
    """
    global _redis_client

    # Si ya existe una instancia, retornarla
    if _redis_client is not None:
        return _redis_client

    try:
        # Configuración desde variables de entorno
        redis_host = os.environ.get("REDIS_HOST", "localhost")
        redis_port = int(os.environ.get("REDIS_PORT", 6379))
        redis_db = int(os.environ.get("REDIS_DB", 0))
        redis_password = os.environ.get("REDIS_PASSWORD")

        # Crear cliente Redis
        _redis_client = redis.Redis(
            host=redis_host,
            port=redis_port,
            db=redis_db,
            password=redis_password if redis_password else None,
            decode_responses=False,  # Mantener como bytes para compatibilidad
            socket_connect_timeout=5,
            socket_timeout=5
        )

        # Test connection
        _redis_client.ping()
        logger.info(f"✅ Redis connected: {redis_host}:{redis_port} (db={redis_db})")

        return _redis_client

    except redis.ConnectionError as e:
        logger.error(f"❌ Redis connection failed: {e}")
        return None
    except Exception as e:
        logger.error(f"❌ Error initializing Redis client: {e}")
        return None


def reset_redis_client():
    """
    Resetea la instancia compartida del cliente Redis.

    Útil para testing o cuando se necesita reconectar.
    """
    global _redis_client
    if _redis_client:
        try:
            _redis_client.close()
        except Exception:
            pass
    _redis_client = None
