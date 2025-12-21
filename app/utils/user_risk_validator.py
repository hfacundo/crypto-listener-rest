# crypto-listener/app/utils/user_risk_validator.py
"""
User Risk Profile Validator
============================
Validador unificado que aplica las reglas de protección de un usuario
antes de permitir abrir un trade.

Integra:
- Circuit Breaker (pausa global después de drawdown)
- Schedule (horarios de operación)
- Recent Trade Cooldown (cooldown por símbolo después de pérdidas)
- Trade Limits (max trades simultáneos)
- Signal Quality (probability y RR mínimos)
"""

import json
import logging
import sys
import os
import psycopg2.extras
from typing import Tuple, Dict, Optional, List
from datetime import datetime, timezone, timedelta
from decimal import Decimal

# Imports de módulos existentes
from app.utils.binance.binance_client import get_binance_client_for_user
from app.utils.binance.utils import is_trade_allowed_by_schedule_utc

# Import del sistema de protección de trading
# TradeProtectionSystem proporciona:
# - Circuit breaker (pausa después de drawdown)
# - Anti-repetition (cooldown después de stops)
# - Symbol blacklist (bloquear símbolos con mal performance)
# - Historial de trades en PostgreSQL
try:
    from app.utils.trade_protection import TradeProtectionSystem
except ImportError as e:
    print(f"⚠️ Warning: Could not import TradeProtectionSystem: {e}")
    print(f"⚠️ Anti-repetition, circuit breaker, and symbol blacklist features will be disabled")
    TradeProtectionSystem = None

logger = logging.getLogger(__name__)


class UserRiskProfileValidator:
    """
    Validador centralizado de riesgo por usuario.

    Cada usuario tiene su propio perfil de riesgo definido en local_rules.py,
    y este validador asegura que TODAS las reglas se cumplan antes de abrir un trade.
    """

    def __init__(self, user_id: str, strategy: str, rules: Dict):
        """
        Args:
            user_id: ID del usuario (ej: "hufsa", "copy_trading")
            strategy: Estrategia (ej: "archer_dual")
            rules: Reglas del usuario desde local_rules.py
        """
        self.user_id = user_id
        self.strategy = strategy
        self.rules = rules

        # Redis client para caching
        try:
            from app.utils.db.redis_client import get_redis_client
            self.redis_client = get_redis_client()
        except Exception as e:
            logger.warning(f"Could not get Redis client: {e}")
            self.redis_client = None

        # TradeProtectionSystem (PostgreSQL) para memoria histórica
        if TradeProtectionSystem:
            try:
                self.protection_system = TradeProtectionSystem()
            except Exception as e:
                logger.error(f"Could not initialize TradeProtectionSystem: {e}")
                self.protection_system = None
        else:
            self.protection_system = None

        # Cache keys
        self.cache_prefix = f"user_risk:{user_id}:{strategy}"

    # ═══════════════════════════════════════════════════════════════════
    # MÉTODO PRINCIPAL: validate_trade
    # ═══════════════════════════════════════════════════════════════════

    def validate_trade(
        self,
        symbol: str,
        direction: str,
        entry_price: float,
        stop_price: float,
        target_price: float,
        probability: float,
        rr: float
    ) -> Tuple[bool, str, Dict]:
        """
        Validación COMPLETA de un trade contra el perfil del usuario.

        Ejecuta todas las validaciones en orden de prioridad:
        1. Circuit Breaker (pausa global)
        2. Schedule (horarios)
        3. Recent Trade Cooldown (cooldown por símbolo)
        4. Trade Limits (max simultáneos)
        5. Signal Quality (probability y RR mínimos)

        Returns:
            Tuple[bool, str, Dict]:
                - can_trade: True si todas las validaciones pasaron
                - rejection_reason: Razón del rechazo (vacío si aprobado)
                - validation_info: Info adicional (capital_multiplier, etc.)
        """

        validation_results = {
            "user_id": self.user_id,
            "strategy": self.strategy,
            "symbol": symbol,
            "timestamp": datetime.now(timezone.utc).isoformat()
        }

        # ═══════════════════════════════════════════════════════════════
        # VALIDACIÓN 1: Circuit Breaker (máxima prioridad)
        # ═══════════════════════════════════════════════════════════════
        if self.protection_system and self.rules.get("circuit_breaker", {}).get("enabled", False):
            config = self.rules["circuit_breaker"]

            try:
                breaker_active, breaker_reason = self.protection_system.should_activate_circuit_breaker(
                    user_id=self.user_id,
                    strategy=self.strategy,
                    max_drawdown_threshold=config.get("max_drawdown_pct", -30.0),
                    max_consecutive_losses=config.get("max_consecutive_losses", 5)
                )

                if breaker_active:
                    validation_results["failed_at"] = "circuit_breaker"
                    validation_results["config_used"] = config
                    return False, f"CIRCUIT_BREAKER: {breaker_reason}", validation_results
            except Exception as e:
                logger.error(f"Error checking circuit breaker: {e}")

        # ═══════════════════════════════════════════════════════════════
        # VALIDACIÓN 3: Schedule (horarios de operación)
        # ═══════════════════════════════════════════════════════════════
        if self.rules.get("schedule", {}).get("enabled", False):
            can_trade, schedule_reason = self._check_schedule()

            if not can_trade:
                validation_results["failed_at"] = "schedule"
                return False, f"SCHEDULE: {schedule_reason}", validation_results

        # ═══════════════════════════════════════════════════════════════
        # VALIDACIÓN 4A: Recent Trade Validator (optimizado, sin Binance)
        # ═══════════════════════════════════════════════════════════════
        # NUEVO: Validación rápida que NO llama a Binance
        # Confía en que crypto-guardian actualiza BD en tiempo real vía WebSocket
        try:
            from app.utils.recent_trade_validator import get_recent_trade_validator

            recent_validator = get_recent_trade_validator()
            # FIXED: Usar cooldown_hours directo (no nested en anti_repetition)
            cooldown_hours = self.rules.get("cooldown_hours", 4)

            can_trade, rejection_reason = recent_validator.should_allow_trade(
                user_id=self.user_id,
                strategy=self.strategy,
                symbol=symbol,
                cooldown_hours=cooldown_hours
            )

            if not can_trade:
                validation_results["failed_at"] = "recent_trade_cooldown"
                validation_results["reason"] = rejection_reason
                logger.info(f"🚫 {self.user_id} - Recent trade cooldown: {symbol} → {rejection_reason}")
                return False, f"RECENT_TRADE_COOLDOWN: {rejection_reason}", validation_results

        except Exception as e:
            logger.error(f"Error checking recent trade validator: {e}")
            # Si falla recent_trade_validator, continuar con otras validaciones

        # ═══════════════════════════════════════════════════════════════
        # VALIDACIÓN 7: Trade Limits (max trades simultáneos)
        # ═══════════════════════════════════════════════════════════════
        from app.trade_limits import check_trade_limit

        can_trade, limit_reason, limit_info = check_trade_limit(
            self.user_id, self.rules, symbol
        )

        if not can_trade:
            validation_results["failed_at"] = "trade_limits"
            validation_results["limit_info"] = limit_info
            return False, f"TRADE_LIMIT: {limit_reason}", validation_results

        # ═══════════════════════════════════════════════════════════════
        # VALIDACIÓN 5: Signal Quality (validación simplificada)
        # ═══════════════════════════════════════════════════════════════
        # Valida probability y RR mínimos (sin SQS complejo)

        min_prob = self.rules.get("min_probability", 58)
        min_rr_threshold = self.rules.get("min_rr", 1.1)

        logger.info(f"🤖 {self.user_id} - Validation: prob={probability}% (min={min_prob}%), rr={rr:.2f} (min={min_rr_threshold})")

        # Check probability threshold
        if probability < min_prob:
            validation_results["failed_at"] = "probability_threshold"
            reason = f"Probability {probability}% < {min_prob}% (user threshold)"
            logger.warning(f"🚫 {self.user_id} - REJECTED: {reason}")
            return False, f"PROBABILITY_REJECTED: {reason}", validation_results

        # Check RR threshold
        if rr < min_rr_threshold:
            validation_results["failed_at"] = "rr_threshold"
            reason = f"RR {rr:.2f} < {min_rr_threshold} (user threshold)"
            logger.warning(f"🚫 {self.user_id} - REJECTED: {reason}")
            return False, f"RR_REJECTED: {reason}", validation_results

        # ═══════════════════════════════════════════════════════════════
        # ✅ TODAS LAS VALIDACIONES PASARON
        # ═══════════════════════════════════════════════════════════════
        validation_results["status"] = "APPROVED"
        validation_results["capital_multiplier"] = 1.0
        validation_results["quality_grade"] = "APPROVED"

        logger.info(f"✅ {self.user_id} - Trade APPROVED for {symbol}: prob={probability}% >= {min_prob}%, rr={rr:.2f} >= {min_rr_threshold}")

        return True, "ALL_VALIDATIONS_PASSED", validation_results

    # ═══════════════════════════════════════════════════════════════════
    # MÉTODOS HELPER - Schedule
    # ═══════════════════════════════════════════════════════════════════

    def _check_schedule(self) -> Tuple[bool, str]:
        """Verifica si el usuario está en horario permitido."""
        try:
            now_utc = datetime.now(timezone.utc)

            if not is_trade_allowed_by_schedule_utc(self.rules, now_utc):
                return False, f"Outside trading hours ({now_utc.strftime('%A %H:%M:%S')} UTC)"

            return True, ""

        except Exception as e:
            logger.error(f"❌ Error checking schedule for {self.user_id}: {e}")
            return True, ""


    # ═══════════════════════════════════════════════════════════════════
    # MÉTODO PÚBLICO - Registrar Trade Abierto
    # ═══════════════════════════════════════════════════════════════════

    def record_trade_opened(
        self,
        symbol: str,
        direction: str,
        entry_time: datetime,
        entry_price: float,
        stop_price: float,
        target_price: float,
        probability: float,
        rr: float,
        order_id: int = None,
        sl_order_id: int = None,
        tp_order_id: int = None
    ) -> int:
        """
        Registra un trade abierto en TradeProtectionSystem (PostgreSQL).

        Args:
            order_id: Order ID de Binance (entry)
            sl_order_id: Order ID de Binance (stop loss)
            tp_order_id: Order ID de Binance (take profit)

        Returns:
            int: trade_id de PostgreSQL
        """
        if not self.protection_system:
            logger.warning(f"TradeProtectionSystem not available, cannot record trade")
            return -1

        try:
            trade_id = self.protection_system.record_trade(
                user_id=self.user_id,
                strategy=self.strategy,
                symbol=symbol,
                direction=direction,
                entry_time=entry_time,
                entry_price=entry_price,
                stop_price=stop_price,
                target_price=target_price,
                probability=probability,
                sqs=0.0,  # Legacy parameter, no longer used
                rr=rr,
                order_id=order_id,
                sl_order_id=sl_order_id,
                tp_order_id=tp_order_id
            )

            logger.info(f"✅ {self.user_id} - Trade recorded in PostgreSQL: {symbol} (trade_id={trade_id})")

            return trade_id

        except Exception as e:
            logger.error(f"❌ Error recording trade for {self.user_id}: {e}")
            return -1
