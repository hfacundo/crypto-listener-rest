# app/utils/binance/validators.py

import time
import traceback
from decimal import Decimal
from app.utils.constants import (
    DEFAULT_RISK_PCT, RISK_PCT, MAX_SLIPPAGE_PCT, MAX_SLIPPAGE, MIN_RR,
    DEFAULT_MIN_RR, SELL, BUY, DEFAULT_ORDER_RETRIES, DEFAULT_DELAY
)
from app.utils.binance.utils import (
    get_dynamic_spread_limits, get_available_usdt_balance,
    adjust_price_to_tick
)
from app.utils.binance.dynamic_rules import (
    get_dynamic_slippage_limits
)
from app.utils.db.query_executor import get_latest_order_id_for_symbol, update_trade_status


# 🔹 Función para calcular saldo usable
def calculate_risk_capital(rules, client):
    """
    Calcula el capital a arriesgar basado en el balance USDT y el porcentaje de riesgo.
    """
    try:
        free_balance = get_available_usdt_balance(client)  # Ya es float
        risk_pct = float(rules.get(RISK_PCT, DEFAULT_RISK_PCT)) / 100
        capital_to_risk = free_balance * risk_pct
        print(f"✅ Balance OK. Required: {capital_to_risk:.2f}, Available: {free_balance:.2f}")
        return capital_to_risk

    except Exception as e:
        print(f"❌ Error calculando capital a riesgo: {e}")
        traceback.print_exc()
        return 0.0

# 🔹 Verifica si existe una orden activa en Binance Futures para el símbolo dado.
def order_exists_for_symbol(symbol, client, user_id: str):
    try:
        open_orders = client.futures_get_open_orders(symbol=symbol)
        return len(open_orders) > 0
    except Exception as e:
        print(f"❌ Error al consultar órdenes abiertas para {symbol} ({user_id}): {e}")
        return False

# 🔹 Valida si el símbolo tiene suficiente liquidez en el order book (profundidad mínima en USDT dentro de cierto margen de precio).
# Ejemplos:
# - min_depth_base = 20,000 significa: Solo opero este símbolo si hay al menos 20,000 USDT entre órdenes de compra y venta cercanas al precio actual.
# - depth_pct = 0.005 → 0.5%
#   Si el mark_price = 10,000, tu rango será:
#   - min_price = 10,000 × (1 - 0.005) = 9950
#   - max_price = 10,000 × (1 + 0.005) = 10,050
def validate_liquidity(symbol, min_depth, depth_pct, order_book, mark_price):

    if min_depth is None:
        print("❌ Falta configuración: 'min_depth_base' en rules.")
        return False

    if depth_pct is None:
        print("❌ Falta configuración: 'depth_pct' en rules.")
        return False

    try:
        print(f"order_book {order_book}")
        bids = order_book.get("bids", [])
        asks = order_book.get("asks", [])

        # Calcular rango superior e inferior permitido
        price_min = mark_price * (1 - depth_pct)
        price_max = mark_price * (1 + depth_pct)

        # Calcular profundidad total dentro del rango en USDT
        def depth_sum(levels):
            return sum(float(p) * float(q) for p, q in levels if price_min <= float(p) <= price_max)

        bid_depth = depth_sum(bids)
        ask_depth = depth_sum(asks)
        total_depth = bid_depth + ask_depth

        #print(f"📊 Profundidad BID: {bid_depth:.2f} USDT, ASK: {ask_depth:.2f} USDT, TOTAL: {total_depth:.2f} USDT")

        if total_depth >= min_depth:
            return True
        else:
            print(f"⚠️ Profundidad insuficiente: {total_depth:.2f} USDT (mínimo requerido: {min_depth})")
            return False

    except Exception as e:
        print(f"❌ Error al validar liquidez para {symbol}: {e}")
        return False

def validate_spread(symbol: str, entry_price: float, filters: dict, order_book: dict, mark_price: float) -> bool:
    book = {
        "bids": order_book["bids"][:5],
        "asks": order_book["asks"][:5]
    }
  
    best_bid = float(book["bids"][0][0])
    best_ask = float(book["asks"][0][0])
    spread = best_ask - best_bid
    spread_pct = spread / entry_price

    limits = get_dynamic_spread_limits(symbol, filters, mark_price)

    if spread > limits["max_spread"]:
        print(f"❌ Spread absoluto ({spread}) excede el máximo permitido ({limits['max_spread']})")
        return False

    if spread_pct > limits["max_spread_pct"]:
        print(f"❌ Spread relativo ({spread_pct:.6f}) excede el máximo permitido ({limits['max_spread_pct']:.6f})")
        return False

    return True

def validate_slippage(symbol: str, entry_price: float, order_book: dict) -> bool:
    book = {
        "bids": order_book["bids"][:5],
        "asks": order_book["asks"][:5]
    }
    if not book:
        print(f"❌ Orderbook no disponible para {symbol}")
        return False

    best_ask = float(book["asks"][0][0]) if book["asks"] else None
    best_bid = float(book["bids"][0][0]) if book["bids"] else None

    if not best_ask or not best_bid:
        print(f"❌ Orderbook incompleto para {symbol} (ask: {best_ask}, bid: {best_bid})")
        return False

    mark_price = (best_ask + best_bid) / 2
    slippage = abs(entry_price - mark_price)

    limits = get_dynamic_slippage_limits(symbol)
    max_slippage = limits.get(MAX_SLIPPAGE)
    max_slippage_pct = limits.get(MAX_SLIPPAGE_PCT)

    print(f"🧪 Validación de slippage para {symbol}") 
    print(f"📈 Entry: {entry_price:.6f}, Mark: {mark_price:.6f}, Slippage: {slippage:.6f}") 
    print(f"🎯 Máx slippage: abs={max_slippage:.6f}, pct={max_slippage_pct:.6f} ({entry_price * max_slippage_pct:.6f})") 

    if slippage > max_slippage or slippage > entry_price * max_slippage_pct:
        print(f"❌ Slippage demasiado alto para {symbol}")
        return False
    
    print(f"✅ Slippage aceptable para {symbol}")
    return True


def adjust_prices_by_slippage(entry_price, stop_loss, target_price, symbol, filters, mark_price):
    """
    Si hay slippage leve, ajusta entry_price al mark_price y reescala SL y TP
    manteniendo la misma distancia relativa.

    Returns:
        tuple: Nuevos (entry_price, stop_loss, target_price)
    """
    try:

        tick_size = float(filters["PRICE_FILTER"]["tickSize"])

        original_sl_distance = abs(entry_price - stop_loss)
        original_tp_distance = abs(target_price - entry_price)

        if entry_price > stop_loss:
            # LONG
            new_sl = mark_price  - original_sl_distance
            new_tp = mark_price  + original_tp_distance
            direction = "LONG"
        else:
            # SHORT
            new_sl = mark_price  + original_sl_distance
            new_tp = mark_price  - original_tp_distance
            direction = "SHORT"

         # Ajustar todos los precios al tickSize permitido
        entry_adj = adjust_price_to_tick(mark_price, tick_size)
        sl_adj    = adjust_price_to_tick(new_sl, tick_size)
        tp_adj    = adjust_price_to_tick(new_tp, tick_size)

        print(f"🔁 Ajuste de precios por slippage para {symbol} [{direction}]")
        print(f"📥 Original entry={entry_price:.4f}, SL={stop_loss:.4f}, TP={target_price:.4f}")
        print(f"📤 Nuevo entry={entry_adj:.4f}, SL={sl_adj:.4f}, TP={tp_adj:.4f}")
        print(f"📏 Distancias SL={original_sl_distance:.4f}, TP={original_tp_distance:.4f}")

        return entry_adj, sl_adj, tp_adj

    except Exception as e:
        print(f"❌ Error ajustando precios por slippage: {e}")
        return entry_price, stop_loss, target_price


def validate_min_rr_again(rr: float, probability: float, rules: dict) -> bool:
    """
    Validates that the risk-reward meets the minimum threshold defined in rules.

    Args:
        rr (float): Risk-reward ratio after slippage/spread.
        probability (float): Probability returned by OpenAI (0-100).
        rules (dict): Dictionary with thresholds like 'min_rr'.

    Returns:
        bool: True if RR is acceptable, False otherwise.
    """
    min_rr = float(rules.get(MIN_RR, DEFAULT_MIN_RR))

    if rr < min_rr:
        print(f"❌ RR={rr:.2f} is below min_rr={min_rr}")
        return False

    print(f"✅ RR={rr:.2f} passed validation")
    return True

def validate_balance(capital_to_risk: float, client) -> bool:
    """
    Validates that the capital to risk does not exceed the available USDT balance.

    Args:
        capital_to_risk (float): Capital to risk based on risk_pct and balance.

    Returns:
        bool: True if balance is sufficient, False otherwise.
    """
    available_balance = get_available_usdt_balance(client)
    
    if capital_to_risk > available_balance:
        print(f"❌ Insufficient balance. Required: {capital_to_risk:.2f}, Available: {available_balance:.2f}")
        return False

    print(f"✅ Balance OK. Required: {capital_to_risk:.2f}, Available: {available_balance:.2f}")
    return True



def validate_symbol_filters(filters: dict, symbol: str) -> bool:
    """
    Verifica que los filtros esenciales estén presentes y tengan valores válidos para operar el símbolo.

    Args:
        filters (dict): Filtros devueltos por get_symbol_filters().
        symbol (str): Nombre del símbolo (ej. BTCUSDT).

    Returns:
        bool: True si los filtros son válidos, False si falta alguno o tiene valores inválidos.
    """
    required_filters = ["LOT_SIZE", "PRICE_FILTER", "MIN_NOTIONAL"]
    
    for ftype in required_filters:
        if ftype not in filters:
            print(f"❌ Falta el filtro '{ftype}' para {symbol}")
            return False

    # Validar campos clave dentro de cada filtro
    try:
        lot_size = filters["LOT_SIZE"]
        price_filter = filters["PRICE_FILTER"]
        min_notional = filters["MIN_NOTIONAL"]

        if float(lot_size["minQty"]) <= 0:
            print(f"❌ minQty inválido para {symbol}")
            return False
        if float(lot_size["stepSize"]) <= 0:
            print(f"❌ stepSize inválido para {symbol}")
            return False
        if float(price_filter["tickSize"]) <= 0:
            print(f"❌ tickSize inválido para {symbol}")
            return False
        if float(min_notional.get("notional", 0)) <= 0:
            print(f"❌ notional mínimo inválido para {symbol}")
            return False

        return True

    except Exception as e:
        print(f"❌ Error al validar filtros de {symbol}: {e}")
        return False

def calculate_quantity(entry_price: float, stop_loss: float, rules: dict, client, capital_to_risk: float = None) -> float:
    """
    Calcula la cantidad de contratos a comprar/vender basándose en el capital a arriesgar
    y la distancia al stop loss.

    Args:
        entry_price (float): Precio de entrada.
        stop_loss (float): Precio de stop loss.
        direction (str): BUY o SELL.
        rules (dict): Contiene el porcentaje de riesgo y otros valores configurables.

    Returns:
        float: Cantidad (qty) de contratos a operar.
    """
    if capital_to_risk is None:
        capital_to_risk = calculate_risk_capital(rules, client)
    if capital_to_risk <= 0:
        print("❌ Capital a riesgo inválido.")
        return 0.0

    entry = Decimal(str(entry_price))
    stop = Decimal(str(stop_loss))

    # Calcula la distancia al SL (siempre positiva)
    distance = abs(entry - stop)

    if distance <= 0:
        print("❌ Distancia entre entry y stop inválida.")
        return 0.0

    # qty = capital / distancia al SL
    qty = Decimal(str(capital_to_risk)) / distance

    return float(qty)

def validate_quantity(qty: float, entry_price: float, filters: dict) -> bool:
    """
    Valida que la cantidad cumpla con minQty, stepSize y minNotional.

    Args:
        qty (float): Cantidad a validar.
        entry_price (float): Precio actual de entrada.
        filters (dict): Filtros de Binance (LOT_SIZE, MIN_NOTIONAL).

    Returns:
        bool: True si es válida, False si no cumple alguna regla.
    """
    try:
        min_qty = Decimal(filters["LOT_SIZE"]["minQty"])
        step_size = Decimal(filters["LOT_SIZE"]["stepSize"])
        notional_min = Decimal(filters["MIN_NOTIONAL"].get("notional", "0"))

        qty_dec = Decimal(str(qty))
        price_dec = Decimal(str(entry_price))
        notional = qty_dec * price_dec

        # Valida cantidad mínima
        if qty_dec < min_qty:
            print(f"❌ qty {qty} es menor que minQty {min_qty}")
            return False

        # Valida múltiplo exacto de stepSize
        rounded_qty = (qty_dec // step_size) * step_size
        if rounded_qty != qty_dec:
            print(f"❌ qty {qty} no es múltiplo exacto de stepSize {step_size}")
            return False

        # Valida notional mínimo (qty * precio)
        if notional < notional_min:
            print(f"❌ Notional {notional:.4f} menor que mínimo {notional_min}")
            return False

        return True

    except Exception as e:
        print(f"❌ Error validando qty: {e}")
        return False

def validate_price_filters(stop_loss: float, target_price: float, filters: dict) -> bool:
    """
    Valida que los precios SL y TP sean múltiplos exactos de tickSize y estén dentro del rango permitido.

    Args:
        entry_price (float): Precio de entrada (solo para referencia).
        stop_loss (float): Precio de stop loss.
        target_price (float): Precio de take profit.
        filters (dict): Filtros de Binance (PRICE_FILTER).

    Returns:
        bool: True si ambos precios son válidos, False si no.
    """
    try:
        tick_size = Decimal(filters["PRICE_FILTER"]["tickSize"])
        min_price = Decimal(filters["PRICE_FILTER"].get("minPrice", "0"))
        max_price = Decimal(filters["PRICE_FILTER"].get("maxPrice", "1000000"))

        for price, label in [(stop_loss, "SL"), (target_price, "TP")]:
            price_dec = Decimal(str(price))

            # Valida múltiplo de tickSize
            rounded_price = (price_dec // tick_size) * tick_size
            if rounded_price != price_dec:
                print(f"❌ {label}={price} no es múltiplo exacto de tickSize={tick_size}")
                return False

            # Valida rango permitido
            if price_dec < min_price or price_dec > max_price:
                print(f"❌ {label}={price} fuera de rango permitido ({min_price} - {max_price})")
                return False

        return True

    except Exception as e:
        print(f"❌ Error validando precios SL/TP: {e}")
        return False

def create_market_order(symbol: str, direction: str, quantity: float, retries: int = 3, delay: int = 2, client=None, user_id: str = None) -> dict:
    """
    Crea una orden de mercado (MARKET) en Binance Futures con reintentos si falla por timeout.

    Args:
        symbol (str): Ej. BTCUSDT
        direction (str): BUY o SELL
        quantity (float): Cantidad a operar
        retries (int): Número máximo de intentos
        delay (int): Segundos a esperar entre intentos

    Returns:
        dict: Diccionario con el resultado de la orden (éxito, orderId, etc.)
    """

    for attempt in range(1, retries + 1):
        try:
            response = client.futures_create_order(
                symbol=symbol,
                side=direction,
                type="MARKET",
                quantity=quantity
            )

            print(f"✅ Orden MARKET ejecutada: {direction} {quantity} {symbol} ({user_id})")
            return {
                "success": True,
                "order": response
            }

        except Exception as e:
            print(f"❌ Intento {attempt} fallido para {symbol} ({user_id}): {e}")
            if attempt < retries:
                print(f"⏳ Reintentando en {delay} segundos...")
                time.sleep(delay)
            else:
                print(f"❌ Todos los intentos fallaron para {symbol}.")

    return {
        "success": False,
        "error": f"Falló después de {retries} intentos."
    }


def create_stop_loss_order(symbol: str, direction: str, stop_price: float, client, user_id: str, working_type: str = "CONTRACT_PRICE") -> dict:
    """
    Crea una orden STOP_MARKET en Binance Futures para cortar pérdidas (Stop Loss).

    Args:
        symbol (str): Ej. "BTCUSDT"
        side (str): "SELL" para posiciones LONG, "BUY" para posiciones SHORT
        stop_price (float): Precio de activación del SL
        client: Cliente de Binance
        user_id (str): ID del usuario
        working_type (str): "CONTRACT_PRICE" (last price - más rápido) o "MARK_PRICE" (mark price - más estable)
                           Default: CONTRACT_PRICE para mejor protección en crashes

    Returns:
        dict: Respuesta de Binance si fue exitosa, None si falló.

    Nota sobre workingType:
        - CONTRACT_PRICE (Last Price): Ejecuta inmediatamente en crashes reales, mejor protección en liquidaciones masivas
        - MARK_PRICE: Más estable, evita wicks pero puede retrasarse en crashes
    """
    try:
        result = client.futures_create_order(
            symbol=symbol,
            side=direction,
            type="STOP_MARKET",
            stopPrice=stop_price,
            closePosition=True,  # ✅ cerrar toda la posición automáticamente
            workingType=working_type,  # CONTRACT_PRICE (last price) o MARK_PRICE
            newOrderRespType="RESULT"
        )
        print(f"✅ Orden STOP_MARKET ({working_type}) creada: {direction} {symbol} ({user_id}) @ {stop_price}")
        return result
    except Exception as e:
        print(f"❌ Error al crear STOP_MARKET para {symbol} ({user_id}): {e}")
        return None

def create_take_profit_order(symbol: str, direction: str, stop_price: float, client, user_id: str):
    """
    Crea una orden TAKE_PROFIT_MARKET en Binance Futures.

    Args:
        symbol (str): Ej. "BTCUSDT"
        side (str): BUY o SELL
        stop_price (float): Precio que activa el take profit.
        quantity (float): Cantidad a vender/comprar.

    Returns:
        dict: Respuesta de Binance o None si hay error.
    """

    try:
        response = client.futures_create_order(
            symbol=symbol,
            side=direction,
            type="TAKE_PROFIT_MARKET",
            stopPrice=round(stop_price, 4),
            closePosition=True,  # ✅ NUEVO
            workingType="MARK_PRICE",  # ✅ NUEVO
            newOrderRespType="RESULT"  # ✅ NUEVO
        )
        print(f"✅ Orden TAKE_PROFIT_MARKET ({user_id}) (closePosition=True) creada: {direction} {symbol} @ {stop_price}")
        return response
    except Exception as e:
        print(f"❌ Error al crear orden TAKE_PROFIT para {symbol} ({user_id}): {e}")
        return None

def create_safe_trade_with_sl_tp(
    symbol: str,
    entry_price: float,
    stop_loss: float,
    target_price: float,
    rr: float,
    direction: str,
    rules: dict,
    quantity: float,
    client,
    user_id: str
) -> dict:
    """
    Crea una operación segura en Binance Futures con orden MARKET + SL + TP.
    Si alguna orden SL o TP falla, cancela la entrada para evitar quedar expuesto.
    """

    print(f"\n🚀 Iniciando create_safe_trade_with_sl_tp para {symbol} ({user_id})")
    print(f"📊 Entry: {entry_price}, SL: {stop_loss}, TP: {target_price}, RR: {rr}, Dir: {direction}")
    print(f"📋 Rules: {rules}")

    # Paso 1: Crear orden MARKET
    # symbol: str, direction: str, quantity: float, retries: int = 3, delay: int = 2
    retries = DEFAULT_ORDER_RETRIES
    delay = DEFAULT_DELAY
    result = create_market_order(symbol=symbol, direction=direction, quantity=quantity, retries=retries, delay=delay, client=client, user_id=user_id)
    print(f"📤 Resultado MARKET ({user_id}):")
    print(result)

    if not result.get("success"):
        print(f"❌ Error al crear orden MARKET ({user_id}).")
        return {
            "success": False,
            "step": "MARKET_ORDER",
            "error": result.get("error")
        }

    order_data = result["order"]
    order_id = order_data.get("orderId")
    print(f"⏳ Esperando que orden {order_id} esté FILLED...")

    try:
        for i in range(retries):
            order_status = client.futures_get_order(symbol=symbol, orderId=order_id)
            print(f"🔍 Intento {i+1}/{retries}: Estado actual = {order_status.get('status')}")
            if order_status.get("status") == "FILLED":
                print(f"✅ Orden MARKET ejecutada:\n{order_status}")
                break
            time.sleep(1)
        else:
            print("⚠️ Timeout esperando ejecución de orden MARKET.")
            return {
                "success": False,
                "step": "WAIT_MARKET_FILL",
                "error": "Timeout esperando FILLED"
            }

        # Paso 2: Crear Stop Loss
        sl_direction = SELL if direction == BUY else BUY
        print(f"📉 Intentando crear STOP LOSS ({user_id}) en {stop_loss} ({sl_direction})")
        sl_result = create_stop_loss_order(symbol, sl_direction, stop_loss, client, user_id)
        print(f"🛑 Resultado SL ({user_id}):")
        print(sl_result)

        if not sl_result:
            print(f"❌ SL falló. Cancelando orden original {order_id}")
            client.futures_cancel_order(symbol=symbol, orderId=order_id)
            return {
                "success": False,
                "step": "STOP_LOSS",
                "error": "Error al crear SL. Orden cancelada.",
                "entry_order_id": order_id
            }

        # Paso 3: Crear Take Profit
        print(f"🎯 Intentando crear TAKE PROFIT en {target_price} ({sl_direction})")
        tp_result = create_take_profit_order(symbol, sl_direction, target_price, client, user_id)
        print(f"🎯 Resultado TP ({user_id}):")
        print(tp_result)

        if not tp_result:
            print(f"❌ TP falló. Cancelando orden original {order_id} ({user_id})")
            client.futures_cancel_order(symbol=symbol, orderId=order_id)
            return {
                "success": False,
                "step": "TAKE_PROFIT",
                "error": "Error al crear TP. Orden cancelada.",
                "entry_order_id": order_id
            }

        sl_order_id = sl_result.get("orderId") if sl_result else None
        tp_order_id = tp_result.get("orderId") if tp_result else None
        print("✅ Operación completada con SL y TP creados.")
        return {
            "success": True,
            "step": "ALL_OK",
            "order_id": order_id,
            "tp_order_id": tp_order_id,
            "sl_order_id": sl_order_id,
            "entry_price": entry_price,
            "stop_loss": stop_loss,
            "target_price": target_price,
            "rr": rr
        }

    except Exception as e:
        print(f"❌ Excepción inesperada: {e}")
        return {
            "success": False,
            "step": "EXCEPTION",
            "error": str(e),
            "entry_order_id": order_id
        }

def cancel_orphan_orders(symbol: str, client, user_id: str):
    """
    Función principal que intenta cancelar órdenes huérfanas si no hay posición abierta.

    Args:
        symbol (str): Ejemplo "BTCUSDT"
    """
    order_id = get_latest_order_id_for_symbol(symbol, user_id)
    if not order_id:
        print(f"⚠️ No se encontró order_id reciente en BD para {symbol}")
        return

    cancel_orphan_orders_if_position_closed(symbol, client, user_id)


def cancel_orphan_orders_if_position_closed(symbol: str, client, user_id: str):
    """
    Si no hay posición abierta en Binance, cancela órdenes SL/TP huérfanas.

    Args:
        symbol (str): Ejemplo "BTCUSDT"
    """

    try:
        # Verificar si hay posición activa
        positions = client.futures_position_information(symbol=symbol)
        if not positions or float(positions[0]["positionAmt"]) == 0.0:
            print(f"🔍 Sin posición activa para {symbol}, revisando órdenes abiertas...")

            open_orders = client.futures_get_open_orders(symbol=symbol)

            if not open_orders:
                print(f"❔ No hay órdenes abiertas para {symbol}, posible cierre manual.")
                # Confirmamos que había order_id en BD antes de esta llamada
                update_trade_status(symbol, user_id, status="close_manual")
                return
            
            for order in open_orders:
                if order["type"] in ["STOP_MARKET", "TAKE_PROFIT_MARKET"]:
                    print(f"🧹 Cancelando orden huérfana {order['type']} (ID: {order['orderId']}) de {user_id}")
                    client.futures_cancel_order(symbol=symbol, orderId=order["orderId"])

                    # Actualizar status del trade
                    if order["type"] == "STOP_MARKET":
                        update_trade_status(symbol, user_id, status="success")
                    elif order["type"] == "TAKE_PROFIT_MARKET":
                        update_trade_status(symbol, user_id, status="fail")
        else:
            print(f"✅ Posición activa detectada para {symbol} ({user_id}), no se cancelan órdenes.")

    except Exception as e:
        print(f"❌ Error al cancelar órdenes huérfanas para {symbol} ({user_id}): {e}")