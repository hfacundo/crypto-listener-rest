# app/multi_user_execution.py

import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Dict, Any, Tuple

from app.utils.logger_config import get_logger
logger = get_logger()
from app.futures import (
    close_position_and_cancel_orders,
    adjust_stop_only_for_open_position,
    half_close_and_move_be
)
from app.utils.binance.binance_client import get_binance_client_for_user
from app.utils.db.query_executor import get_rules
from app.market_validation import get_fresh_market_data, validate_guardian_decision_freshness


def execute_guardian_action_for_user(user_id: str, symbol: str, action: str,
                                   message: Dict[str, Any],
                                   fresh_market_data: Dict[str, Any] = None,
                                   adjusted_params: Dict[str, Any] = None) -> Dict[str, Any]:
    """
    Ejecuta acción de guardian para un usuario específico
    """
    try:
        # CRITICAL FIX: Crear event loop en threads para evitar asyncio errors
        import asyncio
        try:
            loop = asyncio.get_event_loop()
            if loop.is_closed():
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
        except RuntimeError:
            # No hay event loop en este thread, crear uno nuevo
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

        client = get_binance_client_for_user(user_id)
        rules = get_rules(user_id, "archer_model")  # Estrategia default

        # Verificar si guardian está habilitado para este usuario
        if not rules.get("use_guardian", True):
            return {
                "user_id": user_id,
                "success": False,
                "reason": "guardian_disabled",
                "timestamp": time.time()
            }

        # Verificar reglas específicas de half_close
        if action == "half_close" and not rules.get("use_guardian_half", False):
            return {
                "user_id": user_id,
                "success": False,
                "reason": "half_close_disabled",
                "timestamp": time.time()
            }

        symbol_upper = symbol.upper()
        execution_start = time.time()

        # Ejecutar según tipo de acción
        if action == "close":
            result = close_position_and_cancel_orders(symbol_upper, client, user_id)
            action_type = "CLOSE"

        elif action == "adjust":
            # Usar stop ajustado si está disponible
            stop_price = adjusted_params.get("stop", message.get("stop"))
            if stop_price is None:
                return {
                    "user_id": user_id,
                    "success": False,
                    "reason": "no_stop_price",
                    "timestamp": time.time()
                }

            stop_price = float(stop_price)

            # Extraer level_metadata si está disponible (trailing stop multinivel)
            level_metadata = message.get("level_metadata")

            result = adjust_stop_only_for_open_position(symbol_upper, stop_price, client, user_id, level_metadata)
            action_type = "ADJUST"

        elif action == "half_close":
            result = half_close_and_move_be(symbol_upper, client, user_id)
            action_type = "HALF_CLOSE"

        else:
            return {
                "user_id": user_id,
                "success": False,
                "reason": f"unknown_action_{action}",
                "timestamp": time.time()
            }

        execution_end = time.time()
        execution_time = execution_end - execution_start

        # Procesar resultado
        success = result.get("success", False)
        error_msg = result.get("error", "")

        return {
            "user_id": user_id,
            "success": success,
            "action": action_type,
            "execution_time_sec": round(execution_time, 3),
            "result": result,
            "market_price_at_execution": fresh_market_data.get("mark_price") if fresh_market_data else None,
            "timestamp": execution_end,
            "reason": "executed_successfully" if success else f"execution_failed_{error_msg}"
        }

    except Exception as e:
        return {
            "user_id": user_id,
            "success": False,
            "action": action,
            "error": str(e),
            "timestamp": time.time(),
            "reason": f"exception_{str(e)}"
        }
    finally:
        # Cleanup event loop para evitar memory leaks en threads
        try:
            import asyncio
            loop = asyncio.get_event_loop()
            if loop and not loop.is_running():
                loop.close()
        except:
            pass  # Ignorar errores de cleanup


def execute_close_parallel(users: List[str], symbol: str, message: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Ejecuta CLOSE en paralelo para múltiples usuarios (velocidad crítica)
    """
    logger.info(f"🚨 Executing CLOSE in parallel for {len(users)} users: {symbol}")

    results = []
    with ThreadPoolExecutor(max_workers=len(users), thread_name_prefix="guardian_close") as executor:
        # Submit todas las tareas
        future_to_user = {}
        for user_id in users:
            # Obtener datos frescos por separado para cada usuario (evita race conditions)
            future = executor.submit(
                execute_guardian_action_for_user,
                user_id, symbol, "close", message, None, None
            )
            future_to_user[future] = user_id

        # Recoger resultados con timeout
        for future in as_completed(future_to_user, timeout=15):
            user_id = future_to_user[future]
            try:
                result = future.result(timeout=10)
                results.append(result)
                success_emoji = "✅" if result["success"] else "❌"
                logger.info(f"{success_emoji} Close executed for {user_id}: {result.get('reason', 'unknown')}")
            except Exception as e:
                error_result = {
                    "user_id": user_id,
                    "success": False,
                    "error": str(e),
                    "reason": f"parallel_execution_failed_{str(e)}",
                    "timestamp": time.time()
                }
                results.append(error_result)
                logger.error(f"❌ Close failed for {user_id}: {e}")

    return results


def execute_adjust_sequential(users: List[str], symbol: str, message: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Ejecuta ADJUST secuencialmente con datos frescos para cada usuario
    """
    logger.info(f"🔧 Executing ADJUST sequentially for {len(users)} users: {symbol}")

    results = []
    for i, user_id in enumerate(users):
        try:
            # Breve delay entre usuarios para evitar conflicts
            if i > 0:
                time.sleep(0.3)

            # Obtener datos frescos para este usuario
            fresh_data = get_fresh_market_data(symbol, user_id)

            # Validar decisión con datos frescos
            is_valid, reason, adjusted_params = validate_guardian_decision_freshness(message, fresh_data)

            if not is_valid:
                result = {
                    "user_id": user_id,
                    "success": False,
                    "reason": f"validation_failed_{reason}",
                    "timestamp": time.time()
                }
                results.append(result)
                logger.warning(f"⚠️ Adjust validation failed for {user_id}: {reason}")
                continue

            # Ejecutar adjust con parámetros ajustados
            result = execute_guardian_action_for_user(
                user_id, symbol, "adjust", message, fresh_data, adjusted_params
            )
            results.append(result)

            success_emoji = "✅" if result["success"] else "❌"
            stop_price = adjusted_params.get("stop", message.get("stop", "N/A"))
            logger.info(f"{success_emoji} Adjust executed for {user_id}: stop={stop_price}, reason={result.get('reason')}")

        except Exception as e:
            error_result = {
                "user_id": user_id,
                "success": False,
                "error": str(e),
                "reason": f"sequential_execution_failed_{str(e)}",
                "timestamp": time.time()
            }
            results.append(error_result)
            logger.error(f"❌ Adjust failed for {user_id}: {e}")

    return results


def execute_half_close_sequential(users: List[str], symbol: str, message: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Ejecuta HALF_CLOSE secuencialmente con validación
    """
    logger.info(f"💰 Executing HALF_CLOSE sequentially for {len(users)} users: {symbol}")

    results = []
    for i, user_id in enumerate(users):
        try:
            if i > 0:
                time.sleep(0.5)  # Delay más largo para half_close

            # Datos frescos y validación
            fresh_data = get_fresh_market_data(symbol, user_id)
            is_valid, reason, adjusted_params = validate_guardian_decision_freshness(message, fresh_data)

            if not is_valid:
                result = {
                    "user_id": user_id,
                    "success": False,
                    "reason": f"validation_failed_{reason}",
                    "timestamp": time.time()
                }
                results.append(result)
                logger.warning(f"⚠️ Half close validation failed for {user_id}: {reason}")
                continue

            # Ejecutar half_close
            result = execute_guardian_action_for_user(
                user_id, symbol, "half_close", message, fresh_data, adjusted_params
            )
            results.append(result)

            success_emoji = "✅" if result["success"] else "❌"
            logger.info(f"{success_emoji} Half close executed for {user_id}: {result.get('reason')}")

        except Exception as e:
            error_result = {
                "user_id": user_id,
                "success": False,
                "error": str(e),
                "reason": f"half_close_execution_failed_{str(e)}",
                "timestamp": time.time()
            }
            results.append(error_result)
            logger.error(f"❌ Half close failed for {user_id}: {e}")

    return results


def execute_multi_user_guardian_action(users: List[str], symbol: str,
                                     message: Dict[str, Any]) -> Dict[str, Any]:
    """
    Orchestrador principal para ejecución multi-usuario optimizada
    """
    action = message.get("action", "").lower()
    execution_start = time.time()

    logger.info(f"🛡️ Guardian multi-user execution: {action.upper()} for {symbol} across {len(users)} users")

    # Ejecutar según estrategia óptima por tipo de acción
    if action == "close":
        # CLOSE: Paralelo para velocidad máxima
        results = execute_close_parallel(users, symbol, message)

    elif action == "adjust":
        # ADJUST: Secuencial con fresh data para precisión
        results = execute_adjust_sequential(users, symbol, message)

    elif action == "half_close":
        # HALF_CLOSE: Secuencial con validación estricta
        results = execute_half_close_sequential(users, symbol, message)

    else:
        # Acción desconocida
        results = [{
            "user_id": user_id,
            "success": False,
            "reason": f"unknown_action_{action}",
            "timestamp": time.time()
        } for user_id in users]

    execution_end = time.time()
    total_execution_time = execution_end - execution_start

    # Compilar estadísticas
    successful_executions = [r for r in results if r.get("success", False)]
    failed_executions = [r for r in results if not r.get("success", False)]

    summary = {
        "action": action.upper(),
        "symbol": symbol.upper(),
        "total_users": len(users),
        "successful_users": len(successful_executions),
        "failed_users": len(failed_executions),
        "success_rate": len(successful_executions) / len(users) * 100 if users else 0,
        "total_execution_time_sec": round(total_execution_time, 3),
        "results": results,
        "timestamp": execution_end
    }

    # Log final
    logger.info(f"📊 Guardian execution summary: {action.upper()} {symbol}")
    logger.info(f"   ✅ Success: {len(successful_executions)}/{len(users)} users ({summary['success_rate']:.1f}%)")
    logger.info(f"   ⏱️  Total time: {total_execution_time:.3f}s")

    return summary