"""
Audit logging for manual trading operations.

This module provides functions to log all manual trading operations to PostgreSQL
for compliance, traceability, and debugging purposes.
"""
import json
from typing import Dict, Any, Optional
from contextvars import ContextVar

# Import request_id_var from main (will be imported dynamically to avoid circular import)
request_id_var: ContextVar[str] = ContextVar('request_id', default=None)


def log_manual_operation(
    user_id: str,
    symbol: str,
    operation: str,
    params: Dict[str, Any],
    result: Optional[Dict[str, Any]],
    success: bool,
    error: Optional[str] = None,
    ip_address: Optional[str] = None
) -> bool:
    """
    Log a manual trading operation to the audit table.

    Args:
        user_id: User identifier
        symbol: Trading pair (e.g., "BTCUSDT")
        operation: Operation type ("close_position", "set_sl", "set_tp", etc.)
        params: Request parameters dict
        result: Operation result dict (if any)
        success: Whether operation succeeded
        error: Error message (if failed)
        ip_address: Client IP address (optional)

    Returns:
        True if logged successfully, False otherwise

    Example:
        >>> log_manual_operation(
        ...     user_id="copy_trading",
        ...     symbol="BTCUSDT",
        ...     operation="set_sl",
        ...     params={"stop_loss": 44000.0, "force_adjust": False},
        ...     result={"order_id": "123456", "stop": 44000.0},
        ...     success=True
        ... )
    """
    try:
        from app.utils.db.query_executor import get_db_connection

        # Get request_id from context
        try:
            # Try to import from main.py
            from main import request_id_var as main_request_id_var
            request_id = main_request_id_var.get()
        except:
            request_id = request_id_var.get()

        # Prepare data
        params_json = json.dumps(params) if params else '{}'
        result_json = json.dumps(result) if result else None

        query = """
        INSERT INTO manual_operations_audit
        (user_id, symbol, operation, params, result, success, error, request_id, ip_address)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        """

        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute(query, (
                user_id,
                symbol.upper(),
                operation,
                params_json,
                result_json,
                success,
                error,
                request_id,
                ip_address
            ))
        conn.commit()
        conn.close()

        return True

    except Exception as e:
        print(f"⚠️ Error logging audit: {e}")
        # Don't raise - audit logging should not break the main operation
        return False


def get_user_audit_history(
    user_id: str,
    limit: int = 100,
    operation: Optional[str] = None,
    symbol: Optional[str] = None
) -> list:
    """
    Get audit history for a user.

    Args:
        user_id: User identifier
        limit: Maximum number of records to return
        operation: Filter by operation type (optional)
        symbol: Filter by symbol (optional)

    Returns:
        List of audit records

    Example:
        >>> history = get_user_audit_history("copy_trading", limit=50, operation="set_sl")
        >>> for record in history:
        ...     print(f"{record['timestamp']}: {record['symbol']} - {record['operation']}")
    """
    try:
        from app.utils.db.query_executor import get_db_connection

        # Build query with filters
        conditions = ["user_id = %s"]
        params = [user_id]

        if operation:
            conditions.append("operation = %s")
            params.append(operation)

        if symbol:
            conditions.append("symbol = %s")
            params.append(symbol.upper())

        where_clause = " AND ".join(conditions)

        query = f"""
        SELECT
            id,
            timestamp,
            user_id,
            symbol,
            operation,
            params,
            result,
            success,
            error,
            request_id,
            ip_address
        FROM manual_operations_audit
        WHERE {where_clause}
        ORDER BY timestamp DESC
        LIMIT %s
        """

        params.append(limit)

        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute(query, tuple(params))
            columns = [desc[0] for desc in cur.description]
            results = [dict(zip(columns, row)) for row in cur.fetchall()]
        conn.close()

        return results

    except Exception as e:
        print(f"⚠️ Error fetching audit history: {e}")
        return []


def get_audit_statistics(user_id: Optional[str] = None) -> Dict[str, Any]:
    """
    Get audit statistics (total operations, success rate, etc.).

    Args:
        user_id: User identifier (optional, if None returns stats for all users)

    Returns:
        Dict with statistics

    Example:
        >>> stats = get_audit_statistics("copy_trading")
        >>> print(f"Success rate: {stats['success_rate']:.1f}%")
    """
    try:
        from app.utils.db.query_executor import get_db_connection

        where_clause = "WHERE user_id = %s" if user_id else ""
        params = (user_id,) if user_id else ()

        query = f"""
        SELECT
            COUNT(*) as total_operations,
            SUM(CASE WHEN success THEN 1 ELSE 0 END) as successful_operations,
            SUM(CASE WHEN NOT success THEN 1 ELSE 0 END) as failed_operations,
            COUNT(DISTINCT user_id) as unique_users,
            COUNT(DISTINCT symbol) as unique_symbols,
            COUNT(DISTINCT operation) as unique_operations
        FROM manual_operations_audit
        {where_clause}
        """

        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute(query, params)
            result = cur.fetchone()
        conn.close()

        if result:
            total = result[0] or 0
            successful = result[1] or 0
            failed = result[2] or 0
            success_rate = (successful / total * 100) if total > 0 else 0

            return {
                "total_operations": total,
                "successful_operations": successful,
                "failed_operations": failed,
                "success_rate": round(success_rate, 2),
                "unique_users": result[3] or 0,
                "unique_symbols": result[4] or 0,
                "unique_operations": result[5] or 0
            }

        return {
            "total_operations": 0,
            "successful_operations": 0,
            "failed_operations": 0,
            "success_rate": 0,
            "unique_users": 0,
            "unique_symbols": 0,
            "unique_operations": 0
        }

    except Exception as e:
        print(f"⚠️ Error fetching audit statistics: {e}")
        return {}


def get_recent_failures(limit: int = 20) -> list:
    """
    Get recent failed operations for debugging.

    Args:
        limit: Maximum number of records to return

    Returns:
        List of recent failed operations

    Example:
        >>> failures = get_recent_failures(10)
        >>> for fail in failures:
        ...     print(f"{fail['timestamp']}: {fail['user_id']}/{fail['symbol']} - {fail['error']}")
    """
    try:
        from app.utils.db.query_executor import get_db_connection

        query = """
        SELECT
            id,
            timestamp,
            user_id,
            symbol,
            operation,
            params,
            error,
            request_id
        FROM manual_operations_audit
        WHERE success = false
        ORDER BY timestamp DESC
        LIMIT %s
        """

        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute(query, (limit,))
            columns = [desc[0] for desc in cur.description]
            results = [dict(zip(columns, row)) for row in cur.fetchall()]
        conn.close()

        return results

    except Exception as e:
        print(f"⚠️ Error fetching recent failures: {e}")
        return []
