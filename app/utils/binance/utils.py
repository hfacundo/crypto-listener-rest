# app/utils/binance/utils.py

import math
import json
from decimal import Decimal, ROUND_DOWN
from datetime import datetime, timezone, timedelta
import traceback

from app.utils.constants import (
    MAX_SPREAD, MAX_SPREAD_PCT, DEFAULT_MAX_LEVERAGE, DEFAULT_SPREAD_MULTIPLIER
)
from app.utils.config.settings import (
    COPY_TRADING, COPY_2
)
from app.utils.db.query_executor import get_category
from app.utils.binance.s3 import (
    load_filters_from_s3, save_filters_to_s3
)

def get_dynamic_spread_limits(symbol: str, filters: dict, mark_price: float) -> dict:
    tick_size = float(filters["PRICE_FILTER"]["tickSize"])
    print(f"datos de get_dynamic_spread_limits -> mark_price={mark_price}, tick_size={tick_size}")

    # Intentar usar valor configurable dinámicamente
    multiplier = get_dynamic_spread_multiplier(symbol)

    max_spread = tick_size * multiplier
    max_spread_pct = max_spread / mark_price
    print(f"datos de get_dynamic_spread_limits -> multiplier={multiplier}, max_spread={max_spread}, max_spread_pct={max_spread_pct}")

    return {
        MAX_SPREAD: max_spread,
        MAX_SPREAD_PCT: max_spread_pct
    }


def get_dynamic_spread_multiplier(symbol: str) -> int:
    category = get_category(symbol)

    if category == 1: # high liquidity
        return 2
    elif category == 2: # medium liquidity
        return 3
    elif category == 3: # low liquidity:
        return 4
    else:
        return DEFAULT_SPREAD_MULTIPLIER  # Valor por defecto para símbolos no clasificados


def get_symbol_filters(symbol: str, client) -> dict:
    filters_cache = load_filters_from_s3()

    # Verificar si los filtros están presentes y actualizados
    try:
        if filters_cache:
            updated_at = filters_cache.get("_updated_at")
            if updated_at:
                    updated_time = datetime.fromisoformat(updated_at).astimezone(timezone.utc)
                    if datetime.now(timezone.utc) - updated_time < timedelta(hours=12):
                        if symbol in filters_cache:
                            return filters_cache[symbol]
    except Exception as e:
        traceback.print_exc()
        print(f"⚠️ Error al interpretar fecha de actualización: {e}")

    # Si no hay filtros válidos o están vencidos, los descarga
    print(f"📥 Descargando filtros desde Binance...")
    exchange_info = client.futures_exchange_info()
    new_filters = {}
    for s in exchange_info["symbols"]:
        f_dict = {}
        for f in s["filters"]:
            f_dict[f["filterType"]] = f
        new_filters[s["symbol"]] = f_dict

    # Guardar en S3
    new_filters["_updated_at"] = datetime.now(timezone.utc).isoformat()
    save_filters_to_s3(new_filters)

    return new_filters.get(symbol, {})


def get_mark_price(symbol: str, client) -> float:
    symbol = symbol 
    try:
        data = client.futures_mark_price(symbol=symbol)
        return float(data["markPrice"])
    except Exception as e:
        print(f"❌ Error al obtener mark price para {symbol}: {e}")
        return -1.0  # o lanza una excepción si prefieres


def get_available_usdt_balance(client) -> float:
    """
    Returns the available USDT balance from Binance Futures account.

    Returns:
        float: Free USDT balance.
    """
    balance_data = client.futures_account_balance()

    for asset in balance_data:
        if asset.get("asset") == "USDT":
            print(f"asset found: {asset}")
            return float(asset.get("availableBalance", 0.0))
            # return float(asset.get("balance", 0.0)) # Para total balance

    print("⚠️ USDT balance not found in futures account.")
    return 0.0



def round_quantity_to_step_size(qty: float, filters: dict) -> float:
    """
    Redondea la cantidad (qty) hacia abajo al múltiplo más cercano permitido por stepSize.

    Args:
        qty (float): Cantidad original.
        filters (dict): Filtros de Binance con stepSize en LOT_SIZE.

    Returns:
        float: Cantidad redondeada válida.
    """
    step_size = Decimal(filters["LOT_SIZE"]["stepSize"])
    qty_dec = Decimal(str(qty))

    # Redondea hacia abajo al múltiplo más cercano
    rounded = (qty_dec // step_size) * step_size
    return float(rounded.quantize(step_size, rounding=ROUND_DOWN))


def get_current_leverage(symbol: str, client) -> int:
    """
    Obtiene el máximo leverage permitido por Binance para el símbolo dado.
    Este valor es fijo por Binance y no depende de posiciones abiertas.

    Args:
        symbol (str): Ej. "BTCUSDT"

    Returns:
        int: Valor máximo permitido de leverage, o -1 si ocurre error.
    """
    try:
        info = client.futures_exchange_info()

        for s in info["symbols"]:
            if s["symbol"] == symbol:
                # La clave "leverageFilter" ya no existe, ahora se usa "filters"
                for f in s["filters"]:
                    if f["filterType"] == "MARKET_LOT_SIZE":  # Esto no tiene el leverage
                        continue
                # El campo que contiene niveles de margen es:
                # "leverageBrackets" o similar, pero NO VIENE en esta llamada
                # En su lugar, usamos una heurística segura:
                return DEFAULT_MAX_LEVERAGE  # El valor máximo estándar para la mayoría de símbolos

    except Exception as e:
        print(f"❌ Error al obtener el leverage permitido para {symbol}: {e}")

    return -1


def get_max_allowed_leverage(symbol: str, client, user_id: str) -> int:
    """
    Retorna el leverage máximo permitido para un símbolo en Binance Futures.

    Args:
        symbol (str): Ej. BTCUSDT

    Returns:
        int: Valor máximo permitido (ej. 125), o -1 si falla.
    """
    try:
        brackets = client.futures_leverage_bracket(symbol=symbol)
        return int(brackets[0]["brackets"][0]["initialLeverage"])
    except Exception as e:
        print(f"❌ Error al obtener leverage máximo para {symbol} ({user_id}): {e}")
        return -1
    

def set_leverage(symbol: str, desired_leverage: int, client, user_id) -> tuple[bool, int]:
    """
    Establece el leverage deseado respetando reglas por user_id y límites del exchange.
    Para COPY_TRADING y COPY_2 se fuerza a 20x siempre.
    Devuelve (éxito, leverage_aplicado).
    """

    # 1) Regla dura: COPY_TRADING y COPY_2 siempre 20x (ignora lo que venga en desired_leverage)
    if user_id in [COPY_TRADING, COPY_2]:
        desired_leverage = 20

    # 2) Límite real permitido por el exchange (puede ser < 20 en algunos símbolos)
    max_allowed = get_max_allowed_leverage(symbol, client, user_id)
    if desired_leverage > max_allowed:
        print(f"⚠️ Ajustando leverage deseado ({desired_leverage}) al máximo permitido ({max_allowed}) para {symbol} ({user_id})")
        desired_leverage = max_allowed

    # 3) Validación de rango general (DEFAULT_MAX_LEVERAGE es tu techo absoluto global)
    if desired_leverage < 1 or desired_leverage > DEFAULT_MAX_LEVERAGE:
        print(f"❌ Leverage inválido: {desired_leverage}. Debe estar entre 1 y {DEFAULT_MAX_LEVERAGE}.")
        return False, 1

    # 4) Evitar llamada si ya está en el valor deseado
    current_leverage = get_current_leverage(symbol, client)
    if current_leverage == desired_leverage:
        print(f"✅ Leverage actual ({current_leverage}x) ya es el deseado para {symbol} ({user_id})")
        return True, desired_leverage

    # 5) Aplicar cambio
    try:
        client.futures_change_leverage(symbol=symbol, leverage=desired_leverage)
        print(f"🔁 Leverage actualizado a {desired_leverage}x para {symbol} ({user_id})")
        return True, desired_leverage
    except Exception as e:
        print(f"❌ Error al cambiar leverage para {symbol}: {e}")
        return False, 1


def adjust_quantity_to_step_size(qty: float, step_size: float) -> float:
    """
    Ajusta la cantidad a un múltiplo válido de stepSize según Binance.
    Redondea hacia abajo para evitar errores por exceso.
    """
    precision = int(round(-math.log10(step_size)))
    adjusted_qty = math.floor(qty / step_size) * step_size
    return round(adjusted_qty, precision)


def adjust_price_to_tick(price: float, tick_size: float) -> float:
    """
    Ajusta el precio al tickSize más cercano (redondeo hacia abajo para evitar errores).
    """
    precision = int(round(-math.log10(tick_size)))
    adjusted = math.floor(price / tick_size) * tick_size
    return round(adjusted, precision)


def is_trade_allowed_by_schedule_utc(rules: dict, now_utc: datetime = None) -> bool:
    schedule_text = rules.get("schedule")
    if not schedule_text:
        print("🟢 Sin restricciones: permitido 24/7")
        return True  # No hay restricciones, se permite operar 24/7

    try:
        schedule = json.loads(schedule_text)
    except Exception as e:
        print(f"❌ Error al interpretar schedule: {e}")
        return True  # Por seguridad, asumimos que se permite

    current_day = now_utc.strftime("%A")  # Ej: "Monday"
    current_time = now_utc.time()

    if current_day not in schedule or not schedule[current_day]:
        print(f"⛔ Operación rechazada: no permitido en {current_day}")
        return False

    for start_str, end_str in schedule[current_day]:
        start = datetime.strptime(start_str, "%H:%M").time()
        end = datetime.strptime(end_str, "%H:%M").time()

        if start <= current_time <= end:
            print(f"✅ Permitido: dentro del rango {start_str}-{end_str}")
            return True  # Está dentro del horario permitido

    print(f"⛔ Operación rechazada: fuera del horario permitido en {current_day} (UTC)")
    return False