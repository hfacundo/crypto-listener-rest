#!/usr/bin/env python3
"""
Script de prueba para verificar que el Algo Order API funciona correctamente.
Ejecuta este script en modo Testnet antes de desplegar a producción.

Uso:
    export USE_BINANCE_TESTNET=true
    export BINANCE_FUTURES_API_KEY_FUTURES=tu_testnet_key
    export BINANCE_FUTURES_API_SECRET_FUTURES=tu_testnet_secret
    python test_algo_orders.py
"""

import os
import sys
from decimal import Decimal

# Agregar el directorio raíz al path para poder importar
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.utils.binance.binance_client import get_binance_client_for_user
from app.utils.binance.utils import get_mark_price, get_symbol_filters, adjust_price_to_tick
from app.utils.binance.validators import create_stop_loss_order, create_take_profit_order

def test_algo_orders(symbol="BTCUSDT", user_id="futures"):
    """
    Prueba la creación de órdenes usando el Algo Order API.

    Args:
        symbol: Par de trading a probar (default: BTCUSDT)
        user_id: ID del usuario (default: futures)
    """
    print(f"\n{'='*60}")
    print(f"  TEST: Algo Order API para {symbol}")
    print(f"{'='*60}\n")

    # Verificar que estamos en Testnet
    use_testnet = os.environ.get("USE_BINANCE_TESTNET", "false").lower() == "true"
    if not use_testnet:
        print("⚠️  WARNING: USE_BINANCE_TESTNET no está activado")
        print("   Asegúrate de estar usando Testnet antes de continuar!")
        response = input("\n¿Deseas continuar de todos modos? (yes/no): ")
        if response.lower() != "yes":
            print("❌ Test cancelado por seguridad")
            return False

    try:
        # 1. Obtener cliente
        print("1️⃣  Obteniendo cliente de Binance...")
        client = get_binance_client_for_user(user_id)
        print("   ✅ Cliente obtenido correctamente\n")

        # 2. Obtener precio actual
        print(f"2️⃣  Obteniendo mark price para {symbol}...")
        mark_price = get_mark_price(symbol, client)
        print(f"   ✅ Mark Price: {mark_price:.2f} USDT\n")

        # 3. Obtener filtros y calcular precios de SL/TP
        print("3️⃣  Calculando precios de Stop Loss y Take Profit...")
        filters = get_symbol_filters(symbol, client)
        tick_size = float(filters["PRICE_FILTER"]["tickSize"])

        # Calcular precios de prueba (SL: -2%, TP: +4%)
        stop_price = mark_price * 0.98
        target_price = mark_price * 1.04

        # Ajustar a tick size
        stop_price = adjust_price_to_tick(stop_price, tick_size)
        target_price = adjust_price_to_tick(target_price, tick_size)

        print(f"   📉 Stop Loss:    {stop_price:.2f} USDT (-2%)")
        print(f"   📈 Take Profit:  {target_price:.2f} USDT (+4%)")
        print(f"   ✅ Precios ajustados a tick size: {tick_size}\n")

        # 4. Verificar balance
        print("4️⃣  Verificando balance...")
        account = client.futures_account()
        usdt_balance = None
        for asset in account.get("assets", []):
            if asset["asset"] == "USDT":
                usdt_balance = float(asset["availableBalance"])
                break

        if usdt_balance:
            print(f"   ✅ Balance disponible: {usdt_balance:.2f} USDT\n")
        else:
            print("   ⚠️  No se pudo obtener el balance USDT\n")

        # 5. Preguntar si crear una posición de prueba
        print("5️⃣  ¿Deseas crear una posición de prueba?")
        print("   Esto creará:")
        print(f"   - Una orden MARKET de compra pequeña en {symbol}")
        print(f"   - Una orden STOP_LOSS usando Algo Order API")
        print(f"   - Una orden TAKE_PROFIT usando Algo Order API")
        print()
        response = input("   Continuar? (yes/no): ")

        if response.lower() != "yes":
            print("\n❌ Test cancelado. No se crearon órdenes.")
            return True

        # 6. Crear orden MARKET de prueba (muy pequeña)
        print("\n6️⃣  Creando orden MARKET de prueba...")

        # Calcular quantity mínima permitida
        min_qty = float(filters["LOT_SIZE"]["minQty"])
        step_size = float(filters["LOT_SIZE"]["stepSize"])

        # Usar cantidad mínima + 1 step para seguridad
        test_qty = min_qty + step_size

        print(f"   Cantidad: {test_qty} {symbol.replace('USDT', '')}")

        try:
            market_order = client.futures_create_order(
                symbol=symbol,
                side="BUY",
                type="MARKET",
                quantity=test_qty
            )
            print(f"   ✅ Orden MARKET ejecutada (OrderID: {market_order.get('orderId')})\n")
        except Exception as e:
            print(f"   ❌ Error al crear orden MARKET: {e}")
            return False

        # 7. Crear Stop Loss usando Algo Order API
        print("7️⃣  Creando STOP LOSS usando Algo Order API...")
        sl_result = create_stop_loss_order(
            symbol=symbol,
            direction="SELL",
            stop_price=stop_price,
            client=client,
            user_id=user_id
        )

        if sl_result:
            algo_id = sl_result.get("algoId") or sl_result.get("orderId")
            print(f"   ✅ Stop Loss creado correctamente")
            print(f"   📋 Algo ID: {algo_id}")
            print(f"   📉 Precio: {stop_price:.2f} USDT\n")
        else:
            print("   ❌ Error al crear Stop Loss")
            return False

        # 8. Crear Take Profit usando Algo Order API
        print("8️⃣  Creando TAKE PROFIT usando Algo Order API...")
        tp_result = create_take_profit_order(
            symbol=symbol,
            direction="SELL",
            stop_price=target_price,
            client=client,
            user_id=user_id
        )

        if tp_result:
            algo_id = tp_result.get("algoId") or tp_result.get("orderId")
            print(f"   ✅ Take Profit creado correctamente")
            print(f"   📋 Algo ID: {algo_id}")
            print(f"   📈 Precio: {target_price:.2f} USDT\n")
        else:
            print("   ❌ Error al crear Take Profit")
            return False

        # 9. Verificar órdenes creadas
        print("9️⃣  Verificando Algo Orders activas...")
        try:
            algo_orders = client._request_futures_api(
                'get',
                'algoOpenOrders',
                signed=True,
                data={"symbol": symbol}
            )

            # Manejar diferentes formatos de respuesta
            if isinstance(algo_orders, dict) and "openOrders" in algo_orders:
                open_algo_orders = algo_orders["openOrders"]
            elif isinstance(algo_orders, list):
                open_algo_orders = algo_orders
            else:
                open_algo_orders = []

            if open_algo_orders:
                print(f"   ✅ {len(open_algo_orders)} Algo Order(s) activa(s):")
                for order in open_algo_orders:
                    order_type = order.get("algoType") or order.get("type", "UNKNOWN")
                    algo_id = order.get("algoId")
                    stop = order.get("stopPrice", "N/A")
                    print(f"      - {order_type} @ {stop} (algoId: {algo_id})")
            else:
                print("   ⚠️  No se encontraron Algo Orders activas")

        except Exception as e:
            print(f"   ⚠️  No se pudieron obtener Algo Orders: {e}")

        # 10. Resumen
        print(f"\n{'='*60}")
        print("  ✅ TEST COMPLETADO EXITOSAMENTE")
        print(f"{'='*60}\n")
        print("📋 Resumen:")
        print(f"   • Posición abierta:     {test_qty} {symbol.replace('USDT', '')}")
        print(f"   • Stop Loss:            {stop_price:.2f} USDT")
        print(f"   • Take Profit:          {target_price:.2f} USDT")
        print(f"   • Usando Algo Order API: ✅")
        print()
        print("🔍 Verifica en Binance Testnet:")
        print(f"   https://testnet.binancefuture.com/")
        print()
        print("⚠️  IMPORTANTE:")
        print("   Las órdenes SL/TP están en 'Algo Orders', no en 'Open Orders'")
        print()

        # Preguntar si cerrar la posición
        response = input("¿Deseas cerrar la posición de prueba ahora? (yes/no): ")
        if response.lower() == "yes":
            print("\n🧹 Cerrando posición...")
            try:
                # Cerrar posición
                close_order = client.futures_create_order(
                    symbol=symbol,
                    side="SELL",
                    type="MARKET",
                    quantity=test_qty,
                    reduceOnly=True
                )
                print("   ✅ Posición cerrada\n")

                # Cancelar Algo Orders huérfanas
                print("   Cancelando Algo Orders huérfanas...")
                for order in open_algo_orders:
                    algo_id = order.get("algoId")
                    if algo_id:
                        try:
                            client._request_futures_api(
                                'delete',
                                'algoOrder',
                                signed=True,
                                data={"symbol": symbol, "algoId": algo_id}
                            )
                            print(f"      ✅ Cancelada algoId: {algo_id}")
                        except Exception as e:
                            print(f"      ⚠️  Error cancelando {algo_id}: {e}")

                print("\n✅ Limpieza completada")

            except Exception as e:
                print(f"   ❌ Error al cerrar posición: {e}")

        return True

    except Exception as e:
        print(f"\n❌ Error durante el test: {e}")
        import traceback
        traceback.print_exc()
        return False


if __name__ == "__main__":
    print("\n🧪 Script de Prueba: Algo Order API\n")

    # Verificar variables de entorno
    print("Verificando configuración...")
    if not os.environ.get("USE_BINANCE_TESTNET"):
        print("⚠️  Variable USE_BINANCE_TESTNET no encontrada")
        print("   Recomendación: export USE_BINANCE_TESTNET=true\n")

    if not os.environ.get("BINANCE_FUTURES_API_KEY_FUTURES"):
        print("❌ Variable BINANCE_FUTURES_API_KEY_FUTURES no encontrada")
        print("   Necesitas configurar las API keys de Testnet")
        print("   Ver TESTNET_GUIDE.md para más información\n")
        sys.exit(1)

    # Ejecutar test
    success = test_algo_orders()

    sys.exit(0 if success else 1)
