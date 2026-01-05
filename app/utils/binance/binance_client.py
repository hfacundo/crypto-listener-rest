# app/utils/binance/binance_client.py

import os
from binance.client import Client
from app.utils.config.settings import (
    get_binance_api_key_for_user,
    get_binance_api_secret_for_user
)

from app.utils.logger_config import get_logger
logger = get_logger()

def get_binance_client_for_user(user_id: str):
    """
    Crea un cliente de Binance para el usuario especificado.
    Soporta modo Testnet para pruebas sin riesgo.

    Para activar Testnet, define en .env:
        USE_BINANCE_TESTNET=true

    Args:
        user_id: ID del usuario

    Returns:
        Client: Cliente de Binance (producción o testnet)
    """
    api_key = get_binance_api_key_for_user(user_id)
    api_secret = get_binance_api_secret_for_user(user_id)

    # Verificar si se debe usar Testnet
    use_testnet = os.environ.get("USE_BINANCE_TESTNET", "false").lower() == "true"

    if use_testnet:
        # Configurar cliente para Testnet de Binance Futures
        client = Client(api_key, api_secret, testnet=True)

        # IMPORTANTE: python-binance no configura automáticamente las URLs de Futures Testnet
        # Debemos hacerlo manualmente
        client.API_URL = 'https://testnet.binancefuture.com'  # REST API
        client.FUTURES_URL = 'https://testnet.binancefuture.com'  # Futures REST API

        logger.warning(f"⚠️ TESTNET MODE ENABLED para {user_id}")
        logger.warning(f"   Usando: {client.FUTURES_URL}")
    else:
        # Cliente de producción normal
        client = Client(api_key, api_secret)

    return client