# app/config/settings.py

import os

COPY_TRADING = "copy_trading"
FUTURES = "futures"
HUFSA = "hufsa"
COPY_2 = "copy_2"

def get_binance_api_key_for_user(user_id: str) -> str:
    if user_id == COPY_TRADING:
        key = os.environ.get("BINANCE_FUTURES_API_KEY_COPY")
    elif user_id == FUTURES:
        key = os.environ.get("BINANCE_FUTURES_API_KEY_FUTURES")
    elif user_id == HUFSA:
        key = os.environ.get("BINANCE_FUTURES_API_KEY_HUFSA")
    elif user_id == COPY_2:
        key = os.environ.get("BINANCE_FUTURES_API_KEY_COPY_2")
    else:
        raise RuntimeError(f"❌ La variable BINANCE_API_KEY no está definida para {user_id}")
    
    if not key:
        raise RuntimeError(f"❌ La variable BINANCE_API_KEY no está definida para {user_id}")
    return key

def get_binance_api_secret_for_user(user_id: str) -> str:
    if user_id == COPY_TRADING:
        secret = os.environ.get("BINANCE_FUTURES_API_SECRET_COPY")
    elif user_id == FUTURES:
        secret = os.environ.get("BINANCE_FUTURES_API_SECRET_FUTURES")
    elif user_id == HUFSA:
        secret = os.environ.get("BINANCE_FUTURES_API_SECRET_HUFSA")
    elif user_id == COPY_2:
        secret = os.environ.get("BINANCE_FUTURES_API_SECRET_COPY_2")
    else:
        raise RuntimeError(f"❌ La variable BINANCE_FUTURES_API_SECRET no está definida para {user_id}")
    
    if not secret:
        raise RuntimeError("❌ La variable BINANCE_API_SECRET no está definida.")
    return secret

def get_database_url() -> str:
    # Use same variable name as crypto-analyzer-redis for consistency
    url = os.environ.get("DATABASE_URL_CRYPTO_TRADER")
    if not url:
        raise RuntimeError("❌ La variable DATABASE_URL_CRYPTO_TRADER no está definida.")
    return url

# S3 and SNS functions removed - not needed in REST API version