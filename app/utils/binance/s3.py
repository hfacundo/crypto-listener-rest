# app/utils/binance/s3.py

import boto3
import json
from datetime import datetime, timezone, timedelta
from app.utils.constants import (
    MIN_DEPTH_BASE, DEPTH_PCT
)
from app.utils.config.settings import get_bucket_name

BUCKET_NAME = get_bucket_name()
OBJECT_KEY_FILTERS = 'crypto-listener-cache-bucket/binance/filters.json'


s3 = boto3.client('s3')

def load_filters_from_s3():
    try:
        obj = s3.get_object(Bucket=BUCKET_NAME, Key=OBJECT_KEY_FILTERS)
        return json.loads(obj["Body"].read())
    except Exception as e:
        print(f"⚠️ No se pudo cargar filters.json desde S3: {e}")
        return None

def save_filters_to_s3(data: dict):
    try:
        s3.put_object(
            Bucket=BUCKET_NAME,
            Key=OBJECT_KEY_FILTERS,
            Body=json.dumps(data),
            ContentType='application/json'
        )
        print(f"✅ Filtros guardados en S3")
    except Exception as e:
        print(f"❌ Error al guardar filtros en S3: {e}")


def load_depth_config_from_s3(symbol: str):
    key = f"crypto-listener-cache-bucket/binance/depth_config/{symbol}.json"

    try:
        obj = s3.get_object(Bucket=BUCKET_NAME, Key=key)
        data = json.loads(obj["Body"].read())
        timestamp = datetime.fromisoformat(data["_updated_at"]).astimezone(timezone.utc)


        if datetime.now(timezone.utc) - timestamp < timedelta(minutes=5): # TTL 5 minutes
            return {k: data[k] for k in [MIN_DEPTH_BASE, DEPTH_PCT]}
    except Exception as e:
        print(f"⚠️ No se pudo cargar depth_config desde S3: {e}")
    return None


def save_depth_config_to_s3(symbol, data):
    key = f"crypto-listener-cache-bucket/binance/depth_config/{symbol}.json"
    
    try:
        data["_updated_at"] = datetime.now(timezone.utc).isoformat()
        s3.put_object(
            Bucket=BUCKET_NAME,
            Key=key,
            Body=json.dumps(data),
            ContentType="application/json"
        )
        print(f"✅ depth_config para {key} guardado en S3")
    except Exception as e:
        print(f"❌ Error al guardar depth_config en S3: {e}")