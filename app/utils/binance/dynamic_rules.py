# app/utils/binance/dynamic_rules.py

import json
import traceback
from sqlalchemy import create_engine, text
from app.utils.constants import (
    DEFAULT_LIQUIDITY_TIERS, MIN_DEPTH_BASE, DEPTH_PCT,
    MAX_SLIPPAGE_PCT, MAX_SLIPPAGE, DEFAULT_MAX_SLIPPAGE_PCT,
    DEFAULT_MAX_SLIPPAGE, TABLE_CRYPTOS
)
# S3 cache removed - data fetched directly from Binance

# Lazy initialization del engine (solo cuando se necesita consultar BD)
_engine = None

def get_engine():
    """Inicializa el engine de SQLAlchemy solo cuando se necesita"""
    global _engine
    if _engine is None:
        from app.utils.config.settings import get_database_url
        _engine = create_engine(get_database_url(), echo=False, pool_pre_ping=True, future=True)
    return _engine


def adjust_base_depth_and_depth_pct_for_symbol(symbol, client, order_book, mark_price):
    """
    Ajusta dinámicamente los valores de `min_depth_base` y `depth_pct`
    según el volumen reciente y la profundidad del order book del símbolo.
    Permite sobrescribir los tiers desde rules["liquidity_tiers"] si está definido.

    Usa klines de Redis (crypto-data-redis) para evitar llamadas API innecesarias.
    """
    try:
        # Intentar obtener klines desde Redis primero (crypto-data-redis)
        from app.utils.binance.binance_cache_client import get_binance_cache_client
        cache_client = get_binance_cache_client()
        klines = cache_client.get_klines_from_redis(symbol, interval="1m", limit=60)

        # Fallback a API si Redis no disponible
        if not klines:
            print(f"⚠️ Klines not in Redis for {symbol}, falling back to API")
            klines = client.futures_klines(symbol=symbol, interval="1m", limit=60)

        total_quote_volume = sum(float(k[7]) for k in klines)
        avg_quote_volume = total_quote_volume / 60

        bids = order_book.get("bids", [])
        asks = order_book.get("asks", [])

        depth_pct_eval = 0.005
        min_price = mark_price * (1 - depth_pct_eval)
        max_price = mark_price * (1 + depth_pct_eval)

        def sum_depth(levels):
            return sum(float(p) * float(q) for p, q in levels if min_price <= float(p) <= max_price)

        depth_usdt = sum_depth(bids) + sum_depth(asks)

        # Usa tu variable global DEFAULT_LIQUIDITY_TIERS
        tiers = DEFAULT_LIQUIDITY_TIERS
        if isinstance(tiers, str):
            try:
                tiers = json.loads(tiers)
            except:
                tiers = DEFAULT_LIQUIDITY_TIERS

        for tier in tiers:
            if avg_quote_volume > tier["vol"] and depth_usdt > tier["depth"]:
                result = {MIN_DEPTH_BASE: tier[MIN_DEPTH_BASE], DEPTH_PCT: tier[DEPTH_PCT]}
                print(f"✅ Dynamic depth config for {symbol}: {result}")
                return result

        fallback = {MIN_DEPTH_BASE: 10000, DEPTH_PCT: 0.01}
        print(f"⚠️ Using fallback depth config for {symbol}: {fallback}")
        return fallback

    except Exception as e:
        print(f"❌ Error ajustando reglas dinámicas para {symbol}: {e}")
        return {MIN_DEPTH_BASE: 15000, DEPTH_PCT: 0.008}


def get_dynamic_slippage_limits(symbol: str) -> dict:
    symbol = symbol.lower()
    """
    Retorna los límites de slippage para un símbolo desde la tabla 'cryptos'.
    Si no se encuentra, usa valores por defecto.
    """
    with get_engine().begin() as conn:
        result = conn.execute(text(f"""
            SELECT max_slippage_pct, max_slippage
            FROM {TABLE_CRYPTOS}
            WHERE symbol = :symbol
        """), {"symbol": symbol}).fetchone()

    if result:
        max_slippage_pct = result[0] if result[0] is not None else DEFAULT_MAX_SLIPPAGE_PCT
        max_slippage = result[1] if result[1] is not None else DEFAULT_MAX_SLIPPAGE
        print(f"⚙️ Slippage configurado para {symbol}: pct={max_slippage_pct}, abs={max_slippage}")
        return {
            MAX_SLIPPAGE_PCT: max_slippage_pct,
            MAX_SLIPPAGE: max_slippage
        }
    else:
        print(f"⚠️ No hay configuración de slippage en BD para {symbol}, usando default.")
        return {
            MAX_SLIPPAGE_PCT: DEFAULT_MAX_SLIPPAGE_PCT,
            MAX_SLIPPAGE: DEFAULT_MAX_SLIPPAGE
        }
