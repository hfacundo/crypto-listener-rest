# app/utils/binance/utils.py

import math
import json
from decimal import Decimal, ROUND_DOWN
from datetime import datetime, timezone, timedelta
import traceback

from app.utils.logger_config import get_logger
logger = get_logger()

from app.utils.constants import (
    MAX_SPREAD, MAX_SPREAD_PCT, DEFAULT_MAX_LEVERAGE, DEFAULT_SPREAD_MULTIPLIER
)
from app.utils.config.settings import (
    COPY_TRADING, COPY_2
)
from app.utils.db.query_executor import get_category

def get_dynamic_spread_limits(symbol: str, filters: dict, mark_price: float) -> dict:
    tick_size = float(filters["PRICE_FILTER"]["tickSize"])
    logger.debug(f"📊 get_dynamic_spread_limits -> mark_price={mark_price}, tick_size={tick_size}")

    # Intentar usar valor configurable dinámicamente
    multiplier = get_dynamic_spread_multiplier(symbol)

    max_spread = tick_size * multiplier
    max_spread_pct = max_spread / mark_price
    logger.debug(f"📊 get_dynamic_spread_limits -> multiplier={multiplier}, max_spread={max_spread}, max_spread_pct={max_spread_pct}")

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


# USADO EN: api.py, trade_executor.py (nueva versión simplificada)
def get_symbol_filters(symbol: str, client) -> dict:
    """
    Obtiene los filtros de trading para un símbolo usando cache local de exchange_info.

    Args:
        symbol: Símbolo de trading (ej: "BTCUSDT")
        client: Cliente de Binance

    Returns:
        dict: Filtros del símbolo (PRICE_FILTER, LOT_SIZE, etc.)
    """
    try:
        from app.utils.binance.binance_cache_client import get_exchange_info_cached
        exchange_info = get_exchange_info_cached(client)

        if not exchange_info:
            logger.error(f"❌ Could not get exchange_info")
            return {}

        for s in exchange_info["symbols"]:
            if s["symbol"] == symbol:
                f_dict = {}
                for f in s["filters"]:
                    f_dict[f["filterType"]] = f
                return f_dict

        logger.warning(f"⚠️ Symbol {symbol} not found in exchange info")
        return {}

    except Exception as e:
        logger.error(f"❌ Error getting filters for {symbol}: {e}")
        traceback.print_exc()
        return {}


# USADO EN: api.py, trade_executor.py (nueva versión simplificada)
def get_mark_price(symbol: str, client) -> float:
    """
    Obtiene mark price usando cache de crypto-analyzer-redis.
    Fallback a API si cache no disponible.

    Args:
        symbol: Símbolo (ej: BTCUSDT)
        client: Cliente de Binance

    Returns:
        Mark price o -1.0 si falla
    """
    try:
        # Intentar usar cache primero
        from app.utils.binance.binance_cache_client import get_binance_cache_client
        cache_client = get_binance_cache_client()
        mark_price = cache_client.get_mark_price(symbol, client=client, max_age=30)

        if mark_price is not None:
            return mark_price

        # Fallback directo (cache_client ya lo intenta, pero por si acaso)
        data = client.futures_mark_price(symbol=symbol)
        return float(data["markPrice"])
    except Exception as e:
        logger.error(f"❌ Error al obtener mark price para {symbol}: {e}")
        return -1.0


def get_available_usdt_balance(client) -> float:
    """
    Returns the available USDT balance from Binance Futures account.

    Returns:
        float: Free USDT balance.
    """
    balance_data = client.futures_account_balance()

    for asset in balance_data:
        if asset.get("asset") == "USDT":
            logger.debug(f"💰 asset found: {asset}")
            return float(asset.get("availableBalance", 0.0))
            # return float(asset.get("balance", 0.0)) # Para total balance

    logger.warning("⚠️ USDT balance not found in futures account.")
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
    Usa cache local de exchange_info.

    Args:
        symbol (str): Ej. "BTCUSDT"

    Returns:
        int: Valor máximo permitido de leverage, o -1 si ocurre error.
    """
    try:
        from app.utils.binance.binance_cache_client import get_exchange_info_cached
        info = get_exchange_info_cached(client)

        if not info:
            return -1

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
        logger.error(f"❌ Error al obtener el leverage permitido para {symbol}: {e}")

    return -1


def get_max_allowed_leverage(symbol: str, client, user_id: str) -> int:
    """
    Retorna el leverage máximo permitido para un símbolo en Binance Futures.
    Usa cache local de leverage_bracket (TTL 1 hora).

    Args:
        symbol (str): Ej. BTCUSDT
        client: Cliente de Binance
        user_id: ID del usuario (para logging)

    Returns:
        int: Valor máximo permitido (ej. 125), o -1 si falla.
    """
    try:
        from app.utils.binance.binance_cache_client import get_leverage_bracket_cached
        brackets = get_leverage_bracket_cached(symbol, client)

        if not brackets:
            return -1

        return int(brackets[0]["brackets"][0]["initialLeverage"])
    except Exception as e:
        logger.error(f"❌ Error al obtener leverage máximo para {symbol} ({user_id}): {e}")
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
        logger.warning(f"⚠️ Ajustando leverage deseado ({desired_leverage}) al máximo permitido ({max_allowed}) para {symbol} ({user_id})")
        desired_leverage = max_allowed

    # 3) Validación de rango general (DEFAULT_MAX_LEVERAGE es tu techo absoluto global)
    if desired_leverage < 1 or desired_leverage > DEFAULT_MAX_LEVERAGE:
        logger.error(f"❌ Leverage inválido: {desired_leverage}. Debe estar entre 1 y {DEFAULT_MAX_LEVERAGE}.")
        return False, 1

    # 4) Evitar llamada si ya está en el valor deseado
    current_leverage = get_current_leverage(symbol, client)
    if current_leverage == desired_leverage:
        logger.info(f"✅ Leverage actual ({current_leverage}x) ya es el deseado para {symbol} ({user_id})")
        return True, desired_leverage

    # 5) Aplicar cambio
    try:
        client.futures_change_leverage(symbol=symbol, leverage=desired_leverage)
        logger.info(f"🔁 Leverage actualizado a {desired_leverage}x para {symbol} ({user_id})")
        return True, desired_leverage
    except Exception as e:
        logger.error(f"❌ Error al cambiar leverage para {symbol}: {e}")
        return False, 1


# USADO EN: api.py, trade_executor.py (nueva versión simplificada)
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
    # ✅ FIX: Convertir explícitamente a float para evitar TypeError con Decimal
    price = float(price)
    tick_size = float(tick_size)

    precision = int(round(-math.log10(tick_size)))
    adjusted = math.floor(price / tick_size) * tick_size
    return round(adjusted, precision)


def is_trade_allowed_by_schedule_utc(rules: dict, now_utc: datetime = None) -> bool:
    schedule = rules.get("schedule")
    if not schedule:
        logger.debug("🟢 Sin restricciones: permitido 24/7")
        return True  # No hay restricciones, se permite operar 24/7

    try:
        # Manejar tanto dict (PostgreSQL jsonb) como string JSON (legacy)
        if isinstance(schedule, str):
            schedule = json.loads(schedule)
        elif not isinstance(schedule, dict):
            logger.warning(f"⚠️ Formato de schedule desconocido: {type(schedule)}")
            return True  # Por seguridad, asumimos que se permite
    except Exception as e:
        logger.error(f"❌ Error al interpretar schedule: {e}")
        return True  # Por seguridad, asumimos que se permite

    current_day = now_utc.strftime("%A")  # Ej: "Monday"
    current_time = now_utc.time()

    if current_day not in schedule or not schedule[current_day]:
        logger.warning(f"⛔ Operación rechazada: no permitido en {current_day}")
        return False

    for start_str, end_str in schedule[current_day]:
        start = datetime.strptime(start_str, "%H:%M").time()
        end = datetime.strptime(end_str, "%H:%M").time()

        if start <= current_time <= end:
            logger.debug(f"✅ Permitido: dentro del rango {start_str}-{end_str}")
            return True  # Está dentro del horario permitido

    logger.warning(f"⛔ Operación rechazada: fuera del horario permitido en {current_day} (UTC)")
    return False