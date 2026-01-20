"""
api.py - REST API para ejecución de trades

Endpoints:
  POST /trade  - Abre un trade para todos los usuarios
  GET /health  - Health check
"""

from fastapi import FastAPI, HTTPException
from typing import List

# Binance client
from app.utils.binance.binance_client import get_binance_client_for_user
from app.utils.config.settings import COPY_TRADING, FUTURES, HUFSA, COPY_2

# Constantes
from app.utils.config.config_constants import BUY, SELL, validate_direction

# Trade execution
from trade_executor import TradeRequest, process_trade_for_user


# ========== CONFIGURACIÓN ==========
USERS: List[str] = [COPY_TRADING, HUFSA, COPY_2, FUTURES]


# ========== APP ==========
app = FastAPI(
    title="crypto-listener-rest",
    description="REST API para ejecución de trades con stop loss garantizado",
    version="2.0.0"
)


# ========== ENDPOINTS ==========
@app.post("/trade")
async def open_trade(request: TradeRequest):
    """
    Abre un trade con stop loss garantizado para todos los usuarios.

    Flujo:
      1. Validar request (precios coherentes con dirección)
      2. Para cada usuario:
         - Obtener cliente Binance
         - Ejecutar process_trade_for_user (pasos 3-10)
      3. Retornar resultados
    """
    symbol = request.symbol.upper()

    # ========== PASO 1: Validar request ==========
    # Validar dirección (solo acepta BUY o SELL)
    try:
        direction = validate_direction(request.trade)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # Validar precios según dirección
    # BUY (LONG): stop < entry < target
    # SELL (SHORT): stop > entry > target
    if direction == BUY:
        if not (request.stop < request.entry < request.target):
            raise HTTPException(
                status_code=400,
                detail=f"Para BUY: stop ({request.stop}) < entry ({request.entry}) < target ({request.target})"
            )
    elif direction == SELL:
        if not (request.stop > request.entry > request.target):
            raise HTTPException(
                status_code=400,
                detail=f"Para SELL: stop ({request.stop}) > entry ({request.entry}) > target ({request.target})"
            )

    # ========== PASO 2: Procesar para cada usuario ==========
    results = []
    for user_id in USERS:
        try:
            client = get_binance_client_for_user(user_id)
            result = process_trade_for_user(
                user_id=user_id,
                client=client,
                request=request
            )
            results.append(result)
        except Exception as e:
            results.append({
                "user_id": user_id,
                "success": False,
                "reason": f"client_error: {str(e)}"
            })

    # ========== Resumen ==========
    successful = sum(1 for r in results if r.get("success"))
    failed = len(results) - successful

    return {
        "status": "completed",
        "symbol": symbol,
        "direction": direction,
        "successful": successful,
        "failed": failed,
        "total_users": len(USERS),
        "results": results
    }


@app.get("/health")
async def health():
    """Health check"""
    return {"status": "ok", "version": "2.0.0"}


# ========== MAIN ==========
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000, log_level="info")
