# crypto-listener-rest

REST API service for immediate cryptocurrency trade execution on EC2. This replaces the Lambda-based crypto-listener to avoid NAT Gateway costs and provide synchronous trade processing.

## Architecture

```
crypto-analyzer (EC2) → HTTP POST → crypto-listener-rest (EC2)
                                         ↓
                                    Process immediately
                                         ↓
                                    Binance API + PostgreSQL + Redis
```

## Key Features

- **Immediate Processing**: Trades are processed synchronously, no queuing
- **Time-Sensitive**: If a trade fails, it fails immediately (no retry after restart)
- **Multi-User**: Processes trades for all configured users in parallel
- **Zero Additional Cost**: Runs on existing EC2, eliminates Lambda + NAT Gateway costs ($32-50/month → $0)
- **Full Control**: Easy debugging with `journalctl` and localhost-only access

## Installation

### 1. Deploy to EC2

```bash
cd /mnt/d/Development/python/crypto-listener-rest
chmod +x deploy.sh
./deploy.sh
```

### 2. Configure Database

Edit the systemd service file:

```bash
sudo nano /etc/systemd/system/crypto-listener.service
```

Update the `DATABASE_URL` line with your actual PostgreSQL password:

```ini
Environment="DATABASE_URL=postgresql://app_user:YOUR_ACTUAL_PASSWORD@localhost:5432/crypto_trader"
```

### 3. Start the Service

```bash
# Start the service
sudo systemctl start crypto-listener

# Enable auto-start on boot
sudo systemctl enable crypto-listener

# Check status
sudo systemctl status crypto-listener

# View logs
sudo journalctl -u crypto-listener -f
```

## API Endpoints

### Health Check

```bash
curl http://localhost:8000/health
```

Response:
```json
{
  "status": "ok",
  "service": "crypto-listener-rest",
  "environment": "main",
  "users": ["User_1", "User_3", "User_2", "User_4"],
  "strategy": "archer_dual",
  "database": "connected"
}
```

### Execute Trade

```bash
curl -X POST http://localhost:8000/execute-trade \
  -H "Content-Type: application/json" \
  -d '{
    "symbol": "BTCUSDT",
    "entry": 45000.0,
    "stop": 44500.0,
    "target": 46000.0,
    "trade": "LONG",
    "rr": 2.0,
    "probability": 75.0,
    "signal_quality_score": 8.5
  }'
```

Response:
```json
{
  "status": "completed",
  "symbol": "BTCUSDT",
  "successful": 4,
  "failed": 0,
  "total_users": 4,
  "execution_time_sec": 0.523,
  "results": [
    {
      "user_id": "User_1",
      "success": true,
      "reason": "trade_created",
      "trade_id": 12345
    }
  ]
}
```

### Guardian Actions

Close a position:

```bash
curl -X POST http://localhost:8000/guardian \
  -H "Content-Type: application/json" \
  -d '{
    "symbol": "BTCUSDT",
    "action": "close",
    "user_id": "User_1"
  }'
```

Adjust stop loss:

```bash
curl -X POST http://localhost:8000/guardian \
  -H "Content-Type: application/json" \
  -d '{
    "symbol": "BTCUSDT",
    "action": "adjust",
    "stop": 45500.0
  }'
```

Half close:

```bash
curl -X POST http://localhost:8000/guardian \
  -H "Content-Type: application/json" \
  -d '{
    "symbol": "BTCUSDT",
    "action": "half_close",
    "user_id": "User_1"
  }'
```

### Get Statistics

```bash
curl http://localhost:8000/stats
```

### Interactive Docs

Visit `http://localhost:8000/docs` for interactive Swagger UI documentation (only accessible from localhost).

## Integration with crypto-analyzer

Update your crypto-analyzer code to call the REST API instead of SNS:

```python
import requests

def on_signal_detected(signal_data):
    """Called when a trading signal is detected"""

    # 1. Save to DB first (for auditing)
    trade_id = save_trade_to_db(signal_data)

    # 2. Send to crypto-listener-rest API (fire-and-forget)
    try:
        response = requests.post(
            "http://localhost:8000/execute-trade",
            json={
                "symbol": signal_data["symbol"],
                "entry": signal_data["entry"],
                "stop": signal_data["stop"],
                "target": signal_data["target"],
                "trade": signal_data["direction"],
                "rr": signal_data["rr"],
                "probability": signal_data["probability"],
                "signal_quality_score": signal_data.get("sqs", 0)
            },
            timeout=2  # Fast timeout, don't wait
        )

        if response.status_code == 200:
            result = response.json()
            logger.info(f"Trade executed: {result['successful']}/{result['total_users']} users")
        else:
            logger.error(f"Trade failed: {response.text}")

    except requests.Timeout:
        logger.warning("API timeout, trade saved in DB")
    except Exception as e:
        logger.error(f"API error: {e}")
```

## Service Management

```bash
# View logs
sudo journalctl -u crypto-listener -f

# Restart service
sudo systemctl restart crypto-listener

# Stop service
sudo systemctl stop crypto-listener

# Check status
sudo systemctl status crypto-listener

# View recent logs
sudo journalctl -u crypto-listener -n 100
```

## Testing

### Test locally during development

```bash
cd /mnt/d/Development/python/crypto-listener-rest

# Create virtual environment
python3 -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Set environment variables
export DATABASE_URL="postgresql://app_user:password@localhost:5432/crypto_trader"
export DEPLOYMENT_ENV="main"

# Run locally
python main.py
```

### Test the API

```bash
# Health check
curl http://localhost:8000/health

# Test trade (will actually execute!)
curl -X POST http://localhost:8000/execute-trade \
  -H "Content-Type: application/json" \
  -d @test-trade.json
```

Create `test-trade.json`:

```json
{
  "symbol": "BTCUSDT",
  "entry": 45000.0,
  "stop": 44500.0,
  "target": 46000.0,
  "trade": "LONG",
  "rr": 2.0,
  "probability": 75.0,
  "signal_quality_score": 8.5
}
```

## Troubleshooting

### Service won't start

```bash
# Check service status
sudo systemctl status crypto-listener

# View detailed logs
sudo journalctl -u crypto-listener -n 50

# Check if port 8000 is available
sudo netstat -tulpn | grep 8000
```

### Database connection errors

```bash
# Test PostgreSQL connection
psql -h localhost -U app_user -d crypto_trader

# Check DATABASE_URL in service file
sudo nano /etc/systemd/system/crypto-listener.service

# After editing, reload and restart
sudo systemctl daemon-reload
sudo systemctl restart crypto-listener
```

### Permission errors

```bash
# Ensure correct ownership
sudo chown -R ubuntu:ubuntu /home/ubuntu/crypto-listener-rest

# Check service user
grep "^User=" /etc/systemd/system/crypto-listener.service
```

## Migration from Lambda

Once crypto-listener-rest is running and tested:

1. ✅ Deploy crypto-listener-rest to EC2
2. ✅ Update crypto-analyzer to call REST API
3. ✅ Test with small trades
4. ⚠️ Keep Lambda running for a few days as backup
5. 🗑️ Delete Lambda function
6. 🗑️ Remove NAT Gateway (saves ~$32-50/month)
7. 🗑️ Delete SNS subscriptions (optional, keep SNS topic if needed for other purposes)

## Differences from Lambda Version

| Feature | Lambda + SNS | REST on EC2 |
|---------|-------------|-------------|
| Processing | Asynchronous | Synchronous |
| Retry | Automatic (SNS) | None (intentional) |
| Response | Fire-and-forget | Immediate |
| Cost | $32-50/month (NAT) | $0 (uses existing EC2) |
| Debugging | CloudWatch | journalctl |
| Latency | 100-500ms | <10ms |
| Cold starts | Yes | No |

## Security Notes

⚠️ **Important**: The service runs on `127.0.0.1:8000` (localhost only) and is NOT exposed to the internet.

- Only accessible from the same EC2 instance
- API keys are stored in systemd environment variables
- Consider moving API keys to AWS Secrets Manager or HashiCorp Vault for production

## Performance

- **Latency**: <10ms (localhost)
- **Throughput**: Processes 4 users in parallel (~0.5s total)
- **No cold starts**: Always ready
- **Resource usage**: ~50MB RAM, minimal CPU

## License

Same as parent crypto-analyzer project.
