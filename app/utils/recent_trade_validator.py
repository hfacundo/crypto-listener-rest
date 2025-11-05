#!/usr/bin/env python3
# app/utils/recent_trade_validator.py
"""
Validador de Trades Recientes - Sistema Optimizado sin Llamadas a Binance.

Valida si se puede abrir un nuevo trade basándose en historial de BD.
NO llama a Binance - confía en que crypto-guardian actualiza BD en tiempo real.

REGLAS:
1. Si el último trade perdió (stop_hit) hace < 6 horas → RECHAZAR
2. Si el último trade ganó (target_hit) → PERMITIR inmediatamente
3. Si el último trade perdió pero hace > 6 horas → PERMITIR
4. Si no hay historial → PERMITIR
5. Si trade está 'active' en BD → Verificar Redis (no Binance)

VENTAJAS:
- ✅ 0 llamadas a Binance (lee solo BD + Redis)
- ✅ Latencia < 10ms (query simple)
- ✅ Sin riesgo de rate limits
- ✅ BD actualizada por crypto-guardian vía WebSocket
"""

from datetime import datetime, timezone
from typing import Tuple, Dict, Optional
import psycopg2
import psycopg2.extras

from app.utils.db.redis_client import get_redis_client
from app.utils.logger_config import get_logger

logger = get_logger(__name__)


class RecentTradeValidator:
    """
    Validador de trades recientes sin llamadas a Binance.

    Arquitectura:
    - crypto-guardian actualiza BD en tiempo real (< 1s) vía WebSocket
    - crypto-listener-rest solo LEE de BD
    - Redis como fuente de verdad para trades activos
    """

    def __init__(self, db_config: Optional[Dict] = None):
        """
        Args:
            db_config: Config de PostgreSQL. Si None, usa DATABASE_URL_CRYPTO_TRADER.
        """
        if db_config:
            self.db_config = db_config
        else:
            # Parse DATABASE_URL_CRYPTO_TRADER
            import os
            from urllib.parse import urlparse

            db_url = os.getenv('DATABASE_URL_CRYPTO_TRADER')
            if not db_url:
                raise RuntimeError("DATABASE_URL_CRYPTO_TRADER not set")

            parsed = urlparse(db_url)
            self.db_config = {
                'host': parsed.hostname or 'localhost',
                'port': parsed.port or 5432,
                'database': parsed.path.lstrip('/') if parsed.path else 'crypto_analyzer',
                'user': parsed.username or 'postgres',
                'password': parsed.password or 'postgres'
            }

        # Redis client
        self.redis_client = get_redis_client()

    def _get_conn(self):
        """Get database connection."""
        return psycopg2.connect(**self.db_config, client_encoding='UTF8')

    def should_allow_trade(
        self,
        user_id: str,
        strategy: str,
        symbol: str,
        cooldown_hours: int = 6
    ) -> Tuple[bool, str]:
        """
        Valida si se puede abrir un nuevo trade sin llamar a Binance.

        FLUJO OPTIMIZADO:
        1. Verificar Redis: ¿Existe trade activo?
        2. Consultar BD: Último trade del símbolo
        3. Si tiene exit_time → Aplicar cooldown
        4. Si exit_reason='active' → Verificar Redis

        Args:
            user_id: ID del usuario
            strategy: Estrategia (ej: "archer_dual")
            symbol: Símbolo del trade (ej: "BTCUSDT")
            cooldown_hours: Horas de cooldown después de stop_hit (default: 6)

        Returns:
            Tuple[bool, str]: (can_trade, rejection_reason)
        """
        symbol_lower = symbol.lower()

        # ===================================================================
        # PASO 1: Verificar si existe trade ACTIVO en Redis
        # ===================================================================
        if self._trade_exists_in_redis(user_id, symbol_lower):
            return False, f"Trade already active for {symbol} (found in Redis)"

        # ===================================================================
        # PASO 2: Consultar último trade de BD
        # ===================================================================
        last_trade = self._get_last_trade_from_db(user_id, strategy, symbol_lower)

        if not last_trade:
            # Sin historial → Permitir
            return True, "No previous trades"

        # ===================================================================
        # PASO 3: BD con exit_time → Usar datos de BD (más eficiente)
        # ===================================================================
        if last_trade['exit_time'] is not None:
            exit_reason = last_trade['exit_reason']
            exit_time = last_trade['exit_time']

            # Asegurar que exit_time es timezone-aware
            if exit_time.tzinfo is None:
                exit_time = exit_time.replace(tzinfo=timezone.utc)

            # Solo aplicar cooldown si PERDIÓ
            if exit_reason == 'stop_hit':
                hours_since_close = (datetime.now(timezone.utc) - exit_time).total_seconds() / 3600

                if hours_since_close < cooldown_hours:
                    return False, (
                        f"Stop hit {hours_since_close:.1f}h ago for {symbol} "
                        f"(cooldown: {cooldown_hours}h, remaining: {cooldown_hours - hours_since_close:.1f}h)"
                    )

            # Ganó o cooldown expiró → Permitir
            return True, f"OK (last trade: {exit_reason}, closed {self._format_time_ago(exit_time)})"

        # ===================================================================
        # PASO 4: exit_time NULL pero exit_reason != 'active'
        # ===================================================================
        # Caso raro: datos corruptos o proceso de actualización a medias
        if last_trade['exit_reason'] != 'active':
            logger.warning(
                f"⚠️ Trade with exit_reason='{last_trade['exit_reason']}' but no exit_time: "
                f"{user_id}/{symbol}"
            )
            # Permitir por seguridad (asumir que ya cerró)
            return True, f"OK (trade marked as {last_trade['exit_reason']} but no exit_time)"

        # ===================================================================
        # PASO 5: exit_reason='active' pero NO en Redis
        # ===================================================================
        # Esto significa que el trade se cerró pero BD aún no se actualizó
        # (WebSocket lo procesará en < 1 segundo)
        #
        # Race condition: crypto-guardian eliminó de Redis pero aún no actualizó PostgreSQL
        # Solución: Buscar si hay un trade CERRADO recientemente

        recent_closed_trade = self._get_recent_closed_trade(user_id, strategy, symbol_lower, minutes=30)

        if recent_closed_trade:
            # Hay un trade cerrado recientemente, verificar si ganó o perdió
            exit_reason = recent_closed_trade['exit_reason']
            exit_time = recent_closed_trade['exit_time']

            # Asegurar que exit_time es timezone-aware
            if exit_time.tzinfo is None:
                exit_time = exit_time.replace(tzinfo=timezone.utc)

            if exit_reason == 'stop_hit':
                # Trade perdedor → Aplicar cooldown para evitar revenge trading
                hours_since_close = (datetime.now(timezone.utc) - exit_time).total_seconds() / 3600

                if hours_since_close < cooldown_hours:
                    return False, (
                        f"Stop hit {hours_since_close:.1f}h ago for {symbol} "
                        f"(cooldown: {cooldown_hours}h, remaining: {cooldown_hours - hours_since_close:.1f}h)"
                    )

            # Trade ganador (target_hit, manual_close, guardian_close) o cooldown expiró → Permitir
            logger.info(
                f"✅ Recent closed trade found for {user_id}/{symbol}: {exit_reason} "
                f"({self._format_time_ago(exit_time)}) - Allowing new trade"
            )
            return True, f"OK (last trade: {exit_reason}, closed {self._format_time_ago(exit_time)})"

        # No hay trade cerrado reciente, verificar cuánto tiempo ha pasado desde entry
        entry_time = last_trade['entry_time']
        if entry_time.tzinfo is None:
            entry_time = entry_time.replace(tzinfo=timezone.utc)

        hours_since_entry = (datetime.now(timezone.utc) - entry_time).total_seconds() / 3600

        if hours_since_entry < 0.5:  # < 30 minutos
            # Muy reciente, esperar a que WebSocket/cleanup procese
            return False, (
                f"Trade recently opened for {symbol} ({hours_since_entry * 60:.1f} min ago), "
                f"waiting for sync"
            )
        else:
            # Ya pasó tiempo suficiente, permitir
            # (crypto-guardian-cleanup debería haberlo procesado)
            logger.info(
                f"⚠️ Trade marked 'active' in DB but not in Redis and entry > 30min ago: "
                f"{user_id}/{symbol} (allowing trade)"
            )
            return True, "OK (old active trade, likely closed)"

    def _trade_exists_in_redis(self, user_id: str, symbol: str) -> bool:
        """
        Verifica si existe un trade activo en Redis.

        Args:
            user_id: ID del usuario
            symbol: Símbolo del trade (lowercase)

        Returns:
            True si existe trade activo
        """
        try:
            # Probar diferentes formatos de key
            possible_keys = [
                f"guardian:trades:{user_id}:{symbol}",
                f"guardian:trades:{symbol}",
                f"trade:{user_id}:{symbol}"
            ]

            for key in possible_keys:
                if self.redis_client.exists(key):
                    return True

            return False

        except Exception as e:
            logger.error(f"❌ Error checking Redis for {user_id}/{symbol}: {e}")
            # En caso de error, retornar False (fail-safe: permitir trade)
            return False

    def _get_last_trade_from_db(
        self,
        user_id: str,
        strategy: str,
        symbol: str
    ) -> Optional[Dict]:
        """
        Obtiene el último trade del símbolo desde BD.

        Args:
            user_id: ID del usuario
            strategy: Estrategia
            symbol: Símbolo del trade (lowercase)

        Returns:
            Dict con campos del trade o None si no existe
        """
        query = """
        SELECT
            id,
            entry_time,
            entry_price,
            exit_time,
            exit_price,
            exit_reason,
            stop_price,
            target_price
        FROM trade_history
        WHERE user_id = %s
          AND strategy = %s
          AND symbol = %s
        ORDER BY entry_time DESC
        LIMIT 1
        """

        conn = None
        try:
            conn = self._get_conn()
            with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
                cur.execute(query, (user_id, strategy, symbol))
                result = cur.fetchone()

                if result:
                    return dict(result)
                else:
                    return None

        except Exception as e:
            logger.error(f"❌ Error querying last trade from DB: {e}")
            return None

        finally:
            if conn:
                conn.close()

    def _get_recent_closed_trade(
        self,
        user_id: str,
        strategy: str,
        symbol: str,
        minutes: int = 30
    ) -> Optional[Dict]:
        """
        Busca trades cerrados recientemente (últimos N minutos).

        Esto es útil para detectar race conditions donde:
        - crypto-guardian ya eliminó el trade de Redis
        - Pero la consulta de "último trade" aún muestra exit_reason='active'
        - Necesitamos buscar si hay un trade MÁS RECIENTE con exit_time

        Args:
            user_id: ID del usuario
            strategy: Estrategia
            symbol: Símbolo del trade (lowercase)
            minutes: Ventana de tiempo para buscar (default: 30 min)

        Returns:
            Dict con campos del trade cerrado o None si no existe
        """
        query = """
        SELECT
            id,
            entry_time,
            entry_price,
            exit_time,
            exit_price,
            exit_reason,
            stop_price,
            target_price
        FROM trade_history
        WHERE user_id = %s
          AND strategy = %s
          AND symbol = %s
          AND exit_time IS NOT NULL
          AND exit_time >= NOW() - INTERVAL '%s minutes'
        ORDER BY exit_time DESC
        LIMIT 1
        """

        conn = None
        try:
            conn = self._get_conn()
            with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
                cur.execute(query, (user_id, strategy, symbol, minutes))
                result = cur.fetchone()

                if result:
                    return dict(result)
                else:
                    return None

        except Exception as e:
            logger.error(f"❌ Error querying recent closed trade from DB: {e}")
            return None

        finally:
            if conn:
                conn.close()

    def _format_time_ago(self, dt: datetime) -> str:
        """Formatea tiempo transcurrido desde un datetime."""
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)

        delta = datetime.now(timezone.utc) - dt
        hours = delta.total_seconds() / 3600

        if hours < 1:
            return f"{hours * 60:.0f} min ago"
        elif hours < 24:
            return f"{hours:.1f}h ago"
        else:
            return f"{hours / 24:.1f}d ago"


# Instancia global (lazy initialization)
_validator_instance = None


def get_recent_trade_validator() -> RecentTradeValidator:
    """Obtiene instancia global del validador (singleton)."""
    global _validator_instance
    if _validator_instance is None:
        _validator_instance = RecentTradeValidator()
    return _validator_instance
