"""
config_constants.py - Constantes globales para evitar strings hardcodeados

Uso:
    from app.utils.config.config_constants import BUY, SELL, VALID_DIRECTIONS
"""

# ========== DIRECCIONES DE TRADE ==========
# Binance usa BUY/SELL para las órdenes
BUY = "BUY"
SELL = "SELL"

# Set de direcciones válidas para validación
VALID_DIRECTIONS = {BUY, SELL}


# ========== TIPOS DE ORDEN ==========
MARKET = "MARKET"
LIMIT = "LIMIT"
STOP_MARKET = "STOP_MARKET"
TAKE_PROFIT_MARKET = "TAKE_PROFIT_MARKET"


# ========== ESTADOS DE TRADE ==========
TRADE_SUCCESS = "success"
TRADE_FAILED = "failed"
TRADE_ACTIVE = "active"


# ========== TABLAS DE BASE DE DATOS ==========
TABLE_TRADE_RECORDS = "trade_records"
TABLE_USER_RULES = "user_rules"


# ========== RULE KEYS (campos en user_rules.rules_config) ==========
RULE_ENABLED = "enabled"
RULE_RISK_PCT = "risk_pct"
RULE_MAX_LEVERAGE = "max_leverage"
RULE_MIN_PROBABILITY = "min_probability"
RULE_MIN_RR = "min_rr"
RULE_MIN_EV = "min_ev"
RULE_MAX_TRADES_OPEN = "max_trades_open"
RULE_COOLDOWN_HOURS = "cooldown_hours"

# Schedule
RULE_SCHEDULE = "schedule"
RULE_SCHEDULE_ENABLED = "enabled"
RULE_SCHEDULE_TIMEZONE = "timezone"

# Circuit Breaker
RULE_CIRCUIT_BREAKER = "circuit_breaker"
RULE_CB_ENABLED = "enabled"
RULE_CB_TIERS = "tiers"
RULE_CB_CONSECUTIVE_LOSSES = "consecutive_losses"
RULE_CB_PAUSE_HOURS = "pause_hours"
RULE_CB_MAX_CONSECUTIVE_LOSSES = "max_consecutive_losses"
RULE_CB_PAUSE_DURATION_HOURS = "pause_duration_hours"

# Signal Quality - Grok filters
RULE_MIN_GROK_CONFIDENCE = "min_grok_confidence"
RULE_MIN_GROK_TIMING = "min_grok_timing_quality"
RULE_MAX_GROK_RISK = "max_grok_risk_level"


# ========== GROK VALUES ==========
# Valores válidos para campos de Grok (ordenados de mejor a peor)

# grok_action: Solo ENTER permite el trade
GROK_ACTION_ENTER = "ENTER"
GROK_ACTION_WAIT = "WAIT"
GROK_ACTION_REJECT = "REJECT"

# grok_confidence: HIGH es mejor
GROK_CONFIDENCE_HIGH = "HIGH"
GROK_CONFIDENCE_MEDIUM = "MEDIUM"
GROK_CONFIDENCE_LOW = "LOW"
GROK_CONFIDENCE_LEVELS = [GROK_CONFIDENCE_HIGH, GROK_CONFIDENCE_MEDIUM, GROK_CONFIDENCE_LOW]

# grok_timing_quality: OPTIMAL es mejor
GROK_TIMING_OPTIMAL = "OPTIMAL"
GROK_TIMING_GOOD = "GOOD"
GROK_TIMING_FAIR = "FAIR"
GROK_TIMING_LEVELS = [GROK_TIMING_OPTIMAL, GROK_TIMING_GOOD, GROK_TIMING_FAIR]

# grok_risk_level: LOW es mejor (orden invertido)
GROK_RISK_LOW = "LOW"
GROK_RISK_MEDIUM = "MEDIUM"
GROK_RISK_HIGH = "HIGH"
GROK_RISK_LEVELS = [GROK_RISK_LOW, GROK_RISK_MEDIUM, GROK_RISK_HIGH]


# ========== EXIT REASONS ==========
EXIT_REASON_ACTIVE = "active"
EXIT_REASON_TARGET_HIT = "target_hit"
EXIT_REASON_STOP_HIT = "stop_hit"
EXIT_REASON_TIMEOUT_WIN = "timeout_win"
EXIT_REASON_TIMEOUT_LOST = "timeout_lost"
EXIT_REASON_MANUAL_CLOSE_WIN = "manual_close_win"
EXIT_REASON_MANUAL_CLOSE_LOST = "manual_close_lost"
EXIT_REASON_UNKNOWN = "unknown"

# Sets para clasificación
EXIT_REASONS_WIN = {EXIT_REASON_TARGET_HIT, EXIT_REASON_TIMEOUT_WIN, EXIT_REASON_MANUAL_CLOSE_WIN}
EXIT_REASONS_LOSS = {EXIT_REASON_STOP_HIT, EXIT_REASON_TIMEOUT_LOST, EXIT_REASON_MANUAL_CLOSE_LOST, EXIT_REASON_UNKNOWN}

# Exit reasons que requieren cooldown por símbolo
# NOTA: timeout_lost NO requiere cooldown porque el trade ya esperó N horas por el timeout
EXIT_REASONS_COOLDOWN = {EXIT_REASON_STOP_HIT, EXIT_REASON_MANUAL_CLOSE_LOST}


# ========== HELPER FUNCTIONS ==========
def validate_direction(direction: str) -> str:
    """
    Valida que la dirección sea BUY o SELL.

    Args:
        direction: String con la dirección

    Returns:
        "BUY" o "SELL" (normalizado a mayúsculas)

    Raises:
        ValueError: Si la dirección no es BUY o SELL
    """
    if direction is None:
        raise ValueError("Dirección no puede ser None")

    d = direction.upper().strip()

    if d not in VALID_DIRECTIONS:
        raise ValueError(f"Dirección inválida: '{direction}'. Debe ser BUY o SELL")

    return d


def get_opposite_direction(direction: str) -> str:
    """
    Retorna la dirección opuesta (para SL/TP).

    Args:
        direction: BUY o SELL

    Returns:
        Dirección opuesta

    Raises:
        ValueError: Si la dirección no es BUY o SELL
    """
    validated = validate_direction(direction)

    if validated == BUY:
        return SELL
    elif validated == SELL:
        return BUY
