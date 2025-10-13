# app/db/db.py

import json
import traceback
import os
from typing import Optional
from sqlalchemy import create_engine, text
from app.utils.constants import (
    TABLE_RULES, TABLE_CRYPTOS, TABLE_TRADES, DEFAULT_SPREAD_MULTIPLIER
)

# NUEVO: Importar diccionario local de rules (solo como emergency fallback)
from app.utils.db.local_rules import get_local_rules

# Lazy initialization del engine (solo cuando se necesita consultar BD)
_engine = None

def get_engine():
    """Inicializa el engine de SQLAlchemy solo cuando se necesita"""
    global _engine
    if _engine is None:
        from app.utils.config.settings import get_database_url
        _engine = create_engine(get_database_url(), echo=False, pool_pre_ping=True, future=True)
    return _engine

def get_rules(user_id: str, strategy: str) -> dict:
    """
    Devuelve las reglas configurables desde PostgreSQL.

    Comportamiento:
    1. SIEMPRE intenta consultar PostgreSQL primero (tabla user_rules)
    2. Si falla la conexión a BD, usa local_rules.py como emergency fallback

    Esto permite gestionar todas las rules desde la base de datos con cambios en tiempo real.
    """

    # SIEMPRE intentar PostgreSQL primero
    print(f"🗄️ Consultando rules desde PostgreSQL (user_rules) para {user_id}/{strategy}")
    try:
        with get_engine().begin() as conn:
            result = conn.execute(
                text("SELECT rules_config FROM user_rules WHERE user_id = :user_id AND strategy = :strategy"),
                {"user_id": user_id, "strategy": strategy}
            ).fetchone()

        if result:
            # JSONB se devuelve como dict directamente por psycopg2
            print(f"✅ Rules obtenidas desde PostgreSQL para {user_id}/{strategy}")
            return result[0]
        else:
            print(f"⚠️ No se encontraron rules para {user_id}/{strategy} en PostgreSQL")
            print(f"🔄 Fallback a local_rules.py...")
            # Fallback a local_rules si no existe en BD
            try:
                return get_local_rules(user_id, strategy)
            except ValueError as e:
                print(f"❌ Error en fallback a local_rules: {e}")
                return {}

    except Exception as e:
        print(f"❌ Error consultando PostgreSQL: {e}")
        print(f"🆘 EMERGENCY FALLBACK: Usando local_rules.py para {user_id}/{strategy}")
        traceback.print_exc()

        # Emergency fallback a local_rules si falla conexión a BD
        try:
            return get_local_rules(user_id, strategy)
        except ValueError as fallback_error:
            print(f"❌ Emergency fallback también falló: {fallback_error}")
            return {}


def is_symbol_banned(user_id: str, strategy: str, symbol: str) -> bool:
    """
    Verifica si un símbolo está en la lista de banned_symbols del usuario.

    Comportamiento:
    1. SIEMPRE intenta consultar PostgreSQL primero
    2. Si falla la conexión, retorna False (fail-safe: permitir trade)

    Args:
        user_id: ID del usuario
        strategy: Estrategia (ej: "archer_dual")
        symbol: Símbolo a verificar (ej: "BTCUSDT")

    Returns:
        bool: True si el símbolo está baneado, False si no lo está
    """
    symbol = symbol.upper()  # Normalizar a mayúsculas

    try:
        with get_engine().begin() as conn:
            result = conn.execute(
                text("SELECT banned_symbols FROM user_rules WHERE user_id = :user_id AND strategy = :strategy"),
                {"user_id": user_id, "strategy": strategy}
            ).fetchone()

        if result and result[0]:
            banned_list = result[0]  # JSONB array como lista de Python
            is_banned = symbol in banned_list
            if is_banned:
                print(f"🚫 {symbol} está en la lista de banned symbols para {user_id}/{strategy}")
            return is_banned
        else:
            return False
    except Exception as e:
        print(f"⚠️ Error verificando banned symbols desde PostgreSQL: {e}")
        return False  # En caso de error, permitir el trade (fail-safe)


def save_trade(
    symbol: str,
    direction: str,
    probability: float,
    capital_risked: float,
    leverage: int,
    rr: float,
    rules: dict,
    order_data: dict,
    user_id: str,
    strategy: str
) -> bool:
    symbol = symbol.lower()
    try:
        with get_engine().begin() as conn:            
            conn.execute(text(f"""
                INSERT INTO {TABLE_TRADES} (
                    symbol, order_id, sl_order_id, tp_order_id, trade, rr, entry_price, stop_loss,
                    take_profit, capital_risked, leverage, user_id, rules, probability, strategy, created_at
                )
                VALUES (
                    :symbol, :order_id, :sl_order_id, :tp_order_id, :trade, :rr, :entry_price, :stop_loss,
                    :take_profit, :capital_risked, :leverage, :user_id, :rules, :probability, :strategy, NOW()
                );
            """), {
                "symbol": symbol,
                "order_id": order_data["order_id"],
                "sl_order_id": order_data["sl_order_id"],
                "tp_order_id": order_data["tp_order_id"],
                "trade": direction,
                "rr": rr,
                "entry_price": order_data["entry"],
                "stop_loss": order_data["stop_loss"],
                "take_profit": order_data["target"],
                "capital_risked": capital_risked,
                "leverage": leverage,
                "user_id": user_id,
                "rules": json.dumps(rules),  # asegúrate que rules sea serializable
                "probability": probability,
                "strategy": strategy
            })

            return True

    except Exception as e:
        print(f"❌ Error guardando trade y recomendación para {symbol}: {e}")
        traceback.print_exc()
        return False

    


def get_latest_order_id_for_symbol(symbol: str, user_id: str) -> Optional[str]:
    symbol = symbol.lower()
    """
    Retorna el último order_id registrado para el símbolo dado.

    Args:
        symbol (str): Ejemplo "BTCUSDT"

    Returns:
        str | None: Último order_id si existe, de lo contrario None.
    """
    with get_engine().begin() as conn:
        result = conn.execute(text(f"""
            SELECT t.order_id
            FROM {TABLE_TRADES} t
            WHERE t.symbol = :symbol
            AND t.user_id = :user_id
            ORDER BY t.created_at DESC
            LIMIT 1;
        """), {"symbol": symbol, "user_id": user_id}).fetchone()

    return result[0] if result else None


def update_trade_status(symbol: str, user_id: str, status: str) -> None:
    """
    Actualiza el campo 'status' del trade más reciente con el symbol y user_id dados.
    Solo actualiza si el último trade tiene status 'open'

    Args:
        symbol (str): Ejemplo "BTCUSDT"
        user_id (str): ID del usuario
        status (str): "success" o "fail"
    """
    symbol = symbol.lower()

    try:
        with get_engine().begin() as conn:
            result = conn.execute(text(f"""
                UPDATE {TABLE_TRADES}
                SET status = :status,
                    updated_at = NOW()
                WHERE id = (
                    SELECT id
                    FROM {TABLE_TRADES}
                    WHERE symbol = :symbol
                    AND user_id = :user_id
                    AND status = 'open'
                    ORDER BY created_at DESC
                    LIMIT 1
                )
                RETURNING id;
            """), {"symbol": symbol, "user_id": user_id, "status": status})

            updated_id = result.fetchone()
            if updated_id:
                print(f"🔄 Estado del trade actualizado a '{status}' para {symbol} ({user_id})")
            else:
                print(f"⚠️ No se encontró trade para actualizar con {symbol} ({user_id})")

    except Exception as e:
        print(f"❌ Error al actualizar estado del trade para {symbol} ({user_id}): {e}")



def get_category(symbol: str) -> int:
    symbol = symbol.lower()
    """
    Obtiene la categoría registrada para una cripto en la tabla ct_cryptos.

    Args:
        symbol (str): Símbolo como "BTCUSDT".

    Returns:
        str | None: Categoría si existe, de lo contrario None.
    """
    with get_engine().begin() as conn:
        result = conn.execute(
            text(f"SELECT category FROM {TABLE_CRYPTOS} WHERE symbol = :symbol"),
            {"symbol": symbol},
        ).fetchone()

    return int(result[0]) if result else DEFAULT_SPREAD_MULTIPLIER