# app/utils/binance/futures.py
import sys
import os
from decimal import Decimal
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app.utils.binance.validators import (
    validate_liquidity,
    validate_spread,
    validate_slippage,
    adjust_prices_by_slippage,
    validate_min_rr_again,
    validate_balance,
    validate_symbol_filters,
    calculate_quantity,
    validate_quantity,
    validate_price_filters,
    create_safe_trade_with_sl_tp,
    calculate_risk_capital,
    cancel_orphan_orders,
    order_exists_for_symbol,
    create_stop_loss_order,
    create_take_profit_order
)

# Imports para PostgreSQL y Redis
try:
    from app.utils.db.redis_client import get_redis_client
except ImportError:
    get_redis_client = None

try:
    from app.utils.trade_protection import TradeProtectionSystem
except ImportError:
    TradeProtectionSystem = None
    print("⚠️ TradeProtectionSystem not available")
from app.utils.binance.utils import (
    set_leverage,
    get_symbol_filters,
    adjust_quantity_to_step_size,
    get_mark_price
)
from app.utils.binance.dynamic_rules import (
    adjust_base_depth_and_depth_pct_for_symbol
)
from app.utils.constants import (
    MIN_DEPTH_BASE, DEPTH_PCT, MAX_LEVERAGE, DEFAULT_MAX_LEVERAGE
)
from app.utils.db.query_executor import save_trade

from app.utils.binance.utils import adjust_price_to_tick
# from app.utils.message_sns import send_message  # Removed: PostgreSQL/Redis handle state, SNS not needed

def create_order(symbol, entry_price, stop_loss, target_price, direction, rr, probability, rules, client, user_id, capital_to_risk=None, leverage_override=None):
    #1. Obtener y validar filtros de Binance (LOT_SIZE, PRICE_FILTER, etc.)
    filters = get_symbol_filters(symbol, client)
    print(f"{symbol} filters:", filters)
    if not validate_symbol_filters(filters, symbol):
        print(f"❌ Filtros inválidos para {symbol} ({user_id})")
        return {"success": False, "error": "Invalid symbol filters"}

    # 2. Ajustar dinámicamente profundidad mínima y profundidad relativa
    # Obtener orderbook DIRECTO desde Binance API (sin cache Redis)
    order_book = client.futures_order_book(symbol=symbol, limit=100)
    print(f"📘 Orderbook obtenido desde API: bids={len(order_book.get('bids', []))}, asks={len(order_book.get('asks', []))}")

    # Obtener mark_price con cache (la función get_mark_price en utils.py ya usa cache)
    mark_price = get_mark_price(symbol, client)
    
    depth_config = adjust_base_depth_and_depth_pct_for_symbol(symbol, client, order_book, mark_price)
    min_depth = depth_config[MIN_DEPTH_BASE]
    depth_pct = depth_config[DEPTH_PCT]
    print(f"depth_config - min_depth {min_depth}, depth_pct {depth_pct}")

    # 3. Validar liquidez (profundidad mínima)
    if not validate_liquidity(symbol, min_depth, depth_pct, order_book, mark_price, client):
        print(f"❌ Liquidez insuficiente para {symbol} ({user_id})")
        return {"success": False, "error": "Insufficient liquidity"}

    # 4. Validar spread absoluto y relativo
    if not validate_spread(symbol, entry_price, filters, order_book, mark_price):
        print(f"❌ Spread demasiado alto para {symbol} ({user_id})")
        return {"success": False, "error": "Spread too high"}

    # 5. Validar slippage absoluto y relativo y ajustar precios si es necesario
    if not validate_slippage(symbol, entry_price, order_book):
        print(f"❌ Slippage demasiado alto para {symbol} ({user_id})")
        return {"success": False, "error": "Slippage too high"}
    else:
        entry_price, stop_loss, target_price = adjust_prices_by_slippage(
            entry_price, stop_loss, target_price, symbol, filters, mark_price
        )

    # 6. Validar RR mínimo después del ajuste de precios
    if not validate_min_rr_again(rr, probability, rules):
        print(f"❌ RR ajustado ya no cumple el mínimo para {symbol} ({user_id})")
        return {"success": False, "error": "Adjusted RR below minimum"}
    
    # 7. Usar capital ajustado o calcular capital base
    if capital_to_risk is None:
        capital_to_risk = calculate_risk_capital(rules, client)
        print(f"💰 Using base capital: {capital_to_risk:.2f}")
    else:
        print(f"💰 Using SQS-adjusted capital: {capital_to_risk:.2f}")

    if not validate_balance(capital_to_risk, client):
        print(f"❌ Balance insuficiente para operar {symbol} ({user_id})")
        return {"success": False, "error": "Insufficient balance"}
    

    # 8. Calcular cantidad de contrato (qty) con capital SQS-ajustado
    qty = calculate_quantity(entry_price, stop_loss, rules, client, capital_to_risk)
    step_size = float(filters["LOT_SIZE"]["stepSize"])
    qty = adjust_quantity_to_step_size(qty, step_size)
    if not validate_quantity(qty, entry_price, filters):
        print(f"❌ Cantidad inválida para {symbol} ({user_id})")
        return {"success": False, "error": "Invalid quantity"}

    # 9. Validar precios SL/TP con PRICE_FILTER y tickSize
    if not validate_price_filters(stop_loss, target_price, filters):
        print(f"❌ SL o TP fuera de rango permitido para {symbol}")
        return {"success": False, "error": "Invalid SL or TP price"}

    # 10. Ajustar leverage según reglas (o usar override de test mode)
    desired_leverage = leverage_override if leverage_override else rules.get(MAX_LEVERAGE, DEFAULT_MAX_LEVERAGE)
    if leverage_override:
        print(f"🧪 Using test leverage override: {leverage_override}x for {symbol} ({user_id})")

    success, applied_leverage = set_leverage(symbol, desired_leverage, client, user_id)
    if not success:
        print(f"❌ No se pudo establecer apalancamiento para {symbol} ({user_id})")
        return {"success": False, "error": "Failed to set leverage"}

    # 11. Crear orden de entrada y SL/TP
    trade_result = create_safe_trade_with_sl_tp(
        symbol,
        entry_price,
        stop_loss,
        target_price,
        rr,
        direction,
        rules,
        qty,
        client,
        user_id    
    )

    if not trade_result.get("success"):
        return trade_result

    # 12. Retornar estructura final
    return {
        "success": True,
        "order_id": trade_result.get("order_id"),
        "tp_order_id": trade_result.get("tp_order_id"),
        "sl_order_id": trade_result.get("sl_order_id"),
        "entry": entry_price,
        "stop_loss": stop_loss,
        "target": target_price,
        "quantity": qty,  # NUEVO: retornar quantity para registro
        "capital_risked": capital_to_risk,
        "leverage": applied_leverage
    }


def create_trade(symbol, entry_price, stop_loss, target_price, direction, rr, probability, rules, client, user_id, strategy, signal_quality_score=0, capital_multiplier=1.0, leverage_override=None):
    """
    Crea una orden de trading segura con stop loss y take profit.
    Valida todos los parámetros y ajusta según las reglas dinámicas.
    """
    symbol = symbol.upper()
    print(f"🔄 Validando trade para {symbol}...")

    # 1. Cancelar órdenes TP o SL remanentes
    cancel_orphan_orders(symbol, client, user_id)

    # Validar RR mínimo (la probabilidad ya fue validada por SQS)
    if rr < rules.get("min_rr"):
        print(f"❌ RR por debajo del mínimo para {symbol}")
        return {"success": False, "error": "RR below minimum"}

    # NOTA: La validación de probabilidad se realiza previamente en SQSEvaluator usando sqs_config.absolute_minimums
    # No se valida aquí porque SQSEvaluator ya aprobó/rechazó el trade según su configuración de probabilidad mínima

    # 2. Verificar si ya existe una posición abierta
    if order_exists_for_symbol(symbol, client, user_id):
            print(f"⚠️ Ya existe una posición abierta para {symbol} ({user_id}), se omite operación.")
            return {"success": False, "error": f"Ya existe una posición abierta para {symbol} ({user_id}), se omite operación."}

    # 1. Validar precios iniciales
    if not entry_price or not stop_loss or not target_price:
        print(f"❌ Precios inválidos para {symbol} ({user_id})")
        return {"success": False, "error": "Invalid entry/stop/target prices"}

    # 3. Calcular capital ajustado por SQS
    base_capital = calculate_risk_capital(rules, client)
    capital_to_risk = base_capital * capital_multiplier

    # 🧪 TEST MODE: Pasar leverage override si existe
    if leverage_override:
        print(f"🧪 Test mode: Using leverage override {leverage_override}x (user: {user_id})")

    # 4. Crear la orden con capital ajustado por SQS y leverage override (si existe)
    order_result = create_order(symbol, entry_price, stop_loss, target_price, direction, rr, probability, rules, client, user_id, capital_to_risk, leverage_override)
    
    if order_result is None or not order_result.get("success") or "order_id" not in order_result:
        print(f"⚠️ Orden fallida, no se guardará: {order_result} para {symbol}")
        return {"success": False, "error": f"Orden fallida para {symbol}"}


    # Trade info already saved in PostgreSQL and Redis by main.py
    # No need to send SNS - crypto-guardian reads from PostgreSQL/Redis
    capital_risked = order_result.get("capital_risked")
    leverage_used = order_result.get("leverage")

    print(f"✅ Trade creado exitosamente para {symbol} (saved in PostgreSQL & Redis)")

    # Retornar datos completos para registro en PostgreSQL
    return {
        "success": True,
        "message": f"Trade creado exitosamente para {symbol} con TP/SL",
        "quantity": order_result.get("quantity", 0),
        "capital_used": capital_risked,
        "order_id": order_result.get("order_id"),
        "sl_order_id": order_result.get("sl_order_id"),  # NUEVO
        "tp_order_id": order_result.get("tp_order_id"),  # NUEVO
        "entry_price": entry_price,
        "stop_loss": stop_loss,
        "target_price": target_price
    }
    # else:
    #     print(f"❌ Error al guardar trade en la base de datos para {symbol}")
    #     return {"success": False, "error": "Failed to save trade in database"}


# NOTE: get_symbol_filters is imported from app.utils.binance.utils (line 40)
# Fetches filters directly from Binance API (no caching needed on EC2)

# NEW: helper to read current position amount (+long / -short / 0)
def _get_position_amt(symbol: str, client) -> float:
    pos = client.futures_position_information(symbol=symbol)
    if not pos:
        return 0.0
    return float(pos[0]["positionAmt"])


def _update_trade_in_postgresql(symbol: str, user_id: str, strategy: str, exit_price: float, exit_reason: str, pnl: float, client) -> bool:
    """
    Actualiza el trade en PostgreSQL cuando se cierra una posición.
    Obtiene el trade_id de Redis y actualiza el resultado en PostgreSQL.

    Args:
        symbol: El símbolo del trade (ej: BTCUSDT)
        user_id: ID del usuario
        strategy: Nombre de la estrategia
        exit_price: Precio de salida
        exit_reason: Razón del cierre ('stop_loss', 'take_profit', 'guardian_close', 'half_close', 'manual')
        pnl: PnL en USDT (debe obtenerse ANTES de cerrar la posición)
        client: Cliente de Binance

    Returns:
        bool: True si se actualizó exitosamente, False si no
    """
    if not TradeProtectionSystem or not get_redis_client:
        return False

    try:
        redis_client = get_redis_client()
        if not redis_client:
            return False

        # Obtener trade_id de Redis
        trade_key = f"trade_id:{user_id}:{symbol.upper()}"
        trade_id = redis_client.get(trade_key)

        if not trade_id:
            print(f"⚠️ No trade_id found in Redis for {symbol} ({user_id})")
            return False

        trade_id = trade_id.decode('utf-8') if isinstance(trade_id, bytes) else trade_id

        # Actualizar en PostgreSQL (PNL ya calculado ANTES de cerrar)
        protection_system = TradeProtectionSystem()

        success = protection_system.update_trade_exit(
            user_id=user_id,
            strategy=strategy,
            trade_id=trade_id,
            exit_price=exit_price,
            exit_reason=exit_reason,
            pnl=pnl  # ← PNL ya obtenido ANTES de cerrar
        )

        if success:
            print(f"📝 Trade {trade_id} updated in PostgreSQL: exit={exit_price:.2f}, reason={exit_reason}, pnl={pnl:.2f}")
            # Limpiar Redis
            redis_client.delete(trade_key)

        return success

    except Exception as e:
        print(f"⚠️ Error updating trade in PostgreSQL: {e}")
        return False

# NEW: close (reduce-only) + cleanup SL/TP
def close_position_and_cancel_orders(symbol: str, client, user_id: str, strategy: str = "archer_model") -> dict:
    """
    Cierra completamente una posición abierta y cancela todas las órdenes pendientes.
    Actualiza el trade en PostgreSQL con el resultado.

    Args:
        symbol: Símbolo del trade
        client: Cliente de Binance
        user_id: ID del usuario
        strategy: Nombre de la estrategia (default: "archer_model")

    Returns:
        dict con success, order_id o error
    """
    try:
        position_amt = _get_position_amt(symbol, client)
        if position_amt == 0:
            return {"success": True, "message": "No open position to close"}

        # IMPORTANTE: Obtener PNL ANTES de cerrar (después será 0)
        pos_info = client.futures_position_information(symbol=symbol)
        unrealized_pnl = 0.0
        if pos_info:
            unrealized_pnl = float(pos_info[0].get("unRealizedProfit", 0))

        # Obtener precio actual antes de cerrar (para PostgreSQL)
        mark_price = get_mark_price(symbol, client)

        side = "SELL" if position_amt > 0 else "BUY"
        qty = abs(position_amt)

        resp = client.futures_create_order(
            symbol=symbol,
            side=side,
            type="MARKET",
            quantity=qty,
            reduceOnly=True
        )

        # Actualizar PostgreSQL con el cierre (PNL obtenido ANTES de cerrar)
        _update_trade_in_postgresql(
            symbol=symbol,
            user_id=user_id,
            strategy=strategy,
            exit_price=mark_price,
            exit_reason="guardian_close",
            pnl=unrealized_pnl,  # ← PNL real obtenido ANTES de cerrar
            client=client
        )

        # Best-effort cleanup of any resting conditional orders
        cancel_orphan_orders(symbol, client, user_id)

        # DUAL WRITE CLEANUP: Eliminar guardian trade de Redis
        try:
            redis_client = get_redis_client()
            if redis_client:
                guardian_key = f"guardian:trades:{user_id}:{symbol.upper()}"
                redis_client.delete(guardian_key)
                print(f"🧹 Guardian trade removed from Redis: {guardian_key}")
        except Exception as e:
            print(f"⚠️ Error removing guardian trade from Redis: {e}")

        return {"success": True, "order_id": resp.get("orderId")}
    except Exception as e:
        return {"success": False, "error": f"close_position failed: {e}"}

# NEW: adjust SL/TP for current position (replaces existing SL/TP safely)
def adjust_sl_tp_for_open_position(symbol: str, new_stop: float, new_target: float, client, user_id: str) -> dict:
    try:
        # 1) Get filters and normalize to tick size
        filters = get_symbol_filters(symbol, client)
        tick_size = float(filters["PRICE_FILTER"]["tickSize"])
        stop_r   = adjust_price_to_tick(new_stop, tick_size)
        target_r = adjust_price_to_tick(new_target, tick_size)

        # 2) Validate price filters (multiple of tick & in-range)
        if not validate_price_filters(stop_r, target_r, filters):
            return {"success": False, "error": "SL/TP outside PRICE_FILTER bounds"}

        # 3) Ensure there's a position and detect direction
        position_amt = _get_position_amt(symbol, client)
        if position_amt == 0:
            return {"success": False, "error": "No open position to adjust"}

        direction = "BUY" if position_amt > 0 else "SELL"
        mark_price = get_mark_price(symbol, client)

        # Basic directional sanity checks to avoid inverted targets
        if direction == "BUY" and not (stop_r < mark_price <= target_r):
            return {"success": False, "error": "Invalid levels for LONG (expect stop < mark <= target)"}
        if direction == "SELL" and not (target_r <= mark_price < stop_r):
            return {"success": False, "error": "Invalid levels for SHORT (expect target <= mark < stop)"}

        # 4) Cancel existing conditional exits (avoid duplicates/conflicts)
        cancel_orphan_orders(symbol, client, user_id)

        # 5) Recreate SL/TP as closePosition orders using your validators helpers
        exit_side = "SELL" if direction == "BUY" else "BUY"

        sl_res = create_stop_loss_order(symbol, exit_side, stop_r, client, user_id)
        if not sl_res:
            return {"success": False, "error": "Failed to create STOP_MARKET"}

        tp_res = create_take_profit_order(symbol, exit_side, target_r, client, user_id)
        if not tp_res:
            return {"success": False, "error": "Failed to create TAKE_PROFIT_MARKET"}

        return {
            "success": True,
            "direction": direction,
            "stop": stop_r,
            "target": target_r
        }
    except Exception as e:
        return {"success": False, "error": f"adjust_sl_tp failed: {e}"}


def adjust_stop_only_for_open_position(symbol: str, new_stop: float, client, user_id: str, level_metadata: dict = None) -> dict:
    """
    Ajusta SOLO el Stop Loss de una posición abierta:
      - Detecta dirección por positionAmt (BUY si >0, SELL si <0)
      - Obtiene el STOP_MARKET actual desde órdenes abiertas
      - Enforce: tighten-only (nunca aflojar el SL)
      - Cancela el/los STOP_MARKET actuales y crea uno nuevo
      - No toca el TP existente
      - Actualiza Redis con tracking de nivel aplicado (si level_metadata provisto)

    Args:
        symbol: Trading pair
        new_stop: New stop price
        client: Binance client
        user_id: User ID
        level_metadata: Optional dict with trailing stop level info
            {
                "level_name": "break_even",
                "level_threshold_pct": 35,
                "previous_level": "towards_be_20"
            }

    Returns:
        Dict with success, stop, level_applied, redis_updated, etc.
    """
    try:
        # 0) Validar que haya posición
        positions = client.futures_position_information(symbol=symbol)
        if not positions or float(positions[0].get("positionAmt", "0")) == 0.0:
            return {"success": False, "error": "No open position to adjust"}

        position_amt = float(positions[0]["positionAmt"])
        direction = "BUY" if position_amt > 0 else "SELL"
        exit_side = "SELL" if direction == "BUY" else "BUY"

        # 1) Normalizar new_stop a tick y validar rango
        filters = get_symbol_filters(symbol, client)
        tick_size = Decimal(str(filters["PRICE_FILTER"]["tickSize"]))
        min_price = Decimal(str(filters["PRICE_FILTER"].get("minPrice", "0")))
        max_price = Decimal(str(filters["PRICE_FILTER"].get("maxPrice", "100000000")))

        ns = Decimal(str(new_stop))
        # redondear a múltiplo exacto de tick
        ns_rounded = (ns // tick_size) * tick_size
        if ns_rounded != ns:
            ns = ns_rounded

        if ns < min_price or ns > max_price:
            return {"success": False, "error": f"Stop {ns} outside PRICE_FILTER bounds"}

        new_stop_f = float(ns)

        # 2) Encontrar STOP_MARKET actual (tanto en órdenes tradicionales como Algo Orders)
        open_orders = client.futures_get_open_orders(symbol=symbol)
        current_stop = None
        stop_orders = []  # Lista de tuplas: (order_dict, is_algo_order)

        # 2a) Buscar en órdenes tradicionales
        for o in open_orders:
            if o.get("type") == "STOP_MARKET":
                stop_orders.append((o, False))  # False = no es Algo Order
                try:
                    current_stop = float(o.get("stopPrice"))
                except Exception:
                    pass

        # 2b) Buscar en Algo Orders (nuevo endpoint desde 2025-12-09)
        try:
            # ✅ CORREGIDO: usar 'openAlgoOrders' (con O mayúscula)
            algo_response = client._request_futures_api('get', 'openAlgoOrders', signed=True, data={"symbol": symbol})
            algo_orders = []
            if isinstance(algo_response, dict) and "openOrders" in algo_response:
                algo_orders = algo_response["openOrders"]
            elif isinstance(algo_response, list):
                algo_orders = algo_response

            for algo_order in algo_orders:
                order_type = algo_order.get("algoType") or algo_order.get("type", "")
                if order_type in ["STOP_MARKET", "STOP"]:
                    stop_orders.append((algo_order, True))  # True = es Algo Order
                    try:
                        current_stop = float(algo_order.get("stopPrice", 0))
                    except Exception:
                        pass
        except Exception as e:
            print(f"⚠️ Could not fetch Algo Orders for {symbol}: {e}")

        if current_stop is None:
            # No hay SL existente -> permitir crear uno directamente (pero aún validar sanity)
            pass
        else:
            # 3) Regla tighten-only
            if direction == "BUY":
                # para LONG, solo permitir SL >= current_stop
                if new_stop_f < current_stop:
                    return {"success": False, "error": f"Looser stop not allowed (current {current_stop}, new {new_stop_f})"}
            else:
                # para SHORT, solo permitir SL <= current_stop
                if new_stop_f > current_stop:
                    return {"success": False, "error": f"Looser stop not allowed (current {current_stop}, new {new_stop_f})"}

        # 4) Chequeo de sanidad respecto al mark actual
        mark_price = get_mark_price(symbol, client)
        if direction == "BUY" and not (new_stop_f < mark_price):
            return {"success": False, "error": "Invalid SL for LONG (expected new_stop < mark)"}
        if direction == "SELL" and not (new_stop_f > mark_price):
            return {"success": False, "error": "Invalid SL for SHORT (expected new_stop > mark)"}

        # 5) Cancelar SOLO los STOP_MARKET actuales (dejar TP intacto)
        # stop_orders contiene tuplas: (order_dict, is_algo_order)
        for order_tuple in stop_orders:
            order, is_algo = order_tuple
            try:
                if is_algo:
                    # Cancelar Algo Order
                    algo_id = order.get("algoId")
                    if algo_id:
                        client._request_futures_api('delete', 'algoOrder', signed=True, data={"symbol": symbol, "algoId": algo_id})
                        print(f"✅ Cancelled Algo STOP_MARKET (algoId: {algo_id})")
                else:
                    # Cancelar orden tradicional
                    client.futures_cancel_order(symbol=symbol, orderId=order["orderId"])
                    print(f"✅ Cancelled traditional STOP_MARKET (orderId: {order['orderId']})")
            except Exception as e:
                order_id = order.get("algoId") if is_algo else order.get("orderId")
                print(f"⚠️ Could not cancel STOP_MARKET {order_id}: {e}")

        # 6) Crear nuevo STOP_MARKET (closePosition=True)
        sl_res = create_stop_loss_order(symbol, exit_side, new_stop_f, client, user_id)
        if not sl_res:
            return {"success": False, "error": "Failed to create new STOP_MARKET"}

        # 7) Actualizar Redis para que crypto-guardian use el valor actualizado
        import json
        import time

        # Extraer metadata del nivel (si disponible)
        level_name = "manual_adjust"
        if level_metadata:
            level_name = level_metadata.get("level_name", "manual_adjust")
            previous_level = level_metadata.get("previous_level")
        else:
            previous_level = None

        redis_updated = False
        redis_client = None

        try:
            redis_client = get_redis_client()
            if redis_client:
                guardian_key = f"guardian:trades:{user_id}:{symbol.upper()}"
                trade_data = redis_client.get(guardian_key)

                if trade_data:
                    trade_dict = json.loads(trade_data)

                    # Guardar previous_level antes de actualizar
                    previous_level_from_redis = trade_dict.get('ts_level_applied')

                    # CRÍTICO: Preservar original_stop si ya existe, o guardarlo ahora
                    if 'original_stop' not in trade_dict:
                        # Primera vez: guardar el stop actual como original
                        trade_dict['original_stop'] = current_stop if current_stop else new_stop_f

                    # Actualizar campos básicos (NUNCA modificar original_stop)
                    trade_dict['stop'] = new_stop_f
                    trade_dict['stop_loss'] = new_stop_f  # Compatibilidad con light_check.py y decisions.py

                    # Actualizar campos de tracking multinivel
                    trade_dict['ts_level_applied'] = level_name
                    trade_dict['ts_last_adjustment_ts'] = time.time()
                    trade_dict['ts_last_adjustment_stop'] = new_stop_f
                    trade_dict['ts_previous_stop'] = current_stop if current_stop else new_stop_f
                    trade_dict['ts_previous_level'] = previous_level or previous_level_from_redis

                    # Intentar guardar en Redis
                    redis_client.setex(guardian_key, 7*24*3600, json.dumps(trade_dict))
                    redis_updated = True
                    print(f"✅ Updated guardian trade stop in Redis: {guardian_key} -> {new_stop_f} (level: {level_name})")
                else:
                    print(f"⚠️ Guardian trade not found in Redis: {guardian_key}")

        except Exception as e:
            # Intentar retry una vez
            print(f"⚠️ Could not update guardian trade in Redis (first attempt): {e}")

            try:
                if redis_client:
                    time.sleep(0.5)  # Esperar 500ms
                    redis_client = get_redis_client()  # Obtener nuevo cliente

                    if redis_client:
                        guardian_key = f"guardian:trades:{user_id}:{symbol.upper()}"
                        trade_data = redis_client.get(guardian_key)

                        if trade_data:
                            trade_dict = json.loads(trade_data)

                            # CRÍTICO: Preservar original_stop si ya existe
                            if 'original_stop' not in trade_dict:
                                trade_dict['original_stop'] = current_stop if current_stop else new_stop_f

                            # Actualizar todos los campos (NUNCA modificar original_stop)
                            trade_dict['stop'] = new_stop_f
                            trade_dict['stop_loss'] = new_stop_f
                            trade_dict['ts_level_applied'] = level_name
                            trade_dict['ts_last_adjustment_ts'] = time.time()
                            trade_dict['ts_last_adjustment_stop'] = new_stop_f
                            trade_dict['ts_previous_stop'] = current_stop if current_stop else new_stop_f
                            trade_dict['ts_previous_level'] = previous_level

                            redis_client.setex(guardian_key, 7*24*3600, json.dumps(trade_dict))
                            redis_updated = True
                            print(f"✅ Redis update succeeded on retry: {guardian_key} -> {new_stop_f}")
            except Exception as retry_error:
                print(f"❌ CRITICAL: Redis update failed on retry: {retry_error}")
                print(f"   Binance updated successfully but Redis sync failed")
                print(f"   Manual verification recommended for {user_id}/{symbol}")

        # Retornar respuesta enriquecida
        return {
            "success": True,
            "direction": direction,
            "stop": new_stop_f,
            "level_applied": level_name,
            "previous_stop": current_stop,
            "adjustment_confirmed": True,
            "redis_updated": redis_updated,
            "timestamp": time.time()
        }

    except Exception as e:
        print("Exception in adjust_stop_only_for_open_position:", e)
        return {"success": False, "error": f"adjust_stop_only failed: {e}"}


def half_close_and_move_be(symbol: str, client, user_id: str) -> dict:
    """
    1) Close 50% of the current position (reduceOnly MARKET).
    2) Move the remaining position's Stop Loss to Break-Even (entryPrice).
    """
    try:
        sym = symbol.upper()

        # --- Read current position ---
        pos = client.futures_position_information(symbol=sym)
        if not pos or float(pos[0].get("positionAmt", "0")) == 0.0:
            return {"success": False, "error": "No open position to half-close"}

        position_amt = float(pos[0]["positionAmt"])  # >0 long, <0 short
        be_price_raw = float(pos[0].get("entryPrice", "0") or 0.0)
        if be_price_raw <= 0:
            return {"success": False, "error": "Invalid entryPrice for BE"}

        # --- Compute 50% qty using LOT_SIZE step ---
        filters = get_symbol_filters(sym, client)
        step_size = float(filters["LOT_SIZE"]["stepSize"])
        qty_half_raw = abs(position_amt) * 0.5

        # Use current mark for validation
        mark_price = get_mark_price(sym, client)

        qty_half = adjust_quantity_to_step_size(qty_half_raw, step_size)
        if not validate_quantity(qty_half, mark_price, filters):
            return {"success": False, "error": f"Half qty invalid after step-size: {qty_half}"}
        if qty_half <= 0.0:
            return {"success": False, "error": "Half qty rounded to zero"}

        # --- Send reduceOnly MARKET to close half ---
        reduce_side = "SELL" if position_amt > 0 else "BUY"
        resp = client.futures_create_order(
            symbol=sym,
            side=reduce_side,
            type="MARKET",
            quantity=qty_half,
            reduceOnly=True
        )

        # --- Re-check remaining position ---
        pos2 = client.futures_position_information(symbol=sym)
        if not pos2:
            return {"success": False, "error": "Position info not available after half-close"}
        remaining_amt = float(pos2[0].get("positionAmt", "0"))
        if abs(remaining_amt) < 1e-12:
            # Fully closed after rounding or partial execution
            cancel_orphan_orders(sym, client, user_id)
            return {"success": True, "message": "Half-close done and position fully closed"}

        # --- Compute BE stop for the remaining position ---
        # Use the updated entryPrice if exchange recalculated it after partial close
        be_price = float(pos2[0].get("entryPrice", be_price_raw) or be_price_raw)
        tick = Decimal(str(filters["PRICE_FILTER"]["tickSize"]))
        be_price_dec = Decimal(str(be_price))

        # Ensure SL is on the correct side of current mark (required by Binance)
        current_mark = Decimal(str(get_mark_price(sym, client)))
        # Direction from remaining position:
        direction = "BUY" if remaining_amt > 0 else "SELL"

        # Adjust 1 tick if needed to satisfy "new_stop < mark" (LONG) or "new_stop > mark" (SHORT)
        if direction == "BUY":
            if be_price_dec >= current_mark:
                be_price_dec = current_mark - tick
        else:  # SELL
            if be_price_dec <= current_mark:
                be_price_dec = current_mark + tick

        new_stop_f = float(be_price_dec)

        # --- Tighten-only SL move to BE ---
        res_sl = adjust_stop_only_for_open_position(sym, new_stop_f, client, user_id)
        if not res_sl or not res_sl.get("success"):
            # Not fatal: maybe SL already tighter than BE, or price relation invalid
            return {
                "success": True,
                "message": "Half-close done; BE stop unchanged",
                "half_close_order_id": resp.get("orderId"),
                "adjust_stop": res_sl
            }

        return {
            "success": True,
            "message": "Half-close done; SL moved to BE",
            "half_close_order_id": resp.get("orderId"),
            "new_stop": res_sl.get("stop")
        }

    except Exception as e:
        print("Exception in half_close_and_move_be:", e)
        return {"success": False, "error": f"half_close_and_move_be failed: {e}"}


if __name__ == "__main__":
    symbol = "BTCUSDT"
    entry_price = get_mark_price(symbol)            # puedes ajustar a precio real
    target_price = entry_price * 1.05
    stop_loss = entry_price * 0.95
    direction = "BUY"
    rr = 1.0
    probability = 75
    rules = {
        "min_rr": 1.0,
        "risk_pct": 3.5,
        "max_leverage": 125
    }

    # imprime la información de la orden
    print(f"Creando orden para {symbol}...")
    print(f"Entry Price: {entry_price}, Stop Loss: {stop_loss}, Target Price: {target_price}")
    print(f"Direction: {direction}, RR: {rr}, Probability: {probability}")
    print(f"Rules: {rules}")    
    
    """
    symbol, entry_price, stop_loss, target_price, direction, rr, probability, rules
    """
    create_trade(symbol, entry_price, stop_loss, target_price, direction, rr, probability, rules)
