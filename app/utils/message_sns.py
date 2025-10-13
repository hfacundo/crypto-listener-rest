

import sys
import os
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import boto3
import json
from app.utils.config.settings import get_sns_topic_arn

def send_message(message: dict):
    sns_arn = get_sns_topic_arn()
    print(f"🪪 TopicArn usado: {sns_arn}")

    client = boto3.client("sns", region_name="eu-central-1")

    response = client.publish(
        TopicArn=sns_arn,
        Message=json.dumps(message),
        Subject="Trade Signal from Crypto Analyzer"
    )

    print(f"✅ Mensaje SNS enviado. MessageId: {response['MessageId']}")

