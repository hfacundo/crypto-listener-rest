"""
Crypto Trading Admin API
=========================
Panel de administración para controlar crypto-listener-rest remotamente.

Funcionalidades:
- Emergency stop (detener todo)
- Pausar/activar usuarios individuales
- Modificar configuración (tier_config, circuit_breaker, etc.)
- Ver estado actual de todos los usuarios
- Sistema de autenticación multi-usuario
"""

from fastapi import FastAPI, HTTPException, Depends, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from typing import Optional, Dict, Any
import psycopg2
import psycopg2.extras
import os
import secrets
import json

# =====================================================================
# Configuration
# =====================================================================

DATABASE_URL = os.getenv(
    "DATABASE_URL_CRYPTO_TRADER"
)

# Usuarios autorizados (usuario:password)
# TODO: Cambiar estas credenciales en producción
AUTHORIZED_USERS = {
    "admin": os.getenv("UI_ADMIN_PASSWORD"),      # Usuario principal
    "viewer": os.getenv("UI_VIEWER_PASSWORD")        # Usuario con permisos de solo lectura
}

# =====================================================================
# FastAPI App
# =====================================================================

app = FastAPI(
    title="Crypto Trading Admin Panel",
    description="Control panel for crypto-listener-rest",
    version="1.0.0"
)

# CORS (permite acceso desde cualquier origen)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount static files
static_dir = os.path.join(os.path.dirname(__file__), "static")
if os.path.exists(static_dir):
    app.mount("/static", StaticFiles(directory=static_dir), name="static")

security = HTTPBasic()

# =====================================================================
# Database Connection
# =====================================================================

def get_db():
    """Get PostgreSQL connection"""
    try:
        conn = psycopg2.connect(DATABASE_URL)
        return conn
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")

# =====================================================================
# Authentication
# =====================================================================

def verify_credentials(credentials: HTTPBasicCredentials = Depends(security)) -> str:
    """Verify user credentials"""
    username = credentials.username
    password = credentials.password

    if username not in AUTHORIZED_USERS:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
            headers={"WWW-Authenticate": "Basic"},
        )

    correct_password = AUTHORIZED_USERS[username]
    is_correct = secrets.compare_digest(password.encode("utf8"), correct_password.encode("utf8"))

    if not is_correct:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
            headers={"WWW-Authenticate": "Basic"},
        )

    return username

# =====================================================================
# Models
# =====================================================================

class UserToggleRequest(BaseModel):
    """Request to enable/disable a user"""
    enabled: bool

class TierConfigUpdate(BaseModel):
    """Update tier_config for a user"""
    enabled: Optional[bool] = None
    max_tier_accepted: Optional[int] = None
    description: Optional[str] = None

class CircuitBreakerUpdate(BaseModel):
    """Update circuit_breaker config"""
    enabled: Optional[bool] = None
    max_losses: Optional[int] = None
    window_minutes: Optional[int] = None
    cooldown_minutes: Optional[int] = None

class RulesUpdate(BaseModel):
    """Generic rules update"""
    field: str
    value: Any

# =====================================================================
# Endpoints - Status & Info
# =====================================================================

@app.get("/", response_class=HTMLResponse)
async def root():
    """Serve dashboard HTML"""
    html_path = os.path.join(os.path.dirname(__file__), "static", "index.html")
    if os.path.exists(html_path):
        with open(html_path, "r") as f:
            return f.read()
    return """
    <html>
        <body>
            <h1>Crypto Trading Admin Panel</h1>
            <p>Dashboard not found. Please ensure static/index.html exists.</p>
            <p><a href="/docs">API Documentation</a></p>
        </body>
    </html>
    """

@app.get("/api/users/status")
async def get_all_users_status(username: str = Depends(verify_credentials)):
    """Get status of all users"""
    conn = get_db()
    cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    try:
        cursor.execute("""
            SELECT
                user_id,
                strategy,
                rules_config->>'enabled' as enabled,
                rules_config->'tier_config'->>'enabled' as tier_filtering_enabled,
                rules_config->'tier_config'->>'max_tier_accepted' as max_tier,
                rules_config->'circuit_breaker'->>'enabled' as circuit_breaker_enabled,
                rules_config->>'risk_pct' as risk_pct,
                created_at,
                updated_at
            FROM user_rules
            WHERE strategy = 'archer_dual'
            ORDER BY user_id
        """)

        users = cursor.fetchall()
        return {"users": users}

    finally:
        cursor.close()
        conn.close()

@app.get("/api/users/{user_id}/config")
async def get_user_config(user_id: str, username: str = Depends(verify_credentials)):
    """Get full configuration for a specific user"""
    conn = get_db()
    cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    try:
        cursor.execute("""
            SELECT
                user_id,
                strategy,
                rules_config,
                banned_symbols,
                created_at,
                updated_at
            FROM user_rules
            WHERE user_id = %s AND strategy = 'archer_dual'
        """, (user_id,))

        user = cursor.fetchone()

        if not user:
            raise HTTPException(status_code=404, detail=f"User {user_id} not found")

        return user

    finally:
        cursor.close()
        conn.close()

# =====================================================================
# Endpoints - Emergency Controls
# =====================================================================

@app.post("/api/emergency/stop-all")
async def emergency_stop_all(username: str = Depends(verify_credentials)):
    """🚨 EMERGENCY: Stop all trading immediately"""

    # Solo admin puede ejecutar emergency stop
    if username != "admin":
        raise HTTPException(status_code=403, detail="Only admin can execute emergency stop")

    conn = get_db()
    cursor = conn.cursor()

    try:
        cursor.execute("""
            UPDATE user_rules
            SET rules_config = jsonb_set(
                rules_config,
                '{enabled}',
                'false'
            )
            WHERE strategy = 'archer_dual'
        """)

        affected_rows = cursor.rowcount
        conn.commit()

        return {
            "status": "success",
            "message": "🚨 EMERGENCY STOP executed - All trading disabled",
            "users_affected": affected_rows
        }

    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))

    finally:
        cursor.close()
        conn.close()

@app.post("/api/emergency/resume-all")
async def resume_all_trading(username: str = Depends(verify_credentials)):
    """Resume trading for all users"""

    if username != "admin":
        raise HTTPException(status_code=403, detail="Only admin can resume all trading")

    conn = get_db()
    cursor = conn.cursor()

    try:
        cursor.execute("""
            UPDATE user_rules
            SET rules_config = jsonb_set(
                rules_config,
                '{enabled}',
                'true'
            )
            WHERE strategy = 'archer_dual'
        """)

        affected_rows = cursor.rowcount
        conn.commit()

        return {
            "status": "success",
            "message": "✅ All trading resumed",
            "users_affected": affected_rows
        }

    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))

    finally:
        cursor.close()
        conn.close()

# =====================================================================
# Endpoints - User Controls
# =====================================================================

@app.post("/api/users/{user_id}/toggle")
async def toggle_user(
    user_id: str,
    request: UserToggleRequest,
    username: str = Depends(verify_credentials)
):
    """Enable or disable trading for a specific user"""

    conn = get_db()
    cursor = conn.cursor()

    try:
        cursor.execute("""
            UPDATE user_rules
            SET rules_config = jsonb_set(
                rules_config,
                '{enabled}',
                %s
            )
            WHERE user_id = %s AND strategy = 'archer_dual'
        """, (json.dumps(request.enabled), user_id))

        if cursor.rowcount == 0:
            raise HTTPException(status_code=404, detail=f"User {user_id} not found")

        conn.commit()

        action = "enabled" if request.enabled else "disabled"
        return {
            "status": "success",
            "message": f"User {user_id} {action}",
            "user_id": user_id,
            "enabled": request.enabled
        }

    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))

    finally:
        cursor.close()
        conn.close()

# =====================================================================
# Endpoints - Tier Config
# =====================================================================

@app.patch("/api/users/{user_id}/tier-config")
async def update_tier_config(
    user_id: str,
    update: TierConfigUpdate,
    username: str = Depends(verify_credentials)
):
    """Update tier_config for a user"""

    conn = get_db()
    cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    try:
        # Get current tier_config
        cursor.execute("""
            SELECT rules_config->'tier_config' as tier_config
            FROM user_rules
            WHERE user_id = %s AND strategy = 'archer_dual'
        """, (user_id,))

        result = cursor.fetchone()
        if not result:
            raise HTTPException(status_code=404, detail=f"User {user_id} not found")

        current_config = result['tier_config'] or {}

        # Update only provided fields
        if update.enabled is not None:
            current_config['enabled'] = update.enabled
        if update.max_tier_accepted is not None:
            current_config['max_tier_accepted'] = update.max_tier_accepted
        if update.description is not None:
            current_config['description'] = update.description

        # Save updated config
        cursor.execute("""
            UPDATE user_rules
            SET rules_config = jsonb_set(
                rules_config,
                '{tier_config}',
                %s
            )
            WHERE user_id = %s AND strategy = 'archer_dual'
        """, (json.dumps(current_config), user_id))

        conn.commit()

        return {
            "status": "success",
            "message": f"Tier config updated for {user_id}",
            "tier_config": current_config
        }

    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))

    finally:
        cursor.close()
        conn.close()

# =====================================================================
# Endpoints - Circuit Breaker
# =====================================================================

@app.patch("/api/users/{user_id}/circuit-breaker")
async def update_circuit_breaker(
    user_id: str,
    update: CircuitBreakerUpdate,
    username: str = Depends(verify_credentials)
):
    """Update circuit_breaker config for a user"""

    conn = get_db()
    cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    try:
        # Get current circuit_breaker config
        cursor.execute("""
            SELECT rules_config->'circuit_breaker' as circuit_breaker
            FROM user_rules
            WHERE user_id = %s AND strategy = 'archer_dual'
        """, (user_id,))

        result = cursor.fetchone()
        if not result:
            raise HTTPException(status_code=404, detail=f"User {user_id} not found")

        current_config = result['circuit_breaker'] or {}

        # Update only provided fields
        if update.enabled is not None:
            current_config['enabled'] = update.enabled
        if update.max_losses is not None:
            current_config['max_losses'] = update.max_losses
        if update.window_minutes is not None:
            current_config['window_minutes'] = update.window_minutes
        if update.cooldown_minutes is not None:
            current_config['cooldown_minutes'] = update.cooldown_minutes

        # Save updated config
        cursor.execute("""
            UPDATE user_rules
            SET rules_config = jsonb_set(
                rules_config,
                '{circuit_breaker}',
                %s
            )
            WHERE user_id = %s AND strategy = 'archer_dual'
        """, (json.dumps(current_config), user_id))

        conn.commit()

        return {
            "status": "success",
            "message": f"Circuit breaker updated for {user_id}",
            "circuit_breaker": current_config
        }

    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))

    finally:
        cursor.close()
        conn.close()

# =====================================================================
# Endpoints - Generic Rules Update
# =====================================================================

@app.patch("/api/users/{user_id}/rules")
async def update_user_rules(
    user_id: str,
    update: RulesUpdate,
    username: str = Depends(verify_credentials)
):
    """Update any field in rules_config"""

    conn = get_db()
    cursor = conn.cursor()

    try:
        # Validate field name (prevent SQL injection)
        allowed_fields = [
            'risk_pct', 'min_rr', 'max_leverage', 'use_guardian',
            'enabled', 'max_position_size'
        ]

        if update.field not in allowed_fields:
            raise HTTPException(
                status_code=400,
                detail=f"Field {update.field} not allowed. Allowed: {allowed_fields}"
            )

        cursor.execute("""
            UPDATE user_rules
            SET rules_config = jsonb_set(
                rules_config,
                %s,
                %s
            )
            WHERE user_id = %s AND strategy = 'archer_dual'
        """, ('{' + update.field + '}', json.dumps(update.value), user_id))

        if cursor.rowcount == 0:
            raise HTTPException(status_code=404, detail=f"User {user_id} not found")

        conn.commit()

        return {
            "status": "success",
            "message": f"Updated {update.field} = {update.value} for {user_id}",
            "user_id": user_id,
            "field": update.field,
            "value": update.value
        }

    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))

    finally:
        cursor.close()
        conn.close()

# =====================================================================
# Health Check
# =====================================================================

@app.get("/health")
async def health_check():
    """Health check endpoint"""
    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("SELECT 1")
        cursor.close()
        conn.close()

        return {
            "status": "healthy",
            "database": "connected"
        }
    except Exception as e:
        return {
            "status": "unhealthy",
            "database": "disconnected",
            "error": str(e)
        }

# =====================================================================
# Run
# =====================================================================

if __name__ == "__main__":
    import uvicorn
    print("🎛️  Starting Crypto Admin Panel...")
    print(f"📊 Dashboard: http://localhost:8080")
    print(f"📖 API Docs: http://localhost:8080/docs")
    uvicorn.run(app, host="0.0.0.0", port=8080)
