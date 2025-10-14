# app/utils/binance/binance_cache_client.py
"""
Cliente para leer cache de Binance generado por crypto-analyzer-redis.
Este módulo NO genera cache, solo lo consume para evitar llamadas API duplicadas.
"""

import json
import time
import logging
from typing import Optional, Dict

logger = logging.getLogger(__name__)

class BinanceCacheClient:
    """
    Cliente read-only para cache de Binance generado por crypto-analyzer-redis.
    Reduce llamadas API reutilizando datos ya cacheados.
    """

    def __init__(self, redis_client):
        """
        Args:
            redis_client: Instancia de ResilientRedisClient
        """
        self.redis_client = redis_client
        self.cache_prefix = "binance_cache"

        # TTLs esperados (debe coincidir con crypto-analyzer-redis)
        self.ttl_config = {
            "mark_price": 30,
            "orderbook": 30,
            "ticker_24h": 60,
        }

        # Stats
        self.stats = {
            'cache_hits': 0,
            'cache_misses': 0,
            'fallback_api_calls': 0
        }

    def get_mark_price(self, symbol: str, client=None, max_age: int = 30) -> Optional[float]:
        """
        Obtiene mark price desde cache de crypto-analyzer-redis.
        Si no está disponible, hace fallback a API call directo.

        Args:
            symbol: Símbolo (ej: BTCUSDT)
            client: Cliente de Binance (para fallback)
            max_age: Edad máxima aceptable del cache en segundos

        Returns:
            Mark price o None
        """
        try:
            cache_key = f"{self.cache_prefix}:mark_price:{symbol.lower()}"

            logger.warning(f"🔍 DEBUG: Buscando mark price en Redis key: '{cache_key}'")

            # Intentar obtener desde cache
            cached_data = self.redis_client.get(cache_key)

            if cached_data:
                logger.warning(f"🔍 DEBUG: Mark price encontrado en Redis para '{symbol}': {cached_data[:100]}...")
                data = json.loads(cached_data)

                # Verificar age
                age = time.time() - data.get('timestamp', 0)

                if age <= max_age:
                    self.stats['cache_hits'] += 1
                    logger.warning(f"✅ DEBUG: Mark price cache HIT: {symbol} (age: {age:.1f}s, price: {data['mark_price']})")
                    return float(data['mark_price'])
                else:
                    logger.warning(f"⚠️ DEBUG: Mark price cache STALE: {symbol} (age: {age:.1f}s > max_age: {max_age}s)")

            else:
                logger.error(f"❌ DEBUG: Mark price NO encontrado en Redis key: '{cache_key}'")

            # Cache miss o stale - hacer fallback a API si tenemos client
            self.stats['cache_misses'] += 1

            if client:
                logger.warning(f"⚠️ Mark price cache MISS: {symbol} - fallback to API")
                self.stats['fallback_api_calls'] += 1
                mark_data = client.futures_mark_price(symbol=symbol.upper())
                logger.warning(f"🔍 DEBUG: Mark price desde API: {mark_data['markPrice']}")
                return float(mark_data["markPrice"])
            else:
                logger.error(f"❌ Mark price cache MISS: {symbol} - no client for fallback")
                return None

        except Exception as e:
            logger.error(f"❌ Error getting mark price from cache for {symbol}: {e}")

            # Fallback a API si disponible
            if client:
                try:
                    self.stats['fallback_api_calls'] += 1
                    mark_data = client.futures_mark_price(symbol=symbol.upper())
                    return float(mark_data["markPrice"])
                except Exception as api_error:
                    logger.error(f"❌ API fallback also failed for {symbol}: {api_error}")

            return None

    def get_orderbook_data(self, symbol: str, depth_limit: int = 100, client=None, max_age: int = 30) -> Optional[Dict]:
        """
        Obtiene orderbook data desde cache de crypto-analyzer-redis.
        Si no está disponible, hace fallback a API call directo.

        Args:
            symbol: Símbolo (ej: BTCUSDT)
            depth_limit: Límite de profundidad (debe coincidir con el cache)
            client: Cliente de Binance (para fallback)
            max_age: Edad máxima aceptable del cache en segundos

        Returns:
            Dict con orderbook data o None
        """
        try:
            cache_key = f"{self.cache_prefix}:orderbook:{symbol.lower()}:{depth_limit}"

            logger.warning(f"🔍 DEBUG: Buscando orderbook en Redis key: '{cache_key}'")

            # Intentar obtener desde cache
            cached_data = self.redis_client.get(cache_key)

            if cached_data:
                logger.warning(f"🔍 DEBUG: Orderbook encontrado en Redis para '{symbol}': {len(cached_data)} bytes")
                data = json.loads(cached_data)

                # Verificar age
                age = time.time() - data.get('timestamp', 0)

                if age <= max_age:
                    self.stats['cache_hits'] += 1
                    orderbook = data['orderbook_data']
                    logger.warning(f"✅ DEBUG: Orderbook cache HIT: {symbol} (age: {age:.1f}s, bids: {len(orderbook.get('bids', []))}, asks: {len(orderbook.get('asks', []))})")
                    return orderbook
                else:
                    logger.warning(f"⚠️ DEBUG: Orderbook cache STALE: {symbol} (age: {age:.1f}s > max_age: {max_age}s)")

            else:
                logger.error(f"❌ DEBUG: Orderbook NO encontrado en Redis key: '{cache_key}'")

            # Cache miss o stale - hacer fallback a API si tenemos client
            self.stats['cache_misses'] += 1

            if client:
                logger.warning(f"⚠️ Orderbook cache MISS: {symbol} - fallback to API")
                self.stats['fallback_api_calls'] += 1
                order_book = client.futures_order_book(symbol=symbol.upper(), limit=depth_limit)

                logger.warning(f"🔍 DEBUG: Orderbook desde API: bids={len(order_book.get('bids', []))}, asks={len(order_book.get('asks', []))}")

                # Retornar raw orderbook (el llamador procesará según necesite)
                return {
                    "bids": order_book.get("bids", []),
                    "asks": order_book.get("asks", [])
                }
            else:
                logger.error(f"❌ Orderbook cache MISS: {symbol} - no client for fallback")
                return None

        except Exception as e:
            logger.error(f"❌ Error getting orderbook from cache for {symbol}: {e}")

            # Fallback a API si disponible
            if client:
                try:
                    self.stats['fallback_api_calls'] += 1
                    order_book = client.futures_order_book(symbol=symbol.upper(), limit=depth_limit)
                    return {
                        "bids": order_book.get("bids", []),
                        "asks": order_book.get("asks", [])
                    }
                except Exception as api_error:
                    logger.error(f"❌ API fallback also failed for {symbol}: {api_error}")

            return None

    def get_klines_from_redis(self, symbol: str, interval: str = "1m", limit: int = 60) -> Optional[list]:
        """
        Obtiene klines desde Redis poblado por crypto-data-redis.

        Args:
            symbol: Símbolo (ej: BTCUSDT)
            interval: Intervalo (1m, 5m, 15m, etc.)
            limit: Número de klines a obtener

        Returns:
            Lista de klines o None
        """
        try:
            # crypto-data-redis guarda candles con formato: candles:symbol:interval
            # Usar Redis Streams (XRANGE) en lugar de keys regulares
            stream_key = f"candles:{symbol.lower()}:{interval}"

            logger.warning(f"🔍 DEBUG: Intentando leer Redis stream key: '{stream_key}'")

            # Leer últimos 'limit' elementos del stream
            entries = self.redis_client.xrevrange(stream_key, max="+", min="-", count=limit)

            logger.warning(f"🔍 DEBUG: Redis XREVRANGE returned {len(entries) if entries else 0} entries for '{stream_key}'")

            if entries:
                logger.warning(f"🔍 DEBUG: Primera entry en stream '{stream_key}': id={entries[0][0]}, fields={list(entries[0][1].keys())}")

                klines = []
                # Convertir formato de stream a formato de klines
                for entry_id, fields in reversed(entries):  # Invertir para orden cronológico
                    try:
                        timestamp_ms = int(entry_id.decode().split('-')[0])

                        # Validar campos requeridos
                        if all(key in fields for key in [b"o", b"h", b"l", b"c", b"v"]):
                            # Formato compatible con Binance klines API
                            kline = [
                                timestamp_ms,                           # 0: Open time
                                fields[b"o"].decode(),                  # 1: Open
                                fields[b"h"].decode(),                  # 2: High
                                fields[b"l"].decode(),                  # 3: Low
                                fields[b"c"].decode(),                  # 4: Close
                                fields[b"v"].decode(),                  # 5: Volume
                                timestamp_ms + 60000,                   # 6: Close time (aprox)
                                fields[b"v"].decode(),                  # 7: Quote asset volume (mismo que volume)
                                0,                                       # 8: Number of trades
                                fields[b"v"].decode(),                  # 9: Taker buy base volume
                                fields[b"v"].decode(),                  # 10: Taker buy quote volume
                                "0"                                      # 11: Ignore
                            ]
                            klines.append(kline)
                        else:
                            missing_fields = [k for k in [b"o", b"h", b"l", b"c", b"v"] if k not in fields]
                            logger.error(f"❌ DEBUG: Entry en '{stream_key}' falta campos: {missing_fields}, tiene: {list(fields.keys())}")
                    except Exception as parse_error:
                        logger.error(f"❌ DEBUG: Error parsing stream entry for {symbol}: {parse_error}")
                        continue

                if klines:
                    self.stats['cache_hits'] += 1
                    logger.warning(f"✅ DEBUG: Klines cache HIT: {symbol} {interval} ({len(klines)} candles from stream)")
                    logger.warning(f"🔍 DEBUG: Primera kline parseada: timestamp={klines[0][0]}, close={klines[0][4]}, volume={klines[0][5]}")
                    return klines
                else:
                    self.stats['cache_misses'] += 1
                    logger.error(f"❌ DEBUG: Klines cache MISS: {symbol} {interval} - stream tiene {len(entries)} entries pero ninguna válida")
                    return None
            else:
                self.stats['cache_misses'] += 1
                logger.error(f"❌ DEBUG: Klines cache MISS: {symbol} {interval} - stream '{stream_key}' vacío o no existe")

                # Verificar si el key existe en Redis
                try:
                    key_type = self.redis_client.type(stream_key)
                    logger.error(f"❌ DEBUG: Redis key '{stream_key}' type: {key_type}")
                    if key_type == b'stream' or key_type == 'stream':
                        stream_info = self.redis_client.xinfo_stream(stream_key)
                        logger.error(f"❌ DEBUG: Stream info para '{stream_key}': length={stream_info.get('length', 0)}")
                except Exception as info_error:
                    logger.error(f"❌ DEBUG: Error obteniendo info de '{stream_key}': {info_error}")

                return None

        except Exception as e:
            logger.error(f"❌ DEBUG: Exception getting klines from Redis stream for {symbol}: {e}")
            import traceback
            logger.error(f"❌ DEBUG: Traceback: {traceback.format_exc()}")
            return None

    def get_cache_stats(self) -> Dict:
        """
        Obtiene estadísticas de uso del cache
        """
        total_requests = self.stats['cache_hits'] + self.stats['cache_misses']
        hit_rate = (self.stats['cache_hits'] / total_requests * 100) if total_requests > 0 else 0

        return {
            'cache_hit_rate': round(hit_rate, 2),
            'total_requests': total_requests,
            'cache_hits': self.stats['cache_hits'],
            'cache_misses': self.stats['cache_misses'],
            'fallback_api_calls': self.stats['fallback_api_calls'],
            'efficiency': 'Excellent' if hit_rate > 80 else 'Good' if hit_rate > 60 else 'Poor'
        }

    def clear_stats(self):
        """Reset estadísticas"""
        self.stats = {
            'cache_hits': 0,
            'cache_misses': 0,
            'fallback_api_calls': 0
        }


# Cache local para exchange_info (casi estático)
_exchange_info_cache = {
    "data": None,
    "timestamp": 0,
    "ttl": 3600  # 1 hora
}

_leverage_bracket_cache = {}  # {symbol: {data, timestamp}}
_leverage_bracket_ttl = 3600  # 1 hora


def get_exchange_info_cached(client) -> Optional[Dict]:
    """
    Obtiene exchange_info con cache local de 1 hora.
    Exchange info es casi estático y cambia muy raramente.

    Args:
        client: Cliente de Binance

    Returns:
        Dict con exchange info o None
    """
    global _exchange_info_cache

    try:
        # Verificar si cache es válido
        age = time.time() - _exchange_info_cache["timestamp"]

        if _exchange_info_cache["data"] is not None and age < _exchange_info_cache["ttl"]:
            logger.debug(f"✅ Exchange info cache HIT (age: {age:.0f}s)")
            return _exchange_info_cache["data"]

        # Cache miss o expirado - obtener desde API
        logger.info("⚠️ Exchange info cache MISS - fetching from API")
        exchange_info = client.futures_exchange_info()

        # Actualizar cache
        _exchange_info_cache["data"] = exchange_info
        _exchange_info_cache["timestamp"] = time.time()

        return exchange_info

    except Exception as e:
        logger.error(f"❌ Error getting exchange_info: {e}")

        # Intentar retornar cache stale si disponible
        if _exchange_info_cache["data"] is not None:
            logger.warning("Using stale exchange_info cache as fallback")
            return _exchange_info_cache["data"]

        return None


def get_leverage_bracket_cached(symbol: str, client) -> Optional[list]:
    """
    Obtiene leverage bracket con cache local de 1 hora.
    Leverage brackets cambian muy raramente.

    Args:
        symbol: Símbolo (ej: BTCUSDT)
        client: Cliente de Binance

    Returns:
        Lista con leverage brackets o None
    """
    global _leverage_bracket_cache

    try:
        symbol_upper = symbol.upper()

        # Verificar si cache es válido
        if symbol_upper in _leverage_bracket_cache:
            cache_entry = _leverage_bracket_cache[symbol_upper]
            age = time.time() - cache_entry["timestamp"]

            if age < _leverage_bracket_ttl:
                logger.debug(f"✅ Leverage bracket cache HIT for {symbol} (age: {age:.0f}s)")
                return cache_entry["data"]

        # Cache miss o expirado - obtener desde API
        logger.info(f"⚠️ Leverage bracket cache MISS for {symbol} - fetching from API")
        brackets = client.futures_leverage_bracket(symbol=symbol_upper)

        # Actualizar cache
        _leverage_bracket_cache[symbol_upper] = {
            "data": brackets,
            "timestamp": time.time()
        }

        return brackets

    except Exception as e:
        logger.error(f"❌ Error getting leverage bracket for {symbol}: {e}")

        # Intentar retornar cache stale si disponible
        if symbol.upper() in _leverage_bracket_cache:
            logger.warning(f"Using stale leverage bracket cache for {symbol}")
            return _leverage_bracket_cache[symbol.upper()]["data"]

        return None


# Instancia global (se inicializa en main.py o donde se tenga redis_client)
_binance_cache_client = None

def get_binance_cache_client():
    """
    Obtiene instancia global de BinanceCacheClient.
    Debe llamarse después de inicializar redis_client.
    """
    global _binance_cache_client

    if _binance_cache_client is None:
        from app.utils.db.redis_client import get_redis_client
        redis_client = get_redis_client()
        _binance_cache_client = BinanceCacheClient(redis_client)

    return _binance_cache_client
