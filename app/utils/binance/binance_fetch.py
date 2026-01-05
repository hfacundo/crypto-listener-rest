"""
Binance API fetch utilities with retry logic and request-level caching.

This module provides resilient wrappers for Binance API calls with:
- Automatic retries with exponential backoff for transient errors
- Request-level caching to avoid duplicate API calls
- Proper error handling and logging
"""
import time
import logging
import requests
from typing import Dict, List, Optional, Any
from contextvars import ContextVar

_early_logger = logging.getLogger(__name__)

try:
    from tenacity import (
        retry,
        stop_after_attempt,
        wait_exponential,
        retry_if_exception_type,
        RetryError
    )
    TENACITY_AVAILABLE = True
except ImportError:
    TENACITY_AVAILABLE = False
    _early_logger.warning("⚠️ tenacity not installed. Retry logic disabled. Install with: pip install tenacity")

try:
    from binance.exceptions import BinanceAPIException
except ImportError:
    # Fallback for when python-binance is not installed
    class BinanceAPIException(Exception):
        def __init__(self, *args, code=None, **kwargs):
            super().__init__(*args, **kwargs)
            self.code = code


# Legacy constants (keep for backward compatibility)
from app.utils.constants import DEFAULT_LAST_N_CANDLES_15M
BINANCE_URL = "https://api.binance.com/api/v3/klines"


# ========== REQUEST-LEVEL CACHE ==========
# Cache is reset on each new request (via middleware clearing)
_position_cache: ContextVar[Dict] = ContextVar('position_cache', default={})
_orders_cache: ContextVar[Dict] = ContextVar('orders_cache', default={})


def clear_request_cache():
    """Clear request-level cache. Should be called by middleware on new request."""
    _position_cache.set({})
    _orders_cache.set({})


# ========== RETRY DECORATORS ==========

if TENACITY_AVAILABLE:
    # Retry configuration for API calls
    retry_config = dict(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception_type((
            BinanceAPIException,
            requests.exceptions.Timeout,
            requests.exceptions.ConnectionError
        )),
        reraise=True
    )

    def api_retry(func):
        """Decorator for Binance API calls with retry logic."""
        return retry(**retry_config)(func)
else:
    # No-op decorator if tenacity not available
    def api_retry(func):
        return func


# ========== POSITION QUERIES WITH RETRY AND CACHE ==========

@api_retry
def get_position_with_retry(symbol: str, client) -> List[Dict]:
    """
    Get position information from Binance with automatic retries.

    Args:
        symbol: Trading pair (e.g., "BTCUSDT")
        client: Binance client instance

    Returns:
        List of position dictionaries from Binance API

    Raises:
        BinanceAPIException: On API errors after retries exhausted
    """
    return client.futures_position_information(symbol=symbol)


def get_position_cached(symbol: str, client, user_id: str) -> List[Dict]:
    """
    Get position with request-level caching to avoid duplicate API calls.

    This function caches position data for the duration of a single HTTP request,
    preventing multiple calls to Binance API for the same position.

    Args:
        symbol: Trading pair (e.g., "BTCUSDT")
        client: Binance client instance
        user_id: User identifier for cache key

    Returns:
        List of position dictionaries from Binance API (cached)

    Example:
        >>> client = get_binance_client_for_user("copy_trading")
        >>> pos1 = get_position_cached("BTCUSDT", client, "copy_trading")
        >>> pos2 = get_position_cached("BTCUSDT", client, "copy_trading")  # Returns cached
        >>> # pos1 and pos2 are the same object, only one API call made
    """
    cache = _position_cache.get()
    key = f"{user_id}:{symbol.upper()}"

    if key not in cache:
        positions = get_position_with_retry(symbol, client)
        cache[key] = positions
        _position_cache.set(cache)

    return cache[key]


# ========== ORDER QUERIES WITH RETRY AND CACHE ==========

@api_retry
def get_open_orders_with_retry(symbol: str, client) -> List[Dict]:
    """
    Get open orders from Binance with automatic retries.

    Args:
        symbol: Trading pair (e.g., "BTCUSDT")
        client: Binance client instance

    Returns:
        List of open order dictionaries from Binance API

    Raises:
        BinanceAPIException: On API errors after retries exhausted
    """
    return client.futures_get_open_orders(symbol=symbol)


def get_open_orders_cached(symbol: str, client, user_id: str) -> List[Dict]:
    """
    Get open orders with request-level caching.

    Args:
        symbol: Trading pair (e.g., "BTCUSDT")
        client: Binance client instance
        user_id: User identifier for cache key

    Returns:
        List of open order dictionaries from Binance API (cached)
    """
    cache = _orders_cache.get()
    key = f"{user_id}:{symbol.upper()}"

    if key not in cache:
        orders = get_open_orders_with_retry(symbol, client)
        cache[key] = orders
        _orders_cache.set(cache)

    return cache[key]


# ========== ALGO ORDERS WITH RETRY ==========

@api_retry
def get_algo_orders_with_retry(symbol: str, client) -> Dict[str, Any]:
    """
    Get Algo Orders (new Binance conditional order endpoint) with retries.

    Args:
        symbol: Trading pair (e.g., "BTCUSDT")
        client: Binance client instance

    Returns:
        Response dict with "openOrders" key containing list of algo orders

    Raises:
        BinanceAPIException: On API errors after retries exhausted
    """
    return client._request_futures_api(
        'get',
        'openAlgoOrders',
        signed=True,
        data={"symbol": symbol.upper()}
    )


# ========== MARK PRICE WITH RETRY ==========

@api_retry
def get_mark_price_with_retry(symbol: str, client) -> Dict[str, Any]:
    """
    Get mark price from Binance with automatic retries.

    Args:
        symbol: Trading pair (e.g., "BTCUSDT")
        client: Binance client instance

    Returns:
        Dict with mark price data from Binance API

    Raises:
        BinanceAPIException: On API errors after retries exhausted
    """
    return client.futures_mark_price(symbol=symbol)


# ========== ORDER CREATION WITH RETRY ==========

@api_retry
def create_order_with_retry(client, **order_params) -> Dict[str, Any]:
    """
    Create order on Binance with automatic retries.

    Args:
        client: Binance client instance
        **order_params: Order parameters (symbol, side, type, quantity, etc.)

    Returns:
        Order creation response from Binance API

    Raises:
        BinanceAPIException: On API errors after retries exhausted

    Example:
        >>> result = create_order_with_retry(
        ...     client,
        ...     symbol="BTCUSDT",
        ...     side="SELL",
        ...     type="MARKET",
        ...     quantity=0.001,
        ...     reduceOnly=True
        ... )
    """
    return client.futures_create_order(**order_params)


# ========== ORDER CANCELLATION WITH RETRY ==========

@api_retry
def cancel_order_with_retry(symbol: str, order_id: int, client) -> Dict[str, Any]:
    """
    Cancel order on Binance with automatic retries.

    Args:
        symbol: Trading pair (e.g., "BTCUSDT")
        order_id: Order ID to cancel
        client: Binance client instance

    Returns:
        Cancellation response from Binance API

    Raises:
        BinanceAPIException: On API errors after retries exhausted
    """
    return client.futures_cancel_order(symbol=symbol, orderId=order_id)


@api_retry
def cancel_algo_order_with_retry(symbol: str, algo_id: int, client) -> Dict[str, Any]:
    """
    Cancel Algo Order on Binance with automatic retries.

    Args:
        symbol: Trading pair (e.g., "BTCUSDT")
        algo_id: Algo Order ID to cancel
        client: Binance client instance

    Returns:
        Cancellation response from Binance API

    Raises:
        BinanceAPIException: On API errors after retries exhausted
    """
    return client._request_futures_api(
        'delete',
        'algoOrder',
        signed=True,
        data={"symbol": symbol.upper(), "algoId": algo_id}
    )


# ========== EXCHANGE INFO WITH RETRY ==========

@api_retry
def get_exchange_info_with_retry(client) -> Dict[str, Any]:
    """
    Get exchange info from Binance with automatic retries.

    Args:
        client: Binance client instance

    Returns:
        Exchange info dict from Binance API

    Raises:
        BinanceAPIException: On API errors after retries exhausted
    """
    return client.futures_exchange_info()


# ========== UTILITY FUNCTIONS ==========

def is_transient_error(exception: Exception) -> bool:
    """
    Check if an exception is a transient error that should be retried.

    Args:
        exception: Exception to check

    Returns:
        True if error is transient, False otherwise
    """
    if isinstance(exception, BinanceAPIException):
        # Rate limit, server errors, timeouts
        transient_codes = [-1003, -1021, -1022, 500, 502, 503, 504]
        return exception.code in transient_codes

    if isinstance(exception, (requests.exceptions.Timeout, requests.exceptions.ConnectionError)):
        return True

    return False


def get_retry_info() -> Dict[str, Any]:
    """
    Get information about retry configuration.

    Returns:
        Dict with retry configuration details
    """
    return {
        "tenacity_available": TENACITY_AVAILABLE,
        "max_attempts": 3 if TENACITY_AVAILABLE else 1,
        "retry_strategy": "exponential_backoff" if TENACITY_AVAILABLE else "none",
        "min_wait_seconds": 1,
        "max_wait_seconds": 10
    }
