# crypto-listener/app/utils/user_risk_validator.py
"""
User Risk Profile Validator
============================
Validador unificado que aplica TODAS las reglas de protección de un usuario
antes de permitir abrir un trade.

Integra:
- TradeProtectionSystem (PostgreSQL) para anti-repetition, circuit breaker, symbol blacklist
- Trade limits (max trades simultáneos)
- SQS evaluation (calidad de señales)
- Daily loss limits (pérdida diaria máxima)
- Max trades protection (evitar abrir si hay muchos perdiendo)
- Portfolio protection (correlación, exposición sectorial)
- Schedule (horarios de operación)
"""

import json
import logging
import sys
import os
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
        sqs: float,
        rr: float,
        tier: int = None
    ) -> Tuple[bool, str, Dict]:
        """
        Validación COMPLETA de un trade contra el perfil del usuario.

        Ejecuta todas las validaciones en orden de prioridad:
        0. Tier Filtering (rechazar tiers demasiado agresivos para el usuario)
        1. Circuit Breaker (pausa global)
        2. Daily Loss Limits (pérdida diaria)
        3. Schedule (horarios)
        4. Anti-Repetition (cooldown por símbolo)
        5. Symbol Blacklist (performance histórico)
        6. Max Trades Protection (trades perdiendo)
        7. Trade Limits (max simultáneos)
        8. Portfolio Protection (correlación, exposición)
        9. SQS Evaluation (calidad de señal)

        Args:
            tier: Tier del trade (1-10) desde crypto-analyzer-redis
                  Si tier_config está habilitado, se valida contra max_tier_accepted

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
        # VALIDACIÓN 0: Tier Filtering (rechazar si tier fuera de rango)
        # ═══════════════════════════════════════════════════════════════
        if tier is not None and self.rules.get("tier_config", {}).get("enabled", False):
            tier_config = self.rules["tier_config"]
            max_tier_accepted = tier_config.get("max_tier_accepted", 10)

            if tier > max_tier_accepted:
                validation_results["failed_at"] = "tier_filtering"
                validation_results["tier_config"] = tier_config
                validation_results["tier_received"] = tier

                logger.warning(f"🎯 {self.user_id} - TIER REJECTED: tier {tier} > max_tier_accepted {max_tier_accepted}")

                return False, f"TIER_REJECTED: tier {tier} exceeds max_tier_accepted {max_tier_accepted} (too aggressive for this user)", validation_results

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
        # VALIDACIÓN 2: Daily Loss Limits
        # ═══════════════════════════════════════════════════════════════
        if self.rules.get("daily_loss_limits", {}).get("enabled", False):
            can_trade, loss_reason, loss_info = self._check_daily_loss_limits()

            if not can_trade:
                validation_results["failed_at"] = "daily_loss_limits"
                validation_results["daily_loss_info"] = loss_info
                return False, f"DAILY_LOSS_LIMIT: {loss_reason}", validation_results

        # ═══════════════════════════════════════════════════════════════
        # VALIDACIÓN 3: Schedule (horarios de operación)
        # ═══════════════════════════════════════════════════════════════
        if self.rules.get("schedule", {}).get("enabled", False):
            can_trade, schedule_reason = self._check_schedule()

            if not can_trade:
                validation_results["failed_at"] = "schedule"
                return False, f"SCHEDULE: {schedule_reason}", validation_results

        # ═══════════════════════════════════════════════════════════════
        # VALIDACIÓN 4: Anti-Repetition (cooldown por símbolo)
        # ═══════════════════════════════════════════════════════════════
        if self.protection_system and self.rules.get("anti_repetition", {}).get("enabled", False):
            config = self.rules["anti_repetition"]

            try:
                should_block, block_reason = self.protection_system.should_block_repetition(
                    user_id=self.user_id,
                    strategy=self.strategy,
                    symbol=symbol,
                    direction=direction,
                    current_price=entry_price,
                    cooldown_hours=config.get("cooldown_after_stop_hours", 6),  # Cooldown real (default: 6h)
                    lookback_hours=config.get("lookback_hours", 48),             # Ventana de búsqueda (default: 48h)
                    min_price_change_pct=config.get("min_price_change_pct", 2.0)
                )

                if should_block:
                    validation_results["failed_at"] = "anti_repetition"
                    validation_results["config_used"] = config
                    return False, f"ANTI_REPETITION: {block_reason}", validation_results
            except Exception as e:
                logger.error(f"Error checking anti-repetition: {e}")

        # ═══════════════════════════════════════════════════════════════
        # VALIDACIÓN 5: Symbol Blacklist (performance histórico)
        # ═══════════════════════════════════════════════════════════════
        if self.protection_system and self.rules.get("symbol_blacklist", {}).get("enabled", False):
            config = self.rules["symbol_blacklist"]

            try:
                should_block, block_reason = self.protection_system.should_block_symbol(
                    user_id=self.user_id,
                    strategy=self.strategy,
                    symbol=symbol,
                    min_trades=config.get("min_trades_for_evaluation", 10),
                    min_win_rate=config.get("min_win_rate_pct", 42.0),
                    max_loss_pct=config.get("max_cumulative_loss_pct", -15.0)
                )

                if should_block:
                    validation_results["failed_at"] = "symbol_blacklist"
                    validation_results["config_used"] = config
                    return False, f"SYMBOL_BLACKLIST: {block_reason}", validation_results
            except Exception as e:
                logger.error(f"Error checking symbol blacklist: {e}")

        # ═══════════════════════════════════════════════════════════════
        # VALIDACIÓN 6: Max Trades Protection
        # ═══════════════════════════════════════════════════════════════
        if self.rules.get("max_trades_protection", {}).get("enabled", False):
            can_trade, max_trades_reason, max_trades_info = self._check_max_trades_protection()

            if not can_trade:
                validation_results["failed_at"] = "max_trades_protection"
                validation_results["max_trades_info"] = max_trades_info
                return False, f"MAX_TRADES_PROTECTION: {max_trades_reason}", validation_results

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
        # VALIDACIÓN 8: Portfolio Protection (opcional)
        # ═══════════════════════════════════════════════════════════════
        if self.rules.get("portfolio_protection", {}).get("enabled", False):
            can_trade, portfolio_reason, portfolio_info = self._check_portfolio_protection(
                symbol, direction
            )

            if not can_trade:
                validation_results["failed_at"] = "portfolio_protection"
                validation_results["portfolio_info"] = portfolio_info
                return False, f"PORTFOLIO_PROTECTION: {portfolio_reason}", validation_results

        # ═══════════════════════════════════════════════════════════════
        # VALIDACIÓN 9: SQS Evaluation (calidad de señal)
        # ═══════════════════════════════════════════════════════════════
        from app.utils.sqs_evaluator import SQSEvaluator

        sqs_evaluator = SQSEvaluator(self.user_id, self.strategy)
        # Pass tier from crypto-analyzer-redis (if provided) for synchronized validation
        sqs_decision = sqs_evaluator.evaluate_trade(probability, sqs, rr, symbol, tier=tier)

        if sqs_decision['action'] == 'reject':
            validation_results["failed_at"] = "sqs_evaluation"
            validation_results["sqs_decision"] = sqs_decision
            return False, f"SQS_REJECTED: {sqs_decision['reason']}", validation_results

        # ═══════════════════════════════════════════════════════════════
        # ✅ TODAS LAS VALIDACIONES PASARON
        # ═══════════════════════════════════════════════════════════════
        validation_results["status"] = "APPROVED"
        validation_results["capital_multiplier"] = sqs_decision.get("capital_multiplier", 1.0)
        validation_results["quality_grade"] = sqs_decision.get("quality_grade", "UNKNOWN")
        validation_results["sqs_decision"] = sqs_decision

        logger.info(f"✅ {self.user_id} - Trade APPROVED for {symbol} ({sqs_decision.get('quality_grade')})")

        return True, "ALL_VALIDATIONS_PASSED", validation_results

    # ═══════════════════════════════════════════════════════════════════
    # MÉTODOS HELPER - Daily Loss Limits
    # ═══════════════════════════════════════════════════════════════════

    def _check_daily_loss_limits(self) -> Tuple[bool, str, Dict]:
        """Verifica si el usuario ha excedido su límite de pérdida diaria."""
        config = self.rules["daily_loss_limits"]
        max_daily_loss_pct = config.get("max_daily_loss_pct", 5.0)
        pause_duration_hours = config.get("pause_duration_hours", 12)

        try:
            if not self.redis_client:
                return True, "", {"error": "redis_unavailable"}

            # Verificar si hay pausa activa en Redis
            pause_key = f"{self.cache_prefix}:daily_loss_pause"
            pause_until_str = self.redis_client.get(pause_key)

            if pause_until_str:
                pause_until = datetime.fromisoformat(pause_until_str.decode() if isinstance(pause_until_str, bytes) else pause_until_str)

                if datetime.now(timezone.utc) < pause_until:
                    time_remaining = pause_until - datetime.now(timezone.utc)
                    return False, f"Daily loss pause active. Resumes in {time_remaining.total_seconds()/3600:.1f}h", {
                        "paused_until": pause_until.isoformat(),
                        "time_remaining_hours": time_remaining.total_seconds() / 3600
                    }

            # Calcular P&L diario desde PostgreSQL
            daily_pnl_pct = self._get_daily_pnl_pct()

            # Verificar si se excedió el límite
            if daily_pnl_pct <= -max_daily_loss_pct:
                # Activar pausa
                pause_until = datetime.now(timezone.utc) + timedelta(hours=pause_duration_hours)
                self.redis_client.setex(
                    pause_key,
                    int(pause_duration_hours * 3600),
                    pause_until.isoformat()
                )

                logger.warning(f"🚨 {self.user_id} - Daily loss limit triggered: {daily_pnl_pct:.2f}% (limit: {max_daily_loss_pct}%)")

                return False, f"Daily loss limit exceeded ({daily_pnl_pct:.2f}% loss). Paused for {pause_duration_hours}h", {
                    "daily_pnl_pct": daily_pnl_pct,
                    "max_daily_loss_pct": max_daily_loss_pct,
                    "paused_until": pause_until.isoformat()
                }

            # OK - No se excedió el límite
            return True, "", {
                "daily_pnl_pct": daily_pnl_pct,
                "max_daily_loss_pct": max_daily_loss_pct,
                "remaining_loss_allowance_pct": max_daily_loss_pct + daily_pnl_pct
            }

        except Exception as e:
            logger.error(f"❌ Error checking daily loss limits for {self.user_id}: {e}")
            return True, "", {"error": str(e)}

    def _get_daily_pnl_pct(self) -> float:
        """Obtiene el P&L diario acumulado del usuario desde medianoche UTC."""
        if not self.protection_system:
            return 0.0

        try:
            conn = self.protection_system._get_conn()

            query = """
                SELECT
                    COALESCE(SUM(pnl_pct), 0) as daily_pnl
                FROM trade_history
                WHERE user_id = %s
                  AND strategy = %s
                  AND exit_time >= DATE_TRUNC('day', NOW() AT TIME ZONE 'UTC')
                  AND exit_reason IN ('target_hit', 'stop_hit', 'timeout', 'manual_close')
            """

            with conn.cursor() as cur:
                cur.execute(query, (self.user_id, self.strategy))
                result = cur.fetchone()
                daily_pnl = float(result[0]) if result else 0.0

            conn.close()
            return daily_pnl

        except Exception as e:
            logger.error(f"❌ Error getting daily P&L for {self.user_id}: {e}")
            return 0.0

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
    # MÉTODOS HELPER - Max Trades Protection
    # ═══════════════════════════════════════════════════════════════════

    def _check_max_trades_protection(self) -> Tuple[bool, str, Dict]:
        """Verifica si hay demasiados trades perdiendo actualmente."""
        config = self.rules["max_trades_protection"]
        trigger_losing_trades = config.get("trigger_losing_trades", 3)
        cooldown_hours = config.get("cooldown_hours", 4)
        losing_threshold_R = config.get("losing_threshold_R", -0.5)

        try:
            if not self.redis_client:
                return True, "", {"error": "redis_unavailable"}

            # Verificar si hay cooldown activo
            cooldown_key = f"{self.cache_prefix}:max_trades_cooldown"
            cooldown_until_str = self.redis_client.get(cooldown_key)

            if cooldown_until_str:
                cooldown_until = datetime.fromisoformat(
                    cooldown_until_str.decode() if isinstance(cooldown_until_str, bytes) else cooldown_until_str
                )

                if datetime.now(timezone.utc) < cooldown_until:
                    time_remaining = cooldown_until - datetime.now(timezone.utc)
                    return False, f"Max trades protection cooldown active. Resumes in {time_remaining.total_seconds()/3600:.1f}h", {
                        "cooldown_until": cooldown_until.isoformat(),
                        "time_remaining_hours": time_remaining.total_seconds() / 3600
                    }

            # Contar trades perdiendo
            losing_count, losing_trades = self._count_losing_trades(losing_threshold_R)

            # Verificar si se debe activar cooldown
            if losing_count >= trigger_losing_trades:
                # Activar cooldown
                cooldown_until = datetime.now(timezone.utc) + timedelta(hours=cooldown_hours)
                self.redis_client.setex(
                    cooldown_key,
                    int(cooldown_hours * 3600),
                    cooldown_until.isoformat()
                )

                logger.warning(f"🚨 {self.user_id} - Max trades protection triggered: {losing_count} trades losing")

                return False, f"{losing_count} trades losing (threshold: {trigger_losing_trades}). Cooldown for {cooldown_hours}h", {
                    "losing_count": losing_count,
                    "trigger_losing_trades": trigger_losing_trades,
                    "losing_trades": losing_trades,
                    "cooldown_until": cooldown_until.isoformat()
                }

            # OK - No se alcanzó el umbral
            return True, "", {
                "losing_count": losing_count,
                "trigger_losing_trades": trigger_losing_trades,
                "remaining_allowance": trigger_losing_trades - losing_count
            }

        except Exception as e:
            logger.error(f"❌ Error checking max trades protection for {self.user_id}: {e}")
            return True, "", {"error": str(e)}

    def _count_losing_trades(self, threshold_R: float) -> Tuple[int, List[Dict]]:
        """Cuenta trades abiertos actualmente con P&L < threshold_R."""
        try:
            client = get_binance_client_for_user(self.user_id)
            positions = client.futures_position_information()

            losing_trades = []

            for pos in positions:
                position_amt = float(pos.get("positionAmt", "0"))

                if abs(position_amt) == 0:
                    continue

                symbol = pos.get("symbol", "")
                entry_price = float(pos.get("entryPrice", "0"))
                mark_price = float(pos.get("markPrice", entry_price))
                unrealized_pnl = float(pos.get("unRealizedProfit", "0"))

                # Obtener trade info desde Redis para calcular R
                trade_info = self._get_trade_info_from_redis(symbol)

                if not trade_info:
                    continue

                stop_price = float(trade_info.get("stop", 0))
                side = trade_info.get("side", "").upper()

                if entry_price == 0 or stop_price == 0:
                    continue

                # Calcular progress en R
                risk_per_unit = abs(entry_price - stop_price)

                if risk_per_unit == 0:
                    continue

                if side == "BUY":
                    progress_R = (mark_price - entry_price) / risk_per_unit
                else:
                    progress_R = (entry_price - mark_price) / risk_per_unit

                # Verificar si está perdiendo
                if progress_R < threshold_R:
                    losing_trades.append({
                        "symbol": symbol,
                        "side": side,
                        "entry_price": entry_price,
                        "mark_price": mark_price,
                        "progress_R": progress_R,
                        "unrealized_pnl": unrealized_pnl
                    })

            return len(losing_trades), losing_trades

        except Exception as e:
            logger.error(f"❌ Error counting losing trades for {self.user_id}: {e}")
            return 0, []

    def _get_trade_info_from_redis(self, symbol: str) -> Optional[Dict]:
        """Obtiene información del trade desde Redis."""
        if not self.redis_client:
            return None

        try:
            trade_key = f"guardian:trades:{symbol.lower()}"
            trade_data = self.redis_client.get(trade_key)

            if not trade_data:
                return None

            trade_info = json.loads(
                trade_data.decode() if isinstance(trade_data, bytes) else trade_data
            )

            return trade_info

        except Exception as e:
            logger.error(f"⚠️ Error getting trade info from Redis for {symbol}: {e}")
            return None

    # ═══════════════════════════════════════════════════════════════════
    # MÉTODOS HELPER - Portfolio Protection
    # ═══════════════════════════════════════════════════════════════════

    def _check_portfolio_protection(self, symbol: str, direction: str) -> Tuple[bool, str, Dict]:
        """Verifica protecciones a nivel de portfolio."""
        try:
            # Validación básica: contar número de posiciones abiertas
            client = get_binance_client_for_user(self.user_id)
            positions = client.futures_position_information()

            open_count = sum(1 for pos in positions if abs(float(pos.get("positionAmt", "0"))) > 0)

            # Límite básico: máximo 10 posiciones simultáneas
            if open_count >= 10:
                return False, f"Too many open positions ({open_count}). Portfolio protection limit.", {
                    "open_count": open_count,
                    "source": "basic_validation"
                }

            return True, "", {"source": "basic_validation", "open_count": open_count}

        except Exception as e:
            logger.error(f"❌ Error checking portfolio protection for {self.user_id}: {e}")
            return True, "", {"error": str(e)}

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
        sqs: float,
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
                sqs=sqs,
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
