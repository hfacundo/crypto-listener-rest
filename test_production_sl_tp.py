#!/usr/bin/env python3
"""
Script de prueba REAL (PRODUCCIÓN) para verificar que Stop Loss y Take Profit
funcionan correctamente después del fix del algoType.

⚠️  ADVERTENCIA: Este script opera en PRODUCCIÓN con dinero real.
   Solo ejecutar si estás seguro de lo que estás haciendo.

Uso:
    python test_production_sl_tp.py
"""

import os
import sys
import time
from decimal import Decimal

# Agregar el directorio raíz al path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.utils.binance.binance_client import get_binance_client_for_user
from app.utils.binance.utils import (
    get_mark_price,
    get_symbol_filters,
    adjust_price_to_tick,
    get_available_usdt_balance
)
from app.utils.binance.validators import create_stop_loss_order, create_take_profit_order


def test_production_sl_tp(
    symbol: str = "BTCUSDT",
    user_id: str = "hufsa",
    risk_pct: float = 1.0,
    sl_pct: float = 2.0,
    tp_pct: float = 4.0,
    leverage: int = 10
):
    """
    Crea un trade real de prueba con Stop Loss y Take Profit.

    Args:
        symbol: Par de trading (default: BTCUSDT)
        user_id: Usuario de Binance (default: hufsa)
        risk_pct: Porcentaje del balance a usar (default: 1.0%)
        sl_pct: Porcentaje de stop loss (default: 2.0%)
        tp_pct: Porcentaje de take profit (default: 4.0%)
        leverage: Apalancamiento a usar (default: 10x)
    """

    print(f"\n{'='*70}")
    print(f"  ⚠️  PRUEBA EN PRODUCCIÓN - DINERO REAL")
    print(f"{'='*70}\n")
    print(f"Usuario:        {user_id}")
    print(f"Símbolo:        {symbol}")
    print(f"Capital:        {risk_pct}% del balance disponible")
    print(f"Apalancamiento: {leverage}x")
    print(f"Stop Loss:      -{sl_pct}%")
    print(f"Take Profit:    +{tp_pct}%")
    print()

    try:
        # 1. Obtener cliente de Binance
        print("1️⃣  Conectando con Binance (Producción)...")
        client = get_binance_client_for_user(user_id)
        print("   ✅ Cliente conectado\n")

        # 2. Obtener balance disponible
        print("2️⃣  Obteniendo balance disponible...")
        available_balance = get_available_usdt_balance(client)
        capital_to_use = available_balance * (risk_pct / 100.0)

        print(f"   💰 Balance disponible: {available_balance:.2f} USDT")
        print(f"   💵 Capital a usar ({risk_pct}%): {capital_to_use:.2f} USDT\n")

        if capital_to_use < 5:
            print(f"❌ Capital insuficiente ({capital_to_use:.2f} USDT < 5 USDT mínimo)")
            return False

        # 3. Obtener precio actual del mercado
        print(f"3️⃣  Obteniendo precio de mercado para {symbol}...")
        mark_price = get_mark_price(symbol, client)
        print(f"   📊 Mark Price: {mark_price:.2f} USDT\n")

        # 4. Obtener filtros del símbolo
        print("4️⃣  Obteniendo filtros del símbolo...")
        filters = get_symbol_filters(symbol, client)
        tick_size = float(filters["PRICE_FILTER"]["tickSize"])
        min_qty = float(filters["LOT_SIZE"]["minQty"])
        step_size = float(filters["LOT_SIZE"]["stepSize"])

        print(f"   📏 Tick size: {tick_size}")
        print(f"   📏 Min quantity: {min_qty}")
        print(f"   📏 Step size: {step_size}\n")

        # 5. Calcular precios de SL y TP
        print("5️⃣  Calculando precios de Stop Loss y Take Profit...")

        # Para LONG: SL abajo, TP arriba
        stop_price = mark_price * (1 - sl_pct / 100.0)
        target_price = mark_price * (1 + tp_pct / 100.0)

        # Ajustar a tick size
        stop_price = adjust_price_to_tick(stop_price, tick_size)
        target_price = adjust_price_to_tick(target_price, tick_size)

        # Calcular R:R
        risk = mark_price - stop_price
        reward = target_price - mark_price
        risk_reward = reward / risk if risk > 0 else 0

        print(f"   📉 Stop Loss:     {stop_price:.2f} USDT (-{sl_pct}%)")
        print(f"   📈 Take Profit:   {target_price:.2f} USDT (+{tp_pct}%)")
        print(f"   ⚖️  Risk/Reward:   1:{risk_reward:.2f}\n")

        # 6. Calcular cantidad a comprar
        print("6️⃣  Calculando cantidad con apalancamiento...")

        # Capital con leverage
        position_size_usdt = capital_to_use * leverage

        # ⚠️ IMPORTANTE: Binance requiere un valor mínimo de posición de 100 USDT
        MIN_NOTIONAL = 100.0
        if position_size_usdt < MIN_NOTIONAL:
            print(f"   ⚠️  Ajustando posición al mínimo de {MIN_NOTIONAL} USDT (notional requirement)")
            position_size_usdt = MIN_NOTIONAL

        # Cantidad de BTC (o el asset correspondiente)
        quantity = position_size_usdt / mark_price

        # Ajustar a step size (redondear hacia arriba para no caer bajo el mínimo)
        quantity_decimal = Decimal(str(quantity))
        step_decimal = Decimal(str(step_size))
        remainder = quantity_decimal % step_decimal
        if remainder != 0:
            # Redondear hacia arriba
            quantity = float(quantity_decimal - remainder + step_decimal)
        else:
            quantity = float(quantity_decimal)

        # Validar cantidad mínima
        if quantity < min_qty:
            quantity = min_qty

        # Recalcular capital real necesario
        actual_position_value = quantity * mark_price

        # Validar que el valor final sea al menos MIN_NOTIONAL
        if actual_position_value < MIN_NOTIONAL:
            # Ajustar cantidad para cumplir con el mínimo
            quantity = (MIN_NOTIONAL / mark_price)
            # Re-ajustar al step size (hacia arriba)
            quantity_decimal = Decimal(str(quantity))
            remainder = quantity_decimal % step_decimal
            if remainder != 0:
                quantity = float(quantity_decimal - remainder + step_decimal)
            else:
                quantity = float(quantity_decimal)
            actual_position_value = quantity * mark_price

        actual_capital_needed = actual_position_value / leverage

        print(f"   📊 Cantidad:            {quantity} {symbol.replace('USDT', '')}")
        print(f"   💵 Valor posición:      {actual_position_value:.2f} USDT")
        print(f"   💰 Margen necesario:    {actual_capital_needed:.2f} USDT (a {leverage}x)\n")

        # 7. Validar que tenemos suficiente balance
        if actual_capital_needed > available_balance:
            print(f"❌ Balance insuficiente para la operación")
            print(f"   Necesario: {actual_capital_needed:.2f} USDT")
            print(f"   Disponible: {available_balance:.2f} USDT")
            return False

        # 8. CONFIRMACIÓN FINAL
        print("="*70)
        print("📋 RESUMEN DE LA OPERACIÓN")
        print("="*70)
        print(f"Usuario:              {user_id}")
        print(f"Símbolo:              {symbol}")
        print(f"Tipo:                 LONG (BUY)")
        print(f"Cantidad:             {quantity} {symbol.replace('USDT', '')}")
        print(f"Precio entrada:       ~{mark_price:.2f} USDT")
        print(f"Stop Loss:            {stop_price:.2f} USDT (-{sl_pct}%)")
        print(f"Take Profit:          {target_price:.2f} USDT (+{tp_pct}%)")
        print(f"Apalancamiento:       {leverage}x")
        print(f"Valor posición:       {actual_position_value:.2f} USDT")
        print(f"Margen requerido:     {actual_capital_needed:.2f} USDT")
        print(f"Balance disponible:   {available_balance:.2f} USDT")
        print(f"Risk/Reward:          1:{risk_reward:.2f}")
        print("="*70)
        print()
        print("⚠️  IMPORTANTE:")
        print("   • Este es un trade REAL con dinero REAL")
        print("   • Se creará una posición LONG en producción")
        print("   • El Stop Loss y Take Profit se establecerán automáticamente")
        print("   • Deberás CERRAR MANUALMENTE la posición después de validar")
        print()

        response = input("¿Confirmas que deseas crear este trade de prueba? (escribir 'SI CONFIRMO'): ")

        if response != "SI CONFIRMO":
            print("\n❌ Operación cancelada por el usuario")
            return False

        # 9. Configurar leverage
        print(f"\n9️⃣  Configurando leverage a {leverage}x...")
        try:
            client.futures_change_leverage(symbol=symbol, leverage=leverage)
            print(f"   ✅ Leverage configurado a {leverage}x\n")
        except Exception as e:
            print(f"   ⚠️  No se pudo cambiar leverage (puede ya estar configurado): {e}\n")

        # 10. Crear orden MARKET de compra
        print("🔟 Creando orden MARKET de compra...")
        try:
            market_order = client.futures_create_order(
                symbol=symbol,
                side="BUY",
                type="MARKET",
                quantity=quantity
            )

            order_id = market_order.get("orderId")
            print(f"   ✅ Orden MARKET ejecutada (OrderID: {order_id})")
            print(f"   📊 Respuesta: {market_order}\n")

            # Esperar un momento para que se llene la orden
            print("   ⏳ Esperando confirmación...")
            time.sleep(2)

            # Verificar que la orden se llenó
            order_status = client.futures_get_order(symbol=symbol, orderId=order_id)
            if order_status.get("status") != "FILLED":
                print(f"   ⚠️  Advertencia: Orden no está FILLED, status: {order_status.get('status')}")
            else:
                avg_price = float(order_status.get("avgPrice", mark_price))
                print(f"   ✅ Orden FILLED a precio promedio: {avg_price:.2f} USDT\n")

        except Exception as e:
            print(f"   ❌ Error al crear orden MARKET: {e}")
            import traceback
            traceback.print_exc()
            return False

        # 11. Crear Stop Loss
        print("1️⃣1️⃣  Creando STOP LOSS con Algo Order API...")
        sl_result = create_stop_loss_order(
            symbol=symbol,
            direction="SELL",  # Para cerrar posición LONG
            stop_price=stop_price,
            client=client,
            user_id=user_id,
            working_type="CONTRACT_PRICE"  # Usar CONTRACT_PRICE para mejor protección
        )

        if sl_result:
            sl_algo_id = sl_result.get("algoId")
            print(f"   ✅ Stop Loss creado correctamente")
            print(f"   📋 Algo ID: {sl_algo_id}")
            print(f"   📉 Precio activación: {stop_price:.2f} USDT")
            print(f"   📊 Respuesta completa: {sl_result}\n")
        else:
            print("   ❌ ERROR: No se pudo crear el Stop Loss")
            print("   ⚠️  ¡CUIDADO! Posición sin protección de SL")
            print("   💡 Debes cerrar la posición manualmente o establecer SL desde la UI\n")
            return False

        # 12. Crear Take Profit
        print("1️⃣2️⃣  Creando TAKE PROFIT con Algo Order API...")
        tp_result = create_take_profit_order(
            symbol=symbol,
            direction="SELL",  # Para cerrar posición LONG
            stop_price=target_price,
            client=client,
            user_id=user_id
        )

        if tp_result:
            tp_algo_id = tp_result.get("algoId")
            print(f"   ✅ Take Profit creado correctamente")
            print(f"   📋 Algo ID: {tp_algo_id}")
            print(f"   📈 Precio activación: {target_price:.2f} USDT")
            print(f"   📊 Respuesta completa: {tp_result}\n")
        else:
            print("   ❌ ERROR: No se pudo crear el Take Profit")
            print("   ⚠️  Posición tiene SL pero no TP")
            print("   💡 Puedes establecer TP manualmente desde la UI\n")

        # 13. Verificar Algo Orders activas
        print("1️⃣3️⃣  Verificando Algo Orders activas...")
        try:
            algo_orders = client._request_futures_api(
                'get',
                'openAlgoOrders',
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
                print(f"   ✅ {len(open_algo_orders)} Algo Order(s) activa(s) para {symbol}:")
                for order in open_algo_orders:
                    order_type = order.get("type", "UNKNOWN")
                    algo_id = order.get("algoId")
                    trigger = order.get("triggerPrice") or order.get("stopPrice", "N/A")
                    side = order.get("side", "N/A")
                    print(f"      • {order_type} ({side}) @ {trigger} (algoId: {algo_id})")
            else:
                print("   ⚠️  No se encontraron Algo Orders activas")

        except Exception as e:
            print(f"   ⚠️  Error al obtener Algo Orders: {e}")

        # 14. Verificar posición abierta
        print("\n1️⃣4️⃣  Verificando posición abierta...")
        try:
            positions = client.futures_position_information(symbol=symbol)
            for pos in positions:
                pos_amt = float(pos.get("positionAmt", 0))
                if pos_amt != 0:
                    entry_price = float(pos.get("entryPrice", 0))
                    unrealized_pnl = float(pos.get("unRealizedProfit", 0))
                    leverage_used = int(pos.get("leverage", 0))

                    print(f"   ✅ Posición confirmada:")
                    print(f"      • Cantidad: {pos_amt}")
                    print(f"      • Precio entrada: {entry_price:.2f} USDT")
                    print(f"      • PnL no realizado: {unrealized_pnl:.2f} USDT")
                    print(f"      • Leverage: {leverage_used}x")
                    break
            else:
                print("   ⚠️  No se encontró posición abierta (puede estar cerrándose)")

        except Exception as e:
            print(f"   ⚠️  Error al verificar posición: {e}")

        # 15. Resumen final
        print(f"\n{'='*70}")
        print("  ✅ TEST COMPLETADO EXITOSAMENTE")
        print(f"{'='*70}\n")

        print("📋 RESUMEN:")
        print(f"   • Usuario:          {user_id}")
        print(f"   • Posición:         LONG {quantity} {symbol.replace('USDT', '')}")
        print(f"   • Precio entrada:   ~{mark_price:.2f} USDT")
        print(f"   • Stop Loss:        {stop_price:.2f} USDT (algoId: {sl_algo_id if sl_result else 'N/A'})")
        print(f"   • Take Profit:      {target_price:.2f} USDT (algoId: {tp_algo_id if tp_result else 'N/A'})")
        print(f"   • Leverage:         {leverage}x")
        print()
        print("✅ VALIDACIÓN:")
        if sl_result and tp_result:
            print("   • Stop Loss y Take Profit creados correctamente ✅")
            print("   • Las órdenes están usando el nuevo Algo Order API ✅")
            print("   • Los parámetros algoType=CONDITIONAL están funcionando ✅")
        elif sl_result:
            print("   • Stop Loss creado ✅")
            print("   • Take Profit falló ❌")
        else:
            print("   • Stop Loss falló ❌")
            print("   • Take Profit no se intentó ❌")

        print()
        print("📱 PRÓXIMOS PASOS:")
        print("   1. Verifica la posición en Binance:")
        print("      https://www.binance.com/en/futures/BTCUSDT")
        print()
        print("   2. Verifica las Algo Orders en:")
        print("      Futures → Orders → Algo Orders")
        print()
        print("   3. Cuando termines la validación, CIERRA LA POSICIÓN MANUALMENTE")
        print()
        print("⚠️  RECUERDA: Esta es una posición REAL que requiere monitoreo.")
        print()

        return True

    except Exception as e:
        print(f"\n❌ Error durante el test: {e}")
        import traceback
        traceback.print_exc()
        return False


if __name__ == "__main__":
    print("\n🧪 Test de Producción: Stop Loss y Take Profit\n")

    # Parámetros configurables
    SYMBOL = "BTCUSDT"
    USER_ID = "hufsa"
    RISK_PCT = 3.0  # 3% del balance (ajustado para balance pequeño)
    SL_PCT = 2.0    # -2% Stop Loss
    TP_PCT = 4.0    # +4% Take Profit
    LEVERAGE = 10   # 10x leverage

    # Ejecutar test
    success = test_production_sl_tp(
        symbol=SYMBOL,
        user_id=USER_ID,
        risk_pct=RISK_PCT,
        sl_pct=SL_PCT,
        tp_pct=TP_PCT,
        leverage=LEVERAGE
    )

    sys.exit(0 if success else 1)
