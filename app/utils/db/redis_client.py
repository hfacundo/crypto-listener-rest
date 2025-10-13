# app/utils/db/redis_client.py
"""
Resilient Redis Client Configuration
=====================================
Cliente Redis robusto con retry automático, circuit breaker y connection pooling.
Idéntico al usado en crypto-analyzer-redis para consistencia.
"""
import redis
import os
import time
import logging
from functools import wraps
from contextlib import contextmanager

# Configuración de logging
logger = logging.getLogger(__name__)

class CircuitBreakerError(Exception):
    """Excepción lanzada cuando el circuit breaker está abierto"""
    pass

class RedisCircuitBreaker:
    """Circuit breaker para conexiones Redis"""

    def __init__(self, failure_threshold=5, timeout=60):
        self.failure_threshold = failure_threshold
        self.timeout = timeout
        self.failure_count = 0
        self.last_failure_time = None
        self.state = 'CLOSED'  # CLOSED, OPEN, HALF_OPEN

    def call(self, func, *args, **kwargs):
        if self.state == 'OPEN':
            if time.time() - self.last_failure_time > self.timeout:
                self.state = 'HALF_OPEN'
                logger.info("🔄 Circuit breaker: Attempting half-open state")
            else:
                raise CircuitBreakerError("Circuit breaker is OPEN")

        try:
            result = func(*args, **kwargs)
            self._on_success()
            return result
        except Exception as e:
            self._on_failure()
            raise e

    def _on_success(self):
        self.failure_count = 0
        if self.state == 'HALF_OPEN':
            self.state = 'CLOSED'
            logger.info("✅ Circuit breaker: Returned to CLOSED state")

    def _on_failure(self):
        self.failure_count += 1
        self.last_failure_time = time.time()

        if self.failure_count >= self.failure_threshold:
            self.state = 'OPEN'
            logger.error(f"🚨 Circuit breaker: OPENED after {self.failure_count} failures")

class ResilientRedisClient:
    """Cliente Redis con retry automático y circuit breaker"""

    def __init__(self, max_retries=3, retry_delay=1, **redis_kwargs):
        self.max_retries = max_retries
        self.retry_delay = retry_delay
        self.circuit_breaker = RedisCircuitBreaker()

        # Configuración de conexión con timeouts apropiados
        self.redis_config = {
            'host': redis_kwargs.get('host', os.getenv('REDIS_HOST', 'localhost')),
            'port': redis_kwargs.get('port', int(os.getenv('REDIS_PORT', 6379))),
            'db': redis_kwargs.get('db', int(os.getenv('REDIS_DB', 0))),
            'password': redis_kwargs.get('password', os.getenv("REDIS_PASSWORD")),
            'decode_responses': redis_kwargs.get('decode_responses', True),
            'socket_timeout': 5,
            'socket_connect_timeout': 3,
            'retry_on_timeout': True,
            'health_check_interval': 30,
            'max_connections': 10
        }

        self._client = None
        self._connection_pool = None
        self._create_connection()

    def _create_connection(self):
        """Crea pool de conexiones Redis (no falla si Redis no disponible)"""
        try:
            self._connection_pool = redis.ConnectionPool(**self.redis_config)
            self._client = redis.Redis(connection_pool=self._connection_pool)

            # Test de conexión
            self._client.ping()
            logger.info(f"✅ Redis connected: {self.redis_config['host']}:{self.redis_config['port']} (db={self.redis_config['db']})")

        except Exception as e:
            logger.warning(f"⚠️ Redis not available: {e}")
            logger.warning("⚠️ Running in NO-REDIS mode (some features disabled)")
            self._client = None
            # NO raise - permitir continuar sin Redis

    def _execute_with_retry(self, operation, *args, **kwargs):
        """Ejecuta operación con retry automático"""

        for attempt in range(self.max_retries + 1):
            try:
                if self._client is None:
                    self._create_connection()

                # DEBUG: Only log if there are still None values
                operation_name = getattr(operation, '__name__', str(operation))
                if any(arg is None for arg in args) or any(v is None for v in kwargs.values()):
                    logger.error(f"🐛 NONE_DEBUG: {operation_name} called with None values - args: {args}, kwargs: {kwargs}")

                return self.circuit_breaker.call(operation, *args, **kwargs)

            except (redis.ConnectionError, redis.TimeoutError) as e:
                if attempt < self.max_retries:
                    wait_time = self.retry_delay * (2 ** attempt)  # Exponential backoff
                    logger.warning(f"⚠️ Redis attempt {attempt + 1} failed: {e}. Retrying in {wait_time}s...")
                    time.sleep(wait_time)

                    # Recrear conexión en caso de error
                    self._client = None
                    continue
                else:
                    logger.error(f"❌ Redis operation failed after {self.max_retries} retries: {e}")
                    raise

            except CircuitBreakerError:
                logger.warning("⚠️ Circuit breaker is open, skipping Redis operation")
                return None

            except Exception as e:
                logger.error(f"❌ Unexpected Redis error: {e}")
                raise

    def get(self, key):
        if key is None:
            logger.error(f"🐛 NONE_DEBUG: get called with None key")
            return None
        return self._execute_with_retry(self._client.get, key)

    def set(self, key, value, ex=None):
        if value is None:
            logger.warning(f"⚠️ Attempted to set None value for key: {key}")
            return None
        return self._execute_with_retry(self._client.set, key, value, ex=ex)

    def setex(self, key, time, value):
        """Set key with expiration time in seconds"""
        if value is None:
            logger.warning(f"⚠️ Attempted to setex None value for key: {key}")
            return None
        return self._execute_with_retry(self._client.setex, key, time, value)

    def exists(self, key):
        return self._execute_with_retry(self._client.exists, key)

    def lrange(self, key, start, end):
        return self._execute_with_retry(self._client.lrange, key, start, end)

    def xrange(self, stream, min=None, max=None, count=None):
        if stream is None:
            logger.error(f"🐛 NONE_DEBUG: xrange called with None stream")
            return []

        kwargs = {}
        if min is not None:
            kwargs['min'] = min
        if max is not None:
            kwargs['max'] = max
        if count is not None:
            kwargs['count'] = count

        return self._execute_with_retry(self._client.xrange, stream, **kwargs)

    def xrevrange(self, stream, max=None, min=None, count=None):
        """Get stream entries in reverse order (newest first)"""
        if stream is None:
            logger.error(f"🐛 NONE_DEBUG: xrevrange called with None stream")
            return []

        kwargs = {}
        if max is not None:
            kwargs['max'] = max
        if min is not None:
            kwargs['min'] = min
        if count is not None:
            kwargs['count'] = count

        return self._execute_with_retry(self._client.xrevrange, stream, **kwargs)

    def sismember(self, key, member):
        return self._execute_with_retry(self._client.sismember, key, member)

    def keys(self, pattern):
        """Get keys matching pattern"""
        return self._execute_with_retry(self._client.keys, pattern)

    def delete(self, *keys):
        """Delete one or more keys"""
        return self._execute_with_retry(self._client.delete, *keys)

    def ping(self):
        return self._execute_with_retry(self._client.ping)

    @contextmanager
    def get_connection_info(self):
        """Context manager para obtener info de conexión"""
        try:
            info = {
                'connected': self._client is not None,
                'circuit_breaker_state': self.circuit_breaker.state,
                'failure_count': self.circuit_breaker.failure_count
            }

            if self._client:
                try:
                    self._client.ping()
                    info['redis_responsive'] = True
                except:
                    info['redis_responsive'] = False

            yield info

        except Exception as e:
            yield {'error': str(e), 'connected': False}


# Instancia global singleton (compatible con código existente)
_redis_client = None

def get_redis_client():
    """
    Retorna instancia compartida de ResilientRedisClient.

    Variables de entorno:
        REDIS_HOST: Host del servidor Redis (default: localhost)
        REDIS_PORT: Puerto del servidor Redis (default: 6379)
        REDIS_DB: Base de datos de Redis (default: 0)
        REDIS_PASSWORD: Password de Redis (opcional)

    Returns:
        ResilientRedisClient: Cliente Redis robusto, o None si falla
    """
    global _redis_client

    if _redis_client is None:
        _redis_client = ResilientRedisClient(
            host=os.getenv('REDIS_HOST', 'localhost'),
            port=int(os.getenv('REDIS_PORT', 6379)),
            db=int(os.getenv('REDIS_DB', 0)),
            password=os.getenv('REDIS_PASSWORD'),
            decode_responses=True
        )

    return _redis_client


def reset_redis_client():
    """Resetea la instancia compartida del cliente Redis"""
    global _redis_client
    if _redis_client and _redis_client._client:
        try:
            _redis_client._client.close()
        except Exception:
            pass
    _redis_client = None
