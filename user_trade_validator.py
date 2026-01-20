"""
user_trade_validator.py - Validador de trades por usuario

Valida si un usuario puede abrir un trade basado en sus reglas de configuración.

Validaciones (en orden):
  1. Usuario habilitado
  2. Schedule (horarios de operación)
  3. Circuit Breaker (pausa tras pérdidas consecutivas)
  4. Recent Trade Cooldown (cooldown por símbolo después de pérdidas)
  5. Trade Limits (máximo de trades simultáneos + posición existente)
  6. Signal Quality (probability, RR mínimos + filtros de Grok)

Uso:
    from user_trade_validator import UserTradeValidator

    validator = UserTradeValidator(user_id, rules, strategy, client)
    can_trade, reason = validator.validate(request)
"""

from datetime import datetime, timezone, timedelta
from typing import Tuple, TYPE_CHECKING

from app.utils.db.trade_repository import get_consecutive_losses, get_last_trade_for_symbol
from app.utils.config.config_constants import (
    # Rule keys
    RULE_ENABLED,
    RULE_RISK_PCT,
    RULE_MAX_LEVERAGE,
    RULE_COOLDOWN_HOURS,
    RULE_MAX_TRADES_OPEN,
    RULE_MIN_PROBABILITY,
    RULE_MIN_RR,
    # Schedule
    RULE_SCHEDULE,
    RULE_SCHEDULE_ENABLED,
    # Circuit Breaker
    RULE_CIRCUIT_BREAKER,
    RULE_CB_ENABLED,
    RULE_CB_TIERS,
    RULE_CB_CONSECUTIVE_LOSSES,
    RULE_CB_PAUSE_HOURS,
    RULE_CB_MAX_CONSECUTIVE_LOSSES,
    RULE_CB_PAUSE_DURATION_HOURS,
    # Cooldown
    EXIT_REASONS_COOLDOWN,
    # Signal Quality - Grok
    RULE_MIN_GROK_CONFIDENCE,
    RULE_MIN_GROK_TIMING,
    RULE_MAX_GROK_RISK,
    GROK_ACTION_ENTER,
    GROK_CONFIDENCE_LEVELS,
    GROK_TIMING_LEVELS,
    GROK_RISK_LEVELS,
)

# Evitar import circular
if TYPE_CHECKING:
    from trade_executor import TradeRequest


# Constantes por defecto (si no están en rules)
DEFAULT_RISK_PCT = 1.0
DEFAULT_LEVERAGE = 20
DEFAULT_COOLDOWN_HOURS = 4
DEFAULT_MAX_TRADES_OPEN = 999  # 999 = sin límite

# Signal Quality defaults
DEFAULT_MIN_PROBABILITY = 50.0
DEFAULT_MIN_RR = 1.0
DEFAULT_MIN_GROK_CONFIDENCE = None  # None = acepta cualquier nivel
DEFAULT_MIN_GROK_TIMING = None      # None = acepta cualquier nivel
DEFAULT_MAX_GROK_RISK = None        # None = acepta cualquier nivel


class UserTradeValidator:
    """
    Validador de trades por usuario.
    Verifica que el trade cumple con las reglas configuradas.
    """

    def __init__(self, user_id: str, rules: dict, strategy: str, client=None):
        """
        Args:
            user_id: ID del usuario (copy_trading, futures, hufsa, copy_2)
            rules: Reglas del usuario obtenidas de get_user_rules()
            strategy: Nombre de la estrategia (necesario para queries)
            client: Cliente de Binance (requerido para trade limits)
        """
        self.user_id = user_id
        self.rules = rules
        self.strategy = strategy
        self.client = client

        # Extraer valores comunes de rules
        self.risk_pct = float(rules.get(RULE_RISK_PCT, DEFAULT_RISK_PCT))
        self.leverage = int(rules.get(RULE_MAX_LEVERAGE, DEFAULT_LEVERAGE))

    def validate(self, request: "TradeRequest") -> Tuple[bool, str]:
        """
        Valida si el usuario puede abrir el trade.

        Args:
            request: TradeRequest con los datos del trade

        Returns:
            Tuple[bool, str]: (puede_operar, razón)
                - (True, "approved") si pasa todas las validaciones
                - (False, "razón") si falla alguna validación
        """
        # ========== 1. USUARIO HABILITADO ==========
        if not self.rules.get(RULE_ENABLED, False):
            return False, "user_disabled"

        # ========== 2. SCHEDULE ==========
        schedule = self.rules.get(RULE_SCHEDULE, {})
        if schedule.get(RULE_SCHEDULE_ENABLED, False):
            is_allowed, schedule_reason = self._check_schedule(schedule)
            if not is_allowed:
                return False, f"schedule:{schedule_reason}"

        # ========== 3. CIRCUIT BREAKER ==========
        circuit_breaker = self.rules.get(RULE_CIRCUIT_BREAKER, {})
        if circuit_breaker.get(RULE_CB_ENABLED, False):
            is_allowed, cb_reason = self._check_circuit_breaker(circuit_breaker)
            if not is_allowed:
                return False, f"circuit_breaker:{cb_reason}"

        # ========== 4. RECENT TRADE COOLDOWN ==========
        # Verificar cooldown por símbolo después de pérdidas
        cooldown_hours = self.rules.get(RULE_COOLDOWN_HOURS, DEFAULT_COOLDOWN_HOURS)
        if cooldown_hours > 0:
            is_allowed, cooldown_reason = self._check_recent_trade_cooldown(
                symbol=request.symbol,
                cooldown_hours=cooldown_hours
            )
            if not is_allowed:
                return False, f"cooldown:{cooldown_reason}"

        # ========== 5. TRADE LIMITS ==========
        # Verificar posición existente y máximo de trades simultáneos
        if self.client is not None:
            max_trades = int(self.rules.get(RULE_MAX_TRADES_OPEN, DEFAULT_MAX_TRADES_OPEN))
            is_allowed, limits_reason = self._check_trade_limits(
                max_trades=max_trades,
                symbol=request.symbol
            )
            if not is_allowed:
                return False, f"trade_limits:{limits_reason}"

        # ========== 6. SIGNAL QUALITY ==========
        # Validar probability, RR y filtros de Grok
        is_allowed, quality_reason = self._check_signal_quality(request)
        if not is_allowed:
            return False, f"signal_quality:{quality_reason}"

        return True, "approved"

    def _check_schedule(self, schedule: dict) -> Tuple[bool, str]:
        """
        Verifica si el momento actual está dentro del horario permitido.

        Args:
            schedule: Dict con días y rangos horarios permitidos

        Returns:
            Tuple[bool, str]: (permitido, razón)
        """
        now_utc = datetime.now(timezone.utc)
        current_day = now_utc.strftime("%A")  # Monday, Tuesday, etc.
        current_time = now_utc.time()

        # Si el día no está en schedule, rechazar
        if current_day not in schedule:
            return False, f"day_not_allowed:{current_day}"

        day_ranges = schedule[current_day]

        # Si el día no tiene rangos definidos, rechazar
        if not day_ranges:
            return False, f"no_ranges_for:{current_day}"

        # Verificar si la hora actual está en algún rango permitido
        for time_range in day_ranges:
            if len(time_range) != 2:
                continue

            start_str, end_str = time_range
            start_time = datetime.strptime(start_str, "%H:%M").time()
            end_time = datetime.strptime(end_str, "%H:%M").time()

            if start_time <= current_time <= end_time:
                return True, ""

        # No está en ningún rango permitido
        return False, f"outside_hours:{current_day}_{current_time.strftime('%H:%M')}"

    def _check_circuit_breaker(self, circuit_breaker: dict) -> Tuple[bool, str]:
        """
        Verifica si el circuit breaker está activo por pérdidas consecutivas.

        Soporta dos formatos de configuración:

        Formato simple:
        {
            "enabled": true,
            "max_consecutive_losses": 5,
            "pause_duration_hours": 4
        }

        Formato con tiers:
        {
            "enabled": true,
            "tiers": [
                {"consecutive_losses": 3, "pause_hours": 2},
                {"consecutive_losses": 5, "pause_hours": 8},
                {"consecutive_losses": 8, "pause_hours": 12},
                {"consecutive_losses": 10, "pause_hours": 24}
            ]
        }

        Returns:
            Tuple[bool, str]: (permitido, razón)
        """
        # Obtener pérdidas consecutivas de la BD
        consecutive_losses, last_loss_time = get_consecutive_losses(
            self.user_id, self.strategy
        )

        # Si no hay pérdidas, permitir
        if consecutive_losses == 0 or last_loss_time is None:
            return True, ""

        now_utc = datetime.now(timezone.utc)

        # Asegurar que last_loss_time tenga timezone
        if last_loss_time.tzinfo is None:
            last_loss_time = last_loss_time.replace(tzinfo=timezone.utc)

        # Determinar el pause_hours aplicable
        pause_hours = self._get_pause_hours(circuit_breaker, consecutive_losses)

        if pause_hours is None:
            # No alcanza ningún threshold, permitir
            return True, ""

        # Calcular hasta cuándo dura la pausa
        pause_until = last_loss_time + timedelta(hours=pause_hours)

        if now_utc < pause_until:
            # Aún en pausa
            remaining = pause_until - now_utc
            remaining_hours = remaining.total_seconds() / 3600
            return False, f"paused:{consecutive_losses}_losses:remaining_{remaining_hours:.1f}h"

        # La pausa ya terminó
        return True, ""

    def _get_pause_hours(self, circuit_breaker: dict, consecutive_losses: int) -> int:
        """
        Determina las horas de pausa según las pérdidas consecutivas.

        Args:
            circuit_breaker: Configuración del circuit breaker
            consecutive_losses: Cantidad de pérdidas consecutivas

        Returns:
            int: Horas de pausa, o None si no aplica ningún threshold
        """
        # Formato con tiers
        if RULE_CB_TIERS in circuit_breaker:
            tiers = circuit_breaker[RULE_CB_TIERS]
            # Ordenar tiers por consecutive_losses DESC para encontrar el tier más alto que aplica
            sorted_tiers = sorted(
                tiers,
                key=lambda t: t[RULE_CB_CONSECUTIVE_LOSSES],
                reverse=True
            )

            for tier in sorted_tiers:
                if consecutive_losses >= tier[RULE_CB_CONSECUTIVE_LOSSES]:
                    return tier[RULE_CB_PAUSE_HOURS]

            return None

        # Formato simple (legacy)
        max_losses = circuit_breaker.get(RULE_CB_MAX_CONSECUTIVE_LOSSES, 5)
        pause_hours = circuit_breaker.get(RULE_CB_PAUSE_DURATION_HOURS, 4)

        if consecutive_losses >= max_losses:
            return pause_hours

        return None

    def _check_recent_trade_cooldown(
        self,
        symbol: str,
        cooldown_hours: int
    ) -> Tuple[bool, str]:
        """
        Verifica si hay cooldown activo para un símbolo específico.

        El cooldown aplica cuando:
        - El último trade del símbolo fue una pérdida (stop_hit o manual_close_lost)
        - Y el cierre fue hace menos de cooldown_hours

        NO aplica cooldown cuando:
        - El último trade ganó (target_hit, timeout_win, manual_close_win)
        - El último trade fue timeout_lost (ya esperó N horas por el timeout)
        - No hay historial para el símbolo
        - El cooldown ya expiró

        Args:
            symbol: Símbolo del trade (ej: BTCUSDT)
            cooldown_hours: Horas de cooldown después de pérdida

        Returns:
            Tuple[bool, str]: (permitido, razón)
        """
        # Obtener último trade cerrado del símbolo
        exit_reason, exit_time = get_last_trade_for_symbol(
            user_id=self.user_id,
            strategy=self.strategy,
            symbol=symbol
        )

        # Si no hay historial, permitir
        if exit_reason is None or exit_time is None:
            return True, ""

        # Si NO es una razón que requiere cooldown, permitir inmediatamente
        if exit_reason not in EXIT_REASONS_COOLDOWN:
            return True, ""

        # Es una pérdida que requiere cooldown, verificar tiempo
        now_utc = datetime.now(timezone.utc)

        # Asegurar que exit_time tenga timezone
        if exit_time.tzinfo is None:
            exit_time = exit_time.replace(tzinfo=timezone.utc)

        hours_since_close = (now_utc - exit_time).total_seconds() / 3600

        if hours_since_close < cooldown_hours:
            # Aún en cooldown
            remaining = cooldown_hours - hours_since_close
            return False, f"{symbol}:{exit_reason}:{hours_since_close:.1f}h_ago:remaining_{remaining:.1f}h"

        # Cooldown expirado
        return True, ""

    def _check_trade_limits(self, max_trades: int, symbol: str) -> Tuple[bool, str]:
        """
        Verifica posición existente y máximo de trades simultáneos.

        Una sola llamada a Binance API para:
        1. Verificar si ya existe posición para el símbolo
        2. Verificar si se alcanzó el máximo de trades (si está configurado)

        Args:
            max_trades: Máximo de trades permitidos (999 = sin límite)
            symbol: Símbolo del trade a abrir

        Returns:
            Tuple[bool, str]: (permitido, razón)
        """
        try:
            # Obtener todas las posiciones de Binance (una sola llamada)
            positions = self.client.futures_position_information()

            # Extraer símbolos con posición abierta (cantidad != 0)
            open_positions = [
                pos.get("symbol", "")
                for pos in positions
                if abs(float(pos.get("positionAmt", "0"))) > 0
            ]

            # 1. Verificar si ya existe posición para este símbolo
            if symbol.upper() in [s.upper() for s in open_positions]:
                return False, f"position_exists:{symbol}"

            # 2. Verificar límite de trades (solo si está configurado)
            current_count = len(open_positions)
            if max_trades < 999 and current_count >= max_trades:
                return False, f"max_exceeded:{current_count}/{max_trades}"

            return True, ""

        except Exception as e:
            # En caso de error, permitir el trade (fail-safe)
            # El error se logueará en otro lugar
            return True, ""

    def _check_signal_quality(self, request: "TradeRequest") -> Tuple[bool, str]:
        """
        Verifica la calidad de la señal: probability, RR y filtros de Grok.

        Validaciones:
        1. probability >= min_probability
        2. rr >= min_rr
        3. grok_action == ENTER (si viene WAIT/REJECT → rechazar)
        4. grok_confidence >= min_grok_confidence (si está configurado)
        5. grok_timing_quality >= min_grok_timing_quality (si está configurado)
        6. grok_risk_level <= max_grok_risk_level (si está configurado)

        Args:
            request: TradeRequest con probability, rr y campos de Grok

        Returns:
            Tuple[bool, str]: (permitido, razón)
        """
        # ========== 1. Validar probability ==========
        min_probability = float(self.rules.get(RULE_MIN_PROBABILITY, DEFAULT_MIN_PROBABILITY))
        if request.probability < min_probability:
            return False, f"probability:{request.probability}<{min_probability}"

        # ========== 2. Validar RR ==========
        min_rr = float(self.rules.get(RULE_MIN_RR, DEFAULT_MIN_RR))
        if request.rr < min_rr:
            return False, f"rr:{request.rr:.2f}<{min_rr}"

        # ========== 3. Validar grok_action ==========
        # Si viene grok_action y no es ENTER, rechazar
        if request.grok_action is not None:
            if request.grok_action.upper() != GROK_ACTION_ENTER:
                return False, f"grok_action:{request.grok_action}"

        # ========== 4. Validar grok_confidence ==========
        min_confidence = self.rules.get(RULE_MIN_GROK_CONFIDENCE, DEFAULT_MIN_GROK_CONFIDENCE)
        if min_confidence and request.grok_confidence:
            if not self._meets_minimum_level(
                request.grok_confidence.upper(),
                min_confidence.upper(),
                GROK_CONFIDENCE_LEVELS
            ):
                return False, f"grok_confidence:{request.grok_confidence}<{min_confidence}"

        # ========== 5. Validar grok_timing_quality ==========
        min_timing = self.rules.get(RULE_MIN_GROK_TIMING, DEFAULT_MIN_GROK_TIMING)
        if min_timing and request.grok_timing_quality:
            if not self._meets_minimum_level(
                request.grok_timing_quality.upper(),
                min_timing.upper(),
                GROK_TIMING_LEVELS
            ):
                return False, f"grok_timing:{request.grok_timing_quality}<{min_timing}"

        # ========== 6. Validar grok_risk_level ==========
        # Nota: Para risk, menor es mejor, así que la lógica es invertida
        max_risk = self.rules.get(RULE_MAX_GROK_RISK, DEFAULT_MAX_GROK_RISK)
        if max_risk and request.grok_risk_level:
            if not self._meets_maximum_level(
                request.grok_risk_level.upper(),
                max_risk.upper(),
                GROK_RISK_LEVELS
            ):
                return False, f"grok_risk:{request.grok_risk_level}>{max_risk}"

        return True, ""

    def _meets_minimum_level(self, actual: str, minimum: str, levels: list) -> bool:
        """
        Verifica si el nivel actual cumple con el mínimo requerido.

        Los niveles están ordenados de mejor a peor en la lista.
        Ejemplo: ["HIGH", "MEDIUM", "LOW"] - HIGH es mejor

        Args:
            actual: Nivel actual del request
            minimum: Nivel mínimo requerido
            levels: Lista de niveles ordenados de mejor a peor

        Returns:
            bool: True si actual es igual o mejor que minimum
        """
        try:
            actual_idx = levels.index(actual)
            min_idx = levels.index(minimum)
            # Índice menor = mejor nivel
            return actual_idx <= min_idx
        except ValueError:
            # Si el valor no está en la lista, permitir (fail-safe)
            return True

    def _meets_maximum_level(self, actual: str, maximum: str, levels: list) -> bool:
        """
        Verifica si el nivel actual no excede el máximo permitido.

        Para risk_level: LOW < MEDIUM < HIGH (LOW es mejor)
        Lista: ["LOW", "MEDIUM", "HIGH"]

        Args:
            actual: Nivel actual del request
            maximum: Nivel máximo permitido
            levels: Lista de niveles ordenados de mejor a peor

        Returns:
            bool: True si actual es igual o mejor que maximum
        """
        try:
            actual_idx = levels.index(actual)
            max_idx = levels.index(maximum)
            # Índice menor = mejor nivel (menor riesgo)
            return actual_idx <= max_idx
        except ValueError:
            # Si el valor no está en la lista, permitir (fail-safe)
            return True
