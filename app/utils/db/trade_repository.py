"""
trade_repository.py - Repositorio para trades y reglas de usuario

Funciones:
  - get_user_rules: Obtener reglas de un usuario (sin fallback)
  - get_consecutive_losses: Contar pérdidas consecutivas para circuit breaker
  - get_last_trade_for_symbol: Obtener último trade de un símbolo (para cooldown)
  - save_trade_record: Guardar trade en trade_records

USADO EN: trade_executor.py, user_trade_validator.py (versión nueva)
"""

import json
import traceback
from datetime import datetime
from typing import Optional, Tuple
from sqlalchemy import text

from app.utils.logger_config import get_logger
from app.utils.config.config_constants import (
    TABLE_TRADE_RECORDS,
    TABLE_USER_RULES,
    EXIT_REASONS_WIN,
    EXIT_REASONS_LOSS,
    EXIT_REASONS_COOLDOWN,
    EXIT_REASON_ACTIVE,
)
from app.utils.db.query_executor import get_engine

logger = get_logger()


def get_user_rules(user_id: str, strategy: str) -> dict:
    """
    Obtiene las reglas de un usuario desde la base de datos.

    A diferencia de get_rules(), esta función:
      - NO tiene fallback a local_rules
      - Lanza excepción si hay error de BD
      - Lanza excepción si no encuentra reglas

    Args:
        user_id: ID del usuario
        strategy: Nombre de la estrategia

    Returns:
        dict: Reglas del usuario

    Raises:
        ValueError: Si no se encuentran reglas para user_id/strategy
        Exception: Si hay error de conexión a BD
    """
    try:
        with get_engine().begin() as conn:
            result = conn.execute(
                text(f"SELECT rules_config FROM {TABLE_USER_RULES} WHERE user_id = :user_id AND strategy = :strategy"),
                {"user_id": user_id, "strategy": strategy}
            ).fetchone()

        if not result:
            raise ValueError(f"No se encontraron reglas para user_id={user_id}, strategy={strategy}")

        rules = result[0]

        # JSONB se devuelve como dict, pero por si acaso
        if isinstance(rules, str):
            rules = json.loads(rules)

        return rules

    except ValueError:
        raise
    except Exception as e:
        logger.error(f"Error obteniendo reglas para {user_id}/{strategy}: {e}")
        raise


def get_consecutive_losses(user_id: str, strategy: str) -> Tuple[int, Optional[datetime]]:
    """
    Cuenta las pérdidas consecutivas más recientes para un usuario/estrategia.

    Recorre los trades cerrados desde el más reciente hasta encontrar una ganancia.
    Se usa para el circuit breaker.

    Args:
        user_id: ID del usuario
        strategy: Nombre de la estrategia

    Returns:
        Tuple[int, Optional[datetime]]: (cantidad_perdidas_consecutivas, timestamp_ultima_perdida)
        - Si no hay pérdidas consecutivas: (0, None)
        - Si hay pérdidas: (count, datetime de la última pérdida)
    """
    try:
        with get_engine().begin() as conn:
            # Obtener últimos 50 trades cerrados (suficiente para cualquier tier)
            result = conn.execute(
                text(f"""
                    SELECT exit_reason, created_at
                    FROM {TABLE_TRADE_RECORDS}
                    WHERE user_id = :user_id
                      AND strategy = :strategy
                      AND exit_reason != 'active'
                    ORDER BY created_at DESC
                    LIMIT 50
                """),
                {"user_id": user_id, "strategy": strategy}
            ).fetchall()

        if not result:
            return 0, None

        consecutive_losses = 0
        last_loss_time = None

        for row in result:
            exit_reason = row[0]
            created_at = row[1]

            if exit_reason in EXIT_REASONS_LOSS:
                consecutive_losses += 1
                if last_loss_time is None:
                    last_loss_time = created_at
            elif exit_reason in EXIT_REASONS_WIN:
                # Encontramos una ganancia, detenemos el conteo
                break
            # Si es otro valor desconocido, lo ignoramos y continuamos

        return consecutive_losses, last_loss_time

    except Exception as e:
        logger.error(f"Error contando pérdidas consecutivas para {user_id}/{strategy}: {e}")
        # En caso de error, retornar 0 para no bloquear trades
        return 0, None


def get_last_trade_for_symbol(
    user_id: str,
    strategy: str,
    symbol: str
) -> Tuple[Optional[str], Optional[datetime]]:
    """
    Obtiene el último trade cerrado de un símbolo específico.

    Se usa para el cooldown por símbolo después de pérdidas.

    Args:
        user_id: ID del usuario
        strategy: Nombre de la estrategia
        symbol: Símbolo del trade (ej: BTCUSDT)

    Returns:
        Tuple[Optional[str], Optional[datetime]]: (exit_reason, exit_time)
        - Si no hay trades cerrados: (None, None)
        - Si hay trade: (exit_reason, datetime de cierre)

    NOTA: El símbolo se compara case-insensitive (LOWER).
    """
    # Normalizar símbolo a lowercase para la query
    symbol = symbol.lower()

    try:
        with get_engine().begin() as conn:
            result = conn.execute(
                text(f"""
                    SELECT exit_reason, exit_time
                    FROM {TABLE_TRADE_RECORDS}
                    WHERE user_id = :user_id
                      AND strategy = :strategy
                      AND LOWER(symbol) = :symbol
                      AND exit_reason != :active
                    ORDER BY exit_time DESC
                    LIMIT 1
                """),
                {
                    "user_id": user_id,
                    "strategy": strategy,
                    "symbol": symbol,
                    "active": EXIT_REASON_ACTIVE
                }
            ).fetchone()

        if not result:
            return None, None

        exit_reason = result[0]
        exit_time = result[1]

        return exit_reason, exit_time

    except Exception as e:
        logger.error(f"Error obteniendo último trade para {user_id}/{strategy}/{symbol}: {e}")
        # En caso de error, retornar None para permitir el trade (fail-safe)
        return None, None


def save_trade_record(
    # Core
    symbol: str,
    user_id: str,
    strategy: str,
    direction: str,

    # Order IDs
    order_id: str,
    sl_order_id: str,
    tp_order_id: str,

    # Trade params
    entry_price: float,
    stop_loss: float,
    take_profit: float,
    quantity: float,
    rr: float,
    leverage: int,
    capital_risked: float,

    # Signal quality
    probability: float,
    ev: Optional[float],
    simulated_probability: Optional[float],
    grok_probability: Optional[float],

    # Grok metadata
    grok_model: Optional[str],
    grok_action: Optional[str],
    grok_confidence: Optional[str],
    grok_risk_level: Optional[str],
    grok_timing_quality: Optional[str],
    grok_key_factor: Optional[str],

    # Config
    rules: dict,

    # Timestamps
    signal_timestamp: Optional[str]
) -> bool:
    """
    Guarda un trade en la tabla trade_records.

    Args:
        Todos los campos de la tabla trade_records

    Returns:
        bool: True si se guardó correctamente, False si falló
    """
    # Symbol en lowercase para consistencia con BD
    symbol = symbol.lower()

    try:
        with get_engine().begin() as conn:
            conn.execute(text(f"""
                INSERT INTO {TABLE_TRADE_RECORDS} (
                    symbol, user_id, strategy, direction,
                    order_id, sl_order_id, tp_order_id,
                    entry_price, stop_loss, take_profit, quantity, rr, leverage, capital_risked,
                    probability, ev, simulated_probability, grok_probability,
                    grok_model, grok_action, grok_confidence, grok_risk_level, grok_timing_quality, grok_key_factor,
                    rules, signal_timestamp, created_at
                )
                VALUES (
                    :symbol, :user_id, :strategy, :direction,
                    :order_id, :sl_order_id, :tp_order_id,
                    :entry_price, :stop_loss, :take_profit, :quantity, :rr, :leverage, :capital_risked,
                    :probability, :ev, :simulated_probability, :grok_probability,
                    :grok_model, :grok_action, :grok_confidence, :grok_risk_level, :grok_timing_quality, :grok_key_factor,
                    :rules, :signal_timestamp, NOW()
                )
            """), {
                "symbol": symbol,
                "user_id": user_id,
                "strategy": strategy,
                "direction": direction,
                "order_id": order_id,
                "sl_order_id": sl_order_id,
                "tp_order_id": tp_order_id,
                "entry_price": entry_price,
                "stop_loss": stop_loss,
                "take_profit": take_profit,
                "quantity": quantity,
                "rr": rr,
                "leverage": leverage,
                "capital_risked": capital_risked,
                "probability": probability,
                "ev": ev,
                "simulated_probability": simulated_probability,
                "grok_probability": grok_probability,
                "grok_model": grok_model,
                "grok_action": grok_action,
                "grok_confidence": grok_confidence,
                "grok_risk_level": grok_risk_level,
                "grok_timing_quality": grok_timing_quality,
                "grok_key_factor": grok_key_factor,
                "rules": json.dumps(rules) if rules else None,
                "signal_timestamp": signal_timestamp
            })

        logger.info(f"[{symbol.upper()}] Trade guardado en {TABLE_TRADE_RECORDS} ({user_id})")
        return True

    except Exception as e:
        logger.error(f"[{symbol.upper()}] Error guardando trade ({user_id}): {e}")
        traceback.print_exc()
        return False
