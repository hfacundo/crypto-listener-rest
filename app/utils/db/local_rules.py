# app/utils/db/local_rules.py
# Configuración local de rules para evitar consultas a la base de datos
# y reducir compute hours en Neon

from app.utils.config.settings import COPY_TRADING, HUFSA, COPY_2, FUTURES

# Diccionario local con configuración de rules por usuario y estrategia
LOCAL_RULES = {
    # ════════════════════════════════════════════════════════════════════
    # COPY_TRADING - Usuario CONSERVADOR
    # Filosofía: Proteger capital a toda costa, ganancias estables
    # ════════════════════════════════════════════════════════════════════
    COPY_TRADING: {
        "archer_dual": {
            # ═══ Core Settings (Configuración básica) ═══
            "enabled": True,              # Activar/desactivar usuario completamente
            "min_rr": 1.1,                # Risk/Reward mínimo aceptado (1.1 = ganar 1.1 por cada 1 arriesgado)
            "min_ev": 0.06,               # Expected Value mínimo (0.06 = 6% de ganancia esperada)
            "risk_pct": 1.0,              # % del capital a arriesgar por trade (1.0 = 1%)
            "max_leverage": 30,           # Apalancamiento máximo permitido (30x)

            # ═══ Guardian Settings (Sistema de protección en tiempo real) ═══
            "use_guardian": True,         # Activar sistema guardian que cierra trades con problemas
            "use_guardian_half": False,   # Si True, usa cierre parcial (50%) + move BE, si False cierra 100%

            # ═══ Trade Limits (Límites de trades simultáneos) ═══
            "max_trades_open": 3,         # Máximo de trades abiertos simultáneamente (Conservador: solo 3)
            "count_method": "positions",  # Método de conteo: "positions" = posiciones abiertas

            # ═══ SQS Configuration (Signal Quality Score - Calidad de señal) ═══
            # JSON string que define cuánto capital asignar según probabilidad + SQS
            # CONSERVADOR: Asigna siempre 1% sin importar la calidad (protección máxima)
            "sqs_config": '{"enabled":true,"description":"Standardized 1% allocation - uniform capital across all quality tiers","probability_tiers":[{"min_probability":62,"min_sqs":75,"capital_multiplier":1,"description":"PREMIUM: 1% capital (62%+ prob + 75+ SQS)"},{"min_probability":60,"min_sqs":65,"capital_multiplier":1,"description":"HIGH QUALITY: 1% capital (60%+ prob + 65+ SQS)"},{"min_probability":58,"min_sqs":60,"capital_multiplier":1,"description":"GOOD: 1% capital (58%+ prob + 60+ SQS)"},{"min_probability":58,"min_sqs":55,"capital_multiplier":1,"description":"STANDARD: 1% capital (58%+ prob + 55+ SQS)"},{"min_probability":58,"min_sqs":50,"capital_multiplier":1,"description":"MINIMUM: 1% capital (58%+ prob + 50+ SQS)"}],"absolute_minimums":{"min_probability":58,"min_sqs":50,"reject_rule":"probability < 58 OR sqs < 50"},"risk_management":{"max_capital_multiplier":1,"max_daily_trades":10,"emergency_stop_sqs":30}}',

            # ═══ 🔥 ANTI-REPETITION (Evitar repetir trades que tocaron stop loss) ═══
            "anti_repetition": {
                "enabled": True,                      # Activar protección anti-repetición
                "cooldown_after_stop_hours": 24,      # Esperar 24 HORAS después de un stop loss antes de re-tradear el mismo símbolo
                "lookback_hours": 48,                 # Mirar atrás 48h en historial para buscar stops previos
                "min_price_change_pct": 3.0,          # Si precio cambió >3% desde último stop, permitir trade (override automático)
                "mode": "per_symbol"                  # Aplicar cooldown por símbolo individual (no global)
            },
            # Ejemplo: Si BTC tocó stop a las 10:00, no tradear BTC hasta las 10:00 del día siguiente
            # EXCEPCIÓN: Si BTC estaba en $50k y ahora está en $51.5k+ (>3%), permitir trade

            # ═══ 🔥 CIRCUIT BREAKER (Pausa automática por drawdown o pérdidas) ═══
            "circuit_breaker": {
                "enabled": True,                      # Activar circuit breaker
                "max_drawdown_pct": -15.0,            # Si cuenta cae -15% desde su máximo, PAUSAR trading
                "max_consecutive_losses": 3,          # Si 3 trades pierden seguidos, PAUSAR trading
                "pause_duration_hours": 48,           # Pausar por 48 HORAS (conservador: pausa larga)
                "recovery_target_pct": 60,            # Debe recuperar 60% del drawdown para reactivar
                                                      # Ej: Si cayó -15%, debe subir +9% para reactivar
                "auto_reset": True                    # Resetear contador de pérdidas al ganar un trade
            },
            # Ejemplo: Cuenta en $10,000, máximo fue $11,000, ahora en $9,350 (-15%) → PAUSA 48h

            # ═══ 🔥 DAILY LOSS LIMITS (Límite de pérdida diaria) ═══
            "daily_loss_limits": {
                "enabled": True,                      # Activar límite diario
                "max_daily_loss_pct": 3.0,            # Si pierdes -3% HOY, PAUSAR trading
                "pause_duration_hours": 24,           # Pausar por 24 horas
                "reset_time_utc": "00:00"             # Resetear contador a medianoche UTC
            },
            # Ejemplo: Cuenta en $10,000, perdiste $300 hoy (-3%) → PAUSA hasta mañana

            # ═══ 🔥 SCHEDULE (Horarios de operación) ═══
            "schedule": {
                "enabled": False,                     # DESACTIVADO = Opera 24/7 sin restricciones
                "timezone": "UTC",                    # Zona horaria (UTC)
                "allowed_periods": []                 # Horarios permitidos (vacío = 24/7)
            },
            # Para activar horarios: enabled: True, allowed_periods: [{"days": ["monday", "tuesday"], "start_time": "09:00", "end_time": "21:00"}]

            # ═══ 🔥 SYMBOL BLACKLIST (Bloquear símbolos con mal rendimiento) ═══
            "symbol_blacklist": {
                "enabled": True,                      # Activar blacklist automática
                "min_trades_for_evaluation": 8,       # Mínimo 8 trades en un símbolo antes de evaluar
                "min_win_rate_pct": 45.0,             # Si win rate < 45%, BLACKLISTEAR símbolo
                "max_cumulative_loss_pct": -12.0,     # Si pérdida acumulada > -12%, BLACKLISTEAR símbolo
                "lookback_days": 60,                  # Evaluar últimos 60 días
                "auto_blacklist": True                # Blacklistear automáticamente (no manual)
            },
            # Ejemplo: ETH tuvo 10 trades, solo 4 ganaron (40% win rate) → BLOQUEADO
            # O: ETH perdió -$1,200 acumulado (-12%) → BLOQUEADO

            # ═══ 🔥 PORTFOLIO PROTECTION (Protección de cartera) ═══
            "portfolio_protection": {
                "enabled": True,                      # Activar protección de portfolio
                "max_correlation_score": 0.75,        # Máxima correlación permitida entre trades (0.75 = 75%)
                "max_sector_exposure_pct": 50,        # Máximo 50% del portfolio en un mismo sector
                "max_portfolio_drawdown_pct": 12,     # Si portfolio cae -12%, pausar
                "pause_on_high_risk": True            # Pausar si riesgo de portfolio es alto
            },
            # Ejemplo: No abrir BTC y ETH al mismo tiempo si correlación > 75%
            # O: Ya tienes 50% en "Layer 1" coins, no abrir más Layer 1

            # ═══ 🔥 MAX TRADES PROTECTION (Pausa si muchos trades perdiendo) ═══
            "max_trades_protection": {
                "enabled": True,                      # Activar protección por trades perdiendo
                "trigger_losing_trades": 2,           # Si 2 trades están perdiendo simultáneamente, PAUSAR nuevos
                "cooldown_hours": 6,                  # Pausar por 6 horas
                "losing_threshold_R": -0.3            # Trade se considera "perdiendo" si está < -0.3R (-30% del riesgo)
            },
            # Ejemplo: Tienes BTC en -0.4R y ETH en -0.5R (2 perdiendo) → NO abrir más trades por 6h
            # R = Risk. Si arriesgaste $100, -0.3R = perdiendo $30

            # ═══ 💰 PROFIT TAKING (DESHABILITADO - se usa trailing stop) ═══
            "profit_taking": {
                "enabled": False,  # Solo trailing stop, NO cierres parciales
                "levels": []
            }
        },

        # ═══════════════════════════════════════════════════════════════
        # archer_model - ML-based strategy (simplified validation)
        # ═══════════════════════════════════════════════════════════════
        "archer_model": {
            "enabled": True,
            "min_rr": 1.1,
            "min_probability": 70,
            "min_ev": 0.06,               # Expected Value mínimo (0.06 = 6% de ganancia esperada)
            "risk_pct": 1.0,
            "max_leverage": 30,
            "max_trades_open": 3,
            "count_method": "positions",
            "use_guardian": True,
            "use_guardian_half": False,

            "anti_repetition": {
                "enabled": True,
                "cooldown_after_stop_hours": 24,
                "lookback_hours": 48,
                "min_price_change_pct": 3.0,
                "mode": "per_symbol"
            },
            "circuit_breaker": {
                "enabled": True,
                "max_drawdown_pct": -15.0,
                "max_consecutive_losses": 3,
                "pause_duration_hours": 48,
                "recovery_target_pct": 60,
                "auto_reset": True
            },
            "daily_loss_limits": {
                "enabled": True,
                "max_daily_loss_pct": 3.0,
                "pause_duration_hours": 24,
                "reset_time_utc": "00:00"
            },
            "schedule": {
                "enabled": False,
                "timezone": "UTC",
                "allowed_periods": []
            },
            "symbol_blacklist": {
                "enabled": True,
                "min_trades_for_evaluation": 8,
                "min_win_rate_pct": 45.0,
                "max_cumulative_loss_pct": -12.0,
                "lookback_days": 60,
                "auto_blacklist": True
            },
            "portfolio_protection": {
                "enabled": True,
                "max_correlation_score": 0.75,
                "max_sector_exposure_pct": 50,
                "max_portfolio_drawdown_pct": 12,
                "pause_on_high_risk": True
            },
            "max_trades_protection": {
                "enabled": True,
                "trigger_losing_trades": 2,
                "cooldown_hours": 6,
                "losing_threshold_R": -0.3
            },
            "profit_taking": {
                "enabled": False,
                "levels": []
            }
        }
    },

    # ════════════════════════════════════════════════════════════════════
    # COPY_2 - Usuario CONSERVADOR (idéntico a COPY_TRADING)
    # ════════════════════════════════════════════════════════════════════
    COPY_2: {
        "archer_dual": {
            # ═══ Core Settings ═══
            "enabled": True,
            "min_rr": 1.1,
            "min_ev": 0.06,               # Expected Value mínimo (0.06 = 6% de ganancia esperada)
            "risk_pct": 1.0,
            "max_leverage": 30,

            # ═══ Guardian Settings ═══
            "use_guardian": True,         # ✅ Trailing stop activo
            "use_guardian_half": False,   # ❌ NO cierres parciales

            # ═══ Trade Limits ═══
            "max_trades_open": 3,
            "count_method": "positions",

            # ═══ SQS Configuration ═══
            "sqs_config": '{"enabled":true,"description":"Standardized 1% allocation - uniform capital across all quality tiers","probability_tiers":[{"min_probability":62,"min_sqs":75,"capital_multiplier":1,"description":"PREMIUM: 1% capital (62%+ prob + 75+ SQS)"},{"min_probability":60,"min_sqs":65,"capital_multiplier":1,"description":"HIGH QUALITY: 1% capital (60%+ prob + 65+ SQS)"},{"min_probability":58,"min_sqs":60,"capital_multiplier":1,"description":"GOOD: 1% capital (58%+ prob + 60+ SQS)"},{"min_probability":58,"min_sqs":55,"capital_multiplier":1,"description":"STANDARD: 1% capital (58%+ prob + 55+ SQS)"},{"min_probability":58,"min_sqs":50,"capital_multiplier":1,"description":"MINIMUM: 1% capital (58%+ prob + 50+ SQS)"}],"absolute_minimums":{"min_probability":58,"min_sqs":50,"reject_rule":"probability < 58 OR sqs < 50"},"risk_management":{"max_capital_multiplier":1,"max_daily_trades":10,"emergency_stop_sqs":30}}',

            # ═══ 🔥 ANTI-REPETITION ═══
            "anti_repetition": {
                "enabled": True,
                "cooldown_after_stop_hours": 24,      # 24h cooldown (conservador)
                "lookback_hours": 48,
                "min_price_change_pct": 3.0,
                "mode": "per_symbol"
            },

            # ═══ 🔥 CIRCUIT BREAKER ═══
            "circuit_breaker": {
                "enabled": True,
                "max_drawdown_pct": -15.0,            # Pausa a -15% DD
                "max_consecutive_losses": 3,
                "pause_duration_hours": 48,
                "recovery_target_pct": 60,
                "auto_reset": True
            },

            # ═══ 🔥 DAILY LOSS LIMITS ═══
            "daily_loss_limits": {
                "enabled": True,
                "max_daily_loss_pct": 3.0,            # Pausa a -3% diario
                "pause_duration_hours": 24,
                "reset_time_utc": "00:00"
            },

            # ═══ 🔥 SCHEDULE ═══
            "schedule": {
                "enabled": False                      # 24/7
            },

            # ═══ 🔥 SYMBOL BLACKLIST ═══
            "symbol_blacklist": {
                "enabled": True,
                "min_trades_for_evaluation": 8,
                "min_win_rate_pct": 45.0,
                "max_cumulative_loss_pct": -12.0,
                "lookback_days": 60,
                "auto_blacklist": True
            },

            # ═══ 🔥 PORTFOLIO PROTECTION ═══
            "portfolio_protection": {
                "enabled": True,
                "max_correlation_score": 0.75,
                "max_sector_exposure_pct": 50,
                "max_portfolio_drawdown_pct": 12,
                "pause_on_high_risk": True
            },

            # ═══ 🔥 MAX TRADES PROTECTION ═══
            "max_trades_protection": {
                "enabled": True,
                "trigger_losing_trades": 2,
                "cooldown_hours": 6,
                "losing_threshold_R": -0.3
            },

            # ═══ 💰 PROFIT TAKING (DESHABILITADO - se usa trailing stop) ═══
            "profit_taking": {
                "enabled": False,  # Solo trailing stop, NO cierres parciales
                "levels": []
            }
        },

        # ═══════════════════════════════════════════════════════════════
        # archer_model - ML-based strategy (simplified validation)
        # ═══════════════════════════════════════════════════════════════
        "archer_model": {
            "enabled": True,
            "min_rr": 1.1,
            "min_probability": 70,
            "min_ev": 0.06,               # Expected Value mínimo (0.06 = 6% de ganancia esperada)
            "risk_pct": 1.0,
            "max_leverage": 30,
            "max_trades_open": 3,
            "count_method": "positions",
            "use_guardian": True,
            "use_guardian_half": False,

            "anti_repetition": {
                "enabled": True,
                "cooldown_after_stop_hours": 24,
                "lookback_hours": 48,
                "min_price_change_pct": 3.0,
                "mode": "per_symbol"
            },
            "circuit_breaker": {
                "enabled": True,
                "max_drawdown_pct": -15.0,
                "max_consecutive_losses": 3,
                "pause_duration_hours": 48,
                "recovery_target_pct": 60,
                "auto_reset": True
            },
            "daily_loss_limits": {
                "enabled": True,
                "max_daily_loss_pct": 3.0,
                "pause_duration_hours": 24,
                "reset_time_utc": "00:00"
            },
            "schedule": {
                "enabled": False,
                "timezone": "UTC",
                "allowed_periods": []
            },
            "symbol_blacklist": {
                "enabled": True,
                "min_trades_for_evaluation": 8,
                "min_win_rate_pct": 45.0,
                "max_cumulative_loss_pct": -12.0,
                "lookback_days": 60,
                "auto_blacklist": True
            },
            "portfolio_protection": {
                "enabled": True,
                "max_correlation_score": 0.75,
                "max_sector_exposure_pct": 50,
                "max_portfolio_drawdown_pct": 12,
                "pause_on_high_risk": True
            },
            "max_trades_protection": {
                "enabled": True,
                "trigger_losing_trades": 2,
                "cooldown_hours": 6,
                "losing_threshold_R": -0.3
            },
            "profit_taking": {
                "enabled": False,
                "levels": []
            }
        }
    },

    # ════════════════════════════════════════════════════════════════════
    # HUFSA - Usuario MODERADO
    # Filosofía: Balance entre crecimiento y protección
    # ════════════════════════════════════════════════════════════════════
    HUFSA: {
        "archer_dual": {
            # ═══ Core Settings ═══
            "enabled": True,
            "min_rr": 1.1,
            "min_ev": 0.06,               # Expected Value mínimo (0.06 = 6% de ganancia esperada)
            "risk_pct": 1.0,
            "max_leverage": 40,               # Mayor apalancamiento que conservador (40x vs 30x)

            # ═══ Guardian Settings ═══
            "use_guardian": True,         # ✅ Trailing stop activo
            "use_guardian_half": False,   # ❌ NO cierres parciales

            # ═══ Trade Limits ═══
            "max_trades_open": 12,            # Muchos más trades simultáneos (12 vs 3)
            "count_method": "positions",

            # ═══ SQS Configuration ═══
            # MODERADO: Capital escalado 0.4x-3x según calidad de señal
            "sqs_config": '{"enabled":true,"description":"Conservative scaling with gradual risk progression","probability_tiers":[{"min_probability":60,"min_sqs":70,"capital_multiplier":3,"description":"ELITE: 3% capital (60%+ prob + 70+ SQS)"},{"min_probability":60,"min_sqs":65,"capital_multiplier":2.5,"description":"PREMIUM PLUS: 2.5% capital (60%+ prob + 65+ SQS)"},{"min_probability":60,"min_sqs":60,"capital_multiplier":2,"description":"PREMIUM: 2% capital (60%+ prob + 60+ SQS)"},{"min_probability":58,"min_sqs":60,"capital_multiplier":1.75,"description":"HIGH QUALITY 58%: 1.75% capital (58%+ prob + 60+ SQS)"},{"min_probability":58,"min_sqs":55,"capital_multiplier":1.5,"description":"STRONG 58%: 1.5% capital (58%+ prob + 55+ SQS)"},{"min_probability":58,"min_sqs":50,"capital_multiplier":1,"description":"STANDARD 58%: 1% capital (58%+ prob + 50+ SQS)"},{"min_probability":58,"min_sqs":45,"capital_multiplier":0.5,"description":"PROBABILITY CARRY: 0.5% capital (58%+ prob compensates low SQS)"},{"min_probability":55,"min_sqs":50,"capital_multiplier":0.5,"description":"BALANCED MEDIUM: 0.75% capital (55%+ prob + 50+ SQS)"},{"min_probability":55,"min_sqs":45,"capital_multiplier":0.4,"description":"MINIMUM SAFE: 0.4% capital (55%+ prob + 45+ SQS)"}],"absolute_minimums":{"min_probability":55,"min_sqs":45,"reject_rule":"probability < 55 OR sqs < 45"},"risk_management":{"max_capital_multiplier":3,"max_daily_trades":20,"emergency_stop_sqs":20}}',

            # ═══ 🔥 ANTI-REPETITION ═══
            "anti_repetition": {
                "enabled": True,
                "cooldown_after_stop_hours": 8,       # Solo 8h cooldown (más agresivo que 24h)
                "lookback_hours": 48,
                "min_price_change_pct": 2.0,          # Solo 2% cambio necesario (vs 3%)
                "mode": "per_symbol"
            },

            # ═══ 🔥 CIRCUIT BREAKER ═══
            "circuit_breaker": {
                "enabled": True,
                "max_drawdown_pct": -25.0,            # Tolera más DD (-25% vs -15%)
                "max_consecutive_losses": 4,          # 4 pérdidas consecutivas (vs 3)
                "pause_duration_hours": 24,           # Pausa más corta (24h vs 48h)
                "recovery_target_pct": 50,            # Solo recuperar 50% para reactivar (vs 60%)
                "auto_reset": True
            },

            # ═══ 🔥 DAILY LOSS LIMITS ═══
            "daily_loss_limits": {
                "enabled": True,
                "max_daily_loss_pct": 6.0,            # Tolera -6% diario (vs -3%)
                "pause_duration_hours": 12,           # Pausa más corta (12h vs 24h)
                "reset_time_utc": "00:00"
            },

            # ═══ 🔥 SCHEDULE ═══
            "schedule": {
                "enabled": False                      # 24/7
            },

            # ═══ 🔥 SYMBOL BLACKLIST ═══
            "symbol_blacklist": {
                "enabled": True,
                "min_trades_for_evaluation": 12,      # Dar más oportunidades (12 vs 8 trades)
                "min_win_rate_pct": 40.0,             # Win rate más permisivo (40% vs 45%)
                "max_cumulative_loss_pct": -18.0,     # Tolera más pérdida (-18% vs -12%)
                "lookback_days": 60,
                "auto_blacklist": True
            },

            # ═══ 🔥 PORTFOLIO PROTECTION ═══
            "portfolio_protection": {
                "enabled": True,
                "max_correlation_score": 0.85,        # Permite más correlación (0.85 vs 0.75)
                "max_sector_exposure_pct": 60,        # 60% en un sector OK (vs 50%)
                "max_portfolio_drawdown_pct": 15,
                "pause_on_high_risk": True
            },

            # ═══ 🔥 MAX TRADES PROTECTION ═══
            "max_trades_protection": {
                "enabled": True,
                "trigger_losing_trades": 4,           # Permite 4 perdiendo (vs 2)
                "cooldown_hours": 4,                  # Cooldown más corto (4h vs 6h)
                "losing_threshold_R": -0.5            # Threshold más bajo (-0.5R vs -0.3R)
            },

            # ═══ 💰 PROFIT TAKING (DESHABILITADO - se usa trailing stop) ═══
            "profit_taking": {
                "enabled": False,  # Solo trailing stop, NO cierres parciales
                "levels": []
            }
        },

        # ═══════════════════════════════════════════════════════════════
        # archer_model - ML-based strategy (simplified validation)
        # ═══════════════════════════════════════════════════════════════
        "archer_model": {
            "enabled": True,
            "min_rr": 1.1,
            "min_probability": 70,
            "min_ev": 0.06,               # Expected Value mínimo (0.06 = 6% de ganancia esperada)
            "risk_pct": 1.0,
            "max_leverage": 40,
            "max_trades_open": 12,  # Más trades (moderado)
            "count_method": "positions",
            "use_guardian": True,
            "use_guardian_half": False,

            "anti_repetition": {
                "enabled": True,
                "cooldown_after_stop_hours": 8,  # Más corto (moderado)
                "lookback_hours": 48,
                "min_price_change_pct": 2.0,  # Más permisivo (moderado)
                "mode": "per_symbol"
            },
            "circuit_breaker": {
                "enabled": True,
                "max_drawdown_pct": -25.0,  # Más tolerante (moderado)
                "max_consecutive_losses": 4,
                "pause_duration_hours": 24,
                "recovery_target_pct": 50,
                "auto_reset": True
            },
            "daily_loss_limits": {
                "enabled": True,
                "max_daily_loss_pct": 6.0,  # Más tolerante (moderado)
                "pause_duration_hours": 12,
                "reset_time_utc": "00:00"
            },
            "schedule": {
                "enabled": False,
                "timezone": "UTC",
                "allowed_periods": []
            },
            "symbol_blacklist": {
                "enabled": True,
                "min_trades_for_evaluation": 10,
                "min_win_rate_pct": 40.0,  # Más permisivo (moderado)
                "max_cumulative_loss_pct": -15.0,  # Más tolerante (moderado)
                "lookback_days": 60,
                "auto_blacklist": True
            },
            "portfolio_protection": {
                "enabled": True,
                "max_correlation_score": 0.80,  # Más permisivo (moderado)
                "max_sector_exposure_pct": 60,
                "max_portfolio_drawdown_pct": 15,
                "pause_on_high_risk": True
            },
            "max_trades_protection": {
                "enabled": True,
                "trigger_losing_trades": 4,  # Más tolerante (moderado)
                "cooldown_hours": 4,
                "losing_threshold_R": -0.5
            },
            "profit_taking": {
                "enabled": False,
                "levels": []
            }
        }
    },

    # ════════════════════════════════════════════════════════════════════
    # FUTURES - Usuario AGRESIVO
    # Filosofía: Maximizar rendimiento, alta tolerancia al riesgo
    # ════════════════════════════════════════════════════════════════════
    FUTURES: {
        "archer_dual": {
            # ═══ Core Settings ═══
            "enabled": True,
            "min_rr": 1.1,
            "min_ev": 0.06,               # Expected Value mínimo (0.06 = 6% de ganancia esperada)
            "risk_pct": 1.0,
            "max_leverage": 50,               # Apalancamiento máximo (50x)

            # ═══ Guardian Settings ═══
            "use_guardian": True,         # ✅ Trailing stop activo
            "use_guardian_half": False,   # ❌ NO cierres parciales (CORREGIDO - antes True)

            # ═══ Trade Limits ═══
            "max_trades_open": 15,            # Muchos trades simultáneos (15)
            "count_method": "positions",

            # ═══ SQS Configuration ═══
            # AGRESIVO: Capital escalado 0.5x-2x (menos agresivo que HUFSA pero más que COPY)
            "sqs_config": '{"enabled":true,"description":"Moderate allocation - 1.5% maximum (copy_trading profile)","probability_tiers":[{"min_probability":62,"min_sqs":80,"capital_multiplier":2,"description":"PREMIUM: 2.0% capital (62%+ prob + 80+ SQS)"},{"min_probability":60,"min_sqs":70,"capital_multiplier":1.5,"description":"HIGH QUALITY: 1.5% capital (60%+ prob + 70+ SQS)"},{"min_probability":58,"min_sqs":65,"capital_multiplier":1.3,"description":"GOOD: 1.3% capital (58%+ prob + 65+ SQS)"},{"min_probability":58,"min_sqs":60,"capital_multiplier":1,"description":"STANDARD: 1% capital (58%+ prob + 60+ SQS)"},{"min_probability":58,"min_sqs":55,"capital_multiplier":0.8,"description":"REDUCED: 0.8% capital (58%+ prob + 55+ SQS)"},{"min_probability":58,"min_sqs":50,"capital_multiplier":0.6,"description":"CONSERVATIVE: 0.6% capital (58%+ prob + 50+ SQS)"},{"min_probability":55,"min_sqs":55,"capital_multiplier":0.5,"description":"MINIMUM: 0.5% capital (55%+ prob + 55+ SQS)"},{"min_probability":60,"min_sqs":45,"capital_multiplier":0.5,"description":"MINIMUM: 0.5% capital (55%+ prob + 55+ SQS)"}],"absolute_minimums":{"min_probability":55,"min_sqs":50,"reject_rule":"probability < 55 OR sqs < 50"},"risk_management":{"max_capital_multiplier":1.5,"max_daily_trades":20,"emergency_stop_sqs":25}}',

            # ═══ 🔥 ANTI-REPETITION ═══
            "anti_repetition": {
                "enabled": True,
                "cooldown_after_stop_hours": 4,       # SOLO 4h cooldown (muy agresivo)
                "lookback_hours": 24,                 # Ventana corta de lookback (24h vs 48h)
                "min_price_change_pct": 1.0,          # Solo 1% cambio necesario (mínimo)
                "mode": "per_symbol"
            },

            # ═══ 🔥 CIRCUIT BREAKER ═══
            "circuit_breaker": {
                "enabled": True,
                "max_drawdown_pct": -40.0,            # Tolera MUCHO DD (-40%) antes de pausar
                "max_consecutive_losses": 6,          # 6 pérdidas consecutivas OK
                "pause_duration_hours": 12,           # Pausa MUY corta (12h)
                "recovery_target_pct": 30,            # Solo recuperar 30% para reactivar (mínimo)
                "auto_reset": True
            },

            # ═══ 🔥 DAILY LOSS LIMITS ═══
            "daily_loss_limits": {
                "enabled": True,
                "max_daily_loss_pct": 10.0,           # Tolera hasta -10% diario (muy alto)
                "pause_duration_hours": 6,            # Pausa ULTRA corta (6h)
                "reset_time_utc": "00:00"
            },

            # ═══ 🔥 SCHEDULE ═══
            "schedule": {
                "enabled": False                      # 24/7 sin restricciones
            },

            # ═══ 🔥 SYMBOL BLACKLIST ═══
            "symbol_blacklist": {
                "enabled": True,
                "min_trades_for_evaluation": 20,      # MUCHOS trades antes de evaluar (20)
                "min_win_rate_pct": 35.0,             # Win rate MUY bajo OK (35%)
                "max_cumulative_loss_pct": -25.0,     # Tolera pérdida ENORME (-25%)
                "lookback_days": 90,                  # Ventana larga (90 días)
                "auto_blacklist": True
            },

            # ═══ 🔥 PORTFOLIO PROTECTION ═══
            "portfolio_protection": {
                "enabled": False,                     # ¡DESACTIVADO! Sin límites de portfolio
                "max_correlation_score": 0.95,        # Si estuviera activo: correlación casi total OK
                "max_sector_exposure_pct": 100,       # 100% en un sector OK
                "max_portfolio_drawdown_pct": 30,
                "pause_on_high_risk": False
            },

            # ═══ 🔥 MAX TRADES PROTECTION ═══
            "max_trades_protection": {
                "enabled": True,
                "trigger_losing_trades": 7,           # Permite 7 trades perdiendo (vs 2-4)
                "cooldown_hours": 2,                  # Cooldown ULTRA corto (2h)
                "losing_threshold_R": -0.8            # Threshold MUY bajo (-0.8R = -80% del riesgo)
            },

            # ═══ 💰 PROFIT TAKING (DESHABILITADO - se usa trailing stop) ═══
            "profit_taking": {
                "enabled": False,  # Solo trailing stop, NO cierres parciales
                "levels": []
            }
        },

        # ═══════════════════════════════════════════════════════════════
        # archer_model - ML-based strategy (simplified validation)
        # ═══════════════════════════════════════════════════════════════
        "archer_model": {
            "enabled": True,
            "min_rr": 1.1,
            "min_probability": 70,
            "min_ev": 0.06,               # Expected Value mínimo (0.06 = 6% de ganancia esperada)
            "risk_pct": 1.0,
            "max_leverage": 50,  # Máximo apalancamiento (agresivo)
            "max_trades_open": 15,  # Muchos trades (agresivo)
            "count_method": "positions",
            "use_guardian": True,
            "use_guardian_half": False,

            "anti_repetition": {
                "enabled": True,
                "cooldown_after_stop_hours": 4,  # Muy corto (agresivo)
                "lookback_hours": 48,
                "min_price_change_pct": 1.5,  # Muy permisivo (agresivo)
                "mode": "per_symbol"
            },
            "circuit_breaker": {
                "enabled": True,
                "max_drawdown_pct": -30.0,  # Muy tolerante (agresivo)
                "max_consecutive_losses": 5,
                "pause_duration_hours": 12,  # Pausa corta (agresivo)
                "recovery_target_pct": 40,
                "auto_reset": True
            },
            "daily_loss_limits": {
                "enabled": True,
                "max_daily_loss_pct": 8.0,  # Muy tolerante (agresivo)
                "pause_duration_hours": 6,
                "reset_time_utc": "00:00"
            },
            "schedule": {
                "enabled": False,
                "timezone": "UTC",
                "allowed_periods": []
            },
            "symbol_blacklist": {
                "enabled": True,
                "min_trades_for_evaluation": 12,
                "min_win_rate_pct": 35.0,  # Muy permisivo (agresivo)
                "max_cumulative_loss_pct": -20.0,  # Muy tolerante (agresivo)
                "lookback_days": 60,
                "auto_blacklist": True
            },
            "portfolio_protection": {
                "enabled": True,
                "max_correlation_score": 0.85,  # Muy permisivo (agresivo)
                "max_sector_exposure_pct": 70,
                "max_portfolio_drawdown_pct": 20,
                "pause_on_high_risk": True
            },
            "max_trades_protection": {
                "enabled": True,
                "trigger_losing_trades": 7,  # Muy tolerante (agresivo)
                "cooldown_hours": 2,
                "losing_threshold_R": -0.8
            },
            "profit_taking": {
                "enabled": False,
                "levels": []
            }
        }
    }
}


def get_local_rules(user_id: str, strategy: str) -> dict:
    """
    Obtiene las rules desde el diccionario local sin consultar la base de datos.

    Args:
        user_id: ID del usuario
        strategy: Estrategia (ej: "archer_dual")

    Returns:
        dict: Rules configuradas para el usuario y estrategia

    Raises:
        ValueError: Si no se encuentra configuración para el usuario/estrategia
    """
    if user_id not in LOCAL_RULES:
        raise ValueError(f"No local rules configured for user_id: {user_id}")

    if strategy not in LOCAL_RULES[user_id]:
        raise ValueError(f"No local rules configured for strategy: {strategy} (user: {user_id})")

    return LOCAL_RULES[user_id][strategy].copy()
