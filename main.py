"""
crypto-listener-rest: FastAPI service for immediate trade execution
"""
import sys
import os
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import json
import time
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, Any, List, Optional

from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

# Import existing crypto-listener logic
from app.futures import (
    create_trade,
    close_position_and_cancel_orders,
    adjust_sl_tp_for_open_position,
    adjust_stop_only_for_open_position,
    half_close_and_move_be,
)
from app.multi_user_execution import execute_multi_user_guardian_action
from app.market_validation import get_fresh_market_data, validate_guardian_decision_freshness, should_proceed_with_execution
from app.trade_limits import check_trade_limit, log_trade_limit_status, get_trade_limit_summary

from app.utils.db.query_executor import get_rules, is_symbol_banned
from app.utils.binance.binance_client import get_binance_client_for_user
from app.utils.config.settings import (
    COPY_TRADING, FUTURES, HUFSA, COPY_2
)
from app.utils.binance.utils import is_trade_allowed_by_schedule_utc
from app.utils.sqs_evaluator import SQSEvaluator
from app.utils.user_risk_validator import UserRiskProfileValidator

# ========== LOGGING CONFIGURATION ==========
from app.utils.logger_config import get_logger

# Inicializar logger global
logger = get_logger("crypto-listener-rest")
# ===========================================

# Configuration
DEPLOYMENT_ENV = os.environ.get("DEPLOYMENT_ENV", "main")

if DEPLOYMENT_ENV == "main":
    USERS = [COPY_TRADING, HUFSA, COPY_2, FUTURES]
    logger.info(f"🟢 Entorno: PRINCIPAL (4 usuarios: COPY_TRADING, HUFSA, COPY_2, FUTURES)")
elif DEPLOYMENT_ENV == "secondary":
    USERS = [COPY_2, FUTURES]
    logger.info(f"🟡 Entorno: SECUNDARIO (COPY_2, FUTURES)")
else:
    raise ValueError(f"DEPLOYMENT_ENV inválido: {DEPLOYMENT_ENV}")

STRATEGY = "archer_dual"

# FastAPI app
app = FastAPI(
    title="crypto-listener-rest",
    description="REST API for immediate crypto trade execution",
    version="1.0.0"
)

# Pydantic models for request validation
class TradeRequest(BaseModel):
    """Request model for trade execution"""
    symbol: str = Field(..., description="Trading pair (e.g., BTCUSDT)")
    entry: float = Field(..., description="Entry price")
    stop: float = Field(..., description="Stop loss price")
    target: float = Field(..., description="Target price")
    trade: str = Field(..., description="Trade direction: LONG or SHORT")
    rr: float = Field(..., description="Risk/Reward ratio")
    probability: float = Field(..., description="Win probability percentage")
    strategy: str = Field(default="archer_dual", description="Strategy name")
    signal_quality_score: Optional[float] = Field(default=0, description="Signal quality score")
    tier: Optional[int] = Field(default=None, description="Signal tier (1-10) from crypto-analyzer-redis")

    class Config:
        json_schema_extra = {
            "example": {
                "symbol": "BTCUSDT",
                "entry": 45000.0,
                "stop": 44500.0,
                "target": 46000.0,
                "trade": "LONG",
                "rr": 2.0,
                "probability": 75.0,
                "strategy": "archer_dual",
                "signal_quality_score": 8.5,
                "tier": 3
            }
        }

# ============================================================================
# DEPRECATED: GuardianRequest model - NO LONGER USED
# ============================================================================
# This model was used by the /guardian endpoint which proxied guardian actions
# to Binance. The new unified_guardian.py in crypto-guardian accesses Binance
# directly, eliminating the HTTP overhead and simplifying the architecture.
#
# Removed: 2025-01-29
# Reason: unified_guardian.py replaces dual monitor system and accesses Binance directly
# ============================================================================
#
# class GuardianRequest(BaseModel):
#     """Request model for guardian actions"""
#     symbol: str = Field(..., description="Trading pair")
#     action: str = Field(..., description="Action: close, adjust, half_close")
#     stop: Optional[float] = Field(default=None, description="New stop price (for adjust)")
#     target: Optional[float] = Field(default=None, description="New target price")
#     user_id: Optional[str] = Field(default=None, description="Specific user (optional)")
#     market_context: Optional[Dict[str, Any]] = Field(default=None, description="Market context data")
#     level_metadata: Optional[Dict[str, Any]] = Field(default=None, description="Trailing stop level metadata (level_name, threshold_pct, previous_level)")
#
#     class Config:
#         json_schema_extra = {
#             "example": {
#                 "symbol": "BTCUSDT",
#                 "action": "close",
#                 "user_id": "User_1"
#             }
#         }

# Core trade processing logic (from Lambda)
def process_user_trade(user_id: str, message: dict, strategy: str) -> dict:
    """
    Procesa un trade para un usuario específico de forma síncrona.

    Args:
        user_id: ID del usuario
        message: Datos del trade
        strategy: Estrategia a usar

    Returns:
        dict con resultado: {"user_id": str, "success": bool, "reason": str}
    """
    log_prefix = f"[{user_id}]"

    # ✨ FIX: Ensure event loop exists in thread (for Binance async operations)
    # Some Binance client operations may use asyncio internally, and threads
    # created by ThreadPoolExecutor don't have an event loop by default
    import asyncio
    try:
        asyncio.get_event_loop()
    except RuntimeError:
        # No event loop in this thread, create one
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        print(f"{log_prefix} Created new event loop for thread")

    try:
        print(f"{log_prefix} Validando trade")
        rules = get_rules(user_id, strategy)

        if not rules.get("enabled"):
            print(f"{log_prefix} Usuario deshabilitado")
            return {"user_id": user_id, "success": False, "reason": "user_disabled"}

        # Extraer campos del mensaje
        symbol = message.get("symbol")
        entry_price = message.get("entry")
        stop_loss = message.get("stop")
        target_price = message.get("target")
        direction = message.get("trade")
        rr = message.get("rr")
        probability = message.get("probability")
        signal_quality_score = message.get("signal_quality_score", 0)
        tier = message.get("tier")  # ✨ NEW: Extract tier from crypto-analyzer-redis

        # 🧪 TEST MODE: Detectar si es un trade de prueba
        is_test = message.get("is_test", False)
        test_users_str = message.get("test_users", None)  # Lista separada por comas
        test_leverage = message.get("test_leverage", None)  # Leverage específico para test

        # Convertir string de usuarios a lista
        test_users_list = []
        if test_users_str:
            test_users_list = [u.strip() for u in test_users_str.split(",")]

        # Debug logging para test mode
        if is_test:
            print(f"{log_prefix} 🧪 TEST MODE DEBUG: is_test={is_test}, test_users={test_users_list}, current_user={user_id}")

        tier_info = f" TIER {tier}" if tier else ""
        test_info = f" 🧪 TEST MODE (LEV {test_leverage}x)" if is_test and test_leverage else (" 🧪 TEST MODE" if is_test else "")
        direction_str = direction.upper() if direction else "UNKNOWN"
        print(f"{log_prefix} {symbol} | {direction_str} | Prob: {probability}% | RR: {rr} | SQS: {signal_quality_score:.1f}{tier_info}{test_info}")

        # 🧪 TEST MODE: Si es test, solo procesar para usuarios en la lista
        if is_test and test_users_list and user_id not in test_users_list:
            print(f"{log_prefix} 🧪 TEST MODE: Skipping user (allowed_users={test_users_list})")
            return {"user_id": user_id, "success": False, "reason": "test_mode_skip"}

        # BANNED SYMBOLS VALIDATION
        if is_symbol_banned(user_id, strategy, symbol):
            print(f"{log_prefix} Trade REJECTED: {symbol} is in banned symbols list")
            return {"user_id": user_id, "success": False, "reason": "banned_symbol"}

        # VALIDACIÓN INTEGRADA
        validator = UserRiskProfileValidator(user_id, strategy, rules)

        can_trade, reason, validation_data = validator.validate_trade(
            symbol=symbol,
            direction=direction,
            entry_price=entry_price,
            stop_price=stop_loss,
            target_price=target_price,
            probability=probability,
            sqs=signal_quality_score,
            rr=rr,
            tier=tier  # ✨ NEW: Pass tier to validator
        )

        if not can_trade:
            print(f"{log_prefix} Trade REJECTED: {reason}")

            # Log información adicional según el tipo de rechazo
            if validation_data.get("current_count") is not None:
                current_count = validation_data.get("current_count", 0)
                max_allowed = validation_data.get("max_allowed", 0)
                print(f"{log_prefix}    Current: {current_count}/{max_allowed} trades open")

                if validation_data.get("current_symbols"):
                    symbols_str = ', '.join(validation_data["current_symbols"])
                    print(f"{log_prefix}    Open positions: {symbols_str}")

            if validation_data.get("daily_loss_pct") is not None:
                daily_loss = validation_data.get("daily_loss_pct", 0)
                print(f"{log_prefix}    Daily loss: {daily_loss:.2f}%")

            if validation_data.get("last_stop_hours_ago") is not None:
                hours_ago = validation_data.get("last_stop_hours_ago", 0)
                cooldown = validation_data.get("cooldown_required_hours", 0)
                print(f"{log_prefix}    Last stop: {hours_ago:.1f}h ago (cooldown: {cooldown}h)")

            return {"user_id": user_id, "success": False, "reason": reason}

        # Obtener capital_multiplier del SQS
        capital_multiplier = validation_data.get("capital_multiplier", 1.0)
        sqs_grade = validation_data.get("sqs_grade", "N/A")

        # 🧪 TEST MODE: Override capital multiplier para usuarios en la lista
        if is_test and test_users_list and user_id in test_users_list:
            original_multiplier = capital_multiplier
            capital_multiplier = 0.001  # 0.1% del capital normal
            print(f"{log_prefix} 🧪 TEST MODE: Capital multiplier overridden: {original_multiplier:.3f}x → {capital_multiplier:.3f}x (0.1%)")

        print(f"{log_prefix} ALL VALIDATIONS PASSED")
        print(f"{log_prefix} Capital multiplier: {capital_multiplier:.3f}x ({sqs_grade})")

        # Log remaining slots if limits are configured
        if validation_data.get("max_allowed", 999) < 999:
            remaining = validation_data.get("max_allowed", 0) - validation_data.get("current_count", 0)
            print(f"{log_prefix} Trade slots: {remaining} remaining")

        # Crear el trade
        client = get_binance_client_for_user(user_id)
        print(f"{log_prefix} Seteando binance client")

        # 🧪 TEST MODE: Preparar leverage específico para test
        leverage_override = test_leverage if (is_test and test_users_list and user_id in test_users_list and test_leverage) else None

        order = create_trade(
            symbol, entry_price, stop_loss, target_price, direction,
            rr, probability, rules, client, user_id, strategy,
            signal_quality_score, capital_multiplier, leverage_override
        )

        if order is not None and order.get("success"):
            print(f"{log_prefix} Trade exitoso")

            # REGISTRAR EN POSTGRESQL Y REDIS
            try:
                # Extraer order_ids del resultado de Binance
                order_id = order.get("order_id")
                sl_order_id = order.get("sl_order_id")
                tp_order_id = order.get("tp_order_id")

                trade_id = validator.record_trade_opened(
                    symbol=symbol,
                    direction=direction,
                    entry_time=datetime.now(timezone.utc),
                    entry_price=entry_price,
                    stop_price=stop_loss,
                    target_price=target_price,
                    probability=probability,
                    sqs=signal_quality_score,
                    rr=rr,
                    order_id=order_id,
                    sl_order_id=sl_order_id,
                    tp_order_id=tp_order_id
                )

                if trade_id and trade_id != -1:
                    print(f"{log_prefix} Trade registered in PostgreSQL: trade_id={trade_id}")

                    # Guardar trade_id en Redis
                    try:
                        from app.utils.db.redis_client import get_redis_client
                        redis_client = get_redis_client()
                        if redis_client:
                            redis_key = f"trade_id:{user_id}:{symbol.upper()}"
                            redis_client.setex(redis_key, 7*24*3600, str(trade_id))
                            print(f"{log_prefix} Trade ID saved to Redis: {redis_key} = {trade_id}")

                            # DUAL WRITE: Guardian trade data
                            guardian_trade_data = {
                                "symbol": symbol.upper(),
                                "side": direction.upper(),
                                "direction": direction.upper(),  # Para light_check.py (compatibilidad)
                                "entry": entry_price,
                                "stop": stop_loss,           # Para trailing_stop.py y guardian_service.py
                                "stop_loss": stop_loss,      # Para light_check.py y decisions.py (compatibilidad)
                                "original_stop": stop_loss,  # CRÍTICO: Preservar stop original para cálculo correcto de R
                                "target": target_price,
                                "user_id": user_id,
                                "strategy": strategy,
                                "timestamp": time.time(),
                                "rr": rr,
                                "probability": probability,
                                "sqs": signal_quality_score,
                                "trade_id": trade_id
                            }
                            guardian_key = f"guardian:trades:{user_id}:{symbol.upper()}"
                            redis_client.setex(
                                guardian_key,
                                7 * 24 * 3600,
                                json.dumps(guardian_trade_data)
                            )
                            print(f"{log_prefix} Guardian trade saved to Redis: {guardian_key}")

                    except Exception as e:
                        print(f"{log_prefix} Error saving to Redis: {e}")

            except Exception as e:
                print(f"{log_prefix} Error registering trade in PostgreSQL: {e}")

            return {"user_id": user_id, "success": True, "reason": "trade_created", "trade_id": trade_id if 'trade_id' in locals() else None}
        else:
            print(f"{log_prefix} Trade no realizado")
            return {"user_id": user_id, "success": False, "reason": "order_failed"}

    except Exception as e:
        print(f"{log_prefix} Error processing: {e}")
        import traceback
        print(f"{log_prefix} Traceback: {traceback.format_exc()}")
        return {"user_id": user_id, "success": False, "reason": f"exception: {str(e)}"}


# API Endpoints
@app.post("/execute-trade")
async def execute_trade(trade: TradeRequest) -> JSONResponse:
    """
    Ejecuta un trade inmediatamente para todos los usuarios configurados.

    Procesa de forma síncrona sin cola - si falla, falla inmediatamente.
    """
    start_time = time.time()

    print(f"📩 Trade request received: {trade.symbol} {trade.trade} @ {trade.entry}")

    # Validar campos requeridos
    if not all([trade.symbol, trade.entry, trade.stop, trade.target, trade.trade, trade.rr, trade.probability]):
        raise HTTPException(status_code=400, detail="Missing required fields")

    # Convertir a dict para procesar
    message = trade.model_dump()

    # PARALLEL EXECUTION: Process all users simultaneously
    print(f"🚀 Processing trade for {len(USERS)} users in parallel")

    with ThreadPoolExecutor(max_workers=len(USERS), thread_name_prefix="User") as executor:
        # Submit all user tasks
        futures = {
            executor.submit(process_user_trade, user_id, message, STRATEGY): user_id
            for user_id in USERS
        }

        # Collect results as they complete
        results = []
        for future in as_completed(futures):
            user_id = futures[future]
            try:
                result = future.result()
                results.append(result)
            except Exception as e:
                print(f"❌ Exception in thread for {user_id}: {e}")
                import traceback
                print(f"Traceback: {traceback.format_exc()}")
                results.append({"user_id": user_id, "success": False, "reason": f"thread_exception: {str(e)}"})

    # Log summary
    successful = sum(1 for r in results if r.get("success"))
    failed = len(results) - successful
    execution_time = time.time() - start_time

    print(f"📊 Trade processing complete: {successful} successful, {failed} failed in {execution_time:.3f}s")

    for result in results:
        status = "✅" if result["success"] else "❌"
        print(f"{status} {result['user_id']}: {result['reason']}")

    return JSONResponse(
        status_code=200 if successful > 0 else 400,
        content={
            "status": "completed",
            "symbol": trade.symbol,
            "successful": successful,
            "failed": failed,
            "total_users": len(USERS),
            "execution_time_sec": round(execution_time, 3),
            "results": results
        }
    )


# ============================================================================
# DEPRECATED: /guardian endpoint - NO LONGER USED
# ============================================================================
# This endpoint was used by the dual monitor system (hot_trailing_monitor.py +
# guardian_service.py) to proxy guardian actions to Binance via HTTP.
#
# The new unified_guardian.py in crypto-guardian accesses Binance directly,
# eliminating this HTTP proxy layer and reducing latency by 20-50ms.
#
# Architecture change:
# OLD: guardian → HTTP POST /guardian → crypto-listener-rest → Binance
# NEW: unified_guardian → Binance (direct)
#
# Removed: 2025-01-29
# Reason: unified_guardian.py replaces dual monitor system
# ============================================================================
#
# @app.post("/guardian")
# async def guardian_action(guardian: GuardianRequest) -> JSONResponse:
#     """
#     Ejecuta una acción del guardian (close, adjust, half_close) inmediatamente.
#     """
#     ... [157 lines of code removed] ...
#     # See git history for full endpoint implementation


@app.get("/health")
async def health_check():
    """Health check endpoint"""
    try:
        # Test database connection
        from app.utils.db.query_executor import get_engine
        from sqlalchemy import text

        engine = get_engine()
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))

        db_status = "connected"
    except Exception as e:
        db_status = f"error: {str(e)}"

    return {
        "status": "ok",
        "service": "crypto-listener-rest",
        "environment": DEPLOYMENT_ENV,
        "users": USERS,
        "strategy": STRATEGY,
        "database": db_status
    }


@app.get("/stats")
async def get_stats():
    """Get current trade statistics for all users"""
    try:
        stats = {}
        for user_id in USERS:
            try:
                rules = get_rules(user_id, STRATEGY)
                summary = get_trade_limit_summary(user_id, rules)
                stats[user_id] = summary
            except Exception as e:
                stats[user_id] = {"error": str(e)}

        return {
            "status": "ok",
            "environment": DEPLOYMENT_ENV,
            "strategy": STRATEGY,
            "user_stats": stats
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get stats: {str(e)}")


@app.get("/")
async def root():
    """Root endpoint with service information"""
    return {
        "service": "crypto-listener-rest",
        "version": "1.0.0",
        "description": "REST API for immediate crypto trade execution",
        "endpoints": {
            "POST /execute-trade": "Execute a trade immediately",
            "POST /guardian": "Execute guardian action (close/adjust/half_close)",
            "GET /health": "Health check",
            "GET /stats": "Get trade statistics",
            "GET /docs": "Interactive API documentation"
        }
    }


if __name__ == "__main__":
    import uvicorn

    # Run on localhost only (not exposed to internet)
    uvicorn.run(
        app,
        host="127.0.0.1",  # localhost only
        port=8000,
        log_level="info"
    )
