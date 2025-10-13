# app/admin_trade_limits.py
# Funciones administrativas para gestionar límites de trades

import json
from typing import Dict, List
from app.trade_limits import get_trade_limit_summary, get_open_positions_count
from app.utils.db.query_executor import get_rules
from app.utils.binance.binance_client import get_binance_client_for_user


def get_all_users_trade_status(users: List[str], strategy: str = "archer_dual") -> Dict:
    """
    Obtiene el estado de límites para todos los usuarios

    Returns:
        Dict con status de todos los usuarios
    """
    status_report = {
        "timestamp": None,
        "strategy": strategy,
        "users": {},
        "summary": {
            "total_users": len(users),
            "users_at_limit": 0,
            "users_near_limit": 0,
            "total_open_trades": 0,
            "avg_utilization": 0
        }
    }

    import time
    status_report["timestamp"] = time.time()

    total_utilization = 0
    users_with_limits = 0

    for user_id in users:
        try:
            rules = get_rules(user_id, strategy)
            summary = get_trade_limit_summary(user_id, rules)

            status_report["users"][user_id] = summary

            # Agregar a estadísticas
            if summary.get("max_trades_configured", 999) < 999:
                users_with_limits += 1
                total_utilization += summary.get("utilization_percentage", 0)

                if summary.get("status") == "AT_LIMIT":
                    status_report["summary"]["users_at_limit"] += 1
                elif summary.get("status") == "NEAR_LIMIT":
                    status_report["summary"]["users_near_limit"] += 1

            status_report["summary"]["total_open_trades"] += summary.get("current_count", 0)

        except Exception as e:
            status_report["users"][user_id] = {
                "error": str(e),
                "status": "ERROR"
            }

    # Calcular utilización promedio
    if users_with_limits > 0:
        status_report["summary"]["avg_utilization"] = round(total_utilization / users_with_limits, 1)

    return status_report


def format_trade_status_report(status_report: Dict) -> str:
    """
    Formatea el reporte de estado en texto legible
    """
    try:
        lines = []
        lines.append("=" * 60)
        lines.append("TRADE LIMITS STATUS REPORT")
        lines.append("=" * 60)

        import datetime
        timestamp = status_report.get("timestamp", 0)
        if timestamp:
            dt = datetime.datetime.fromtimestamp(timestamp)
            lines.append(f"Generated: {dt.strftime('%Y-%m-%d %H:%M:%S')}")

        summary = status_report.get("summary", {})
        lines.append(f"Strategy: {status_report.get('strategy', 'N/A')}")
        lines.append(f"Total Users: {summary.get('total_users', 0)}")
        lines.append(f"Total Open Trades: {summary.get('total_open_trades', 0)}")
        lines.append(f"Average Utilization: {summary.get('avg_utilization', 0)}%")
        lines.append("")

        # Status por usuario
        lines.append("USER DETAILS:")
        lines.append("-" * 40)

        users_data = status_report.get("users", {})
        for user_id, data in users_data.items():
            if data.get("error"):
                lines.append(f"❌ {user_id}: ERROR - {data['error']}")
                continue

            status = data.get("status", "UNKNOWN")
            current = data.get("current_count", 0)
            max_configured = data.get("max_trades_configured", 999)
            symbols = data.get("current_symbols", [])

            status_emoji = {
                "AT_LIMIT": "🚫",
                "NEAR_LIMIT": "⚠️",
                "NORMAL": "✅",
                "ERROR": "❌"
            }.get(status, "❓")

            if max_configured >= 999:
                lines.append(f"{status_emoji} {user_id}: No limit configured ({current} open)")
            else:
                utilization = data.get("utilization_percentage", 0)
                lines.append(f"{status_emoji} {user_id}: {current}/{max_configured} trades ({utilization}%)")

            if symbols:
                lines.append(f"   📋 Positions: {', '.join(symbols)}")

        lines.append("=" * 60)
        return "\n".join(lines)

    except Exception as e:
        return f"Error formatting report: {e}"


def check_user_can_trade(user_id: str, symbol: str, strategy: str = "archer_dual") -> Dict:
    """
    Función de utilidad para verificar si un usuario específico puede hacer un trade
    """
    try:
        rules = get_rules(user_id, strategy)

        from app.trade_limits import check_trade_limit
        can_trade, reason, info = check_trade_limit(user_id, rules, symbol)

        return {
            "user_id": user_id,
            "symbol": symbol,
            "can_trade": can_trade,
            "reason": reason,
            "details": info,
            "recommendation": "ALLOW" if can_trade else "BLOCK"
        }

    except Exception as e:
        return {
            "user_id": user_id,
            "symbol": symbol,
            "can_trade": False,
            "reason": f"check_error_{str(e)}",
            "recommendation": "BLOCK"
        }


def suggest_trade_management_actions(users: List[str], strategy: str = "archer_dual") -> Dict:
    """
    Sugiere acciones de gestión basadas en el estado actual
    """
    status_report = get_all_users_trade_status(users, strategy)
    suggestions = {
        "priority_actions": [],
        "recommendations": [],
        "warnings": []
    }

    for user_id, data in status_report.get("users", {}).items():
        if data.get("error"):
            suggestions["warnings"].append(f"{user_id}: Check user configuration - {data['error']}")
            continue

        status = data.get("status", "UNKNOWN")
        current_count = data.get("current_count", 0)
        max_configured = data.get("max_trades_configured", 999)

        if status == "AT_LIMIT":
            suggestions["priority_actions"].append(
                f"{user_id}: AT LIMIT ({current_count}/{max_configured}) - Consider closing least profitable position"
            )

        elif status == "NEAR_LIMIT":
            suggestions["recommendations"].append(
                f"{user_id}: NEAR LIMIT ({current_count}/{max_configured}) - Monitor closely"
            )

        elif max_configured >= 999:
            suggestions["recommendations"].append(
                f"{user_id}: No trade limit configured - Consider setting max_trades_open"
            )

    return suggestions


# Función de debugging rápido
def quick_status_check(users: List[str], strategy: str = "archer_dual") -> None:
    """
    Quick status check con print directo (para debugging)
    """
    print("\n🔍 QUICK TRADE LIMITS CHECK")
    print("=" * 50)

    for user_id in users:
        try:
            rules = get_rules(user_id, strategy)
            summary = get_trade_limit_summary(user_id, rules)

            status = summary.get("status", "UNKNOWN")
            current = summary.get("current_count", 0)
            max_config = summary.get("max_trades_configured", 999)

            status_emoji = {"AT_LIMIT": "🚫", "NEAR_LIMIT": "⚠️", "NORMAL": "✅"}.get(status, "❓")

            if max_config >= 999:
                print(f"{status_emoji} {user_id}: No limit ({current} open)")
            else:
                print(f"{status_emoji} {user_id}: {current}/{max_config} trades")

        except Exception as e:
            print(f"❌ {user_id}: Error - {e}")

    print("=" * 50)


if __name__ == "__main__":
    # Test con usuarios ejemplo
    test_users = ["COPY_2", "FUTURES"]
    quick_status_check(test_users)