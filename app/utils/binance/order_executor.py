"""
order_executor.py - Ejecución segura de órdenes en Binance Futures

Funciones:
  - create_market_order: Crear orden MARKET con reintentos
  - create_stop_loss_order: Crear SL via Algo Order API
  - create_take_profit_order: Crear TP via Algo Order API
  - emergency_close_position: Cierre de emergencia con múltiples estrategias
  - verify_position_closed: Verificar que posición está cerrada
  - execute_safe_trade: Crear MARKET + SL + TP con protección completa

USADO EN: trade_executor.py (versión nueva)
"""

import time
import traceback

from app.utils.logger_config import get_logger
from app.utils.config.config_constants import BUY, SELL

logger = get_logger()

# Constantes
DEFAULT_ORDER_RETRIES = 3
DEFAULT_DELAY = 2


def create_market_order(
    symbol: str,
    direction: str,
    quantity: float,
    client,
    user_id: str,
    retries: int = DEFAULT_ORDER_RETRIES,
    delay: int = DEFAULT_DELAY
) -> dict:
    """
    Crea una orden MARKET en Binance Futures con reintentos.

    Args:
        symbol: Par de trading (ej: BTCUSDT)
        direction: BUY o SELL
        quantity: Cantidad a operar
        client: Cliente de Binance
        user_id: ID del usuario
        retries: Número máximo de intentos
        delay: Segundos entre intentos

    Returns:
        dict: {"success": bool, "order": dict} o {"success": False, "error": str}
    """
    for attempt in range(1, retries + 1):
        try:
            response = client.futures_create_order(
                symbol=symbol,
                side=direction,
                type="MARKET",
                quantity=quantity
            )

            logger.info(f"[{symbol}] Orden MARKET ejecutada: {direction} {quantity} ({user_id})")
            return {
                "success": True,
                "order": response
            }

        except Exception as e:
            logger.warning(f"[{symbol}] Intento {attempt}/{retries} fallido ({user_id}): {e}")
            if attempt < retries:
                time.sleep(delay)

    return {
        "success": False,
        "error": f"MARKET order falló después de {retries} intentos"
    }


def create_stop_loss_order(
    symbol: str,
    direction: str,
    stop_price: float,
    client,
    user_id: str,
    working_type: str = "CONTRACT_PRICE"
) -> dict:
    """
    Crea orden STOP_MARKET via Algo Order API.

    Args:
        symbol: Par de trading
        direction: SELL para LONG, BUY para SHORT
        stop_price: Precio de activación del SL
        client: Cliente de Binance
        user_id: ID del usuario
        working_type: CONTRACT_PRICE (más rápido) o MARK_PRICE (más estable)

    Returns:
        dict: Respuesta de Binance o None si falla
    """
    try:
        params = {
            "symbol": symbol,
            "side": direction,
            "algoType": "CONDITIONAL",
            "type": "STOP_MARKET",
            "triggerPrice": stop_price,
            "closePosition": "true",
            "workingType": working_type,
            "timestamp": int(time.time() * 1000)
        }

        result = client._request_futures_api('post', 'algoOrder', signed=True, data=params)
        logger.info(f"[{symbol}] SL creado via Algo API: {direction} @ {stop_price} ({user_id})")
        return result

    except Exception as e:
        logger.error(f"[{symbol}] Error al crear SL ({user_id}): {e}")
        traceback.print_exc()
        return None


def create_take_profit_order(
    symbol: str,
    direction: str,
    target_price: float,
    client,
    user_id: str
) -> dict:
    """
    Crea orden TAKE_PROFIT_MARKET via Algo Order API.

    Args:
        symbol: Par de trading
        direction: SELL para LONG, BUY para SHORT
        target_price: Precio de activación del TP
        client: Cliente de Binance
        user_id: ID del usuario

    Returns:
        dict: Respuesta de Binance o None si falla
    """
    try:
        params = {
            "symbol": symbol,
            "side": direction,
            "algoType": "CONDITIONAL",
            "type": "TAKE_PROFIT_MARKET",
            "triggerPrice": round(target_price, 4),
            "closePosition": "true",
            "workingType": "MARK_PRICE",
            "timestamp": int(time.time() * 1000)
        }

        result = client._request_futures_api('post', 'algoOrder', signed=True, data=params)
        logger.info(f"[{symbol}] TP creado via Algo API: {direction} @ {target_price} ({user_id})")
        return result

    except Exception as e:
        logger.error(f"[{symbol}] Error al crear TP ({user_id}): {e}")
        traceback.print_exc()
        return None


def verify_position_closed(symbol: str, client, user_id: str) -> bool:
    """
    Verifica que una posición está cerrada.

    Returns:
        bool: True si posición cerrada (positionAmt == 0)
    """
    try:
        positions = client.futures_position_information(symbol=symbol)
        position_amt = float(positions[0]['positionAmt']) if positions else 0

        if position_amt == 0:
            logger.info(f"[{symbol}] Posición verificada cerrada ({user_id})")
            return True
        else:
            logger.error(f"[{symbol}] Posición aún abierta: {position_amt} ({user_id})")
            return False

    except Exception as e:
        logger.error(f"[{symbol}] Error verificando posición ({user_id}): {e}")
        return False


def get_current_position_amt(symbol: str, client, user_id: str) -> float:
    """
    Obtiene el positionAmt actual de una posición.

    Returns:
        float: Cantidad de la posición (0 si no hay posición o error)
    """
    try:
        positions = client.futures_position_information(symbol=symbol)
        return float(positions[0]['positionAmt']) if positions else 0.0
    except Exception as e:
        logger.error(f"[{symbol}] Error obteniendo posición ({user_id}): {e}")
        return 0.0


def emergency_close_position(
    symbol: str,
    direction: str,
    quantity: float,
    client,
    user_id: str,
    max_retries: int = 5
) -> bool:
    """
    Cierra posición de emergencia cuando falla SL/TP.

    Estrategias:
      1. closePosition=True (Binance cierra toda la posición)
      2. Fallback: reduceOnly con quantity
      3. Verificación post-cierre
      4. Log crítico si todo falla

    Args:
        symbol: Par de trading
        direction: Dirección ORIGINAL del trade (BUY o SELL)
        quantity: Cantidad de la posición
        client: Cliente de Binance
        user_id: ID del usuario
        max_retries: Máximo de reintentos

    Returns:
        bool: True si se cerró, False si falló
    """
    close_direction = SELL if direction == BUY else BUY

    logger.warning(f"[{symbol}] EMERGENCIA: Iniciando cierre de posición ({user_id})")

    # ========== ESTRATEGIA 1: closePosition=True ==========
    for attempt in range(1, max_retries + 1):
        try:
            logger.warning(f"[{symbol}] Intento {attempt}/{max_retries} con closePosition=True")

            client.futures_create_order(
                symbol=symbol,
                side=close_direction,
                type="MARKET",
                closePosition=True
            )

            time.sleep(1)
            if verify_position_closed(symbol, client, user_id):
                logger.info(f"[{symbol}] Posición cerrada exitosamente ({user_id})")
                return True

        except Exception as e:
            logger.error(f"[{symbol}] Intento {attempt} falló: {e}")
            if attempt < max_retries:
                wait_time = min(2 ** attempt, 10)  # Backoff: 2s, 4s, 8s, 10s, 10s
                time.sleep(wait_time)

    # ========== ESTRATEGIA 2: reduceOnly con quantity ==========
    logger.warning(f"[{symbol}] Fallback: reduceOnly con quantity={quantity}")

    for attempt in range(1, 3):
        try:
            client.futures_create_order(
                symbol=symbol,
                side=close_direction,
                type="MARKET",
                quantity=quantity,
                reduceOnly=True
            )

            time.sleep(1)
            if verify_position_closed(symbol, client, user_id):
                logger.info(f"[{symbol}] Posición cerrada con reduceOnly ({user_id})")
                return True

        except Exception as e:
            logger.error(f"[{symbol}] reduceOnly intento {attempt} falló: {e}")
            if attempt < 2:
                time.sleep(2)

    # ========== TODO FALLÓ ==========
    logger.critical(f"[{symbol}] CRÍTICO: NO SE PUDO CERRAR POSICIÓN ({user_id})")
    logger.critical(f"[{symbol}] Direction: {direction}, Quantity: {quantity}")
    logger.critical(f"[{symbol}] ACCIÓN MANUAL REQUERIDA - POSICIÓN SIN SL/TP")

    return False


def execute_safe_trade(
    symbol: str,
    entry_price: float,
    stop_loss: float,
    target_price: float,
    rr: float,
    direction: str,
    quantity: float,
    client,
    user_id: str
) -> dict:
    """
    Ejecuta trade seguro: MARKET + SL + TP.

    GARANTÍA: Si SL o TP falla, la posición se cierra.

    Flujo:
      1. Crear orden MARKET
      2. Esperar FILLED (con protección de timeout)
      3. Crear SL (si falla → cerrar posición)
      4. Crear TP (si falla → cerrar posición)

    Args:
        symbol: Par de trading
        entry_price: Precio de entrada (para logging)
        stop_loss: Precio del stop loss
        target_price: Precio del take profit
        rr: Risk/Reward ratio (para logging)
        direction: BUY o SELL
        quantity: Cantidad a operar
        client: Cliente de Binance
        user_id: ID del usuario

    Returns:
        dict: {
            "success": bool,
            "step": str,  # MARKET_ORDER, WAIT_FILL, STOP_LOSS, TAKE_PROFIT, ALL_OK
            "order_id": str,
            "sl_order_id": str,
            "tp_order_id": str,
            ...
        }
    """
    logger.info(f"[{symbol}] Iniciando execute_safe_trade ({user_id})")
    logger.debug(f"[{symbol}] Entry: {entry_price}, SL: {stop_loss}, TP: {target_price}, Dir: {direction}")

    order_id = None
    retries = DEFAULT_ORDER_RETRIES

    # ========== PASO 1: Crear orden MARKET ==========
    result = create_market_order(
        symbol=symbol,
        direction=direction,
        quantity=quantity,
        client=client,
        user_id=user_id
    )

    if not result.get("success"):
        return {
            "success": False,
            "step": "MARKET_ORDER",
            "error": result.get("error")
        }

    order_data = result["order"]
    order_id = order_data.get("orderId")

    # ========== PASO 2: Esperar FILLED ==========
    try:
        order_filled = False
        for i in range(retries):
            order_status = client.futures_get_order(symbol=symbol, orderId=order_id)
            status = order_status.get("status")
            logger.debug(f"[{symbol}] Intento {i+1}/{retries}: status={status}")

            if status == "FILLED":
                logger.info(f"[{symbol}] Orden MARKET FILLED ({user_id})")
                order_filled = True
                break

            time.sleep(1)

        # FIX: Si timeout, verificar si hay posición abierta antes de retornar
        if not order_filled:
            logger.warning(f"[{symbol}] Timeout esperando FILLED, verificando posición...")

            position_amt = get_current_position_amt(symbol, client, user_id)

            if position_amt != 0:
                # HAY POSICIÓN - intentar cerrar
                logger.error(f"[{symbol}] Posición detectada ({position_amt}) sin confirmación FILLED. Cerrando...")
                closed = emergency_close_position(symbol, direction, abs(position_amt), client, user_id)

                return {
                    "success": False,
                    "step": "WAIT_FILL_TIMEOUT",
                    "error": "Timeout esperando FILLED, posición cerrada por seguridad",
                    "order_id": order_id,
                    "position_closed": closed
                }
            else:
                # No hay posición - la orden probablemente no se ejecutó
                return {
                    "success": False,
                    "step": "WAIT_FILL_TIMEOUT",
                    "error": "Timeout esperando FILLED, no se detectó posición",
                    "order_id": order_id
                }

        # ========== PASO 3: Crear Stop Loss ==========
        sl_direction = SELL if direction == BUY else BUY
        sl_result = create_stop_loss_order(symbol, sl_direction, stop_loss, client, user_id)

        if not sl_result:
            logger.error(f"[{symbol}] SL falló. Cerrando posición ({user_id})")
            closed = emergency_close_position(symbol, direction, quantity, client, user_id)

            return {
                "success": False,
                "step": "STOP_LOSS",
                "error": "SL falló. Posición cerrada." if closed else "SL falló. CRÍTICO: No se pudo cerrar.",
                "order_id": order_id,
                "position_closed": closed
            }

        # ========== PASO 4: Crear Take Profit ==========
        tp_result = create_take_profit_order(symbol, sl_direction, target_price, client, user_id)

        if not tp_result:
            logger.error(f"[{symbol}] TP falló. Cerrando posición ({user_id})")
            closed = emergency_close_position(symbol, direction, quantity, client, user_id)

            return {
                "success": False,
                "step": "TAKE_PROFIT",
                "error": "TP falló. Posición cerrada." if closed else "TP falló. CRÍTICO: No se pudo cerrar.",
                "order_id": order_id,
                "position_closed": closed
            }

        # ========== TODO OK ==========
        sl_order_id = sl_result.get("algoId") or sl_result.get("orderId")
        tp_order_id = tp_result.get("algoId") or tp_result.get("orderId")

        logger.info(f"[{symbol}] Trade completado con SL y TP ({user_id})")

        return {
            "success": True,
            "step": "ALL_OK",
            "order_id": order_id,
            "sl_order_id": sl_order_id,
            "tp_order_id": tp_order_id,
            "entry_price": entry_price,
            "stop_loss": stop_loss,
            "target_price": target_price,
            "rr": rr
        }

    except Exception as e:
        logger.error(f"[{symbol}] Excepción inesperada: {e}")
        traceback.print_exc()

        # Intentar cerrar si hay posición
        position_closed = False
        if order_id:
            position_amt = get_current_position_amt(symbol, client, user_id)
            if position_amt != 0:
                logger.error(f"[{symbol}] Excepción con posición abierta. Cerrando ({user_id})")
                position_closed = emergency_close_position(symbol, direction, abs(position_amt), client, user_id)

        return {
            "success": False,
            "step": "EXCEPTION",
            "error": str(e),
            "order_id": order_id,
            "position_closed": position_closed
        }
