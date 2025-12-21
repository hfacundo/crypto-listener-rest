"""
crypto-listener-rest: FastAPI service for immediate trade execution
"""
import sys
import os
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import json
import time
import uuid
from contextvars import ContextVar
from datetime import datetime, timezone, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, Any, List, Optional

from fastapi import FastAPI, HTTPException, BackgroundTasks, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

# Import existing crypto-listener logic
from app.futures import (
    create_trade,
    close_position_and_cancel_orders,
    adjust_sl_tp_for_open_position,
    adjust_stop_only_for_open_position,
    half_close_and_move_be,
    get_current_sl_tp,  # NEW: Get current SL/TP from orders
    cancel_tp_only,     # NEW: Cancel only TP orders
)
from app.multi_user_execution import execute_multi_user_guardian_action
from app.market_validation import get_fresh_market_data, validate_guardian_decision_freshness, should_proceed_with_execution
from app.trade_limits import check_trade_limit, log_trade_limit_status, get_trade_limit_summary

from app.utils.db.query_executor import get_rules, is_symbol_banned
from app.utils.binance.binance_client import get_binance_client_for_user
from app.utils.config.settings import (
    COPY_TRADING, FUTURES, HUFSA, COPY_2
)
from app.utils.binance.utils import is_trade_allowed_by_schedule_utc, get_mark_price, get_symbol_filters
from app.utils.binance.validators import (
    validate_min_notional_for_manual_trading,
    validate_sl_distance_from_mark_price,
    validate_risk_reward_ratio_for_manual_trading
)
from app.utils.binance.error_handler import (
    handle_binance_exception,
    format_binance_error_for_logging
)
from app.utils.sqs_evaluator import SQSEvaluator
from app.utils.user_risk_validator import UserRiskProfileValidator
from binance.exceptions import BinanceAPIException

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

STRATEGY = "archer_model"

# FastAPI app
app = FastAPI(
    title="crypto-listener-rest",
    description="REST API for immediate crypto trade execution",
    version="1.1.0"  # Updated for improved endpoints
)

# ========== REQUEST ID MIDDLEWARE (Structured Logging) ==========
request_id_var: ContextVar[str] = ContextVar('request_id', default=None)

@app.middleware("http")
async def add_request_id(request: Request, call_next):
    """
    Add unique request ID to each request for traceability.
    Also clears request-level cache from binance_fetch module.
    """
    from app.utils.binance.binance_fetch import clear_request_cache

    request_id = str(uuid.uuid4())[:8]
    request_id_var.set(request_id)

    # Clear request-level cache for new request
    clear_request_cache()

    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    return response
# ==============================================================

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
    ev: Optional[float] = Field(default=None, description="Expected Value (EV)")
    strategy: str = Field(default="archer_model", description="Strategy name")
    signal_quality_score: Optional[float] = Field(default=0, description="Signal quality score")
    tier: Optional[int] = Field(default=None, description="Signal tier (1-10) from crypto-analyzer-redis")
    # ⏰ TIMESTAMP FIELDS (Nov 2025 - Phase 2: Candle-sync)
    generated_at_utc: Optional[str] = Field(default=None, description="Signal generation time (ISO format UTC)")
    generated_timestamp: Optional[float] = Field(default=None, description="Signal generation time (Unix timestamp)")

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
                "strategy": "archer_model",
                "signal_quality_score": 8.5,
                "tier": 3
            }
        }


# ========== MANUAL TRADING CONTROL MODELS ==========
class ClosePositionRequest(BaseModel):
    """Request model for closing a position"""
    user_id: str = Field(..., description="User ID (copy_trading, futures, hufsa, copy_2)")
    symbol: str = Field(..., description="Trading pair (e.g., BTCUSDT)")

    class Config:
        json_schema_extra = {
            "example": {
                "user_id": "copy_trading",
                "symbol": "BTCUSDT"
            }
        }


class SetStopLossRequest(BaseModel):
    """Request model for setting stop loss"""
    user_id: str = Field(..., description="User ID (copy_trading, futures, hufsa, copy_2)")
    symbol: str = Field(..., description="Trading pair (e.g., BTCUSDT)")
    stop_loss: float = Field(..., description="New stop loss price")
    force_adjust: bool = Field(default=False, description="If True, bypass tighten-only validation (allows looser stops)")

    class Config:
        json_schema_extra = {
            "example": {
                "user_id": "copy_trading",
                "symbol": "BTCUSDT",
                "stop_loss": 44000.0,
                "force_adjust": False
            }
        }


class SetTakeProfitRequest(BaseModel):
    """Request model for setting take profit"""
    user_id: str = Field(..., description="User ID (copy_trading, futures, hufsa, copy_2)")
    symbol: str = Field(..., description="Trading pair (e.g., BTCUSDT)")
    take_profit: float = Field(..., description="New take profit price")

    class Config:
        json_schema_extra = {
            "example": {
                "user_id": "copy_trading",
                "symbol": "BTCUSDT",
                "take_profit": 47000.0
            }
        }


class AdjustSLTPRequest(BaseModel):
    """Request model for adjusting both stop loss and take profit"""
    user_id: str = Field(..., description="User ID (copy_trading, futures, hufsa, copy_2)")
    symbol: str = Field(..., description="Trading pair (e.g., BTCUSDT)")
    stop_loss: float = Field(..., description="New stop loss price")
    take_profit: float = Field(..., description="New take profit price")

    class Config:
        json_schema_extra = {
            "example": {
                "user_id": "copy_trading",
                "symbol": "BTCUSDT",
                "stop_loss": 44000.0,
                "take_profit": 47000.0
            }
        }


class FlexibleAdjustRequest(BaseModel):
    """Request model for flexible adjustment: SL only, TP only, both, or remove TP"""
    user_id: str = Field(..., description="User ID (copy_trading, futures, hufsa, copy_2)")
    symbol: str = Field(..., description="Trading pair (e.g., BTCUSDT)")
    stop_loss: Optional[float] = Field(default=None, description="New stop loss price (optional)")
    take_profit: Optional[float] = Field(default=None, description="New take profit price (optional)")
    remove_take_profit: bool = Field(default=False, description="Remove TP order entirely")

    class Config:
        json_schema_extra = {
            "example": {
                "user_id": "copy_trading",
                "symbol": "BTCUSDT",
                "stop_loss": 44000.0,
                "take_profit": None,
                "remove_take_profit": False
            }
        }

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

        # ✨ NEW: Obtener configuración de modelo del usuario (default: "all")
        user_model_filter = rules.get("model", "all")

        # Extraer campos del mensaje
        symbol = message.get("symbol")
        # Normalizar symbol a MAYÚSCULAS para consistencia con BD
        if symbol:
            symbol = symbol.upper()

        entry_price = message.get("entry")
        stop_loss = message.get("stop")
        target_price = message.get("target")
        direction = message.get("trade")
        rr = message.get("rr")
        probability = message.get("probability")
        ev = message.get("ev")  # ✨ NEW: Extract EV from crypto-analyzer-redis
        signal_quality_score = message.get("signal_quality_score", 0)
        tier = message.get("tier")  # ✨ NEW: Extract tier from crypto-analyzer-redis
        model_id = message.get("model_id", "all")  # ✨ NEW: Extract model_id, default "all"

        # ✨ NEW: Validar que el model_id de la señal coincida con la configuración del usuario
        if user_model_filter != "all" and model_id != user_model_filter:
            print(f"{log_prefix} Model REJECTED: User filter='{user_model_filter}', Signal model_id='{model_id}'")
            return {"user_id": user_id, "success": False, "reason": "model_filter_mismatch"}

        print(f"{log_prefix} Model filter check PASSED: User='{user_model_filter}', Signal='{model_id}'")

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
            if test_leverage:
                print(f"{log_prefix} 🧪 Test Mode: LEV {test_leverage}x")
            else:
                print(f"{log_prefix} 🧪 Test Mode: Activo")

        # 🧪 TEST MODE: Si es test, solo procesar para usuarios en la lista
        if is_test and test_users_list and user_id not in test_users_list:
            print(f"{log_prefix} 🧪 TEST MODE: Skipping user (allowed_users={test_users_list})")
            return {"user_id": user_id, "success": False, "reason": "test_mode_skip"}

        # BANNED SYMBOLS VALIDATION
        if is_symbol_banned(user_id, strategy, symbol):
            print(f"{log_prefix} Trade REJECTED: {symbol} is in banned symbols list")
            return {"user_id": user_id, "success": False, "reason": "banned_symbol"}

        # 🔍 DEBUG: Verificar último trade en BD antes de validación
        try:
            from app.utils.trade_protection import TradeProtectionSystem
            protection_system = TradeProtectionSystem()
            conn = protection_system._get_conn()

            query = """
            SELECT id, entry_time, exit_time, exit_reason, stop_price, target_price
            FROM trade_history
            WHERE user_id = %s AND strategy = %s AND symbol = %s
            ORDER BY entry_time DESC
            LIMIT 1
            """

            with conn.cursor() as cur:
                cur.execute(query, (user_id, strategy, symbol))
                last_trade_db = cur.fetchone()

                if last_trade_db:
                    trade_id, entry_time, exit_time, exit_reason, stop_price, target_price = last_trade_db
                    print(f"{log_prefix} 🔍 Last trade in DB: id={trade_id}, exit_reason={exit_reason}, exit_time={exit_time}")

                    if exit_reason == 'active':
                        print(f"{log_prefix} ⚠️ WARNING: Last trade still marked as 'active' in DB - will validate with orphan detector")
                else:
                    print(f"{log_prefix} 🔍 No previous trades in DB for {symbol}")

            conn.close()
        except Exception as e:
            print(f"{log_prefix} ⚠️ Error checking last trade in DB: {e}")

        # VALIDACIÓN INTEGRADA
        validator = UserRiskProfileValidator(user_id, strategy, rules)

        can_trade, reason, validation_data = validator.validate_trade(
            symbol=symbol,
            direction=direction,
            entry_price=entry_price,
            stop_price=stop_loss,
            target_price=target_price,
            probability=probability,
            rr=rr
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
                    rr=rr,
                    order_id=order_id,
                    sl_order_id=sl_order_id,
                    tp_order_id=tp_order_id
                )

                if trade_id and trade_id != -1:
                    print(f"{log_prefix} Trade registered in PostgreSQL: trade_id={trade_id}")
                    # ✅ PostgreSQL is the single source of truth - No Redis writes

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

    # ⏰ TTL VALIDATION (Nov 2025 - Phase 2: Candle-sync)
    # Reject signals that are too old to prevent stale data execution
    if trade.generated_timestamp is not None:
        from app.utils.constants import MAX_SIGNAL_AGE_SECONDS
        signal_age = time.time() - trade.generated_timestamp

        if signal_age > MAX_SIGNAL_AGE_SECONDS:
            print(f"⏱️  Signal REJECTED: Too old ({signal_age:.2f}s > {MAX_SIGNAL_AGE_SECONDS}s)")
            print(f"   Generated at: {trade.generated_at_utc}")
            print(f"   Signal expired - discarding to prevent stale data execution")
            raise HTTPException(
                status_code=400,
                detail=f"Signal expired: {signal_age:.2f}s old (max: {MAX_SIGNAL_AGE_SECONDS}s)"
            )
        else:
            print(f"⏰ Signal freshness: {signal_age:.2f}s old (within {MAX_SIGNAL_AGE_SECONDS}s TTL)")

    # Convertir a dict para procesar
    message = trade.model_dump()

    # Extract strategy from request (archer_model or archer_dual)
    request_strategy = trade.strategy if trade.strategy else STRATEGY
    print(f"📊 Strategy received: {request_strategy}")

    # Calcular horas UTC y Ciudad de México
    now_utc = datetime.now(timezone.utc)
    now_cdmx = now_utc.astimezone(timezone(timedelta(hours=-6)))  # Ciudad de México UTC-6
    time_str = f"🕐 {now_utc.strftime('%H:%M:%S')} UTC ({now_cdmx.strftime('%H:%M:%S')} CDMX)"

    # Log del request recibido (solo una vez)
    direction_str = trade.trade.upper() if trade.trade else "UNKNOWN"
    print(f"\n{'='*70}")
    print(f"📨 NUEVO REQUEST RECIBIDO - {trade.symbol}")
    print(f"{time_str}")
    print(f"{'='*70}")
    print(f"📊 Dirección:     {direction_str}")
    print(f"💹 Entry:         ${trade.entry:,.4f}")
    print(f"🛑 Stop Loss:     ${trade.stop:,.4f}")
    print(f"🎯 Target:        ${trade.target:,.4f}")
    print(f"⚖️  RR:            {trade.rr:.2f}")
    print(f"🎲 Probabilidad:  {trade.probability}%")
    if trade.ev is not None:
        print(f"💰 EV:            {trade.ev:.4f}")
    print(f"{'='*70}\n")

    # PARALLEL EXECUTION: Process all users simultaneously
    print(f"🚀 Processing trade for {len(USERS)} users in parallel")

    with ThreadPoolExecutor(max_workers=len(USERS), thread_name_prefix="User") as executor:
        # Submit all user tasks
        futures = {
            executor.submit(process_user_trade, user_id, message, request_strategy): user_id
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


# ========== MANUAL TRADING CONTROL ENDPOINTS ==========

@app.post("/close-position")
async def close_position(request: ClosePositionRequest):
    """
    Close an open position and cancel all pending orders (SL/TP).

    Args:
        request: ClosePositionRequest with user_id and symbol

    Returns:
        JSON with success status and order details
    """
    user_id = request.user_id
    symbol = request.symbol.upper()

    # Validate user_id
    if user_id not in USERS:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid user_id. Must be one of: {', '.join(USERS)}"
        )

    try:
        logger.info(f"📤 Close position request: {user_id}/{symbol}")

        # Get Binance client
        client = get_binance_client_for_user(user_id)

        # Close position and cancel orders
        result = close_position_and_cancel_orders(
            symbol=symbol,
            client=client,
            user_id=user_id,
            strategy=STRATEGY
        )

        if result.get("success"):
            logger.info(f"✅ Position closed successfully: {user_id}/{symbol}")
            return {
                "success": True,
                "message": "Position closed successfully",
                "user_id": user_id,
                "symbol": symbol,
                "order_id": result.get("order_id")
            }
        else:
            logger.warning(f"⚠️ Failed to close position: {user_id}/{symbol} - {result.get('error')}")
            return {
                "success": False,
                "error": result.get("error", "Unknown error"),
                "user_id": user_id,
                "symbol": symbol
            }

    except HTTPException:
        raise
    except BinanceAPIException as e:
        # PHASE 3: Specific Binance error handling
        error_msg = format_binance_error_for_logging(e, "close_position", user_id, symbol)
        logger.error(f"❌ {error_msg}")
        raise handle_binance_exception(e, "close position", user_id, symbol)
    except Exception as e:
        logger.error(f"❌ Error closing position: {user_id}/{symbol} - {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to close position: {str(e)}"
        )


@app.post("/set-stop-loss")
async def set_stop_loss(request: SetStopLossRequest):
    """
    Update only the stop loss of an open position.
    By default, validates that the new SL is safer than the previous one (tighten-only).
    Use force_adjust=True to bypass this validation (use with caution).

    Args:
        request: SetStopLossRequest with user_id, symbol, stop_loss, and optional force_adjust

    Returns:
        JSON with success status and updated stop loss details
    """
    user_id = request.user_id
    symbol = request.symbol.upper()
    stop_loss = request.stop_loss

    # Validate user_id
    if user_id not in USERS:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid user_id. Must be one of: {', '.join(USERS)}"
        )

    try:
        logger.info(f"🛑 Set stop loss request: {user_id}/{symbol} @ {stop_loss}")

        # Get Binance client and mark price
        client = get_binance_client_for_user(user_id)
        mark_price = get_mark_price(symbol, client)

        # Get current position to determine direction
        positions = client.futures_position_information(symbol=symbol)
        if not positions or float(positions[0].get("positionAmt", "0")) == 0.0:
            raise HTTPException(
                status_code=404,
                detail=f"No open position found for {symbol}"
            )

        position_amt = float(positions[0]["positionAmt"])
        direction = "LONG" if position_amt > 0 else "SHORT"

        # Validate stop loss based on direction
        if direction == "LONG" and stop_loss >= mark_price:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid SL for LONG (expected stop_loss < mark_price). Mark: {mark_price:.2f}, Requested SL: {stop_loss:.2f}"
            )
        elif direction == "SHORT" and stop_loss <= mark_price:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid SL for SHORT (expected stop_loss > mark_price). Mark: {mark_price:.2f}, Requested SL: {stop_loss:.2f}"
            )

        # ===== ENHANCED VALIDATIONS (Phase 2) =====

        # Get symbol filters for enhanced validations
        filters = get_symbol_filters(symbol, client)

        # 1. Validate MIN_NOTIONAL
        is_valid, error = validate_min_notional_for_manual_trading(
            symbol=symbol,
            position_amt=position_amt,
            price=stop_loss,
            filters=filters
        )
        if not is_valid:
            logger.warning(f"⚠️ MIN_NOTIONAL validation failed for {user_id}/{symbol}: {error}")
            raise HTTPException(status_code=400, detail=error)

        # 2. Validate SL distance from mark price (minimum 0.1% to avoid immediate trigger)
        is_valid, error = validate_sl_distance_from_mark_price(
            symbol=symbol,
            stop_loss=stop_loss,
            mark_price=mark_price,
            direction=direction,
            min_distance_pct=0.1
        )
        if not is_valid:
            logger.warning(f"⚠️ SL distance validation failed for {user_id}/{symbol}: {error}")
            raise HTTPException(status_code=400, detail=error)

        # ==========================================

        # Adjust stop loss only
        result = adjust_stop_only_for_open_position(
            symbol=symbol,
            new_stop=stop_loss,
            client=client,
            user_id=user_id,
            enforce_tighten=not request.force_adjust  # Allow bypass of tighten-only mode
        )

        if result.get("success"):
            logger.info(f"✅ Stop loss updated: {user_id}/{symbol} @ {stop_loss}")
            return {
                "success": True,
                "message": "Stop loss updated successfully",
                "user_id": user_id,
                "symbol": symbol,
                "direction": direction,
                "stop_loss": result.get("stop"),
                "mark_price": mark_price,
                "previous_stop": result.get("previous_stop"),
                "level_applied": result.get("level_applied"),
                "redis_updated": result.get("redis_updated", False)
            }
        else:
            logger.warning(f"⚠️ Failed to update stop loss: {user_id}/{symbol} - {result.get('error')}")
            return {
                "success": False,
                "error": result.get("error", "Unknown error"),
                "user_id": user_id,
                "symbol": symbol,
                "mark_price": mark_price
            }

    except HTTPException:
        raise
    except BinanceAPIException as e:
        # PHASE 3: Specific Binance error handling
        error_msg = format_binance_error_for_logging(e, "set_stop_loss", user_id, symbol)
        logger.error(f"❌ {error_msg}")
        raise handle_binance_exception(e, "set stop loss", user_id, symbol)
    except Exception as e:
        logger.error(f"❌ Error setting stop loss: {user_id}/{symbol} - {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to set stop loss: {str(e)}"
        )


@app.post("/set-take-profit")
async def set_take_profit(request: SetTakeProfitRequest):
    """
    Update only the take profit of an open position.
    Keeps the existing stop loss intact.

    Args:
        request: SetTakeProfitRequest with user_id, symbol, and take_profit

    Returns:
        JSON with success status and updated take profit details
    """
    user_id = request.user_id
    symbol = request.symbol.upper()
    take_profit = request.take_profit

    # Validate user_id
    if user_id not in USERS:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid user_id. Must be one of: {', '.join(USERS)}"
        )

    try:
        logger.info(f"🎯 Set take profit request: {user_id}/{symbol} @ {take_profit}")

        # Get Binance client and mark price
        client = get_binance_client_for_user(user_id)
        mark_price = get_mark_price(symbol, client)

        # Get current position to determine direction
        positions = client.futures_position_information(symbol=symbol)
        if not positions or float(positions[0].get("positionAmt", "0")) == 0.0:
            raise HTTPException(
                status_code=404,
                detail=f"No open position found for {symbol}"
            )

        position_amt = float(positions[0]["positionAmt"])
        direction = "LONG" if position_amt > 0 else "SHORT"

        # Validate take profit based on direction
        if direction == "LONG" and take_profit <= mark_price:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid TP for LONG (expected take_profit > mark_price). Mark: {mark_price:.2f}, Requested TP: {take_profit:.2f}"
            )
        elif direction == "SHORT" and take_profit >= mark_price:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid TP for SHORT (expected take_profit < mark_price). Mark: {mark_price:.2f}, Requested TP: {take_profit:.2f}"
            )

        # ===== ENHANCED VALIDATIONS (Phase 2) =====

        # Get symbol filters for enhanced validations
        filters = get_symbol_filters(symbol, client)

        # Validate MIN_NOTIONAL for TP order
        is_valid, error = validate_min_notional_for_manual_trading(
            symbol=symbol,
            position_amt=position_amt,
            price=take_profit,
            filters=filters
        )
        if not is_valid:
            logger.warning(f"⚠️ MIN_NOTIONAL validation failed for {user_id}/{symbol}: {error}")
            raise HTTPException(status_code=400, detail=error)

        # ==========================================

        # Get current stop loss from open orders
        open_orders = client.futures_get_open_orders(symbol=symbol)
        current_sl = None

        # Try traditional orders first
        for order in open_orders:
            if order.get("type") == "STOP_MARKET":
                current_sl = float(order.get("stopPrice", 0))
                break

        # If not found in traditional, check Algo Orders
        if current_sl is None:
            try:
                algo_response = client._request_futures_api('get', 'openAlgoOrders', signed=True, data={"symbol": symbol})
                algo_orders = []
                if isinstance(algo_response, dict) and "openOrders" in algo_response:
                    algo_orders = algo_response["openOrders"]
                elif isinstance(algo_response, list):
                    algo_orders = algo_response

                for algo_order in algo_orders:
                    order_type = algo_order.get("algoType") or algo_order.get("type", "")
                    if order_type in ["STOP_MARKET", "STOP"]:
                        current_sl = float(algo_order.get("stopPrice", 0))
                        break
            except Exception as e:
                logger.warning(f"⚠️ Could not fetch Algo Orders: {e}")

        if current_sl is None:
            raise HTTPException(
                status_code=404,
                detail=f"No stop loss order found for {symbol}. Please use /adjust-sl-tp to set both."
            )

        # Adjust both SL (keep current) and TP (new value)
        result = adjust_sl_tp_for_open_position(
            symbol=symbol,
            new_stop=current_sl,
            new_target=take_profit,
            client=client,
            user_id=user_id
        )

        if result.get("success"):
            logger.info(f"✅ Take profit updated: {user_id}/{symbol} @ {take_profit}")
            return {
                "success": True,
                "message": "Take profit updated successfully",
                "user_id": user_id,
                "symbol": symbol,
                "direction": direction,
                "take_profit": result.get("target"),
                "mark_price": mark_price,
                "stop_loss": result.get("stop")
            }
        else:
            logger.warning(f"⚠️ Failed to update take profit: {user_id}/{symbol} - {result.get('error')}")
            return {
                "success": False,
                "error": result.get("error", "Unknown error"),
                "user_id": user_id,
                "symbol": symbol,
                "mark_price": mark_price
            }

    except HTTPException:
        raise
    except BinanceAPIException as e:
        # PHASE 3: Specific Binance error handling
        error_msg = format_binance_error_for_logging(e, "set_take_profit", user_id, symbol)
        logger.error(f"❌ {error_msg}")
        raise handle_binance_exception(e, "set take profit", user_id, symbol)
    except Exception as e:
        logger.error(f"❌ Error setting take profit: {user_id}/{symbol} - {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to set take profit: {str(e)}"
        )


@app.post("/adjust-sl-tp")
async def adjust_sl_tp(request: AdjustSLTPRequest):
    """
    Update both stop loss and take profit simultaneously.

    Args:
        request: AdjustSLTPRequest with user_id, symbol, stop_loss, and take_profit

    Returns:
        JSON with success status and updated SL/TP details
    """
    user_id = request.user_id
    symbol = request.symbol.upper()
    stop_loss = request.stop_loss
    take_profit = request.take_profit

    # Validate user_id
    if user_id not in USERS:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid user_id. Must be one of: {', '.join(USERS)}"
        )

    try:
        logger.info(f"⚙️ Adjust SL/TP request: {user_id}/{symbol} - SL: {stop_loss}, TP: {take_profit}")

        # Get Binance client and mark price
        client = get_binance_client_for_user(user_id)
        mark_price = get_mark_price(symbol, client)

        # Get current position to determine direction
        positions = client.futures_position_information(symbol=symbol)
        if not positions or float(positions[0].get("positionAmt", "0")) == 0.0:
            raise HTTPException(
                status_code=404,
                detail=f"No open position found for {symbol}"
            )

        position_amt = float(positions[0]["positionAmt"])
        direction = "LONG" if position_amt > 0 else "SHORT"

        # Validate stop loss and take profit based on direction
        if direction == "LONG":
            if stop_loss >= mark_price:
                raise HTTPException(
                    status_code=400,
                    detail=f"Invalid SL for LONG (expected stop_loss < mark_price). Mark: {mark_price:.2f}, Requested SL: {stop_loss:.2f}"
                )
            if take_profit <= mark_price:
                raise HTTPException(
                    status_code=400,
                    detail=f"Invalid TP for LONG (expected take_profit > mark_price). Mark: {mark_price:.2f}, Requested TP: {take_profit:.2f}"
                )
        else:  # SHORT
            if stop_loss <= mark_price:
                raise HTTPException(
                    status_code=400,
                    detail=f"Invalid SL for SHORT (expected stop_loss > mark_price). Mark: {mark_price:.2f}, Requested SL: {stop_loss:.2f}"
                )
            if take_profit >= mark_price:
                raise HTTPException(
                    status_code=400,
                    detail=f"Invalid TP for SHORT (expected take_profit < mark_price). Mark: {mark_price:.2f}, Requested TP: {take_profit:.2f}"
                )

        # ===== ENHANCED VALIDATIONS (Phase 2) =====

        # Get symbol filters for enhanced validations
        filters = get_symbol_filters(symbol, client)

        # 1. Validate MIN_NOTIONAL for SL order
        is_valid, error = validate_min_notional_for_manual_trading(
            symbol=symbol,
            position_amt=position_amt,
            price=stop_loss,
            filters=filters
        )
        if not is_valid:
            logger.warning(f"⚠️ MIN_NOTIONAL validation failed for SL ({user_id}/{symbol}): {error}")
            raise HTTPException(status_code=400, detail=f"Stop Loss: {error}")

        # 2. Validate MIN_NOTIONAL for TP order
        is_valid, error = validate_min_notional_for_manual_trading(
            symbol=symbol,
            position_amt=position_amt,
            price=take_profit,
            filters=filters
        )
        if not is_valid:
            logger.warning(f"⚠️ MIN_NOTIONAL validation failed for TP ({user_id}/{symbol}): {error}")
            raise HTTPException(status_code=400, detail=f"Take Profit: {error}")

        # 3. Validate SL distance from mark price (minimum 0.1% to avoid immediate trigger)
        is_valid, error = validate_sl_distance_from_mark_price(
            symbol=symbol,
            stop_loss=stop_loss,
            mark_price=mark_price,
            direction=direction,
            min_distance_pct=0.1
        )
        if not is_valid:
            logger.warning(f"⚠️ SL distance validation failed for {user_id}/{symbol}: {error}")
            raise HTTPException(status_code=400, detail=error)

        # 4. Validate Risk-Reward ratio (minimum 1.0 by default)
        entry_price = float(positions[0]["entryPrice"])
        is_valid, error = validate_risk_reward_ratio_for_manual_trading(
            entry_price=entry_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            direction=direction,
            min_rr_ratio=1.0
        )
        if not is_valid:
            logger.warning(f"⚠️ Risk-Reward validation failed for {user_id}/{symbol}: {error}")
            # Make RR validation a warning, not a hard error (user can override)
            logger.warning(f"🔔 Proceeding anyway - user may have valid reasons for low RR")

        # ==========================================

        # Adjust both SL and TP
        result = adjust_sl_tp_for_open_position(
            symbol=symbol,
            new_stop=stop_loss,
            new_target=take_profit,
            client=client,
            user_id=user_id
        )

        if result.get("success"):
            logger.info(f"✅ SL/TP updated: {user_id}/{symbol} - SL: {stop_loss}, TP: {take_profit}")
            return {
                "success": True,
                "message": "Stop loss and take profit updated successfully",
                "user_id": user_id,
                "symbol": symbol,
                "direction": direction,
                "stop_loss": result.get("stop"),
                "take_profit": result.get("target"),
                "mark_price": mark_price
            }
        else:
            logger.warning(f"⚠️ Failed to update SL/TP: {user_id}/{symbol} - {result.get('error')}")
            return {
                "success": False,
                "error": result.get("error", "Unknown error"),
                "user_id": user_id,
                "symbol": symbol,
                "mark_price": mark_price
            }

    except HTTPException:
        raise
    except BinanceAPIException as e:
        # PHASE 3: Specific Binance error handling
        error_msg = format_binance_error_for_logging(e, "adjust_sl_tp", user_id, symbol)
        logger.error(f"❌ {error_msg}")
        raise handle_binance_exception(e, "adjust SL/TP", user_id, symbol)
    except Exception as e:
        logger.error(f"❌ Error adjusting SL/TP: {user_id}/{symbol} - {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to adjust SL/TP: {str(e)}"
        )


@app.get("/position-status/{user_id}/{symbol}")
async def get_position_status(user_id: str, symbol: str):
    """
    Get current status of an open position including entry, SL, TP, and unrealized PNL.

    Args:
        user_id: User identifier (path parameter)
        symbol: Trading pair (path parameter, e.g., BTCUSDT)

    Returns:
        JSON with position details

    Example:
        GET /position-status/copy_trading/BTCUSDT
    """
    symbol = symbol.upper()

    # Validate user_id
    if user_id not in USERS:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid user_id. Must be one of: {', '.join(USERS)}"
        )

    try:
        logger.info(f"📊 Position status request: {user_id}/{symbol}")

        # Get Binance client
        client = get_binance_client_for_user(user_id)

        # Get position information
        from app.utils.binance.binance_fetch import get_position_cached
        positions = get_position_cached(symbol, client, user_id)

        if not positions or float(positions[0].get("positionAmt", "0")) == 0.0:
            raise HTTPException(
                status_code=404,
                detail=f"No open position found for {symbol}"
            )

        position = positions[0]
        position_amt = float(position["positionAmt"])

        # Get current SL/TP from orders
        sl_price, tp_price = get_current_sl_tp(symbol, client)

        # Get mark price
        mark_price = get_mark_price(symbol, client)

        # Prepare response
        response = {
            "success": True,
            "user_id": user_id,
            "symbol": symbol,
            "direction": "LONG" if position_amt > 0 else "SHORT",
            "entry_price": float(position["entryPrice"]),
            "position_size": abs(position_amt),
            "unrealized_pnl": float(position["unRealizedProfit"]),
            "mark_price": mark_price,
            "stop_loss": sl_price,
            "take_profit": tp_price,
            "leverage": int(position["leverage"]),
            "margin_type": position.get("marginType", "cross"),
            "liquidation_price": float(position.get("liquidationPrice", 0)),
            "notional": abs(position_amt * mark_price)
        }

        # Calculate risk/reward if both SL and TP are set
        if sl_price and tp_price:
            entry = float(position["entryPrice"])
            risk = abs(entry - sl_price)
            reward = abs(tp_price - entry)
            response["risk_reward_ratio"] = round(reward / risk, 2) if risk > 0 else None

        logger.info(f"✅ Position status retrieved: {user_id}/{symbol} - {response['direction']} @ {response['entry_price']}")

        return response

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ Error getting position status: {user_id}/{symbol} - {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to get position status: {str(e)}"
        )


@app.patch("/adjust-sl-tp-flexible")
async def adjust_sl_tp_flexible(request: FlexibleAdjustRequest):
    """
    Flexible adjustment: update SL only, TP only, both, or remove TP.

    This endpoint allows partial updates to SL/TP without requiring both parameters.

    Args:
        request: FlexibleAdjustRequest with optional stop_loss and take_profit

    Returns:
        JSON with success status and updated values

    Examples:
        - Adjust only SL: {"user_id": "copy_trading", "symbol": "BTCUSDT", "stop_loss": 44000}
        - Adjust only TP: {"user_id": "copy_trading", "symbol": "BTCUSDT", "take_profit": 47000}
        - Adjust both: {"user_id": "copy_trading", "symbol": "BTCUSDT", "stop_loss": 44000, "take_profit": 47000}
        - Remove TP: {"user_id": "copy_trading", "symbol": "BTCUSDT", "remove_take_profit": true}
    """
    user_id = request.user_id
    symbol = request.symbol.upper()

    # Validate user_id
    if user_id not in USERS:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid user_id. Must be one of: {', '.join(USERS)}"
        )

    # Validate that at least one action is specified
    if not any([request.stop_loss, request.take_profit, request.remove_take_profit]):
        raise HTTPException(
            status_code=400,
            detail="Must provide stop_loss, take_profit, or remove_take_profit=true"
        )

    try:
        logger.info(f"🔧 Flexible adjust request: {user_id}/{symbol}")

        # Get Binance client
        client = get_binance_client_for_user(user_id)

        # Get mark price for validation
        mark_price = get_mark_price(symbol, client)

        # Verify position exists
        from app.utils.binance.binance_fetch import get_position_cached
        positions = get_position_cached(symbol, client, user_id)

        if not positions or float(positions[0].get("positionAmt", "0")) == 0.0:
            raise HTTPException(
                status_code=404,
                detail=f"No open position found for {symbol}"
            )

        position_amt = float(positions[0]["positionAmt"])
        direction = "LONG" if position_amt > 0 else "SHORT"

        # Handle remove_take_profit
        if request.remove_take_profit:
            logger.info(f"🗑️ Removing TP for {user_id}/{symbol}")
            result = cancel_tp_only(symbol, client, user_id)

            if result.get("success"):
                return {
                    "success": True,
                    "message": "Take profit removed successfully",
                    "user_id": user_id,
                    "symbol": symbol,
                    "direction": direction,
                    "canceled_count": result.get("canceled_count", 0),
                    "mark_price": mark_price
                }
            else:
                return {
                    "success": False,
                    "error": result.get("error", "Failed to remove TP"),
                    "user_id": user_id,
                    "symbol": symbol
                }

        # Validate prices based on direction
        if request.stop_loss:
            if direction == "LONG" and request.stop_loss >= mark_price:
                raise HTTPException(
                    status_code=400,
                    detail=f"Invalid SL for LONG (expected stop_loss < mark_price). Mark: {mark_price:.2f}, Requested SL: {request.stop_loss:.2f}"
                )
            elif direction == "SHORT" and request.stop_loss <= mark_price:
                raise HTTPException(
                    status_code=400,
                    detail=f"Invalid SL for SHORT (expected stop_loss > mark_price). Mark: {mark_price:.2f}, Requested SL: {request.stop_loss:.2f}"
                )

        if request.take_profit:
            if direction == "LONG" and request.take_profit <= mark_price:
                raise HTTPException(
                    status_code=400,
                    detail=f"Invalid TP for LONG (expected take_profit > mark_price). Mark: {mark_price:.2f}, Requested TP: {request.take_profit:.2f}"
                )
            elif direction == "SHORT" and request.take_profit >= mark_price:
                raise HTTPException(
                    status_code=400,
                    detail=f"Invalid TP for SHORT (expected take_profit < mark_price). Mark: {mark_price:.2f}, Requested TP: {request.take_profit:.2f}"
                )

        # Execute adjustment based on what was provided
        if request.stop_loss and request.take_profit:
            # Adjust both
            logger.info(f"⚙️ Adjusting both SL and TP: {user_id}/{symbol}")
            result = adjust_sl_tp_for_open_position(
                symbol=symbol,
                new_stop=request.stop_loss,
                new_target=request.take_profit,
                client=client,
                user_id=user_id
            )

        elif request.stop_loss:
            # Adjust only SL
            logger.info(f"🛑 Adjusting only SL: {user_id}/{symbol} @ {request.stop_loss}")
            result = adjust_stop_only_for_open_position(
                symbol=symbol,
                new_stop=request.stop_loss,
                client=client,
                user_id=user_id
            )

        elif request.take_profit:
            # Adjust only TP (need to keep current SL)
            logger.info(f"🎯 Adjusting only TP: {user_id}/{symbol} @ {request.take_profit}")

            # Get current SL
            sl_current, _ = get_current_sl_tp(symbol, client)
            if sl_current is None:
                raise HTTPException(
                    status_code=404,
                    detail=f"No current SL found for {symbol}. Use /adjust-sl-tp to set both SL and TP."
                )

            result = adjust_sl_tp_for_open_position(
                symbol=symbol,
                new_stop=sl_current,
                new_target=request.take_profit,
                client=client,
                user_id=user_id
            )

        # Return response
        if result.get("success"):
            logger.info(f"✅ Flexible adjustment successful: {user_id}/{symbol}")
            return {
                "success": True,
                "message": "Position adjusted successfully",
                "user_id": user_id,
                "symbol": symbol,
                "direction": direction,
                "stop_loss": result.get("stop"),
                "take_profit": result.get("target"),
                "mark_price": mark_price
            }
        else:
            logger.warning(f"⚠️ Flexible adjustment failed: {user_id}/{symbol} - {result.get('error')}")
            return {
                "success": False,
                "error": result.get("error", "Unknown error"),
                "user_id": user_id,
                "symbol": symbol,
                "mark_price": mark_price
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ Error in flexible adjustment: {user_id}/{symbol} - {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to adjust position: {str(e)}"
        )


@app.get("/")
async def root():
    """Root endpoint with service information"""
    return {
        "service": "crypto-listener-rest",
        "version": "1.1.0",
        "description": "REST API for immediate crypto trade execution",
        "endpoints": {
            "POST /execute-trade": "Execute a trade immediately",
            "POST /guardian": "Execute guardian action (close/adjust/half_close)",
            "POST /close-position": "Close an open position",
            "POST /set-stop-loss": "Update stop loss of open position",
            "POST /set-take-profit": "Update take profit of open position",
            "POST /adjust-sl-tp": "Update both SL and TP of open position",
            "GET /health": "Health check",
            "GET /stats": "Get trade statistics",
            "GET /docs": "Interactive API documentation"
        },
        "documentation": "See /docs for interactive API documentation or MANUAL_TRADING_API.md for manual trading endpoints"
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
