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
def validate_liquidity(symbol, min_depth, depth_pct, order_book, mark_price, client=None):
    """
    Valida si el símbolo tiene suficiente liquidez en el order book.

    Args:
        symbol: Símbolo a validar
        min_depth: Profundidad mínima requerida en USDT
        depth_pct: Porcentaje de rango de precio para evaluar profundidad
        order_book: Orderbook (formato Binance: {"bids": [[p,q],...], "asks": [[p,q],...]})
        mark_price: Precio de referencia
        client: Cliente de Binance (opcional, para fallback si orderbook inválido)

    Returns:
        bool: True si la liquidez es suficiente
    """
    if min_depth is None:
        print("❌ Falta configuración: 'min_depth_base' en rules.")
        return False

    if depth_pct is None:
        print("❌ Falta configuración: 'depth_pct' en rules.")
        return False

    try:
        # Validar formato del orderbook
        if not isinstance(order_book, dict):
            print(f"⚠️ Orderbook inválido (no es dict): {type(order_book)}")
            order_book = _fetch_orderbook_fallback(symbol, client)
            if not order_book:
                return False

        bids = order_book.get("bids", [])
        asks = order_book.get("asks", [])

        # Validar que bids/asks sean listas y no estén vacías
        if not isinstance(bids, list) or not isinstance(asks, list):
            print(f"⚠️ Orderbook con formato incorrecto - bids/asks no son listas")
            order_book = _fetch_orderbook_fallback(symbol, client)
            if not order_book:
                return False
            bids = order_book.get("bids", [])
            asks = order_book.get("asks", [])

        if len(bids) == 0 or len(asks) == 0:
            print(f"⚠️ Orderbook vacío - bids: {len(bids)}, asks: {len(asks)}")
            order_book = _fetch_orderbook_fallback(symbol, client)
            if not order_book:
                return False
            bids = order_book.get("bids", [])
            asks = order_book.get("asks", [])

        # Calcular rango superior e inferior permitido
        price_min = mark_price * (1 - depth_pct)
        price_max = mark_price * (1 + depth_pct)

        print(f"📊 Validando liquidez {symbol}: range [{price_min:.4f} - {price_max:.4f}], bids={len(bids)}, asks={len(asks)}")

        # Calcular profundidad total dentro del rango en USDT
        def depth_sum(levels):
            total = 0
            for level in levels:
                try:
                    if not isinstance(level, (list, tuple)) or len(level) < 2:
                        continue
                    price = float(level[0])
                    qty = float(level[1])
                    if price_min <= price <= price_max:
                        total += price * qty
                except (ValueError, TypeError, IndexError):
                    continue
            return total

        bid_depth = depth_sum(bids)
        ask_depth = depth_sum(asks)
        total_depth = bid_depth + ask_depth

        print(f"📊 Profundidad BID: {bid_depth:.2f} USDT, ASK: {ask_depth:.2f} USDT, TOTAL: {total_depth:.2f} USDT")

        if total_depth >= min_depth:
            print(f"✅ Liquidez suficiente: {total_depth:.2f} USDT >= {min_depth:.2f} USDT")
            return True
        else:
            print(f"⚠️ Profundidad insuficiente: {total_depth:.2f} USDT (mínimo requerido: {min_depth:.2f} USDT)")
            return False

    except Exception as e:
        print(f"❌ Error al validar liquidez para {symbol}: {e}")
        traceback.print_exc()
        return False


def _fetch_orderbook_fallback(symbol, client):
    """
    Función helper para obtener orderbook directamente de Binance API como fallback.
    Usa depth limit granular optimizado según liquidez del símbolo.

    Args:
        symbol: Símbolo a consultar
        client: Cliente de Binance

    Returns:
        dict: Orderbook o None si falla
    """
    if not client:
        print(f"❌ No se puede hacer fallback a API - client no disponible")
        return None

    try:
        # Usar depth limit granular optimizado (igual que crypto-analyzer-redis)
        depth_limit = _get_depth_limit_granular(symbol)

        print(f"🔄 Fallback: Obteniendo orderbook fresh desde Binance API para {symbol} (depth={depth_limit})")
        order_book = client.futures_order_book(symbol=symbol.upper(), limit=depth_limit)

        if order_book and "bids" in order_book and "asks" in order_book:
            print(f"✅ Orderbook obtenido desde API - bids: {len(order_book['bids'])}, asks: {len(order_book['asks'])}, depth={depth_limit}")
            return order_book
        else:
            print(f"❌ Orderbook desde API inválido")
            return None

    except Exception as e:
        print(f"❌ Error en fallback API para orderbook {symbol}: {e}")
        return None


def _get_depth_limit_granular(symbol: str) -> int:
    """
    Determina el depth limit óptimo usando categorización granular.
    Replica la lógica de crypto-analyzer-redis para consistencia.

    Categorización optimizada para slippage_qty=$3-4K:
    - Ultra-líquidos (BTC, ETH, BNB): 50 niveles suficientes
    - High-liquidity (Top 10): 100 niveles para seguridad
    - Low-liquidity (Memecoins/Nuevos): 100 niveles crítico
    - Mid-liquidity (Resto): 100 niveles balanceado

    IMPORTANTE: Binance Futures API solo acepta: 5, 10, 20, 50, 100, 500, 1000

    Args:
        symbol: Símbolo de la criptomoneda

    Returns:
        int: Depth limit óptimo
    """
    symbol_lower = symbol.lower()

    # Ultra-líquidos: BTC, ETH, BNB
    if symbol_lower in {'btcusdt', 'ethusdt', 'bnbusdt'}:
        return 50

    # High-liquidity: Top altcoins
    HIGH_LIQUIDITY = {
        'btcusdt', 'ethusdt', 'bnbusdt', 'solusdt', 'adausdt', 'dogeusdt', 'xrpusdt', 'ltcusdt',
        'dotusdt', 'linkusdt', 'trxusdt', 'maticusdt', 'avaxusdt', 'xlmusdt'
    }
    if symbol_lower in HIGH_LIQUIDITY:
        return 100  # 75 no es válido en Binance API

    # Low-liquidity: Memecoins y tokens nuevos
    LOW_LIQUIDITY = {
        'virtualusdt', 'vicusdt', 'wifusdt', 'trumpusdt', 'notusdt',
        'opusdt', 'ordiusdt', 'hyperusdt', 'paxgusdt'
    }
    if symbol_lower in LOW_LIQUIDITY:
        return 100

    # Mid-liquidity: Resto de altcoins
    return 100  # 75 no es válido en Binance API

def validate_spread(symbol: str, entry_price: float, filters: dict, order_book: dict, mark_price: float) -> bool:
    """
    Valida spread usando métricas pre-calculadas cuando disponible (crypto-data-redis).
    Fallback a cálculo manual si no están disponibles.
    """
    # ✅ OPTIMIZACIÓN: Usar spread pre-calculado si disponible
    if "spread_pct" in order_book and order_book.get("source") == "websocket_cache":
        # Datos desde crypto-data-redis con spread ya calculado
        spread_pct = order_book["spread_pct"] / 100  # Convertir de % a decimal

        limits = get_dynamic_spread_limits(symbol, filters, mark_price)

        # Calcular spread absoluto desde spread_pct para validar ambos límites
        spread_abs = entry_price * spread_pct

        print(f"📊 Spread optimizado (pre-calculado): {spread_pct*100:.4f}%, abs: {spread_abs:.6f}")

        if spread_abs > limits["max_spread"]:
            print(f"❌ Spread absoluto ({spread_abs:.6f}) excede el máximo permitido ({limits['max_spread']})")
            return False

        if spread_pct > limits["max_spread_pct"]:
            print(f"❌ Spread relativo ({spread_pct:.6f}) excede el máximo permitido ({limits['max_spread_pct']:.6f})")
            return False

        print(f"✅ Spread aceptable (cache hit)")
        return True

    # Fallback: Cálculo manual tradicional
    book = {
        "bids": order_book["bids"][:5],
        "asks": order_book["asks"][:5]
    }

    best_bid = float(book["bids"][0][0])
    best_ask = float(book["asks"][0][0])
    spread = best_ask - best_bid
    spread_pct = spread / entry_price

    limits = get_dynamic_spread_limits(symbol, filters, mark_price)

    print(f"📊 Spread manual: {spread_pct*100:.4f}%, abs: {spread:.6f}")

    if spread > limits["max_spread"]:
        print(f"❌ Spread absoluto ({spread}) excede el máximo permitido ({limits['max_spread']})")
        return False

    if spread_pct > limits["max_spread_pct"]:
        print(f"❌ Spread relativo ({spread_pct:.6f}) excede el máximo permitido ({limits['max_spread_pct']:.6f})")
        return False

    return True

def validate_slippage(symbol: str, entry_price: float, order_book: dict) -> bool:
    """
    Valida slippage usando métricas pre-calculadas cuando disponible (crypto-data-redis).
    Fallback a cálculo manual si no están disponibles.
    """
    # ✅ OPTIMIZACIÓN: Usar slippage pre-calculado si disponible
    if "slippage_pct" in order_book and order_book.get("source") == "websocket_cache":
        # Datos desde crypto-data-redis con slippage ya calculado
        slippage_pct_precalc = order_book["slippage_pct"] / 100  # Convertir de % a decimal
        slippage_abs = entry_price * slippage_pct_precalc

        limits = get_dynamic_slippage_limits(symbol)
        max_slippage = limits.get(MAX_SLIPPAGE)
        max_slippage_pct = limits.get(MAX_SLIPPAGE_PCT)

        print(f"🧪 Validación de slippage optimizada (pre-calculado) para {symbol}")
        print(f"📈 Entry: {entry_price:.6f}, Slippage: {slippage_abs:.6f} ({slippage_pct_precalc*100:.4f}%)")
        print(f"🎯 Máx slippage: abs={max_slippage:.6f}, pct={max_slippage_pct:.6f}")

        if slippage_abs > max_slippage or slippage_pct_precalc > max_slippage_pct:
            print(f"❌ Slippage demasiado alto para {symbol} (cache hit)")
            return False

        print(f"✅ Slippage aceptable para {symbol} (cache hit)")
        return True

    # Fallback: Cálculo manual tradicional
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

    print(f"🧪 Validación de slippage manual para {symbol}")
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
    Usa el nuevo Algo Order API endpoint (migrado desde 2025-12-09).

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
        # ✅ NUEVO: Usar el Algo Order API endpoint (POST /fapi/v1/algoOrder)
        # Binance migró órdenes condicionales desde 2025-12-09
        params = {
            "symbol": symbol,
            "side": direction,
            "algoType": "CONDITIONAL",  # ✅ Tipo de algoritmo debe ser CONDITIONAL
            "type": "STOP_MARKET",      # ✅ Tipo específico de orden condicional
            "triggerPrice": stop_price,  # ✅ Precio de activación (antes stopPrice)
            "closePosition": "true",    # Cierra toda la posición automáticamente
            "workingType": working_type,
            "timestamp": int(time.time() * 1000)  # ✅ Timestamp requerido
            # NOTA: No usar 'reduceOnly' cuando 'closePosition' está activado
        }

        # Hacer llamada directa al endpoint de Algo Orders
        result = client._request_futures_api('post', 'algoOrder', signed=True, data=params)
        print(f"✅ Orden STOP_MARKET ({working_type}) creada via Algo API: {direction} {symbol} ({user_id}) @ {stop_price}")
        return result
    except Exception as e:
        print(f"❌ Error al crear STOP_MARKET para {symbol} ({user_id}): {e}")
        traceback.print_exc()
        return None

def create_take_profit_order(symbol: str, direction: str, stop_price: float, client, user_id: str):
    """
    Crea una orden TAKE_PROFIT_MARKET en Binance Futures.
    Usa el nuevo Algo Order API endpoint (migrado desde 2025-12-09).

    Args:
        symbol (str): Ej. "BTCUSDT"
        side (str): BUY o SELL
        stop_price (float): Precio que activa el take profit.
        quantity (float): Cantidad a vender/comprar.

    Returns:
        dict: Respuesta de Binance o None si hay error.
    """

    try:
        # ✅ NUEVO: Usar el Algo Order API endpoint (POST /fapi/v1/algoOrder)
        # Binance migró órdenes condicionales desde 2025-12-09
        params = {
            "symbol": symbol,
            "side": direction,
            "algoType": "CONDITIONAL",        # ✅ Tipo de algoritmo debe ser CONDITIONAL
            "type": "TAKE_PROFIT_MARKET",     # ✅ Tipo específico de orden condicional
            "triggerPrice": round(stop_price, 4),  # ✅ Precio de activación (antes stopPrice)
            "closePosition": "true",          # Cierra toda la posición automáticamente
            "workingType": "MARK_PRICE",
            "timestamp": int(time.time() * 1000)  # ✅ Timestamp requerido
            # NOTA: No usar 'reduceOnly' cuando 'closePosition' está activado
        }

        # Hacer llamada directa al endpoint de Algo Orders
        response = client._request_futures_api('post', 'algoOrder', signed=True, data=params)
        print(f"✅ Orden TAKE_PROFIT_MARKET ({user_id}) (closePosition=True) creada via Algo API: {direction} {symbol} @ {stop_price}")
        return response
    except Exception as e:
        print(f"❌ Error al crear orden TAKE_PROFIT para {symbol} ({user_id}): {e}")
        traceback.print_exc()
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

        # ✅ NUEVO: Manejar respuesta del Algo Order API (usa 'algoId' en lugar de 'orderId')
        sl_order_id = sl_result.get("algoId") or sl_result.get("orderId") if sl_result else None
        tp_order_id = tp_result.get("algoId") or tp_result.get("orderId") if tp_result else None
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
    Maneja tanto órdenes tradicionales como Algo Orders (migradas desde 2025-12-09).

    Args:
        symbol (str): Ejemplo "BTCUSDT"
    """

    try:
        # Verificar si hay posición activa
        positions = client.futures_position_information(symbol=symbol)
        if not positions or float(positions[0]["positionAmt"]) == 0.0:
            print(f"🔍 Sin posición activa para {symbol}, revisando órdenes abiertas...")

            # 1️⃣ Obtener órdenes tradicionales (endpoint antiguo)
            open_orders = client.futures_get_open_orders(symbol=symbol)

            # 2️⃣ Obtener Algo Orders (nuevo endpoint desde 2025-12-09)
            algo_orders = []
            try:
                # ✅ CORREGIDO: usar 'openAlgoOrders' (con O mayúscula)
                algo_response = client._request_futures_api('get', 'openAlgoOrders', signed=True, data={"symbol": symbol})
                # La respuesta puede tener formato: {"openOrders": [...]} o ser lista directa
                if isinstance(algo_response, dict) and "openOrders" in algo_response:
                    algo_orders = algo_response["openOrders"]
                elif isinstance(algo_response, list):
                    algo_orders = algo_response
            except Exception as e:
                print(f"⚠️ No se pudieron obtener Algo Orders para {symbol}: {e}")

            # Combinar ambas listas
            total_orders = len(open_orders) + len(algo_orders)

            if total_orders == 0:
                print(f"❔ No hay órdenes abiertas para {symbol}, posible cierre manual.")
                # Confirmamos que había order_id en BD antes de esta llamada
                update_trade_status(symbol, user_id, status="close_manual")
                return

            # 3️⃣ Cancelar órdenes tradicionales
            for order in open_orders:
                if order["type"] in ["STOP_MARKET", "TAKE_PROFIT_MARKET"]:
                    print(f"🧹 Cancelando orden huérfana tradicional {order['type']} (ID: {order['orderId']}) de {user_id}")
                    client.futures_cancel_order(symbol=symbol, orderId=order["orderId"])

                    # Actualizar status del trade
                    if order["type"] == "STOP_MARKET":
                        update_trade_status(symbol, user_id, status="success")
                    elif order["type"] == "TAKE_PROFIT_MARKET":
                        update_trade_status(symbol, user_id, status="fail")

            # 4️⃣ Cancelar Algo Orders
            for algo_order in algo_orders:
                order_type = algo_order.get("algoType") or algo_order.get("type", "UNKNOWN")
                algo_id = algo_order.get("algoId")

                if algo_id and order_type in ["STOP_MARKET", "TAKE_PROFIT_MARKET", "STOP", "TAKE_PROFIT"]:
                    print(f"🧹 Cancelando Algo Order huérfana {order_type} (algoId: {algo_id}) de {user_id}")
                    try:
                        client._request_futures_api('delete', 'algoOrder', signed=True, data={"symbol": symbol, "algoId": algo_id})

                        # Actualizar status del trade
                        if order_type in ["STOP_MARKET", "STOP"]:
                            update_trade_status(symbol, user_id, status="success")
                        elif order_type in ["TAKE_PROFIT_MARKET", "TAKE_PROFIT"]:
                            update_trade_status(symbol, user_id, status="fail")
                    except Exception as e:
                        print(f"⚠️ Error cancelando Algo Order {algo_id}: {e}")

        else:
            print(f"✅ Posición activa detectada para {symbol} ({user_id}), no se cancelan órdenes.")

    except Exception as e:
        print(f"❌ Error al cancelar órdenes huérfanas para {symbol} ({user_id}): {e}")
        traceback.print_exc()


# ========== ENHANCED VALIDATIONS FOR MANUAL TRADING ENDPOINTS ==========

def validate_min_notional_for_manual_trading(
    symbol: str,
    position_amt: float,
    price: float,
    filters: dict
) -> tuple:
    """
    Validate that order meets MIN_NOTIONAL requirements for manual trading operations.

    This is critical for SL/TP orders - if they don't meet MIN_NOTIONAL, they will be
    rejected by Binance, leaving the position without protection.

    Args:
        symbol: Trading pair (e.g., "BTCUSDT")
        position_amt: Position amount (size)
        price: Order price (SL or TP)
        filters: Symbol filters from exchange_info

    Returns:
        Tuple of (is_valid: bool, error_message: str)

    Example:
        >>> is_valid, error = validate_min_notional_for_manual_trading(
        ...     "SOLUSDT", 0.01, 115.98, filters
        ... )
        >>> if not is_valid:
        ...     print(f"Error: {error}")
    """
    try:
        notional = abs(position_amt * price)
        min_notional = float(filters.get("MIN_NOTIONAL", {}).get("notional", 0))

        if min_notional > 0 and notional < min_notional:
            return False, (
                f"Order notional {notional:.4f} USDT is below minimum {min_notional:.4f} USDT for {symbol}. "
                f"Position size ({abs(position_amt)}) * Price ({price}) must be >= {min_notional:.4f} USDT"
            )

        return True, ""

    except Exception as e:
        return False, f"Error validating MIN_NOTIONAL: {str(e)}"


def validate_sl_distance_from_mark_price(
    symbol: str,
    stop_loss: float,
    mark_price: float,
    direction: str,
    min_distance_pct: float = 0.1
) -> tuple:
    """
    Validate that stop loss is not too close to mark price.

    Prevents SL orders that would execute immediately due to normal market noise.
    Default minimum distance is 0.1% (10 basis points).

    Args:
        symbol: Trading pair
        stop_loss: Stop loss price
        mark_price: Current mark price
        direction: Position direction ("LONG" or "SHORT")
        min_distance_pct: Minimum distance as percentage (default: 0.1%)

    Returns:
        Tuple of (is_valid: bool, error_message: str)

    Example:
        >>> is_valid, error = validate_sl_distance_from_mark_price(
        ...     "BTCUSDT", 44990.0, 45000.0, "LONG", min_distance_pct=0.1
        ... )
    """
    try:
        distance_pct = abs(stop_loss - mark_price) / mark_price * 100

        if distance_pct < min_distance_pct:
            return False, (
                f"Stop loss too close to mark price: {distance_pct:.3f}% "
                f"(minimum: {min_distance_pct}%). "
                f"SL={stop_loss:.4f}, Mark={mark_price:.4f}. "
                f"This may trigger immediately due to market noise."
            )

        return True, ""

    except Exception as e:
        return False, f"Error validating SL distance: {str(e)}"


def validate_risk_reward_ratio_for_manual_trading(
    entry_price: float,
    stop_loss: float,
    take_profit: float,
    direction: str,
    min_rr_ratio: float = 1.0
) -> tuple:
    """
    Validate that Risk-Reward ratio meets minimum threshold.

    RR Ratio = (TP Distance from Entry) / (SL Distance from Entry)

    A minimum RR of 1.0 means the potential profit should be at least equal to
    the potential loss. Higher values (e.g., 1.5 or 2.0) are more conservative.

    Args:
        entry_price: Position entry price
        stop_loss: Stop loss price
        take_profit: Take profit price
        direction: Position direction ("LONG" or "SHORT")
        min_rr_ratio: Minimum acceptable RR ratio (default: 1.0)

    Returns:
        Tuple of (is_valid: bool, error_message: str)

    Example:
        >>> is_valid, error = validate_risk_reward_ratio_for_manual_trading(
        ...     45000.0, 44000.0, 47000.0, "LONG", min_rr_ratio=1.5
        ... )
    """
    try:
        # Calculate distances
        if direction.upper() == "LONG":
            risk = abs(entry_price - stop_loss)
            reward = abs(take_profit - entry_price)
        else:  # SHORT
            risk = abs(stop_loss - entry_price)
            reward = abs(entry_price - take_profit)

        if risk == 0:
            return False, "Risk cannot be zero (SL equals entry price)"

        rr_ratio = reward / risk

        if rr_ratio < min_rr_ratio:
            return False, (
                f"Risk-Reward ratio {rr_ratio:.2f} is below minimum {min_rr_ratio:.2f}. "
                f"Entry={entry_price:.4f}, SL={stop_loss:.4f}, TP={take_profit:.4f}. "
                f"Consider adjusting TP higher or SL closer to entry."
            )

        return True, ""

    except Exception as e:
        return False, f"Error validating Risk-Reward ratio: {str(e)}"