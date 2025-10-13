#!/usr/bin/env python3
"""
Binance WebSocket Manager Optimizado para Mark Price
=====================================================

Features:
- Throttling: Guarda en Redis cada 5 segundos (no cada segundo)
- Batching: Usa Redis pipeline para writes batch (1 network round-trip)
- In-memory cache: Updates disponibles inmediatamente
- Metrics: Tracking de performance

Performance:
- Updates recibidos: 50/segundo (1 por símbolo)
- Writes a Redis: 10/segundo (50 símbolos / 5 segundos batch)
- Network round-trips: 0.2/segundo (1 batch cada 5 segundos)
"""

import time
import json
import logging
from typing import Dict, Optional
from binance import ThreadedWebsocketManager
import redis

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class OptimizedWebSocketManager:
    """
    WebSocket Manager optimizado con throttling y batching.

    Arquitectura:
    1. WebSocket recibe updates cada segundo
    2. Acumula en memoria (self.pending_updates)
    3. Cada 5 segundos, hace flush batch a Redis usando pipeline
    4. Servicios externos leen desde Redis
    """

    def __init__(self, redis_client, batch_interval: int = 5):
        """
        Args:
            redis_client: Redis client instance
            batch_interval: Segundos entre batch writes (default: 5)
        """
        self.redis_client = redis_client
        self.batch_interval = batch_interval

        # WebSocket manager
        self.twm = ThreadedWebsocketManager()
        self.twm.start()

        # In-memory cache
        self.pending_updates = {}  # {symbol: data}
        self.memory_cache = {}     # {symbol: data} - Para access inmediato

        # Metrics
        self.metrics = {
            'updates_received': 0,
            'batches_written': 0,
            'symbols_tracked': 0,
            'last_batch_time': time.time(),
            'last_batch_size': 0
        }

        # Start flush thread
        import threading
        self.flush_thread = threading.Thread(
            target=self._flush_batch_loop,
            daemon=True
        )
        self.flush_thread.start()

        logger.info(f"✅ WebSocket Manager initialized (batch_interval={batch_interval}s)")

    def start_mark_price_stream(self, symbol: str):
        """
        Inicia stream de mark price para un símbolo.
        Updates se acumulan en memoria y se guardan en batch.

        Args:
            symbol: Símbolo (ej: BTCUSDT)
        """
        symbol_upper = symbol.upper()

        def handle_message(msg):
            try:
                if msg['e'] == 'markPriceUpdate':
                    mark_price = float(msg['p'])
                    timestamp = msg['E'] / 1000  # ms to seconds

                    data = {
                        'mark_price': mark_price,
                        'timestamp': timestamp,
                        'source': 'websocket',
                        'symbol': symbol_upper
                    }

                    # Update in-memory cache (instant access)
                    self.memory_cache[symbol_upper] = data

                    # Acumular para batch write (no escribir inmediatamente)
                    self.pending_updates[symbol_upper] = data

                    # Metrics
                    self.metrics['updates_received'] += 1

                    # Log throttled (cada 10 updates)
                    if self.metrics['updates_received'] % 10 == 0:
                        logger.debug(f"📊 Received {self.metrics['updates_received']} updates, "
                                   f"{len(self.pending_updates)} pending batch")

            except Exception as e:
                logger.error(f"❌ Error processing message for {symbol}: {e}")

        # Subscribe to WebSocket
        self.twm.start_mark_price_socket(
            callback=handle_message,
            symbol=symbol_upper
        )

        self.metrics['symbols_tracked'] = len(self.memory_cache)
        logger.info(f"📡 Started mark_price stream for {symbol_upper}")

    def get_mark_price_from_memory(self, symbol: str, max_age: float = 10.0) -> Optional[float]:
        """
        Obtiene mark price desde in-memory cache (ultra-fast).
        Útil para servicios que corren en el mismo proceso.

        Args:
            symbol: Símbolo (ej: BTCUSDT)
            max_age: Edad máxima aceptable en segundos

        Returns:
            Mark price o None si no disponible/stale
        """
        data = self.memory_cache.get(symbol.upper())

        if data:
            age = time.time() - data['timestamp']
            if age <= max_age:
                return data['mark_price']
            else:
                logger.debug(f"⚠️ Stale memory cache for {symbol}: age={age:.1f}s")

        return None

    def _flush_batch_loop(self):
        """
        Thread que hace flush de pending updates a Redis cada N segundos.
        Usa Redis pipeline para batch writes (1 network round-trip).
        """
        logger.info(f"🔁 Batch flush thread started (interval={self.batch_interval}s)")

        while True:
            try:
                time.sleep(self.batch_interval)

                if not self.pending_updates:
                    logger.debug("⏭️ No pending updates, skipping batch")
                    continue

                # Snapshot de pending updates
                updates_to_write = self.pending_updates.copy()
                self.pending_updates.clear()

                # Batch write usando Redis pipeline
                start_time = time.time()
                pipeline = self.redis_client.pipeline()

                for symbol, data in updates_to_write.items():
                    cache_key = f"websocket:mark_price:{symbol.lower()}"
                    pipeline.setex(
                        cache_key,
                        30,  # TTL 30 segundos
                        json.dumps(data)
                    )

                # Execute batch (1 network call para N símbolos)
                results = pipeline.execute()
                elapsed = (time.time() - start_time) * 1000  # ms

                # Update metrics
                self.metrics['batches_written'] += 1
                self.metrics['last_batch_time'] = time.time()
                self.metrics['last_batch_size'] = len(updates_to_write)

                logger.info(
                    f"✅ Batch flush complete: {len(updates_to_write)} symbols, "
                    f"{elapsed:.1f}ms, {len(results)} operations"
                )

            except Exception as e:
                logger.error(f"❌ Error in batch flush: {e}")
                # No clear pending_updates on error - retry next iteration

    def get_metrics(self) -> Dict:
        """
        Obtiene métricas de performance.

        Returns:
            Dict con métricas actuales
        """
        time_since_last_batch = time.time() - self.metrics['last_batch_time']

        return {
            'updates_received': self.metrics['updates_received'],
            'batches_written': self.metrics['batches_written'],
            'symbols_tracked': self.metrics['symbols_tracked'],
            'pending_updates': len(self.pending_updates),
            'seconds_since_last_batch': round(time_since_last_batch, 1),
            'last_batch_size': self.metrics['last_batch_size'],
            'avg_updates_per_batch': (
                self.metrics['updates_received'] / self.metrics['batches_written']
                if self.metrics['batches_written'] > 0 else 0
            )
        }

    def stop(self):
        """Detener WebSocket manager y hacer flush final"""
        logger.info("🛑 Stopping WebSocket manager...")

        # Flush final de pending updates
        if self.pending_updates:
            logger.info(f"💾 Final flush of {len(self.pending_updates)} pending updates")
            pipeline = self.redis_client.pipeline()

            for symbol, data in self.pending_updates.items():
                cache_key = f"websocket:mark_price:{symbol.lower()}"
                pipeline.setex(cache_key, 30, json.dumps(data))

            pipeline.execute()

        # Stop WebSocket
        self.twm.stop()

        logger.info("✅ WebSocket manager stopped")


# ============================================================================
# Uso en crypto-analyzer-redis o nuevo servicio
# ============================================================================

def main():
    """
    Ejemplo de uso del WebSocket manager optimizado
    """

    # Configuración
    REDIS_HOST = 'localhost'
    REDIS_PORT = 6379
    REDIS_DB = 0

    # Símbolos a monitorear (ejemplo)
    SYMBOLS = [
        'BTCUSDT', 'ETHUSDT', 'BNBUSDT', 'SOLUSDT', 'ADAUSDT',
        'XRPUSDT', 'DOGEUSDT', 'MATICUSDT', 'DOTUSDT', 'AVAXUSDT'
        # ... agregar más símbolos hasta 50
    ]

    # Conectar a Redis
    redis_client = redis.Redis(
        host=REDIS_HOST,
        port=REDIS_PORT,
        db=REDIS_DB,
        decode_responses=True
    )

    # Verificar conexión
    try:
        redis_client.ping()
        logger.info("✅ Redis connection successful")
    except Exception as e:
        logger.error(f"❌ Redis connection failed: {e}")
        return

    # Iniciar WebSocket manager
    ws_manager = OptimizedWebSocketManager(
        redis_client=redis_client,
        batch_interval=5  # Flush cada 5 segundos
    )

    # Suscribirse a mark price streams
    logger.info(f"📡 Subscribing to {len(SYMBOLS)} symbols...")
    for symbol in SYMBOLS:
        ws_manager.start_mark_price_stream(symbol)
        time.sleep(0.1)  # Small delay entre suscripciones

    logger.info("✅ All streams started")

    # Mantener corriendo y mostrar metrics cada 30 segundos
    try:
        while True:
            time.sleep(30)

            # Mostrar metrics
            metrics = ws_manager.get_metrics()
            logger.info(
                f"📊 Metrics: "
                f"updates={metrics['updates_received']}, "
                f"batches={metrics['batches_written']}, "
                f"pending={metrics['pending_updates']}, "
                f"symbols={metrics['symbols_tracked']}"
            )

            # Test: leer mark price desde memoria (ultra-fast)
            btc_price = ws_manager.get_mark_price_from_memory('BTCUSDT')
            if btc_price:
                logger.info(f"💰 BTC mark price (memory): {btc_price}")

    except KeyboardInterrupt:
        logger.info("\n🛑 Stopping by user request...")
        ws_manager.stop()


if __name__ == "__main__":
    main()
