# app/market_validation.py

import time
from typing import Dict, Any, Tuple, Optional
from app.utils.binance.binance_client import get_binance_client_for_user


def get_fresh_market_data(symbol: str, user_id: str) -> Dict[str, Any]:
    """
    Obtiene datos frescos de mercado para validación usando cache cuando sea posible.
    Fallback a API si cache no disponible.
    """
    try:
        from app.utils.binance.binance_cache_client import get_binance_cache_client
        from app.utils.binance.utils import get_mark_price

        client = get_binance_client_for_user(user_id)
        cache_client = get_binance_cache_client()

        # Mark price con cache (30s TTL)
        mark_price = get_mark_price(symbol.upper(), client)

        # Orderbook liviano con cache (30s TTL)
        orderbook_data = cache_client.get_orderbook_data(symbol.upper(), depth_limit=20, client=client, max_age=30)
        if orderbook_data and "bids" in orderbook_data and "asks" in orderbook_data:
            orderbook = orderbook_data
        else:
            # Fallback a API
            orderbook = client.futures_order_book(symbol=symbol.upper(), limit=20)

        best_bid = float(orderbook["bids"][0][0]) if orderbook["bids"] else 0
        best_ask = float(orderbook["asks"][0][0]) if orderbook["asks"] else 0
        spread_pct = ((best_ask - best_bid) / best_ask * 100) if best_ask > 0 else 0

        return {
            "mark_price": mark_price,
            "best_bid": best_bid,
            "best_ask": best_ask,
            "spread_pct": round(spread_pct, 4),
            "timestamp": time.time(),
            "data_source": "fresh_api"
        }

    except Exception as e:
        print(f"❌ Error getting fresh market data for {symbol}: {e}")
        return {
            "mark_price": 0,
            "error": str(e),
            "timestamp": time.time(),
            "data_source": "error"
        }


def validate_guardian_decision_freshness(message: Dict[str, Any],
                                       fresh_data: Dict[str, Any]) -> Tuple[bool, str, Dict[str, Any]]:
    """
    Valida si la decisión del guardian sigue siendo válida con datos frescos

    Returns:
        Tuple[bool, str, Dict]: (is_valid, reason, adjusted_params)
    """
    try:
        market_context = message.get("market_context", {})
        action = message.get("action", "").lower()

        trigger_price = market_context.get("trigger_price", 0)
        trigger_timestamp = market_context.get("timestamp", 0)
        max_drift_pct = message.get("price_scenarios", {}).get("max_acceptable_drift_pct", 1.0)

        current_price = fresh_data.get("mark_price", 0)
        current_timestamp = fresh_data.get("timestamp", time.time())

        if trigger_price == 0 or current_price == 0:
            return True, "no_price_data_for_validation", {}

        # Calcular drifts
        price_drift_pct = abs(current_price - trigger_price) / trigger_price * 100
        time_drift_sec = current_timestamp - trigger_timestamp

        print(f"🔍 Validation: price_drift={price_drift_pct:.3f}%, time_drift={time_drift_sec:.1f}s")

        # Validaciones específicas por tipo de acción
        if action == "close":
            # CLOSE: más tolerante a price drift, urgencia alta
            if time_drift_sec > 60:  # >1 minuto = muy stale
                return False, f"close_too_stale_{time_drift_sec:.1f}s", {}
            if price_drift_pct > 2.0:  # >2% cambio de precio
                print(f"⚠️ Significant price drift for CLOSE, but still executing: {price_drift_pct:.3f}%")
            return True, "close_validated", {}

        elif action == "adjust":
            # ADJUST: recalcular stop si precio drifted
            if price_drift_pct > max_drift_pct:
                # Usar pre-calculated scenarios
                adjusted_stop = get_adjusted_stop_from_scenarios(message, current_price, trigger_price)
                if adjusted_stop:
                    return True, f"stop_recalculated_drift_{price_drift_pct:.3f}%", {"stop": adjusted_stop}
                else:
                    return False, f"adjust_drift_too_high_{price_drift_pct:.3f}%", {}

            if time_drift_sec > 45:  # >45s para adjust
                return False, f"adjust_too_stale_{time_drift_sec:.1f}s", {}

            return True, "adjust_validated", {}

        elif action == "half_close":
            # HALF_CLOSE: Validación MÍNIMA - solo verificar que el trade sigue en profit
            # Guardian ya validó que tocó el 50% usando velas de 1m
            # Aquí solo nos aseguramos que no esté en pérdida

            # Validación de tiempo: más tolerante (90 segundos)
            if time_drift_sec > 90:
                return False, f"half_close_too_stale_{time_drift_sec:.1f}s", {}

            # Obtener entry y side del mensaje para validar profit
            entry = message.get("entry", 0)
            side = message.get("side", "").upper()

            if not entry or not side:
                # Sin datos de entry/side, permitir ejecución (backward compatibility)
                print("⚠️ Half-close without entry/side, allowing execution (legacy)")
                return True, "half_close_validated_legacy", {}

            # Validar que el trade sigue en profit (no importa si retrocedió del 50% al 40%)
            if side == "BUY":
                if current_price <= entry:
                    return False, f"half_close_no_profit_buy_price_{current_price:.6f}_entry_{entry:.6f}", {}
                profit_pct = ((current_price - entry) / entry) * 100
                print(f"✅ Half-close BUY validated: price={current_price:.6f}, entry={entry:.6f}, profit={profit_pct:.3f}%")

            else:  # SELL
                if current_price >= entry:
                    return False, f"half_close_no_profit_sell_price_{current_price:.6f}_entry_{entry:.6f}", {}
                profit_pct = ((entry - current_price) / entry) * 100
                print(f"✅ Half-close SELL validated: price={current_price:.6f}, entry={entry:.6f}, profit={profit_pct:.3f}%")

            return True, "half_close_validated", {}

        else:
            return True, "unknown_action_defaulted", {}

    except Exception as e:
        print(f"❌ Error validating guardian decision: {e}")
        return True, f"validation_error_{str(e)}", {}  # Default to allow execution


def get_adjusted_stop_from_scenarios(message: Dict[str, Any], current_price: float,
                                   trigger_price: float) -> Optional[float]:
    """
    Obtiene stop ajustado usando los scenarios pre-calculados
    """
    try:
        scenarios = message.get("price_scenarios", {})
        original_stop = scenarios.get("original_stop", 0)

        if original_stop == 0:
            return None

        # Determinar qué scenario usar basado en price drift
        price_change_pct = (current_price - trigger_price) / trigger_price * 100

        if 0.4 <= abs(price_change_pct) <= 0.6:
            # Usar scenario 0.5%
            if price_change_pct > 0:
                return scenarios.get("if_price_up_0_5_pct", original_stop)
            else:
                return scenarios.get("if_price_down_0_5_pct", original_stop)

        elif 0.8 <= abs(price_change_pct) <= 1.2:
            # Usar scenario 1%
            if price_change_pct > 0:
                return scenarios.get("if_price_up_1_pct", original_stop)
            else:
                return scenarios.get("if_price_down_1_pct", original_stop)

        else:
            # Fuera de scenarios pre-calculados, usar original
            return original_stop

    except Exception as e:
        print(f"❌ Error getting adjusted stop from scenarios: {e}")
        return None


def should_proceed_with_execution(action: str, validation_result: Tuple[bool, str, Dict]) -> bool:
    """
    Determina si se debe proceder con la ejecución basado en validación
    """
    is_valid, reason, adjusted_params = validation_result

    if is_valid:
        return True

    # Reglas especiales para casos edge
    if action == "close":
        # CLOSE siempre ejecuta salvo casos extremos
        if "too_stale" in reason and "60" in reason:  # >60s stale
            return False
        return True  # Execute close even with some drift

    elif action == "adjust":
        # ADJUST solo si se puede recalcular o drift es menor
        if "recalculated" in reason:
            return True
        if "too_high" in reason:
            return False
        return True

    elif action == "half_close":
        # HALF_CLOSE moderadamente estricto
        return False  # Si no válida, no ejecutar

    return is_valid