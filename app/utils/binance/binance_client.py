# app/utils/binance/binance_client.py

from binance.client import Client
from app.utils.config.settings import (
    get_binance_api_key_for_user, 
    get_binance_api_secret_for_user
)

def get_binance_client_for_user(user_id: str):
    client = Client(get_binance_api_key_for_user(user_id), get_binance_api_secret_for_user(user_id))
    return client