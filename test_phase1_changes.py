#!/usr/bin/env python3
"""
Script de prueba para Fase 1: Cambios en crypto-listener-rest

Prueba:
1. Backward compatibility (requests sin level_metadata)
2. Nueva funcionalidad (requests con level_metadata)
3. Estructura de respuesta enriquecida
"""

import requests
import json
from typing import Dict, Any

# URL del servicio (ajustar según tu configuración)
BASE_URL = "http://localhost:8000"

def test_backward_compatibility():
    """
    Prueba que requests sin level_metadata siguen funcionando
    """
    print("\n" + "="*80)
    print("TEST 1: Backward Compatibility (sin level_metadata)")
    print("="*80)

    # Request legacy sin level_metadata
    payload = {
        "symbol": "BTCUSDT",
        "action": "adjust",
        "stop": 44500.5,
        "user_id": "test_user"
    }

    print("\n📤 Sending legacy request (no level_metadata):")
    print(json.dumps(payload, indent=2))

    try:
        # Nota: Este test solo muestra la estructura, no ejecuta
        # Para ejecutar realmente, descomenta la siguiente línea
        # response = requests.post(f"{BASE_URL}/guardian", json=payload, timeout=10)

        print("\n✅ Expected behavior:")
        print("   - Function should accept request without level_metadata")
        print("   - Redis should update with level_applied='manual_adjust'")
        print("   - Response should include new fields with defaults")

        expected_response = {
            "success": True,
            "direction": "BUY",
            "stop": 44500.5,
            "level_applied": "manual_adjust",  # Default cuando no hay metadata
            "previous_stop": 44200.0,
            "adjustment_confirmed": True,
            "redis_updated": True,
            "timestamp": 1728000000.123
        }

        print("\n📥 Expected response structure:")
        print(json.dumps(expected_response, indent=2))

    except Exception as e:
        print(f"\n❌ Error: {e}")


def test_with_level_metadata():
    """
    Prueba nueva funcionalidad con level_metadata
    """
    print("\n" + "="*80)
    print("TEST 2: New Functionality (con level_metadata)")
    print("="*80)

    # Request con level_metadata del trailing stop multinivel
    payload = {
        "symbol": "BTCUSDT",
        "action": "adjust",
        "stop": 45000.0,  # Break even
        "user_id": "test_user",
        "level_metadata": {
            "level_name": "break_even",
            "level_threshold_pct": 35,
            "previous_level": "towards_be_20"
        }
    }

    print("\n📤 Sending enhanced request (with level_metadata):")
    print(json.dumps(payload, indent=2))

    try:
        # Nota: Este test solo muestra la estructura, no ejecuta
        # Para ejecutar realmente, descomenta la siguiente línea
        # response = requests.post(f"{BASE_URL}/guardian", json=payload, timeout=10)

        print("\n✅ Expected behavior:")
        print("   - Function should extract level_name from metadata")
        print("   - Redis should update with tracking fields:")
        print("     * ts_level_applied='break_even'")
        print("     * ts_last_adjustment_ts=<timestamp>")
        print("     * ts_last_adjustment_stop=45000.0")
        print("     * ts_previous_stop=<previous_value>")
        print("     * ts_previous_level='towards_be_20'")
        print("   - Response should include level_applied='break_even'")

        expected_response = {
            "success": True,
            "direction": "BUY",
            "stop": 45000.0,
            "level_applied": "break_even",  # Del metadata
            "previous_stop": 44500.5,
            "adjustment_confirmed": True,
            "redis_updated": True,
            "timestamp": 1728000000.456
        }

        print("\n📥 Expected response structure:")
        print(json.dumps(expected_response, indent=2))

        expected_redis = {
            "symbol": "BTCUSDT",
            "user_id": "test_user",
            "entry": 44000.0,
            "stop": 45000.0,  # Actualizado
            "stop_loss": 45000.0,  # Actualizado (compatibility)
            "target": 46000.0,
            "ts_level_applied": "break_even",  # NUEVO
            "ts_last_adjustment_ts": 1728000000.456,  # NUEVO
            "ts_last_adjustment_stop": 45000.0,  # NUEVO
            "ts_previous_stop": 44500.5,  # NUEVO
            "ts_previous_level": "towards_be_20"  # NUEVO
        }

        print("\n💾 Expected Redis structure:")
        print(json.dumps(expected_redis, indent=2))

    except Exception as e:
        print(f"\n❌ Error: {e}")


def test_redis_retry():
    """
    Prueba que el retry funciona si Redis falla
    """
    print("\n" + "="*80)
    print("TEST 3: Redis Retry Mechanism")
    print("="*80)

    print("\n✅ Expected behavior when Redis fails:")
    print("   1. First attempt to update Redis fails")
    print("   2. Wait 500ms")
    print("   3. Retry with new Redis client")
    print("   4. If retry succeeds:")
    print("      - redis_updated=True")
    print("      - Log: '✅ Redis update succeeded on retry'")
    print("   5. If retry fails:")
    print("      - redis_updated=False")
    print("      - Log: '❌ CRITICAL: Redis update failed on retry'")
    print("      - Response still returns success=True (Binance was updated)")
    print("      - Warning in response about Redis sync failure")

    expected_response_on_redis_failure = {
        "success": True,  # Binance OK
        "direction": "BUY",
        "stop": 45000.0,
        "level_applied": "break_even",
        "previous_stop": 44500.5,
        "adjustment_confirmed": True,
        "redis_updated": False,  # ⚠️ WARNING
        "timestamp": 1728000000.789
    }

    print("\n📥 Expected response when Redis fails (after retry):")
    print(json.dumps(expected_response_on_redis_failure, indent=2))


def test_all_trailing_stop_levels():
    """
    Prueba todos los niveles del trailing stop multinivel
    """
    print("\n" + "="*80)
    print("TEST 4: All Trailing Stop Levels")
    print("="*80)

    levels = [
        {"name": "towards_be_20", "threshold": 20, "stop_pct": 20, "phase": 1},
        {"name": "break_even", "threshold": 35, "stop_pct": 100, "phase": 1},
        {"name": "profit_20", "threshold": 50, "stop_pct": 20, "phase": 2},
        {"name": "profit_40", "threshold": 60, "stop_pct": 40, "phase": 2},
        {"name": "profit_55", "threshold": 70, "stop_pct": 55, "phase": 2},
        {"name": "profit_70", "threshold": 80, "stop_pct": 70, "phase": 2},
        {"name": "profit_82", "threshold": 88, "stop_pct": 82, "phase": 2},
        {"name": "profit_90", "threshold": 95, "stop_pct": 90, "phase": 2},
    ]

    print("\n📋 Testing sequence of trailing stop adjustments:")
    previous_level = None

    for i, level in enumerate(levels, 1):
        print(f"\n{'─'*60}")
        print(f"Step {i}: Level '{level['name']}' (threshold: {level['threshold']}%)")
        print(f"{'─'*60}")

        payload = {
            "symbol": "BTCUSDT",
            "action": "adjust",
            "stop": 44000 + (i * 100),  # Ejemplo de stops progresivos
            "user_id": "test_user",
            "level_metadata": {
                "level_name": level["name"],
                "level_threshold_pct": level["threshold"],
                "previous_level": previous_level
            }
        }

        print(f"Request: stop={payload['stop']}, level={level['name']}")
        print(f"Expected Redis update: ts_level_applied='{level['name']}'")
        print(f"                       ts_previous_level='{previous_level}'")

        previous_level = level["name"]

    print(f"\n{'─'*60}")
    print("✅ All 8 levels should be trackeable in Redis")
    print("✅ Each adjustment should reference the previous level")


def run_all_tests():
    """
    Ejecuta todos los tests
    """
    print("\n" + "="*80)
    print("FASE 1 - TEST SUITE: crypto-listener-rest Changes")
    print("="*80)
    print("\nNOTA: Este script solo muestra las estructuras esperadas.")
    print("Para ejecutar tests reales, descomentar las llamadas HTTP y")
    print("asegurarse de que crypto-listener-rest esté corriendo.")

    test_backward_compatibility()
    test_with_level_metadata()
    test_redis_retry()
    test_all_trailing_stop_levels()

    print("\n" + "="*80)
    print("RESUMEN DE CAMBIOS IMPLEMENTADOS")
    print("="*80)
    print("""
✅ 1. GuardianRequest ahora acepta level_metadata opcional
✅ 2. adjust_stop_only_for_open_position actualiza Redis con tracking:
      - ts_level_applied
      - ts_last_adjustment_ts
      - ts_last_adjustment_stop
      - ts_previous_stop
      - ts_previous_level
✅ 3. Respuesta HTTP enriquecida con:
      - level_applied
      - previous_stop
      - adjustment_confirmed
      - redis_updated
      - timestamp
✅ 4. Retry automático si Redis falla (1 intento, 500ms delay)
✅ 5. Backward compatibility: requests sin level_metadata funcionan
      con level_applied='manual_adjust'
✅ 6. multi_user_execution.py pasa level_metadata cuando está disponible
    """)

    print("="*80)
    print("PRÓXIMOS PASOS")
    print("="*80)
    print("""
1. Reiniciar crypto-listener-rest:
   $ sudo systemctl restart crypto-listener

2. Verificar logs:
   $ sudo journalctl -u crypto-listener -f

3. Monitorear Redis:
   $ redis-cli
   > KEYS guardian:trades:*
   > GET guardian:trades:<user_id>:<symbol>

4. Cuando confirmes que funciona, proceder con Fase 2 (crypto-guardian)
    """)


if __name__ == "__main__":
    run_all_tests()
