#!/usr/bin/env python3
# app/utils/orphan_order_detector.py
"""
Orphan Order Detector - Detecta y limpia orders huérfanos en Binance.

PROBLEMA:
Cuando un trade pierde (SL tocó), el TP order queda "huérfano" en Binance
hasta que crypto-guardian lo detecta. Esto puede causar que:
1. Redis ya no tenga el trade (eliminado por guardian)
2. BD aún muestre exit_reason='active' (no actualizado)
3. Binance aún tenga el TP order abierto

SOLUCIÓN:
1. Detectar orphan orders comparando BD vs Binance
2. Cancelar orphan orders automáticamente
3. Actualizar BD con exit_reason='stop_hit'
4. Aplicar cooldown de 4 horas para evitar revenge trading
"""

from datetime import datetime, timezone
from typing import Tuple, Dict, Optional, List
import logging

from app.utils.binance.binance_client import get_binance_client_for_user
from app.utils.logger_config import get_logger

logger = get_logger(__name__)


class OrphanOrderDetector:
    """
    Detecta y limpia orders huérfanos en Binance.

    Un orphan order es un TP/SL order que quedó abierto después de que
    el trade se cerró (típicamente porque el lado opuesto tocó primero).
    """

    def __init__(self):
        """Initialize detector."""
        pass

    def check_and_handle_orphan_orders(
        self,
        user_id: str,
        strategy: str,
        symbol: str,
        last_trade_from_db: Dict,
        protection_system
    ) -> Tuple[bool, str, Optional[Dict]]:
        """
        Verifica si hay orphan orders para el trade y los maneja.

        Args:
            user_id: ID del usuario
            strategy: Estrategia
            symbol: Símbolo del trade
            last_trade_from_db: Último trade de BD (con exit_reason='active')
            protection_system: Instancia de TradeProtectionSystem para actualizar BD

        Returns:
            Tuple[bool, str, Optional[Dict]]:
                - has_orphans: True si se encontraron orphan orders
                - action_taken: Descripción de la acción tomada
                - updated_trade_info: Info del trade actualizado (si se actualizó BD)
        """
        try:
            # Verificar si el trade tiene order IDs
            sl_order_id = last_trade_from_db.get('sl_order_id')
            tp_order_id = last_trade_from_db.get('tp_order_id')

            if not sl_order_id and not tp_order_id:
                logger.warning(
                    f"⚠️ Trade in BD without order IDs: {user_id}/{symbol} - "
                    f"Cannot check for orphans"
                )
                return False, "no_order_ids_in_db", None

            # Obtener orders abiertas en Binance
            client = get_binance_client_for_user(user_id)
            open_orders = client.futures_get_open_orders(symbol=symbol.upper())

            logger.info(
                f"🔍 Checking orphan orders for {user_id}/{symbol}: "
                f"BD has SL={sl_order_id}, TP={tp_order_id} | "
                f"Binance has {len(open_orders)} open orders"
            )

            # Buscar los order IDs de BD en Binance
            sl_order_found = None
            tp_order_found = None
            other_orders = []

            for order in open_orders:
                order_id = order.get('orderId')
                if order_id == sl_order_id:
                    sl_order_found = order
                elif order_id == tp_order_id:
                    tp_order_found = order
                else:
                    other_orders.append(order)

            # CASO 1: Ambos orders aún abiertos → Trade realmente activo
            if sl_order_found and tp_order_found:
                logger.info(
                    f"✅ Both SL and TP orders still open for {user_id}/{symbol} - "
                    f"Trade is genuinely active"
                )
                return False, "both_orders_active", None

            # CASO 2: Ningún order abierto → Trade cerró completamente
            if not sl_order_found and not tp_order_found:
                # Verificar si hay órden ejecutada en history
                exit_reason, exit_price, exit_time = self._determine_exit_from_order_history(
                    client, symbol.upper(), sl_order_id, tp_order_id
                )

                if exit_reason:
                    logger.warning(
                        f"🔍 No orders in Binance but found executed order for {user_id}/{symbol}: "
                        f"{exit_reason} at ${exit_price}"
                    )

                    # Actualizar BD
                    self._update_trade_exit_in_db(
                        protection_system,
                        last_trade_from_db['id'],
                        exit_time,
                        exit_price,
                        exit_reason
                    )

                    return True, f"completed_trade_detected_{exit_reason}", {
                        'exit_reason': exit_reason,
                        'exit_price': exit_price,
                        'exit_time': exit_time
                    }
                else:
                    logger.warning(
                        f"⚠️ No open orders and no execution history found for {user_id}/{symbol} - "
                        f"Trade status unknown"
                    )
                    return False, "no_orders_no_history", None

            # CASO 3: Solo SL abierto (TP ejecutado) → GANÓ
            if sl_order_found and not tp_order_found:
                logger.info(
                    f"✅ Orphan SL order detected for {user_id}/{symbol} - "
                    f"TP likely hit, cancelling SL"
                )

                # Cancelar SL orphan
                try:
                    client.futures_cancel_order(symbol=symbol.upper(), orderId=sl_order_id)
                    logger.info(f"🗑️ Cancelled orphan SL order {sl_order_id}")
                except Exception as e:
                    logger.error(f"❌ Error cancelling SL order: {e}")

                # Obtener TP execution info
                exit_price, exit_time = self._get_order_execution_info(
                    client, symbol.upper(), tp_order_id
                )

                # Actualizar BD
                self._update_trade_exit_in_db(
                    protection_system,
                    last_trade_from_db['id'],
                    exit_time or datetime.now(timezone.utc),
                    exit_price or last_trade_from_db.get('target_price', 0),
                    'target_hit'
                )

                return True, "target_hit_orphan_sl_cancelled", {
                    'exit_reason': 'target_hit',
                    'exit_price': exit_price,
                    'exit_time': exit_time,
                    'orphan_cancelled': 'sl'
                }

            # CASO 4: Solo TP abierto (SL ejecutado) → PERDIÓ 🚨
            if tp_order_found and not sl_order_found:
                logger.warning(
                    f"🚨 ORPHAN TP ORDER DETECTED for {user_id}/{symbol} - "
                    f"SL likely hit, cancelling TP"
                )

                # Cancelar TP orphan
                try:
                    client.futures_cancel_order(symbol=symbol.upper(), orderId=tp_order_id)
                    logger.info(f"🗑️ Cancelled orphan TP order {tp_order_id}")
                except Exception as e:
                    logger.error(f"❌ Error cancelling TP order: {e}")

                # Obtener SL execution info
                exit_price, exit_time = self._get_order_execution_info(
                    client, symbol.upper(), sl_order_id
                )

                # Actualizar BD con STOP_HIT
                self._update_trade_exit_in_db(
                    protection_system,
                    last_trade_from_db['id'],
                    exit_time or datetime.now(timezone.utc),
                    exit_price or last_trade_from_db.get('stop_price', 0),
                    'stop_hit'
                )

                logger.warning(
                    f"📝 Updated BD: Trade {last_trade_from_db['id']} marked as STOP_HIT "
                    f"at ${exit_price}"
                )

                return True, "stop_hit_orphan_tp_cancelled", {
                    'exit_reason': 'stop_hit',
                    'exit_price': exit_price,
                    'exit_time': exit_time,
                    'orphan_cancelled': 'tp'
                }

            # No debería llegar aquí
            return False, "unknown_state", None

        except Exception as e:
            logger.error(f"❌ Error checking orphan orders for {user_id}/{symbol}: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return False, f"error: {str(e)}", None

    def _determine_exit_from_order_history(
        self,
        client,
        symbol: str,
        sl_order_id: int,
        tp_order_id: int
    ) -> Tuple[Optional[str], Optional[float], Optional[datetime]]:
        """
        Determina cómo se cerró el trade consultando el historial de orders.

        Returns:
            Tuple[exit_reason, exit_price, exit_time] o (None, None, None)
        """
        try:
            # Obtener todas las orders (incluye ejecutadas)
            all_orders = client.futures_get_all_orders(symbol=symbol, limit=50)

            for order in all_orders:
                order_id = order.get('orderId')
                status = order.get('status')

                if status == 'FILLED':
                    if order_id == sl_order_id:
                        # SL ejecutado
                        avg_price = float(order.get('avgPrice', 0))
                        update_time = order.get('updateTime')
                        exit_time = datetime.fromtimestamp(
                            update_time / 1000, tz=timezone.utc
                        ) if update_time else None
                        return 'stop_hit', avg_price, exit_time

                    elif order_id == tp_order_id:
                        # TP ejecutado
                        avg_price = float(order.get('avgPrice', 0))
                        update_time = order.get('updateTime')
                        exit_time = datetime.fromtimestamp(
                            update_time / 1000, tz=timezone.utc
                        ) if update_time else None
                        return 'target_hit', avg_price, exit_time

            return None, None, None

        except Exception as e:
            logger.error(f"❌ Error checking order history: {e}")
            return None, None, None

    def _get_order_execution_info(
        self,
        client,
        symbol: str,
        order_id: int
    ) -> Tuple[Optional[float], Optional[datetime]]:
        """
        Obtiene precio y tiempo de ejecución de un order.

        Returns:
            Tuple[avg_price, execution_time] o (None, None)
        """
        try:
            order = client.futures_get_order(symbol=symbol, orderId=order_id)

            if order.get('status') == 'FILLED':
                avg_price = float(order.get('avgPrice', 0))
                update_time = order.get('updateTime')
                execution_time = datetime.fromtimestamp(
                    update_time / 1000, tz=timezone.utc
                ) if update_time else None

                return avg_price, execution_time

            return None, None

        except Exception as e:
            logger.error(f"❌ Error getting order execution info: {e}")
            return None, None

    def _update_trade_exit_in_db(
        self,
        protection_system,
        trade_id: int,
        exit_time: datetime,
        exit_price: float,
        exit_reason: str
    ):
        """
        Actualiza el trade en BD con información de salida.

        Args:
            protection_system: Instancia de TradeProtectionSystem
            trade_id: ID del trade en BD
            exit_time: Timestamp de salida
            exit_price: Precio de salida
            exit_reason: Razón de salida (stop_hit, target_hit, etc.)
        """
        try:
            conn = protection_system._get_conn()

            query = """
            UPDATE trade_history
            SET exit_time = %s,
                exit_price = %s,
                exit_reason = %s,
                updated_at = NOW()
            WHERE id = %s
            """

            with conn.cursor() as cur:
                cur.execute(query, (exit_time, exit_price, exit_reason, trade_id))
                conn.commit()

            conn.close()

            logger.info(
                f"✅ Updated trade {trade_id} in BD: "
                f"exit_reason={exit_reason}, exit_price=${exit_price}"
            )

        except Exception as e:
            logger.error(f"❌ Error updating trade {trade_id} in BD: {e}")


# Singleton instance
_detector_instance = None


def get_orphan_order_detector() -> OrphanOrderDetector:
    """Get singleton instance of orphan order detector."""
    global _detector_instance
    if _detector_instance is None:
        _detector_instance = OrphanOrderDetector()
    return _detector_instance
