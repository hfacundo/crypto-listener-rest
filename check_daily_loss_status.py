#!/usr/bin/env python3
"""
Script para consultar el estado de Daily Loss Limit de un usuario
Muestra:
- Cuándo comenzó el bloqueo
- Cuándo finaliza el bloqueo
- P&L diario actual
- Trades cerrados hoy
"""

import sys
import os
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from datetime import datetime, timezone, timedelta
from app.utils.db.redis_client import get_redis_client
from app.utils.trade_protection import TradeProtectionSystem
from app.utils.db.query_executor import get_rules

def check_daily_loss_status(user_id: str, strategy: str = "archer_model"):
    """
    Consulta el estado completo de Daily Loss Limit para un usuario.

    Args:
        user_id: ID del usuario (ej: "hufsa", "copy_trading")
        strategy: Estrategia (default: "archer_model")
    """

    print(f"\n{'='*80}")
    print(f"📊 DAILY LOSS LIMIT STATUS - {user_id.upper()}/{strategy.upper()}")
    print(f"{'='*80}\n")

    # 1. Obtener configuración del usuario
    try:
        rules = get_rules(user_id, strategy)
        daily_loss_config = rules.get("daily_loss_limits", {})

        if not daily_loss_config.get("enabled", False):
            print("⚠️  Daily Loss Limits: DISABLED")
            print("    This user does not have daily loss limits enabled.\n")
            return

        max_daily_loss_pct = daily_loss_config.get("max_daily_loss_pct", 5.0)
        pause_duration_hours = daily_loss_config.get("pause_duration_hours", 12)

        print(f"⚙️  Configuration:")
        print(f"    Max Daily Loss: {max_daily_loss_pct}%")
        print(f"    Pause Duration: {pause_duration_hours} hours")
        print(f"    Reset Time: {daily_loss_config.get('reset_time_utc', '00:00')} UTC\n")

    except Exception as e:
        print(f"❌ Error loading user config: {e}\n")
        return

    # 2. Verificar pausa activa en Redis
    try:
        redis_client = get_redis_client()
        if not redis_client:
            print("❌ Redis not available - cannot check pause status\n")
            return

        cache_prefix = f"user_risk:{user_id}:{strategy}"
        pause_key = f"{cache_prefix}:daily_loss_pause"
        pause_until_str = redis_client.get(pause_key)

        now_utc = datetime.now(timezone.utc)

        if pause_until_str:
            pause_until = datetime.fromisoformat(pause_until_str)

            # Calcular cuándo comenzó la pausa
            pause_started = pause_until - timedelta(hours=pause_duration_hours)
            time_since_start = now_utc - pause_started

            if now_utc < pause_until:
                time_remaining = pause_until - now_utc
                hours_remaining = time_remaining.total_seconds() / 3600
                minutes_remaining = (time_remaining.total_seconds() % 3600) / 60

                print(f"🚨 PAUSE STATUS: ACTIVE")
                print(f"    Pause Started: {pause_started.strftime('%Y-%m-%d %H:%M:%S')} UTC")
                print(f"    Time Elapsed: {time_since_start.total_seconds()/3600:.1f} hours")
                print(f"    Pause Ends: {pause_until.strftime('%Y-%m-%d %H:%M:%S')} UTC")
                print(f"    Time Remaining: {int(hours_remaining)}h {int(minutes_remaining)}m\n")
            else:
                print(f"✅ PAUSE STATUS: EXPIRED (but not cleared from Redis)")
                print(f"    Pause ended at: {pause_until.strftime('%Y-%m-%d %H:%M:%S')} UTC")
                print(f"    Time since expiration: {(now_utc - pause_until).total_seconds()/3600:.1f} hours\n")
                print(f"    💡 Redis key still exists but pause is no longer active.")
                print(f"       Next trade will clear this automatically.\n")
        else:
            print(f"✅ PAUSE STATUS: NO ACTIVE PAUSE\n")

    except Exception as e:
        print(f"❌ Error checking Redis pause status: {e}\n")

    # 3. Calcular P&L diario desde PostgreSQL
    try:
        protection_system = TradeProtectionSystem()
        conn = protection_system._get_conn()

        query = """
            SELECT
                COUNT(*) as total_trades,
                COALESCE(SUM(pnl_pct), 0) as daily_pnl_pct,
                COALESCE(SUM(pnl_usdt), 0) as daily_pnl_usdt,
                SUM(CASE WHEN pnl_pct > 0 THEN 1 ELSE 0 END) as winning_trades,
                SUM(CASE WHEN pnl_pct < 0 THEN 1 ELSE 0 END) as losing_trades
            FROM trade_history
            WHERE user_id = %s
              AND strategy = %s
              AND exit_time >= DATE_TRUNC('day', NOW() AT TIME ZONE 'UTC')
              AND exit_reason IN ('target_hit', 'stop_hit', 'timeout', 'manual_close')
        """

        with conn.cursor() as cur:
            cur.execute(query, (user_id, strategy))
            result = cur.fetchone()

            total_trades = result[0]
            daily_pnl_pct = float(result[1])
            daily_pnl_usdt = float(result[2])
            winning_trades = result[3]
            losing_trades = result[4]

        conn.close()

        # Determinar estado
        pnl_emoji = "🟢" if daily_pnl_pct >= 0 else "🔴"
        remaining_loss_allowance = max_daily_loss_pct + daily_pnl_pct  # Si perdiste -2% y límite es 5%, te quedan 3%

        print(f"{pnl_emoji} TODAY'S P&L (since midnight UTC):")
        print(f"    Total Trades: {total_trades}")
        print(f"    Winning Trades: {winning_trades}")
        print(f"    Losing Trades: {losing_trades}")
        print(f"    Daily P&L: {daily_pnl_pct:.2f}% (${daily_pnl_usdt:.2f})")
        print(f"    Max Allowed Loss: -{max_daily_loss_pct}%")
        print(f"    Remaining Allowance: {remaining_loss_allowance:.2f}%\n")

        # Verificar si se está cerca del límite
        if daily_pnl_pct <= -max_daily_loss_pct:
            print(f"    ⚠️  LIMIT EXCEEDED: You've lost {daily_pnl_pct:.2f}% (limit: -{max_daily_loss_pct}%)")
            print(f"        Trading should be paused for {pause_duration_hours} hours.\n")
        elif daily_pnl_pct < 0 and remaining_loss_allowance <= 1.0:
            print(f"    ⚠️  WARNING: Close to limit! Only {remaining_loss_allowance:.2f}% remaining.\n")

        # 4. Mostrar trades cerrados hoy
        if total_trades > 0:
            print(f"📋 TODAY'S CLOSED TRADES:")
            print(f"    {'Symbol':<12} | {'Side':<5} | {'Exit':<12} | {'PnL %':<8} | {'PnL $':<10} | {'Time'}")
            print(f"    {'-'*75}")

            query_trades = """
                SELECT
                    symbol,
                    direction,
                    exit_reason,
                    pnl_pct,
                    pnl_usdt,
                    exit_time
                FROM trade_history
                WHERE user_id = %s
                  AND strategy = %s
                  AND exit_time >= DATE_TRUNC('day', NOW() AT TIME ZONE 'UTC')
                  AND exit_reason IN ('target_hit', 'stop_hit', 'timeout', 'manual_close')
                ORDER BY exit_time DESC
            """

            conn = protection_system._get_conn()
            with conn.cursor() as cur:
                cur.execute(query_trades, (user_id, strategy))
                trades = cur.fetchall()

                for trade in trades:
                    symbol, direction, exit_reason, pnl_pct, pnl_usdt, exit_time = trade
                    pnl_pct = float(pnl_pct)
                    pnl_usdt = float(pnl_usdt)

                    emoji = "✅" if pnl_pct > 0 else "❌"
                    exit_short = exit_reason.replace('_hit', '').replace('_close', '').upper()[:12]
                    time_str = exit_time.strftime('%H:%M:%S')

                    print(f"    {emoji} {symbol:<10} | {direction:<5} | {exit_short:<12} | {pnl_pct:>7.2f}% | ${pnl_usdt:>9.2f} | {time_str}")

            conn.close()
            print()

    except Exception as e:
        print(f"❌ Error calculating daily P&L: {e}\n")
        import traceback
        print(traceback.format_exc())

    print(f"{'='*80}\n")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Check Daily Loss Limit status for a user")
    parser.add_argument("user_id", nargs="?", default="hufsa", help="User ID (default: hufsa)")
    parser.add_argument("--strategy", default="archer_model", help="Strategy name (default: archer_model)")

    args = parser.parse_args()

    check_daily_loss_status(args.user_id, args.strategy)
