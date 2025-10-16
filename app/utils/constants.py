# app/utils/constants.py

# trade constants
HOLD = "HOLD"
BUY = "BUY"
SELL = "SELL"
BOTH = "BOTH"
ERROR = "ERROR"
PROBABILITY = "probability"
TRADE = "trade"
RR = "rr"

# rules constants
MIN_RR = "min_rr" # Mínimo risk-reward, ej. 2.0 (equivale a 2:1)
MIN_EV = "min_ev" # Mínima esperanza matemática, ej. 0.2
RISK_PCT = "risk_pct" # Máximo porcentaje de capital a arriesgar por trade, eg. 2
MAX_LEVERAGE = "max_leverage" # Apalancamiento máximo, eg. 20
LAST_N_DAYS = "last_n_days" # Últimos n días de velas de ct_prices en intervalos de 1 hora
USE_THREADS = "use_threads" # True o False
CRYPTOS_PER_RUN = "cryptos_per_run" # Cantidad de cryptos a analizar en cada ejecución, eg. 3
CURRENT_IDX = "current_idx" # Índice de la siguiente cripto a seleccionar
RISK_TOLERANCE = "risk_tolerance"
MAX_SLIPPAGE = "max_slippage"
SLIPPAGE_TIERS = "slippage_tiers"
MAX_SPREAD = "max_spread"
MAX_SLIPPAGE_PCT = "max_slippage_pct"
MAX_SPREAD_PCT = "max_spread_pct"
ATR_SLIP_MULT = "atr_slip_mult"
ATR_SPREAD_MULT = "atr_spread_mult"
DEPTH_PCT = "depth_pct"
MIN_DEPTH_BASE = "min_depth_base"
LIQUIDITY_TIERS = "liquidity_tiers"
ORDER_RETRIES = "order_retries"
SPREAD_TICK_MULTIPLIER = "spread_tick_multiplier"
MAX_WORKERS = "max_workers"
SYMBOLS = "symbols"
DELAY = "delay"
MAX_NEWS_PER_SYMBOL = "max_news_per_symbol"
SENTIMENT_BATCH_SIZE = "sentiment_batch_size"
PRINT_LOGS = "print_logs"

# db constants (tables)
TABLE_RULES = "rules"
TABLE_CRYPTOS = "cryptos"
TABLE_TRADES = "trade_history"

# default values
DEFAULT_CRYPTOS_PER_RUN = 3
DEFAULT_CURRENT_IDX = 0
DEFAULT_MIN_EV = 0.0
DEFAULT_MIN_RR = 1.0
DEFAULT_MAX_WORKERS = 3
DEFAULT_LAST_N_DAYS = 3 # Anteriormente 220
DEFAULT_LAST_N_CANDLES_15M = 80
DEFAULT_RISK_PCT = 2.0
DEFAULT_MAX_LEVERAGE = 125
DEFAULT_AVAILABLE_BALANCE = 0.0
DEFAULT_MAX_SLIPPAGE = 20
DEFAULT_MAX_SPREAD = 0.01
DEFAULT_MAX_SLIPPAGE_PCT = 0.008
DEFAULT_MAX_SPREAD_PCT = 0.3 # 0.3%
DEFAULT_TICK_MULTIPLIER  = 3
DEFAULT_ATR_SLIP_MULT = 0.8
DEFAULT_ATR_SPREAD_MULT = 0.2
DEFAULT_DEPTH_PCT = 0.10 # 10% (rango muy amplio para capturar más liquidez)
DEFAULT_MIN_DEPTH_BASE = 100 # Mínimo muy bajo - capital $500
DEFAULT_ORDER_RETRIES = 3
DEFAULT_DELAY = 2
DEFAULT_MAX_NEWS_PER_SYMBOL = 3
DEFAULT_SENTIMENT_BATCH_SIZE = 10
DEFAULT_PRINT_LOGS = False
DEFAULT_SPREAD_MULTIPLIER = 5

DEFAULT_LIQUIDITY_TIERS = [
    {"vol": 2_000_000, "depth": 100_000, "min_depth_base": 10_000, "depth_pct": 0.02},   # Tier 1: Cryptos muy líquidos
    {"vol": 1_000_000, "depth": 50_000, "min_depth_base": 5_000, "depth_pct": 0.05},     # Tier 2: Cryptos medianos
    {"vol": 300_000, "depth": 20_000, "min_depth_base": 1_000, "depth_pct": 0.08},       # Tier 3: Cryptos pequeños
    {"vol": 0, "depth": 0, "min_depth_base": 100, "depth_pct": 0.10}                     # Tier 4: Fallback ultra-permisivo para capital $500
]

# cryptopanic
DEFAULT_MAX_NEWS = 14

# openai constants
OPENAI_MODEL = "gpt-4o"
OPENAI_TEMPERATURE = 0.0
OPENAI_DEFAULT_PROBABILITY = 50.0

# Volumen mínimo aceptable (proporción de velas con volumen positivo)
MIN_VOLUME_RATIO = 0.5  # Recomendado: 0.5 (50%)

# Número mínimo de velas con volumen positivo
MIN_VOLUME_POINTS = 30  # Recomendado: 30
