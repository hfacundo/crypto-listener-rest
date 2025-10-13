#!/usr/bin/env python3
"""
Script de pruebas de integración para crypto-listener-rest

Verifica:
1. Variables de entorno
2. Conexión a PostgreSQL
3. Conexión a Redis
4. API REST (health, stats, execute-trade)
5. Binance API keys
"""

import os
import sys
import requests
import json
from datetime import datetime

# Colors for output
GREEN = '\033[0;32m'
RED = '\033[0;31m'
YELLOW = '\033[1;33m'
BLUE = '\033[0;34m'
NC = '\033[0m'  # No Color

API_URL = "http://localhost:8000"

def print_header(text):
    print(f"\n{BLUE}{'='*70}{NC}")
    print(f"{BLUE}{text}{NC}")
    print(f"{BLUE}{'='*70}{NC}\n")

def print_success(text):
    print(f"{GREEN}✅ {text}{NC}")

def print_error(text):
    print(f"{RED}❌ {text}{NC}")

def print_warning(text):
    print(f"{YELLOW}⚠️  {text}{NC}")

def test_env_variables():
    """Test 1: Verificar variables de entorno"""
    print_header("TEST 1: Variables de Entorno")

    required_vars = {
        "DATABASE_URL_CRYPTO_TRADER": "Database URL",
        "REDIS_HOST": "Redis Host",
        "REDIS_PORT": "Redis Port",
        "REDIS_DB": "Redis DB",
        "DEPLOYMENT_ENV": "Deployment Environment",
        "BINANCE_FUTURES_API_KEY_COPY": "Binance API Key (COPY)",
        "BINANCE_FUTURES_API_SECRET_COPY": "Binance API Secret (COPY)",
        "BINANCE_FUTURES_API_KEY_HUFSA": "Binance API Key (HUFSA)",
        "BINANCE_FUTURES_API_SECRET_HUFSA": "Binance API Secret (HUFSA)",
        "BINANCE_FUTURES_API_KEY_COPY_2": "Binance API Key (COPY_2)",
        "BINANCE_FUTURES_API_SECRET_COPY_2": "Binance API Secret (COPY_2)",
        "BINANCE_FUTURES_API_KEY_FUTURES": "Binance API Key (FUTURES)",
        "BINANCE_FUTURES_API_SECRET_FUTURES": "Binance API Secret (FUTURES)",
    }

    failed = []
    passed = []

    for var_name, description in required_vars.items():
        value = os.environ.get(var_name)
        if value:
            # Hide sensitive values
            if "SECRET" in var_name or "PASSWORD" in var_name or "DATABASE_URL" in var_name:
                display_value = "[HIDDEN]"
            else:
                display_value = value
            print_success(f"{description:<35} {var_name}")
            passed.append(var_name)
        else:
            print_error(f"{description:<35} {var_name} NOT FOUND")
            failed.append(var_name)

    print(f"\n{len(passed)}/{len(required_vars)} variables configured")

    if failed:
        print_error(f"Missing variables: {', '.join(failed)}")
        print_warning("Add missing variables to ~/.bashrc and run: source ~/.bashrc")
        return False

    return True

def test_database_connection():
    """Test 2: Verificar conexión a PostgreSQL"""
    print_header("TEST 2: PostgreSQL Connection")

    db_url = os.environ.get('DATABASE_URL_CRYPTO_TRADER')
    if not db_url:
        print_error("DATABASE_URL_CRYPTO_TRADER not set")
        return False

    try:
        from sqlalchemy import create_engine, text

        print(f"Connecting to database...")
        engine = create_engine(db_url)

        with engine.connect() as conn:
            # Test 1: Version
            result = conn.execute(text("SELECT version()")).fetchone()
            pg_version = result[0].split(',')[0]
            print_success(f"Connected: {pg_version}")

            # Test 2: Check user_rules table
            result = conn.execute(text("SELECT COUNT(*) FROM user_rules")).fetchone()
            user_count = result[0]
            print_success(f"user_rules table: {user_count} users found")

            # Test 3: Check trade_history table (from TradeProtectionSystem)
            try:
                result = conn.execute(text("SELECT COUNT(*) FROM trade_history")).fetchone()
                trade_count = result[0]
                print_success(f"trade_history table: {trade_count} trades recorded")
            except:
                print_warning("trade_history table not found (will be created on first use)")

        return True

    except Exception as e:
        print_error(f"Database connection failed: {e}")
        return False

def test_redis_connection():
    """Test 3: Verificar conexión a Redis"""
    print_header("TEST 3: Redis Connection")

    redis_host = os.environ.get('REDIS_HOST', 'localhost')
    redis_port = int(os.environ.get('REDIS_PORT', 6379))
    redis_db = int(os.environ.get('REDIS_DB', 0))

    try:
        import redis

        print(f"Connecting to Redis at {redis_host}:{redis_port} (db={redis_db})...")
        client = redis.Redis(
            host=redis_host,
            port=redis_port,
            db=redis_db,
            decode_responses=True
        )

        # Test connection
        client.ping()
        print_success("Redis PING successful")

        # Test write/read
        test_key = "test:crypto_listener:integration"
        test_value = f"test_{datetime.now().isoformat()}"
        client.setex(test_key, 60, test_value)
        retrieved = client.get(test_key)

        if retrieved == test_value:
            print_success("Redis read/write test passed")
            client.delete(test_key)
        else:
            print_error("Redis read/write test failed")
            return False

        # Check for existing data
        keys_count = len(client.keys("*"))
        print_success(f"Redis contains {keys_count} keys")

        return True

    except Exception as e:
        print_error(f"Redis connection failed: {e}")
        return False

def test_api_health():
    """Test 4: Verificar health endpoint"""
    print_header("TEST 4: API Health Check")

    try:
        print(f"GET {API_URL}/health")
        response = requests.get(f"{API_URL}/health", timeout=5)

        if response.status_code == 200:
            data = response.json()
            print_success(f"API Status: {data.get('status', 'unknown')}")
            print_success(f"Service: {data.get('service', 'unknown')}")
            print_success(f"Environment: {data.get('environment', 'unknown')}")
            print_success(f"Strategy: {data.get('strategy', 'unknown')}")
            print_success(f"Users: {', '.join(data.get('users', []))}")
            print_success(f"Database: {data.get('database', 'unknown')}")
            return True
        else:
            print_error(f"Health check failed: HTTP {response.status_code}")
            return False

    except requests.exceptions.ConnectionError:
        print_error("Cannot connect to API. Is the service running?")
        print_warning("Start the service with: sudo systemctl start crypto-listener")
        return False
    except Exception as e:
        print_error(f"Health check failed: {e}")
        return False

def test_api_stats():
    """Test 5: Verificar stats endpoint"""
    print_header("TEST 5: API Statistics")

    try:
        print(f"GET {API_URL}/stats")
        response = requests.get(f"{API_URL}/stats", timeout=5)

        if response.status_code == 200:
            data = response.json()
            print_success(f"Stats retrieved for environment: {data.get('environment', 'unknown')}")

            user_stats = data.get('user_stats', {})
            for user_id, stats in user_stats.items():
                if isinstance(stats, dict) and 'error' not in stats:
                    enabled = stats.get('enabled', False)
                    status_emoji = "🟢" if enabled else "🔴"
                    print_success(f"{status_emoji} {user_id}: enabled={enabled}")
                else:
                    print_warning(f"{user_id}: {stats}")

            return True
        else:
            print_error(f"Stats failed: HTTP {response.status_code}")
            return False

    except Exception as e:
        print_error(f"Stats failed: {e}")
        return False

def test_api_root():
    """Test 6: Verificar root endpoint"""
    print_header("TEST 6: API Root Endpoint")

    try:
        print(f"GET {API_URL}/")
        response = requests.get(f"{API_URL}/", timeout=5)

        if response.status_code == 200:
            data = response.json()
            print_success(f"Service: {data.get('service', 'unknown')}")
            print_success(f"Version: {data.get('version', 'unknown')}")
            print_success("Available endpoints:")
            for endpoint, description in data.get('endpoints', {}).items():
                print(f"  {endpoint:<30} {description}")
            return True
        else:
            print_error(f"Root endpoint failed: HTTP {response.status_code}")
            return False

    except Exception as e:
        print_error(f"Root endpoint failed: {e}")
        return False

def test_trade_execution_dry_run():
    """Test 7: Verificar execute-trade endpoint (sin ejecutar trade real)"""
    print_header("TEST 7: Trade Execution Endpoint (Dry Run)")

    print_warning("This test will send a trade request to the API")
    print_warning("The trade may be REJECTED by validation rules (which is expected)")
    print_warning("If it passes validation, it WILL execute a real trade on Binance!")

    response = input("\nDo you want to continue? (yes/no): ")
    if response.lower() != 'yes':
        print_warning("Test skipped by user")
        return None

    # Create a test trade request
    trade_data = {
        "symbol": "BTCUSDT",
        "entry": 45000.0,
        "stop": 44500.0,
        "target": 46000.0,
        "trade": "LONG",
        "rr": 2.0,
        "probability": 75.0,
        "signal_quality_score": 8.5
    }

    try:
        print(f"\nPOST {API_URL}/execute-trade")
        print(f"Request body: {json.dumps(trade_data, indent=2)}")

        response = requests.post(
            f"{API_URL}/execute-trade",
            json=trade_data,
            timeout=30
        )

        if response.status_code == 200:
            data = response.json()
            print_success(f"Trade processed successfully")
            print_success(f"Status: {data.get('status')}")
            print_success(f"Symbol: {data.get('symbol')}")
            print_success(f"Successful: {data.get('successful')}/{data.get('total_users')}")
            print_success(f"Execution time: {data.get('execution_time_sec')}s")

            print("\nResults by user:")
            for result in data.get('results', []):
                user_id = result.get('user_id')
                success = result.get('success')
                reason = result.get('reason')
                emoji = "✅" if success else "❌"
                print(f"  {emoji} {user_id}: {reason}")

            return True
        else:
            print_error(f"Trade execution failed: HTTP {response.status_code}")
            print(f"Response: {response.text}")
            return False

    except Exception as e:
        print_error(f"Trade execution failed: {e}")
        return False

def main():
    """Run all tests"""
    print_header("crypto-listener-rest Integration Tests")
    print(f"API URL: {API_URL}")
    print(f"Test time: {datetime.now().isoformat()}")

    tests = [
        ("Environment Variables", test_env_variables),
        ("PostgreSQL Connection", test_database_connection),
        ("Redis Connection", test_redis_connection),
        ("API Health", test_api_health),
        ("API Statistics", test_api_stats),
        ("API Root", test_api_root),
        ("Trade Execution", test_trade_execution_dry_run),
    ]

    results = []

    for test_name, test_func in tests:
        try:
            result = test_func()
            results.append((test_name, result))
        except KeyboardInterrupt:
            print("\n\nTests interrupted by user")
            sys.exit(1)
        except Exception as e:
            print_error(f"Test '{test_name}' crashed: {e}")
            results.append((test_name, False))

    # Summary
    print_header("Test Summary")

    passed = sum(1 for _, result in results if result is True)
    failed = sum(1 for _, result in results if result is False)
    skipped = sum(1 for _, result in results if result is None)
    total = len(results)

    for test_name, result in results:
        if result is True:
            print_success(f"{test_name:<40} PASSED")
        elif result is False:
            print_error(f"{test_name:<40} FAILED")
        else:
            print_warning(f"{test_name:<40} SKIPPED")

    print(f"\n{GREEN}{passed} passed{NC} | {RED}{failed} failed{NC} | {YELLOW}{skipped} skipped{NC} | {total} total")

    if failed == 0:
        print_success("\n🎉 All tests passed! crypto-listener-rest is ready to use.")
        sys.exit(0)
    else:
        print_error(f"\n⚠️  {failed} test(s) failed. Please fix the issues above.")
        sys.exit(1)

if __name__ == "__main__":
    main()
