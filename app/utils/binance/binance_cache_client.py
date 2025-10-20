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
        # Prefix para diferentes tipos de cache
        self.websocket_prefix = "websocket"
        self.binance_cache_prefix = "binance_cache"

        # TTLs esperados (debe coincidir con crypto-data-redis)
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
            # crypto-data-redis usa prefix "websocket" para mark_price
            cache_key = f"{self.websocket_prefix}:mark_price:{symbol.lower()}"

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

    def get_orderbook_data(self, symbol: str, depth_limit: int = None, client=None, max_age: int = 4) -> Optional[Dict]:
        """
        Obtiene orderbook data con estrategia de cache inteligente.

        Estrategia multi-layer (freshness requirement: ≤4s):
        1. Cache de crypto-analyzer-redis (binance_cache:orderbook:{symbol}:{depth})
        2. WebSocket cache de crypto-data-redis (websocket:orderbook:{symbol}) - DEPRECADO
        3. API call directo con depth granular automático

        Args:
            symbol: Símbolo (ej: BTCUSDT)
            depth_limit: Límite de profundidad (auto-detecta si None)
            client: Cliente de Binance (para fallback)
            max_age: Edad máxima aceptable del cache en segundos (default: 4s)

        Returns:
            Dict con orderbook data o None
        """
        try:
            # Auto-detect depth limit usando la misma lógica que crypto-analyzer-redis
            if depth_limit is None:
                depth_limit = self._get_depth_limit_granular(symbol)

            # 🎯 PRIORIDAD 1: Cache de crypto-analyzer-redis (más reciente, con depth específico)
            # Formato: binance_cache:orderbook:{symbol}:{depth_limit}
            analyzer_cache_key = f"{self.binance_cache_prefix}:orderbook:{symbol.lower()}:{depth_limit}"

            logger.debug(f"🔍 Intentando cache de crypto-analyzer-redis: '{analyzer_cache_key}'")

            cached_data = self.redis_client.get(analyzer_cache_key)

            if cached_data:
                try:
                    cache_entry = json.loads(cached_data)
                    age = time.time() - cache_entry.get('timestamp', 0)

                    if age <= max_age:
                        # Cache HIT desde crypto-analyzer-redis
                        self.stats['cache_hits'] += 1
                        orderbook_data = cache_entry.get('orderbook_data', {})

                        logger.info(f"✅ Orderbook cache HIT (analyzer): {symbol} (age: {age:.1f}s, depth: {depth_limit})")

                        # Retornar datos (ya están en formato procesado)
                        return {
                            **orderbook_data,
                            "source": "analyzer_cache",
                            "cache_age": age
                        }
                    else:
                        logger.debug(f"⏰ Cache de analyzer STALE: {symbol} (age: {age:.1f}s > max_age: {max_age}s)")
                except (json.JSONDecodeError, KeyError) as e:
                    logger.debug(f"⚠️ Error parsing analyzer cache for {symbol}: {e}")

            # 🎯 PRIORIDAD 2: WebSocket cache de crypto-data-redis (DEPRECADO pero backward compatible)
            # Formato: websocket:orderbook:{symbol}
            websocket_cache_key = f"{self.websocket_prefix}:orderbook:{symbol.lower()}"

            logger.debug(f"🔍 Intentando WebSocket cache (deprecated): '{websocket_cache_key}'")

            cached_data = self.redis_client.get(websocket_cache_key)

            if cached_data:
                try:
                    data = json.loads(cached_data)

                    # Verificar age
                    age = time.time() - data.get('timestamp', 0)

                    if age <= max_age:
                        self.stats['cache_hits'] += 1

                        # ✅ Convertir formato de crypto-data-redis (WebSocket) a formato esperado
                        best_bid = data.get('best_bid', 0)
                        best_ask = data.get('best_ask', 0)

                        orderbook = {
                            # Formato compatible con validadores existentes
                            "bids": [[str(best_bid), "1.0"]] if best_bid > 0 else [],
                            "asks": [[str(best_ask), "1.0"]] if best_ask > 0 else [],

                            # Métricas pre-calculadas
                            "spread_pct": data.get('spread_pct', 0),
                            "slippage_pct": data.get('slippage_pct', 0),
                            "depth_bid_usdt": data.get('depth_bid_usdt', 0),
                            "depth_ask_usdt": data.get('depth_ask_usdt', 0),
                            "imbalance_pct": data.get('imbalance_pct', 0),
                            "slippage_qty": data.get('slippage_qty', 0),
                            "category": data.get('category', 'unknown'),
                            "session": data.get('session', 'unknown'),

                            # Metadata
                            "timestamp": data.get('timestamp', 0),
                            "source": "websocket_cache",
                            "cache_age": age
                        }

                        logger.info(f"✅ Orderbook cache HIT (websocket): {symbol} (age: {age:.1f}s, spread: {orderbook['spread_pct']:.4f}%)")
                        return orderbook
                    else:
                        logger.debug(f"⏰ WebSocket cache STALE: {symbol} (age: {age:.1f}s > max_age: {max_age}s)")
                except (json.JSONDecodeError, KeyError) as e:
                    logger.debug(f"⚠️ Error parsing WebSocket cache for {symbol}: {e}")

            # 🎯 PRIORIDAD 3: API call fallback con depth granular
            self.stats['cache_misses'] += 1

            if client:
                logger.warning(f"⚠️ Orderbook cache MISS: {symbol} - fallback to API (depth={depth_limit})")
                self.stats['fallback_api_calls'] += 1

                # API call con depth limit óptimo
                order_book = client.futures_order_book(symbol=symbol.upper(), limit=depth_limit)

                logger.info(f"📞 Orderbook desde API: {symbol} - bids={len(order_book.get('bids', []))}, asks={len(order_book.get('asks', []))}")

                # Procesar orderbook (calcular métricas)
                orderbook_processed = self._process_orderbook_api(order_book, symbol)

                # ✅ CRÍTICO: Guardar en Redis para reutilización (30s TTL)
                try:
                    cache_data = {
                        'orderbook_data': orderbook_processed,
                        'timestamp': time.time()
                    }
                    # Guardar con mismo formato que crypto-analyzer-redis
                    self.redis_client.setex(
                        analyzer_cache_key,
                        self.ttl_config['orderbook'],  # 30 segundos
                        json.dumps(cache_data)
                    )
                    logger.debug(f"💾 Orderbook guardado en Redis: {analyzer_cache_key} (TTL=30s)")
                except Exception as cache_error:
                    logger.warning(f"⚠️ Failed to cache orderbook for {symbol}: {cache_error}")

                # Retornar datos procesados con source
                return {
                    **orderbook_processed,
                    "source": "api_fallback",
                    "cache_age": 0
                }
            else:
                logger.error(f"❌ Orderbook cache MISS: {symbol} - no client for fallback")
                return None

        except Exception as e:
            logger.error(f"❌ Error getting orderbook from cache for {symbol}: {e}")

            # Fallback a API si disponible
            if client:
                try:
                    # Auto-detect depth si no se especificó
                    if depth_limit is None:
                        depth_limit = self._get_depth_limit_granular(symbol)

                    self.stats['fallback_api_calls'] += 1
                    order_book = client.futures_order_book(symbol=symbol.upper(), limit=depth_limit)

                    # Procesar orderbook
                    orderbook_processed = self._process_orderbook_api(order_book, symbol)

                    return {
                        **orderbook_processed,
                        "source": "api_fallback_error",
                        "cache_age": 0
                    }
                except Exception as api_error:
                    logger.error(f"❌ API fallback also failed for {symbol}: {api_error}")

            return None

    def _get_depth_limit_granular(self, symbol: str) -> int:
        """
        Determina el depth limit óptimo usando la misma lógica granular que crypto-analyzer-redis.

        Categorización optimizada para slippage_qty=$3-4K:
        - Ultra-líquidos (BTC, ETH, BNB): 50 niveles suficientes
        - High-liquidity (Top 10): 100 niveles para seguridad
        - Low-liquidity (Memecoins/Nuevos): 100 niveles crítico
        - Mid-liquidity (Resto): 100 niveles balanceado

        IMPORTANTE: Binance Futures API solo acepta: 5, 10, 20, 50, 100, 500, 1000

        Args:
            symbol: Símbolo de la criptomoneda

        Returns:
            int: Depth limit óptimo
        """
        symbol_lower = symbol.lower()

        # Ultra-líquidos: BTC, ETH, BNB tienen liquidez extrema (>$20K por nivel)
        if symbol_lower in {'btcusdt', 'ethusdt', 'bnbusdt'}:
            return 50  # Suficiente para $4K slippage

        # High-liquidity: Top altcoins con liquidez consistente
        HIGH_LIQUIDITY = {
            'btcusdt', 'ethusdt', 'bnbusdt', 'solusdt', 'adausdt', 'dogeusdt', 'xrpusdt', 'ltcusdt',
            'dotusdt', 'linkusdt', 'trxusdt', 'maticusdt', 'avaxusdt', 'xlmusdt'
        }
        if symbol_lower in HIGH_LIQUIDITY:
            return 100  # Margen de seguridad (75 no es válido en Binance API)

        # Low-liquidity: Memecoins y tokens nuevos con orderbook delgado
        LOW_LIQUIDITY = {
            'virtualusdt', 'vicusdt', 'wifusdt', 'trumpusdt', 'notusdt',
            'opusdt', 'ordiusdt', 'hyperusdt', 'paxgusdt'
        }
        if symbol_lower in LOW_LIQUIDITY:
            return 100  # Crítico para precisión

        # Mid-liquidity: Resto de altcoins establecidos
        return 100  # Balance entre precisión y performance (75 no es válido en Binance API)

    def _process_orderbook_api(self, order_book: dict, symbol: str) -> dict:
        """
        Procesa orderbook raw de API y calcula métricas (spread, slippage, depth, imbalance).

        Args:
            order_book: Orderbook raw de Binance API {"bids": [[p,q],...], "asks": [[p,q],...]}
            symbol: Símbolo de la criptomoneda

        Returns:
            dict: Orderbook procesado con métricas calculadas
        """
        try:
            bids = order_book.get("bids", [])
            asks = order_book.get("asks", [])

            if not bids or not asks:
                logger.warning(f"⚠️ Orderbook vacío para {symbol}")
                return {
                    "bids": [],
                    "asks": [],
                    "spread_pct": 0,
                    "slippage_pct": 0,
                    "depth_bid": 0,
                    "depth_ask": 0,
                    "imbalance_pct": 0
                }

            # Best bid/ask
            best_bid = float(bids[0][0])
            best_ask = float(asks[0][0])

            # Spread
            spread_abs = best_ask - best_bid
            spread_pct = (spread_abs / best_ask) * 100

            # Depth (liquidez total en USDT, convertido a millones)
            def total_notional(orders):
                return sum(float(p) * float(q) for p, q in orders)

            bid_notional = total_notional(bids)
            ask_notional = total_notional(asks)

            # Imbalance (positivo = más bids, negativo = más asks)
            total_notional_sum = bid_notional + ask_notional
            imbalance_pct = (
                ((bid_notional - ask_notional) / total_notional_sum) * 100
                if total_notional_sum > 0 else 0
            )

            # Slippage (estimación de ejecución de market order para $3K)
            slippage_qty = 3000  # DEFAULT_SLIPPAGE_QTY

            def estimate_slippage(orderbook_side, qty_usdt):
                """Estima precio promedio de ejecución"""
                filled = 0
                total_qty = 0

                for price_str, qty_str in orderbook_side:
                    price = float(price_str)
                    qty = float(qty_str)
                    notional = price * qty

                    if filled + notional >= qty_usdt:
                        remaining = qty_usdt - filled
                        qty_needed = remaining / price
                        total_qty += qty_needed
                        break

                    filled += notional
                    total_qty += qty

                avg_price = qty_usdt / total_qty if total_qty > 0 else best_ask
                return avg_price

            # Calcular slippage para BUY (market order en asks)
            avg_execution_price = estimate_slippage(asks, slippage_qty)
            slippage_pct = ((avg_execution_price - best_ask) / best_ask) * 100

            return {
                "bids": bids,
                "asks": asks,
                "spread_abs": round(spread_abs, 6),
                "spread_pct": round(spread_pct, 4),
                "depth_bid": round(bid_notional / 1_000_000, 2),  # Millones
                "depth_ask": round(ask_notional / 1_000_000, 2),  # Millones
                "imbalance_pct": round(imbalance_pct, 2),
                "slippage_pct": round(slippage_pct, 4)
            }

        except Exception as e:
            logger.error(f"❌ Error procesando orderbook API para {symbol}: {e}")
            return {
                "bids": order_book.get("bids", []),
                "asks": order_book.get("asks", []),
                "spread_pct": 0,
                "slippage_pct": 0,
                "depth_bid": 0,
                "depth_ask": 0,
                "imbalance_pct": 0
            }

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
                        # Manejar entry_id como bytes o string
                        if isinstance(entry_id, bytes):
                            entry_id_str = entry_id.decode()
                        else:
                            entry_id_str = str(entry_id)

                        timestamp_ms = int(entry_id_str.split('-')[0])

                        # Helper para obtener valores de fields (manejar bytes y strings)
                        def get_field(field_name):
                            # Intentar con bytes primero
                            if isinstance(field_name, str):
                                field_name_bytes = field_name.encode()
                            else:
                                field_name_bytes = field_name

                            # Buscar key en formato bytes o string
                            value = fields.get(field_name_bytes) or fields.get(field_name if isinstance(field_name, str) else field_name.decode())

                            if value is None:
                                return None

                            # Retornar como string
                            if isinstance(value, bytes):
                                return value.decode()
                            return str(value)

                        # Validar campos requeridos (intentar ambos formatos)
                        required_fields = ["o", "h", "l", "c", "v"]
                        field_values = {}

                        for field in required_fields:
                            val = get_field(field)
                            if val is None:
                                logger.error(f"❌ DEBUG: Entry en '{stream_key}' falta campo '{field}', tiene: {list(fields.keys())}")
                                break
                            field_values[field] = val
                        else:
                            # Todos los campos encontrados, crear kline
                            kline = [
                                timestamp_ms,                           # 0: Open time
                                field_values["o"],                      # 1: Open
                                field_values["h"],                      # 2: High
                                field_values["l"],                      # 3: Low
                                field_values["c"],                      # 4: Close
                                field_values["v"],                      # 5: Volume
                                timestamp_ms + 60000,                   # 6: Close time (aprox)
                                field_values["v"],                      # 7: Quote asset volume (mismo que volume)
                                0,                                       # 8: Number of trades
                                field_values["v"],                      # 9: Taker buy base volume
                                field_values["v"],                      # 10: Taker buy quote volume
                                "0"                                      # 11: Ignore
                            ]
                            klines.append(kline)

                    except Exception as parse_error:
                        logger.error(f"❌ DEBUG: Error parsing stream entry for {symbol}: {parse_error}")
                        import traceback
                        logger.error(f"❌ DEBUG: Traceback: {traceback.format_exc()}")
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
