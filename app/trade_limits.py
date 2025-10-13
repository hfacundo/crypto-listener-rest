# app/trade_limits.py

from typing import Dict, Tuple, List
from app.utils.binance.binance_client import get_binance_client_for_user
import logging

logger = logging.getLogger(__name__)


def parse_rule_value(rules: Dict, rule_name: str, default_value=None, value_type=str):
    """
    Helper para parsear valores de rules de manera segura

    Args:
        rules: Dict de rules resultado de get_rules()
        rule_name: Nombre de la regla
        default_value: Valor por defecto
        value_type: Tipo esperado (str, int, float, bool)

    Returns:
        Valor parseado o default_value
    """
    try:
        raw_value = rules.get(rule_name)
        if raw_value is None:
            return default_value

        if value_type == int:
            return int(raw_value)
        elif value_type == float:
            return float(raw_value)
        elif value_type == bool:
            if isinstance(raw_value, str):
                return raw_value.lower() in ('true', '1', 'yes', 'on')
            return bool(raw_value)
        else:
            return str(raw_value)

    except (ValueError, TypeError) as e:
        logger.warning(f"Error parsing rule {rule_name} with value {raw_value}: {e}")
        return default_value


def get_open_positions_count(user_id: str) -> Tuple[int, List[str]]:
    """
    Obtiene el número de posiciones abiertas para un usuario

    Returns:
        Tuple[int, List[str]]: (count, list_of_symbols)
    """
    try:
        client = get_binance_client_for_user(user_id)
        positions = client.futures_position_information()

        open_positions = []
        for pos in positions:
            position_amt = float(pos.get("positionAmt", "0"))
            if abs(position_amt) > 0:
                symbol = pos.get("symbol", "")
                open_positions.append(symbol)

        return len(open_positions), open_positions

    except Exception as e:
        logger.error(f"Error getting open positions for {user_id}: {e}")
        return 0, []


def get_open_orders_count(user_id: str) -> Tuple[int, List[str]]:
    """
    Obtiene el número de órdenes abiertas para un usuario
    (útil si se quiere contar pending orders en lugar de positions)

    Returns:
        Tuple[int, List[str]]: (count, list_of_symbols)
    """
    try:
        client = get_binance_client_for_user(user_id)
        orders = client.futures_get_open_orders()

        symbols_with_orders = list(set([order.get("symbol", "") for order in orders]))

        return len(symbols_with_orders), symbols_with_orders

    except Exception as e:
        logger.error(f"Error getting open orders for {user_id}: {e}")
        return 0, []


def check_trade_limit(user_id: str, rules: Dict, new_symbol: str = None) -> Tuple[bool, str, Dict]:
    """
    Verifica si el usuario puede abrir un nuevo trade según sus límites

    Args:
        user_id: ID del usuario
        rules: Reglas del usuario (resultado de get_rules)
        new_symbol: Símbolo del nuevo trade (opcional)

    Returns:
        Tuple[bool, str, Dict]: (can_trade, reason, info)
    """
    try:
        # Obtener max_trades_open usando helper
        max_trades = parse_rule_value(rules, "max_trades_open", 999, int)
        if max_trades >= 999:
            return True, "no_limit_configured", {}

        # Obtener count_method usando helper
        count_method = parse_rule_value(rules, "count_method", "positions", str)

        # Obtener conteo actual
        if count_method == "orders":
            current_count, current_list = get_open_orders_count(user_id)
            count_type = "orders"
        else:
            current_count, current_list = get_open_positions_count(user_id)
            count_type = "positions"

        # Verificar si ya existe posición en este símbolo
        symbol_exists = False
        if new_symbol and new_symbol.upper() in [s.upper() for s in current_list]:
            symbol_exists = True

        info = {
            "current_count": current_count,
            "max_allowed": max_trades,
            "count_type": count_type,
            "current_symbols": current_list,
            "symbol_exists": symbol_exists
        }

        # Casos de validación
        if symbol_exists:
            return False, f"position_already_exists_for_{new_symbol}", info

        if current_count >= max_trades:
            return False, f"max_trades_exceeded_{current_count}/{max_trades}", info

        return True, "within_limits", info

    except Exception as e:
        logger.error(f"Error checking trade limit for {user_id}: {e}")
        return True, f"limit_check_error_{str(e)}", {}  # Allow trade on error


def get_trade_limit_summary(user_id: str, rules: Dict) -> Dict:
    """
    Obtiene un resumen del estado de límites para un usuario

    Args:
        rules: Resultado de get_rules() con estructura {rule_name: rule_value}

    Returns:
        Dict con información detallada de límites
    """
    try:
        # Parsear valores usando helper
        max_trades = parse_rule_value(rules, "max_trades_open", 999, int)
        count_method = parse_rule_value(rules, "count_method", "positions", str)

        if count_method == "orders":
            current_count, current_list = get_open_orders_count(user_id)
        else:
            current_count, current_list = get_open_positions_count(user_id)

        remaining_slots = max(0, max_trades - current_count) if max_trades < 999 else 999
        utilization_pct = (current_count / max_trades * 100) if max_trades < 999 else 0

        return {
            "user_id": user_id,
            "max_trades_configured": max_trades,
            "current_count": current_count,
            "remaining_slots": remaining_slots,
            "utilization_percentage": round(utilization_pct, 1),
            "count_method": count_method,
            "current_symbols": current_list,
            "is_at_limit": current_count >= max_trades if max_trades < 999 else False,
            "status": "AT_LIMIT" if current_count >= max_trades and max_trades < 999
                     else "NEAR_LIMIT" if utilization_pct > 80
                     else "NORMAL"
        }

    except Exception as e:
        logger.error(f"Error getting trade limit summary for {user_id}: {e}")
        return {
            "user_id": user_id,
            "error": str(e),
            "status": "ERROR"
        }


def log_trade_limit_status(user_id: str, rules: Dict, symbol: str = None):
    """
    Log del estado de límites para debugging
    """
    try:
        summary = get_trade_limit_summary(user_id, rules)

        if summary.get("error"):
            logger.warning(f"⚠️ {user_id} - Trade limit check error: {summary['error']}")
            return

        status = summary["status"]
        current = summary["current_count"]
        max_configured = summary["max_trades_configured"]

        if max_configured >= 999:
            logger.debug(f"📊 {user_id} - No trade limit configured")
        elif status == "AT_LIMIT":
            logger.warning(f"🚫 {user_id} - AT LIMIT: {current}/{max_configured} trades open")
        elif status == "NEAR_LIMIT":
            logger.info(f"⚠️ {user_id} - NEAR LIMIT: {current}/{max_configured} trades open ({summary['utilization_percentage']}%)")
        else:
            logger.info(f"✅ {user_id} - Within limits: {current}/{max_configured} trades open")

        if symbol:
            if symbol.upper() in [s.upper() for s in summary["current_symbols"]]:
                logger.warning(f"🔄 {user_id} - Position already exists for {symbol}")

        if summary["current_symbols"]:
            logger.debug(f"📋 {user_id} - Current positions: {', '.join(summary['current_symbols'])}")

    except Exception as e:
        logger.error(f"Error logging trade limit status for {user_id}: {e}")


def suggest_position_to_close(user_id: str, rules: Dict) -> Dict:
    """
    Sugiere qué posición cerrar cuando se alcanza el límite
    (función avanzada para futuras mejoras)
    """
    try:
        summary = get_trade_limit_summary(user_id, rules)

        if not summary.get("is_at_limit"):
            return {"suggestion": "no_action_needed"}

        # Lógica básica: sugerir la posición más antigua o menos rentable
        # (esto se puede expandir con más inteligencia)

        current_symbols = summary.get("current_symbols", [])
        if not current_symbols:
            return {"suggestion": "no_positions_found"}

        # Por ahora, sugerir el primer símbolo (se puede mejorar con P&L analysis)
        return {
            "suggestion": "close_oldest_position",
            "recommended_symbol": current_symbols[0],
            "reason": "oldest_position_heuristic",
            "all_positions": current_symbols
        }

    except Exception as e:
        logger.error(f"Error suggesting position to close for {user_id}: {e}")
        return {"suggestion": "error", "error": str(e)}