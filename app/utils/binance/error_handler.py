"""
Enhanced Binance API error handling.

This module provides specific error handling for common Binance API error codes,
translating technical error codes into user-friendly messages.
"""

from typing import Dict, Optional, Tuple
from binance.exceptions import BinanceAPIException
from fastapi import HTTPException


# Mapping of common Binance error codes to user-friendly messages
BINANCE_ERROR_CODES = {
    -1000: ("Unknown error", 500),
    -1001: ("Internal server error", 500),
    -1003: ("Rate limit exceeded. Please wait before retrying", 429),
    -1010: ("ERROR_MSG_RECEIVED", 400),
    -1013: ("Invalid quantity", 400),
    -1015: ("Too many new orders", 429),
    -1021: ("Timestamp for this request is outside of the recvWindow", 500),
    -1022: ("Invalid signature", 500),
    -2010: ("New order rejected (insufficient balance or invalid parameters)", 400),
    -2011: ("Cancel order rejected", 400),
    -2013: ("Order does not exist", 404),
    -2014: ("API-key format invalid", 500),
    -2015: ("Invalid API-key, IP, or permissions for action", 403),
    -2019: ("Insufficient margin. Please add more funds to your account", 400),
    -4001: ("Price * quantity (notional value) is below the minimum required", 400),
    -4003: ("Quantity is below the minimum required for this symbol", 400),
    -4013: ("Price * quantity exceeds your available balance", 400),
    -4044: ("Invalid close position order type (must use MARKET order to close)", 400),
    -4045: ("Reduce-only order would increase position size", 400),
    -4046: ("Order would immediately trigger (stop price too close to mark price)", 400),
    -4061: ("Order's notional value is below the minimum required for this symbol", 400),
    -4114: ("TimeInForce parameter sent when not required", 400),
    -4131: ("Price validation failed (not multiple of tickSize or out of bounds)", 400),
    -4164: ("Order notional value is too small. Minimum required", 400),
    -5021: ("Due to the order could not be executed as maker, the Post Only order will be rejected", 400),
}


def get_binance_error_message(error_code: int) -> Tuple[str, int]:
    """
    Get user-friendly error message and HTTP status code for a Binance error code.

    Args:
        error_code: Binance API error code (negative integer)

    Returns:
        Tuple of (error_message, http_status_code)

    Example:
        >>> msg, status = get_binance_error_message(-2019)
        >>> print(msg, status)
        ('Insufficient margin. Please add more funds to your account', 400)
    """
    if error_code in BINANCE_ERROR_CODES:
        return BINANCE_ERROR_CODES[error_code]

    # Default for unknown error codes
    return (f"Binance API error {error_code}", 500)


def handle_binance_exception(
    e: BinanceAPIException,
    operation: str = "operation",
    user_id: Optional[str] = None,
    symbol: Optional[str] = None
) -> HTTPException:
    """
    Convert a BinanceAPIException into a user-friendly HTTPException.

    This function translates Binance error codes into clear, actionable error messages
    for API users.

    Args:
        e: The BinanceAPIException to handle
        operation: Description of the operation that failed (e.g., "close position", "set stop loss")
        user_id: User ID (optional, for logging)
        symbol: Trading symbol (optional, for logging)

    Returns:
        HTTPException with appropriate status code and error message

    Example:
        >>> try:
        ...     client.futures_create_order(...)
        ... except BinanceAPIException as e:
        ...     raise handle_binance_exception(e, "create order", "copy_trading", "BTCUSDT")
    """
    error_code = e.code
    binance_message = e.message

    # Get user-friendly message and status code
    friendly_message, status_code = get_binance_error_message(error_code)

    # Build detailed error message
    context = f"{user_id}/{symbol}" if user_id and symbol else (user_id or symbol or "N/A")

    error_detail = {
        "error": friendly_message,
        "operation": operation,
        "context": context,
        "binance_code": error_code,
        "binance_message": binance_message
    }

    # Customize message based on error code
    if error_code == -2019:
        # Insufficient margin - add helpful guidance
        error_detail["suggestion"] = "Deposit more USDT to your Futures wallet or close other positions to free up margin"

    elif error_code == -4001 or error_code == -4061 or error_code == -4164:
        # Notional value too small
        error_detail["suggestion"] = "Increase your position size or adjust the price to meet the minimum notional requirement (usually $5-10 USDT)"

    elif error_code == -4131:
        # Price validation failed
        error_detail["suggestion"] = "Ensure price is a multiple of the symbol's tickSize and within allowed price range"

    elif error_code == -4046:
        # Stop price too close to mark
        error_detail["suggestion"] = "Move your stop loss further from the current market price to avoid immediate trigger"

    elif error_code == -1003:
        # Rate limit exceeded
        error_detail["suggestion"] = "Wait 1 minute before retrying. Reduce request frequency to avoid rate limits"

    elif error_code == -1021:
        # Timestamp issue
        error_detail["suggestion"] = "Server time sync issue. Try again in a few seconds"

    return HTTPException(status_code=status_code, detail=error_detail)


def is_retryable_binance_error(error_code: int) -> bool:
    """
    Determine if a Binance error code represents a retryable error.

    Retryable errors are typically transient issues like rate limits,
    server errors, or timestamp issues that may succeed on retry.

    Args:
        error_code: Binance API error code

    Returns:
        True if the error is retryable, False otherwise

    Example:
        >>> is_retryable_binance_error(-1003)  # Rate limit
        True
        >>> is_retryable_binance_error(-2019)  # Insufficient margin
        False
    """
    # Retryable errors
    retryable_codes = {
        -1000,  # Unknown error (might be transient)
        -1001,  # Internal server error
        -1003,  # Rate limit (should retry with backoff)
        -1021,  # Timestamp issue
    }

    return error_code in retryable_codes


def format_binance_error_for_logging(
    e: BinanceAPIException,
    operation: str,
    user_id: Optional[str] = None,
    symbol: Optional[str] = None
) -> str:
    """
    Format a Binance exception for structured logging.

    Args:
        e: The BinanceAPIException
        operation: Operation description
        user_id: User ID (optional)
        symbol: Symbol (optional)

    Returns:
        Formatted error string for logging

    Example:
        >>> log_msg = format_binance_error_for_logging(exc, "set_sl", "copy_trading", "BTCUSDT")
        >>> logger.error(log_msg)
    """
    context = f"{user_id}/{symbol}" if user_id and symbol else (user_id or symbol or "N/A")
    friendly_msg, _ = get_binance_error_message(e.code)

    return (
        f"Binance error during {operation} ({context}): "
        f"[{e.code}] {friendly_msg} | "
        f"Original message: {e.message}"
    )
