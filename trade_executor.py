"""
trade_executor.py - Lógica de ejecución de trades

Flujo de ejecución:
  0. Obtener reglas del usuario (get_user_rules)
  1. Validar reglas (UserTradeValidator - incluye posición existente y trade limits)
  2. Obtener mark_price y filters
  3. Calcular quantity
  4. Configurar leverage
  5-7. Crear MARKET + SL + TP (execute_safe_trade)
  8. Registrar en DB (save_trade_record)
"""

from pydantic import BaseModel, Field
from typing import Optional, Dict, Any, Tuple

# Constantes
from app.utils.config.config_constants import BUY, SELL

# Binance helpers
from app.utils.binance.utils import (
    get_mark_price,
    get_symbol_filters,
    adjust_price_to_tick,
    adjust_quantity_to_step_size,
    get_available_usdt_balance,
    set_leverage
)
from app.utils.binance.validators import (
    validate_symbol_filters,
    validate_quantity,
    validate_balance
)
from app.utils.binance.order_executor import execute_safe_trade

# Validación de usuario
from user_trade_validator import UserTradeValidator

# Database
from app.utils.db.trade_repository import save_trade_record, get_user_rules


# ========== HELPERS ==========

def calculate_capital(client, risk_pct: float) -> float:
    """
    Calcula el capital a arriesgar basado en el balance USDT y el porcentaje de riesgo.

    Args:
        client: Cliente de Binance
        risk_pct: Porcentaje de riesgo (ej: 1.0 significa 1%)

    Returns:
        float: Capital a arriesgar en USDT, o 0.0 si hay error
    """
    try:
        free_balance = get_available_usdt_balance(client)
        capital_to_risk = free_balance * (risk_pct / 100)
        return capital_to_risk
    except Exception as e:
        return 0.0


# NOTA: No usar adjust_prices_by_slippage() de validators.py porque:
#   - Usa strings "LONG"/"SHORT" hardcodeados (inconsistente con BUY/SELL)
#   - Infiere dirección en vez de recibirla como parámetro
#   - original_rr es opcional (puede dar resultados inconsistentes)
#   - No retorna el RR ajustado
# En su lugar, usar adjust_prices_to_mark() definida aquí.

def adjust_prices_to_mark(
    mark_price: float,
    stop: float,
    direction: str,
    rr: float,
    tick_size: float
) -> Tuple[float, float, float, float]:
    """
    Ajusta los precios al mark_price actual manteniendo la distancia SL y el RR.

    Cuando llega una señal, el precio puede haber cambiado. Esta función
    recalcula entry, SL y TP basándose en el mark_price actual.

    Args:
        mark_price: Precio actual de Binance
        stop: Stop loss del request original
        direction: BUY o SELL (debe ser validado antes)
        rr: Risk/Reward ratio original
        tick_size: Tick size del símbolo para ajustar precios

    Returns:
        Tuple[float, float, float, float]: (new_entry, new_stop, new_target, actual_rr)

    Raises:
        ValueError: Si dirección no es BUY o SELL, o si los cálculos resultan en precios inválidos
    """
    # Validar dirección
    if direction not in (BUY, SELL):
        raise ValueError(f"Dirección inválida: {direction}. Debe ser BUY o SELL")

    # Validar inputs
    if mark_price <= 0:
        raise ValueError(f"mark_price debe ser > 0, recibido: {mark_price}")
    if stop <= 0:
        raise ValueError(f"stop debe ser > 0, recibido: {stop}")
    if rr <= 0:
        raise ValueError(f"rr debe ser > 0, recibido: {rr}")
    if tick_size <= 0:
        raise ValueError(f"tick_size debe ser > 0, recibido: {tick_size}")

    # Calcular distancia SL original
    sl_distance = abs(mark_price - stop)

    if direction == BUY:
        # LONG: SL está debajo del entry
        new_entry = mark_price
        new_stop = mark_price - sl_distance
        new_target = mark_price + (sl_distance * rr)
    else:
        # SHORT: SL está arriba del entry
        new_entry = mark_price
        new_stop = mark_price + sl_distance
        new_target = mark_price - (sl_distance * rr)

    # Validar que los precios sean positivos
    if new_stop <= 0:
        raise ValueError(f"new_stop calculado es <= 0: {new_stop}")
    if new_target <= 0:
        raise ValueError(f"new_target calculado es <= 0: {new_target}")

    # Ajustar al tick_size de Binance
    entry_adj = adjust_price_to_tick(new_entry, tick_size)
    stop_adj = adjust_price_to_tick(new_stop, tick_size)
    target_adj = adjust_price_to_tick(new_target, tick_size)

    # Calcular RR real después del ajuste al tick
    actual_sl_distance = abs(entry_adj - stop_adj)
    actual_tp_distance = abs(target_adj - entry_adj)
    actual_rr = actual_tp_distance / actual_sl_distance if actual_sl_distance > 0 else 0

    return entry_adj, stop_adj, target_adj, actual_rr


# ========== MODELS ==========
class TradeRequest(BaseModel):
    """
    Request para abrir un trade.
    Formato enviado por crypto-analyzer-redis.
    """
    # Campos requeridos
    symbol: str = Field(..., description="Par de trading (ej: BTCUSDT)")
    trade: str = Field(..., description="Dirección: BUY o SELL")
    probability: float = Field(..., description="Probabilidad de éxito (0-100)")
    rr: float = Field(..., description="Risk/Reward ratio")
    entry: float = Field(..., description="Precio de entrada")
    stop: float = Field(..., description="Precio de stop loss")
    target: float = Field(..., description="Precio de take profit")
    strategy: str = Field(default="archer_model", description="Nombre de la estrategia")

    # Metadata (opcional)
    ev: Optional[float] = Field(None, description="Expected Value")
    mark_price: Optional[float] = Field(None, description="Mark price al momento de la señal")
    grok_model: Optional[str] = Field(None, description="Modelo de Grok usado")
    simulated_probability: Optional[float] = Field(None, description="Probabilidad Stage 1")
    grok_probability: Optional[float] = Field(None, description="Probabilidad Stage 2 (Grok)")
    timestamp: Optional[str] = Field(None, description="Timestamp de la señal ISO format")

    # Grok metadata (opcional)
    # Valores posibles ordenados de mejor a peor para filtrado
    grok_action: Optional[str] = Field(None, description="Acción de Grok: ENTER | WAIT | REJECT")
    grok_confidence: Optional[str] = Field(None, description="Confianza: HIGH | MEDIUM | LOW (HIGH es mejor)")
    grok_risk_level: Optional[str] = Field(None, description="Nivel de riesgo: LOW | MEDIUM | HIGH (LOW es mejor)")
    grok_timing_quality: Optional[str] = Field(None, description="Calidad del timing: OPTIMAL | GOOD | FAIR (OPTIMAL es mejor)")
    grok_key_factor: Optional[str] = Field(None, description="Factor clave de la decisión (texto libre)")

    class Config:
        json_schema_extra = {
            "example": {
                "symbol": "ETHUSDT",
                "trade": "BUY",
                "probability": 72,
                "rr": 1.5,
                "entry": 3500.0,
                "stop": 3450.0,
                "target": 3575.0,
                "strategy": "archer_model",
                "ev": 0.58,
                "mark_price": 3498.5,
                "timestamp": "2025-01-18T12:30:00Z"
            }
        }


# ========== TRADE EXECUTION ==========
def process_trade_for_user(
    user_id: str,
    client,
    request: TradeRequest
) -> Dict[str, Any]:
    """
    Procesa un trade para un usuario específico.
    Ejecuta los pasos 3-10 del flujo.

    Args:
        user_id: ID del usuario (copy_trading, futures, hufsa, copy_2)
        client: Cliente de Binance
        request: Request completo con todos los datos del trade y metadata

    Returns:
        Dict con resultado: {"user_id": str, "success": bool, "reason": str, ...}
    """
    # Extraer campos del request (dirección ya validada en api.py)
    symbol = request.symbol.upper()
    direction = request.trade.upper()
    entry = request.entry
    stop = request.stop
    target = request.target
    strategy = request.strategy

    # Metadata útil para logging/decisiones
    probability = request.probability
    rr = request.rr

    try:
        # ========== PASO 0: Obtener reglas del usuario ==========
        rules = get_user_rules(user_id, strategy)

        # ========== PASO 1: Validar reglas del usuario ==========
        validator = UserTradeValidator(user_id, rules, strategy, client)
        can_trade, reason = validator.validate(request)
        if not can_trade:
            return {
                "user_id": user_id,
                "success": False,
                "reason": reason
            }

        # Extraer valores de validator
        risk_pct = validator.risk_pct

        # ========== PASO 2: Obtener mark_price y filters ==========
        mark_price = get_mark_price(symbol, client)
        if mark_price <= 0:
            return {
                "user_id": user_id,
                "success": False,
                "reason": "mark_price_error"
            }

        filters = get_symbol_filters(symbol, client)
        if not filters or not validate_symbol_filters(filters, symbol):
            return {
                "user_id": user_id,
                "success": False,
                "reason": "invalid_symbol_filters"
            }

        # Ajustar precios al mark_price actual (mantiene distancia SL y RR)
        tick_size = float(filters["PRICE_FILTER"]["tickSize"])
        entry, stop, target, actual_rr = adjust_prices_to_mark(
            mark_price=mark_price,
            stop=stop,
            direction=direction,
            rr=rr,
            tick_size=tick_size
        )

        # ========== PASO 3: Calcular quantity ==========
        # Calcular capital a arriesgar (balance * risk_pct)
        # risk_pct viene del paso 1 (UserTradeValidator)
        capital_to_risk = calculate_capital(client, risk_pct)

        # Validar que hay balance suficiente
        if not validate_balance(capital_to_risk, client):
            return {
                "user_id": user_id,
                "success": False,
                "reason": "insufficient_balance"
            }

        # Calcular quantity (capital / distancia_SL)
        sl_distance = abs(entry - stop)
        if sl_distance <= 0:
            return {
                "user_id": user_id,
                "success": False,
                "reason": "invalid_sl_distance"
            }
        qty = capital_to_risk / sl_distance

        # Ajustar al stepSize de Binance
        step_size = float(filters["LOT_SIZE"]["stepSize"])
        qty = adjust_quantity_to_step_size(qty, step_size)

        # Validar quantity (minQty, stepSize, notional)
        if not validate_quantity(qty, entry, filters):
            return {
                "user_id": user_id,
                "success": False,
                "reason": "invalid_quantity"
            }

        # ========== PASO 4: Configurar leverage ==========
        desired_leverage = validator.leverage
        leverage_ok, applied_leverage = set_leverage(symbol, desired_leverage, client, user_id)
        if not leverage_ok:
            return {
                "user_id": user_id,
                "success": False,
                "reason": "leverage_config_failed",
                "desired_leverage": desired_leverage
            }

        # ========== PASOS 5-7: Crear MARKET + SL + TP ==========
        # Esta función maneja:
        #   - Paso 6: Crear orden MARKET
        #   - Paso 7: Crear SL (si falla → emergency close)
        #   - Paso 8: Crear TP (si falla → emergency close)
        trade_result = execute_safe_trade(
            symbol=symbol,
            entry_price=entry,
            stop_loss=stop,
            target_price=target,
            rr=actual_rr,
            direction=direction,
            quantity=qty,
            client=client,
            user_id=user_id
        )

        if not trade_result.get("success"):
            return {
                "user_id": user_id,
                "success": False,
                "reason": f"trade_failed_{trade_result.get('step', 'unknown')}",
                "error": trade_result.get("error"),
                "position_closed": trade_result.get("position_closed", False)
            }

        # ========== PASO 8: Registrar en DB ==========
        saved = save_trade_record(
            # Core
            symbol=symbol,
            user_id=user_id,
            strategy=strategy,
            direction=direction,

            # Order IDs
            order_id=trade_result.get("order_id"),
            sl_order_id=trade_result.get("sl_order_id"),
            tp_order_id=trade_result.get("tp_order_id"),

            # Trade params
            entry_price=entry,
            stop_loss=stop,
            take_profit=target,
            quantity=qty,
            rr=actual_rr,
            leverage=applied_leverage,
            capital_risked=capital_to_risk,

            # Signal quality
            probability=probability,
            ev=request.ev,
            simulated_probability=request.simulated_probability,
            grok_probability=request.grok_probability,

            # Grok metadata
            grok_model=request.grok_model,
            grok_action=request.grok_action,
            grok_confidence=request.grok_confidence,
            grok_risk_level=request.grok_risk_level,
            grok_timing_quality=request.grok_timing_quality,
            grok_key_factor=request.grok_key_factor,

            # Config (reglas del usuario al momento del trade)
            rules=validator.rules,

            # Timestamps
            signal_timestamp=request.timestamp
        )

        return {
            "user_id": user_id,
            "success": True,
            "reason": "trade_created",
            "order_id": trade_result.get("order_id"),
            "sl_order_id": trade_result.get("sl_order_id"),
            "tp_order_id": trade_result.get("tp_order_id"),
            "entry": entry,
            "stop_loss": stop,
            "target": target,
            "quantity": qty,
            "leverage": applied_leverage,
            "rr": actual_rr,
            "saved_to_db": saved
        }

    except Exception as e:
        return {
            "user_id": user_id,
            "success": False,
            "reason": f"error: {str(e)}"
        }
