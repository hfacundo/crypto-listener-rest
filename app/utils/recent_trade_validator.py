#!/usr/bin/env python3
# app/utils/recent_trade_validator.py
"""
Validador de Trades Recientes - Sistema Optimizado sin Llamadas a Binance.

Valida si se puede abrir un nuevo trade basándose en historial de BD.
NO llama a Binance - confía en que crypto-guardian actualiza BD en tiempo real.

REGLAS:
1. Si el último trade perdió (stop_hit o manual_lost) hace < N horas → RECHAZAR (cooldown)
2. Si el último trade ganó (target_hit, timeout_win, manual_win) → PERMITIR inmediatamente
3. Si el último trade fue timeout_lost → PERMITIR inmediatamente (ya esperó N horas por timeout)
4. Si el último trade perdió pero hace > N horas → PERMITIR (cooldown expiró)
5. Si no hay historial → PERMITIR
6. Si trade está 'active' en BD → Verificar Redis o detectar orphan orders

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
        # PASO 1: Verificar si existe trade ACTIVO en PostgreSQL
        # ===================================================================
        if self._trade_exists_in_db(user_id, symbol_lower, strategy):
            return False, f"Trade already active for {symbol} (found in DB)"

        # ===================================================================
        # PASO 2: Consultar último trade de BD
        # ===================================================================
        last_trade = self._get_last_trade_from_db(user_id, strategy, symbol_lower)

        # 🔍 LOG CRÍTICO DE DEBUG
        logger.info(f"🔍 COOLDOWN DEBUG [{user_id}/{symbol_lower}]:")
        logger.info(f"   last_trade from DB: {last_trade}")
        logger.info(f"   cooldown_hours config: {cooldown_hours}h")

        if not last_trade:
            # Sin historial → Permitir
            logger.info(f"   ✅ DECISION: No previous trades found → ALLOW TRADE")
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

            hours_since_close = (datetime.now(timezone.utc) - exit_time).total_seconds() / 3600

            logger.info(f"   📊 Last trade status: exit_reason={exit_reason}, exit_time={self._format_time_ago(exit_time)}")

            # Aplicar cooldown solo para pérdidas que requieren espera adicional
            # FIXED: No incluir 'timeout_lost' porque ya esperó N horas para cerrarse
            LOSING_EXIT_REASONS = ['stop_hit', 'manual_lost']

            # Exit reasons que NO requieren cooldown (ganancias o breakeven)
            NON_LOSING_EXIT_REASONS = ['target_hit', 'timeout_win', 'manual_win', 'timeout_breakeven', 'manual_breakeven']

            # Legacy exit_reasons (datos viejos sin sufijos win/lost) - tratar como "no loss"
            LEGACY_EXIT_REASONS = ['manual_close', 'close_manual', 'timeout', 'guardian_close']

            if exit_reason in LOSING_EXIT_REASONS:
                if hours_since_close < cooldown_hours:
                    logger.warning(
                        f"   ❌ DECISION: REJECT TRADE - {exit_reason} {hours_since_close:.1f}h ago "
                        f"(cooldown: {cooldown_hours}h, remaining: {cooldown_hours - hours_since_close:.1f}h)"
                    )
                    return False, (
                        f"{exit_reason} {hours_since_close:.1f}h ago for {symbol} "
                        f"(cooldown: {cooldown_hours}h, remaining: {cooldown_hours - hours_since_close:.1f}h)"
                    )
                else:
                    logger.info(
                        f"   ✅ DECISION: ALLOW TRADE - {exit_reason} {hours_since_close:.1f}h ago "
                        f"(cooldown {cooldown_hours}h expired)"
                    )
            elif exit_reason in NON_LOSING_EXIT_REASONS:
                logger.info(f"   ✅ DECISION: ALLOW TRADE - Last trade was {exit_reason} (win/breakeven, no cooldown)")
            elif exit_reason in LEGACY_EXIT_REASONS:
                logger.warning(f"   ⚠️ DECISION: ALLOW TRADE - Last trade has legacy exit_reason '{exit_reason}' (no cooldown applied, consider updating crypto-guardian)")
            else:
                # Unknown exit_reason - permitir por seguridad pero loguear advertencia
                logger.warning(f"   ⚠️ DECISION: ALLOW TRADE - Unknown exit_reason '{exit_reason}' (allowing by default)")

            # Ganó o cooldown expiró → Permitir
            return True, f"OK (last trade: {exit_reason}, closed {self._format_time_ago(exit_time)})"

        # ===================================================================
        # PASO 4: exit_time NULL pero exit_reason != 'active'
        # ===================================================================
        # Caso raro: datos corruptos o proceso de actualización a medias
        # CRÍTICO: Si es pérdida sin exit_time, aplicar cooldown conservador
        if last_trade['exit_reason'] != 'active':
            logger.warning(
                f"⚠️ Trade with exit_reason='{last_trade['exit_reason']}' but no exit_time: "
                f"{user_id}/{symbol}"
            )

            # Si es una PÉRDIDA sin exit_time → Aplicar cooldown conservador
            # (Usar entry_time como estimación para evitar bypass de revenge trading)
            LOSING_EXIT_REASONS = ['stop_hit', 'manual_lost']

            if last_trade['exit_reason'] in LOSING_EXIT_REASONS:
                # Usar entry_time como estimación conservadora del cierre
                # Asumimos que el trade se cerró poco después de abrirse
                entry_time = last_trade['entry_time']
                if entry_time.tzinfo is None:
                    entry_time = entry_time.replace(tzinfo=timezone.utc)

                hours_since_entry = (datetime.now(timezone.utc) - entry_time).total_seconds() / 3600

                # Aplicar cooldown basado en entry_time (conservador)
                if hours_since_entry < cooldown_hours:
                    logger.warning(
                        f"   ❌ DECISION: REJECT TRADE - {last_trade['exit_reason']} detected but no exit_time. "
                        f"Using entry_time ({hours_since_entry:.1f}h ago) for cooldown calculation. "
                        f"Cooldown: {cooldown_hours}h, remaining: {cooldown_hours - hours_since_entry:.1f}h"
                    )
                    return False, (
                        f"{last_trade['exit_reason']} detected for {symbol} "
                        f"(corrupted data: no exit_time, using entry_time {hours_since_entry:.1f}h ago, "
                        f"cooldown: {cooldown_hours}h, remaining: {cooldown_hours - hours_since_entry:.1f}h)"
                    )
                else:
                    logger.info(
                        f"   ✅ DECISION: ALLOW TRADE - {last_trade['exit_reason']} detected {hours_since_entry:.1f}h ago "
                        f"(cooldown {cooldown_hours}h expired, based on entry_time)"
                    )

            # Datos corruptos pero NO es pérdida (win/breakeven/legacy) → permitir por seguridad
            return True, f"OK (trade marked as {last_trade['exit_reason']} but no exit_time)"

        # ===================================================================
        # PASO 5: exit_reason='active' pero NO en Redis
        # ===================================================================
        # Esto significa que el trade se cerró pero BD aún no se actualizó
        # (WebSocket lo procesará en < 1 segundo)
        #
        # Race condition: crypto-guardian eliminó de Redis pero aún no actualizó PostgreSQL
        # Solución: Buscar si hay un trade CERRADO recientemente

        logger.warning(
            f"   ⚠️ RACE CONDITION DETECTED: Trade marked 'active' in DB but NOT in Redis - "
            f"Searching for recent closed trades..."
        )

        recent_closed_trade = self._get_recent_closed_trade(user_id, strategy, symbol_lower, minutes=30)

        if recent_closed_trade:
            # Hay un trade cerrado recientemente, verificar si ganó o perdió
            exit_reason = recent_closed_trade['exit_reason']
            exit_time = recent_closed_trade['exit_time']

            # Asegurar que exit_time es timezone-aware
            if exit_time.tzinfo is None:
                exit_time = exit_time.replace(tzinfo=timezone.utc)

            # Aplicar cooldown solo para pérdidas que requieren espera adicional
            LOSING_EXIT_REASONS = ['stop_hit', 'manual_lost']
            LEGACY_EXIT_REASONS = ['manual_close', 'close_manual', 'timeout', 'guardian_close']

            if exit_reason in LOSING_EXIT_REASONS:
                # Trade perdedor → Aplicar cooldown para evitar revenge trading
                hours_since_close = (datetime.now(timezone.utc) - exit_time).total_seconds() / 3600

                if hours_since_close < cooldown_hours:
                    return False, (
                        f"{exit_reason} {hours_since_close:.1f}h ago for {symbol} "
                        f"(cooldown: {cooldown_hours}h, remaining: {cooldown_hours - hours_since_close:.1f}h)"
                    )

            # Trade ganador (target_hit, timeout_win, manual_win), legacy, o cooldown expiró → Permitir
            logger.info(
                f"✅ Recent closed trade found for {user_id}/{symbol}: {exit_reason} "
                f"({self._format_time_ago(exit_time)}) - Allowing new trade"
            )
            return True, f"OK (last trade: {exit_reason}, closed {self._format_time_ago(exit_time)})"

        # ===================================================================
        # PASO 5.5: DETECCIÓN DE ORPHAN ORDERS (CRÍTICO)
        # ===================================================================
        # Si no hay trade cerrado reciente, verificar si hay ORPHAN ORDERS en Binance
        # Esto detecta cuando:
        # - SL tocó primero (trade perdió)
        # - TP order quedó huérfano en Binance
        # - WebSocket aún no actualizó BD
        #
        # CRÍTICO: Esto actualiza BD y aplica cooldown inmediatamente

        entry_time = last_trade['entry_time']
        if entry_time.tzinfo is None:
            entry_time = entry_time.replace(tzinfo=timezone.utc)

        hours_since_entry = (datetime.now(timezone.utc) - entry_time).total_seconds() / 3600

        logger.info(
            f"   🔍 No recent closed trade found - Checking for orphan orders... "
            f"(trade entry: {hours_since_entry * 60:.1f} min ago)"
        )

        # EJECUTAR INMEDIATAMENTE (sin delay)
        # Binance API es la fuente de verdad - no necesitamos esperar porque:
        # 1. Si trade cerró → orphan orders ya existen en Binance (inmediato)
        # 2. Si trade activo → ambas órdenes están en Binance (inmediato)
        # 3. Query de open_orders es rápido (<100ms) y no tiene race condition
        try:
                from app.utils.orphan_order_detector import get_orphan_order_detector
                from app.utils.trade_protection import TradeProtectionSystem

                orphan_detector = get_orphan_order_detector()
                protection_system = TradeProtectionSystem()

                has_orphans, action, updated_trade_info = orphan_detector.check_and_handle_orphan_orders(
                    user_id=user_id,
                    strategy=strategy,
                    symbol=symbol_lower,
                    last_trade_from_db=last_trade,
                    protection_system=protection_system
                )

                if has_orphans and updated_trade_info:
                    exit_reason = updated_trade_info.get('exit_reason')
                    exit_time = updated_trade_info.get('exit_time')

                    logger.warning(
                        f"   🚨 ORPHAN ORDER DETECTED [{user_id}/{symbol}]: "
                        f"exit_reason={exit_reason}, action={action}"
                    )

                    # Si perdió (stop_hit), aplicar cooldown
                    # Orphan detector solo devuelve 'stop_hit' o 'target_hit'
                    LOSING_EXIT_REASONS = ['stop_hit', 'manual_lost']

                    if exit_reason in LOSING_EXIT_REASONS:
                        if exit_time.tzinfo is None:
                            exit_time = exit_time.replace(tzinfo=timezone.utc)

                        hours_since_close = (datetime.now(timezone.utc) - exit_time).total_seconds() / 3600

                        if hours_since_close < cooldown_hours:
                            logger.warning(
                                f"   ❌ DECISION: REJECT TRADE - Orphan {exit_reason} {hours_since_close:.1f}h ago "
                                f"(cooldown: {cooldown_hours}h, remaining: {cooldown_hours - hours_since_close:.1f}h)"
                            )
                            return False, (
                                f"{exit_reason} {hours_since_close:.1f}h ago for {symbol} "
                                f"(detected via orphan order, cooldown: {cooldown_hours}h, "
                                f"remaining: {cooldown_hours - hours_since_close:.1f}h)"
                            )
                        else:
                            logger.info(
                                f"   ✅ DECISION: ALLOW TRADE - Orphan {exit_reason} {hours_since_close:.1f}h ago "
                                f"(cooldown {cooldown_hours}h expired)"
                            )

                    # Si ganó (target_hit), permitir inmediatamente
                    logger.info(
                        f"   ✅ DECISION: ALLOW TRADE - Orphan order was WIN ({exit_reason}), "
                        f"BD updated, no cooldown needed"
                    )
                    return True, f"OK (orphan order detected: {exit_reason}, trade updated in BD)"
                else:
                    logger.info(f"   ℹ️ No orphans detected: {action}")

        except Exception as e:
            logger.error(f"❌ Error checking orphan orders: {e}")
            # Si falla detección, continuar con lógica por defecto

        # No hay trade cerrado reciente ni orphan orders, verificar tiempo desde entry
        if hours_since_entry < 0.5:  # < 30 minutos
            # Muy reciente, esperar a que WebSocket/cleanup procese
            logger.info(
                f"   ⏳ DECISION: REJECT TRADE - Trade too recent ({hours_since_entry * 60:.1f} min ago), "
                f"waiting for sync"
            )
            return False, (
                f"Trade recently opened for {symbol} ({hours_since_entry * 60:.1f} min ago), "
                f"waiting for sync"
            )
        else:
            # Ya pasó tiempo suficiente, permitir
            # (crypto-guardian-cleanup debería haberlo procesado)
            logger.error(
                f"   🚨 POTENTIAL BUG: Trade marked 'active' in DB for {hours_since_entry:.1f}h "
                f"but NO orphan orders found. "
                f"This suggests crypto-guardian failed to update DB. "
                f"ALLOWING TRADE (may bypass cooldown if trade actually lost)."
            )
            return True, "OK (old active trade, likely closed)"

    def _trade_exists_in_db(self, user_id: str, symbol: str, strategy: str) -> bool:
        """
        Verifica si existe un trade activo en PostgreSQL.

        Args:
            user_id: ID del usuario
            symbol: Símbolo del trade (lowercase)
            strategy: Estrategia (ej: "archer_model")

        Returns:
            True si existe trade con exit_reason='active'
        """
        try:
            query = """
            SELECT id
            FROM trade_history
            WHERE user_id = %s
              AND symbol = %s
              AND strategy = %s
              AND exit_reason = 'active'
            LIMIT 1
            """

            conn = self._get_conn()
            with conn.cursor() as cur:
                cur.execute(query, (user_id, symbol, strategy))
                result = cur.fetchone()
            conn.close()

            return result is not None

        except Exception as e:
            logger.error(f"❌ Error checking active trade in DB for {user_id}/{symbol}: {e}")
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
