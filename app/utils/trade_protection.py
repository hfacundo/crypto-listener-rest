#!/usr/bin/env python3
# app/utils/trade_protection.py
"""
Sistema de protección de trading:
1. Anti-Repetition Filter: Evita repetir trades fallidos
2. Circuit Breaker: Pausa estrategia en drawdowns extremos
3. Symbol Performance Tracker: Blacklistea símbolos tóxicos

Usa PostgreSQL para persistencia (mejor que Redis para análisis histórico).
"""

import psycopg2
import psycopg2.extras
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple
import os


class TradeProtectionSystem:
    """
    Sistema unificado de protección de trading.
    Integra anti-repetition, circuit breaker y symbol tracking.
    """

    def __init__(self, db_config: Optional[Dict] = None):
        """
        Args:
            db_config: Config de PostgreSQL. Si None, usa variables de entorno.
        """
        if db_config:
            self.db_config = db_config
        else:
            # Try DATABASE_URL_CRYPTO_TRADER first (compatible with existing code)
            db_url = os.getenv('DATABASE_URL_CRYPTO_TRADER')
            if db_url:
                # Parse postgresql://user:password@host:port/database
                from urllib.parse import urlparse
                parsed = urlparse(db_url)
                self.db_config = {
                    'host': parsed.hostname or 'localhost',
                    'port': parsed.port or 5432,
                    'database': parsed.path.lstrip('/') if parsed.path else 'crypto_analyzer',
                    'user': parsed.username or 'postgres',
                    'password': parsed.password or 'postgres'
                }
            else:
                # Fallback to individual env vars
                self.db_config = {
                    'host': os.getenv('DB_HOST', 'localhost'),
                    'port': int(os.getenv('DB_PORT', 5432)),
                    'database': os.getenv('DB_NAME', 'crypto_analyzer'),
                    'user': os.getenv('DB_USER', 'postgres'),
                    'password': os.getenv('DB_PASSWORD', 'postgres')
                }

        self._init_tables()

    def _get_conn(self):
        """Get database connection"""
        # Fix UTF-8 encoding for PostgreSQL connection
        config = self.db_config.copy()
        if 'password' in config:
            # Ensure password is properly encoded
            if isinstance(config['password'], str):
                config['password'] = config['password'].encode('utf-8').decode('utf-8')
        return psycopg2.connect(**config, client_encoding='UTF8')

    def _init_tables(self):
        """
        Crea tablas necesarias si no existen.

        Tablas:
        - trade_history: Registro de todos los trades (para anti-repetition y symbol tracker)
        - strategy_state: Estado de la estrategia (para circuit breaker)
        """
        schema = """
        -- Tabla de histórico de trades
        CREATE TABLE IF NOT EXISTS trade_history (
            id SERIAL PRIMARY KEY,
            user_id VARCHAR(50) NOT NULL,
            strategy VARCHAR(50) NOT NULL,
            symbol VARCHAR(20) NOT NULL,
            direction VARCHAR(10) NOT NULL,
            entry_time TIMESTAMP NOT NULL,
            entry_price DECIMAL(20, 8) NOT NULL,
            exit_time TIMESTAMP,
            exit_price DECIMAL(20, 8),
            stop_price DECIMAL(20, 8),
            target_price DECIMAL(20, 8),
            exit_reason VARCHAR(20),  -- 'target_hit', 'stop_hit', 'timeout_win', 'timeout_lost', 'timeout_breakeven', 'manual_win', 'manual_lost', 'manual_breakeven', 'active'
            pnl_pct DECIMAL(10, 4),
            pnl_usdt DECIMAL(15, 2),
            probability DECIMAL(5, 2),
            sqs DECIMAL(5, 2),
            rr DECIMAL(10, 2),
            order_id BIGINT,
            sl_order_id BIGINT,
            tp_order_id BIGINT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE INDEX IF NOT EXISTS idx_trade_history_user_strategy
            ON trade_history(user_id, strategy, entry_time DESC);
        CREATE INDEX IF NOT EXISTS idx_trade_history_symbol_direction
            ON trade_history(symbol, direction, entry_time DESC);
        CREATE INDEX IF NOT EXISTS idx_trade_history_exit_reason
            ON trade_history(exit_reason);
        CREATE INDEX IF NOT EXISTS idx_trade_history_order_id
            ON trade_history(order_id);

        -- Migración de datos antiguos: separar strategy_name en user_id + strategy
        -- Solo si existe columna strategy_name
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'trade_history' AND column_name = 'strategy_name'
            ) THEN
                -- Actualizar user_id y strategy desde strategy_name existente
                UPDATE trade_history
                SET
                    user_id = COALESCE(user_id, SPLIT_PART(strategy_name, '_', 1)),
                    strategy = COALESCE(strategy, REGEXP_REPLACE(strategy_name, '^[^_]+_', ''))
                WHERE user_id IS NULL OR strategy IS NULL;

                -- Eliminar columna strategy_name después de migrar
                ALTER TABLE trade_history DROP COLUMN IF EXISTS strategy_name;
            END IF;
        END $$;

        -- Tabla de estado de estrategia (para circuit breaker)
        CREATE TABLE IF NOT EXISTS strategy_state (
            id SERIAL PRIMARY KEY,
            strategy_name VARCHAR(50) UNIQUE NOT NULL,
            cumulative_pnl_pct DECIMAL(10, 4) DEFAULT 0,
            peak_pnl_pct DECIMAL(10, 4) DEFAULT 0,
            current_drawdown_pct DECIMAL(10, 4) DEFAULT 0,
            max_drawdown_pct DECIMAL(10, 4) DEFAULT 0,
            total_trades INT DEFAULT 0,
            winning_trades INT DEFAULT 0,
            losing_trades INT DEFAULT 0,
            consecutive_wins INT DEFAULT 0,
            consecutive_losses INT DEFAULT 0,
            circuit_breaker_active BOOLEAN DEFAULT FALSE,
            circuit_breaker_since TIMESTAMP,
            last_trade_time TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        -- Inicializar estrategias si no existen
        INSERT INTO strategy_state (strategy_name)
        VALUES ('claude'), ('sniper'), ('archer_dual')
        ON CONFLICT (strategy_name) DO NOTHING;
        """

        conn = self._get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(schema)
            conn.commit()
            # print("✅ Trade protection tables initialized")
        except Exception as e:
            print(f"⚠️ Error initializing tables: {e}")
            conn.rollback()
        finally:
            conn.close()

    # ═══════════════════════════════════════════════════════════════════
    # 1. ANTI-REPETITION FILTER
    # ═══════════════════════════════════════════════════════════════════

    def should_block_repetition(
        self,
        user_id: str,
        strategy: str,
        symbol: str,
        direction: str,
        current_price: float,
        cooldown_hours: int = 6,
        lookback_hours: int = 48,
        min_price_change_pct: float = 2.0
    ) -> Tuple[bool, Optional[str]]:
        """
        Bloquea trade si:
        1. Mismo símbolo+dirección falló recientemente (últimas lookback_hours)
        2. NO han pasado cooldown_hours desde el stop loss
        3. EXCEPCIÓN: Si precio cambió significativamente (>min_price_change_pct), permitir

        Args:
            user_id: ID del usuario
            strategy: Nombre de la estrategia
            symbol: Símbolo del trade (ej: "BTCUSDT")
            direction: Dirección (ej: "BUY", "SELL")
            current_price: Precio actual del trade
            cooldown_hours: Horas mínimas de espera después de un stop loss (default: 6h)
            lookback_hours: Ventana de tiempo para buscar stops previos (default: 48h)
            min_price_change_pct: % de cambio de precio para override automático (default: 2.0%)

        Returns:
            (should_block, reason)
        """
        query = """
        SELECT
            entry_price,
            entry_time,
            exit_time,
            pnl_pct,
            exit_reason,
            updated_at
        FROM trade_history
        WHERE user_id = %s
          AND strategy = %s
          AND symbol = %s
          AND direction = %s
          AND exit_reason = 'stop_hit'
          AND (exit_time > NOW() - INTERVAL '%s hours'
               OR (exit_time IS NULL AND updated_at > NOW() - INTERVAL '%s hours'))
        ORDER BY COALESCE(exit_time, updated_at) DESC
        LIMIT 1
        """

        conn = self._get_conn()
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
                cur.execute(query, (user_id, strategy, symbol, direction, lookback_hours, lookback_hours))
                failed_trade = cur.fetchone()

                if not failed_trade:
                    return False, None  # No hay trades fallidos recientes

                # ═══════════════════════════════════════════════════════════
                # VALIDACIÓN 1: Tiempo transcurrido desde el stop loss
                # ═══════════════════════════════════════════════════════════
                # Usar exit_time si existe, sino updated_at, sino entry_time como último fallback
                exit_time = failed_trade['exit_time'] or failed_trade.get('updated_at') or failed_trade['entry_time']

                # Asegurar que exit_time tiene timezone
                if exit_time.tzinfo is None:
                    exit_time = exit_time.replace(tzinfo=timezone.utc)

                time_since_stop = datetime.now(timezone.utc) - exit_time
                hours_since_stop = time_since_stop.total_seconds() / 3600

                # ═══════════════════════════════════════════════════════════
                # VALIDACIÓN 2: Cambio de precio (override)
                # ═══════════════════════════════════════════════════════════
                failed_price = float(failed_trade['entry_price'])
                price_change_pct = abs((current_price - failed_price) / failed_price * 100)

                # Si precio cambió significativamente, permitir trade (override)
                if price_change_pct >= min_price_change_pct:
                    return False, None  # Override: precio cambió suficiente

                # ═══════════════════════════════════════════════════════════
                # BLOQUEAR: No pasó suficiente tiempo Y precio no cambió
                # ═══════════════════════════════════════════════════════════
                if hours_since_stop < cooldown_hours:
                    hours_remaining = cooldown_hours - hours_since_stop
                    reason = (
                        f"⛔ ANTI-REPETITION COOLDOWN: {symbol} {direction} hit stop {hours_since_stop:.1f}h ago "
                        f"(${failed_price:.6f}, PnL: {failed_trade['pnl_pct']:.2f}%). "
                        f"Cooldown: {cooldown_hours}h | Remaining: {hours_remaining:.1f}h. "
                        f"Price change: {price_change_pct:.2f}% (< {min_price_change_pct}% threshold)"
                    )
                    return True, reason

                # Cooldown expirado, permitir trade
                return False, None

        except Exception as e:
            print(f"⚠️ Error checking anti-repetition: {e}")
            return False, None
        finally:
            conn.close()

    def record_trade(
        self,
        user_id: str,
        strategy: str,
        symbol: str,
        direction: str,
        entry_time: datetime,
        entry_price: float,
        stop_price: float,
        target_price: float,
        probability: float,
        sqs: float,
        rr: float,
        order_id: int = None,
        sl_order_id: int = None,
        tp_order_id: int = None
    ) -> int:
        """
        Registra un nuevo trade en estado 'active'.

        Args:
            user_id: ID del usuario (ej: "hufsa", "copy_trading")
            strategy: Nombre de la estrategia (ej: "archer_dual")
            symbol: Símbolo del trade (ej: "BTCUSDT") - se normalizará a mayúsculas
            direction: Dirección (ej: "BUY", "SELL")
            entry_time: Timestamp de entrada
            entry_price: Precio de entrada
            stop_price: Precio de stop loss
            target_price: Precio de take profit
            probability: Probabilidad de éxito (0-100)
            sqs: Signal Quality Score (0-100)
            rr: Risk/Reward ratio
            order_id: Order ID de Binance (entry)
            sl_order_id: Order ID de Binance (stop loss)
            tp_order_id: Order ID de Binance (take profit)

        Returns:
            int: trade_id para posterior actualización
        """
        # Normalizar symbol a MAYÚSCULAS para consistencia en BD
        symbol = symbol.upper()

        query = """
        INSERT INTO trade_history (
            user_id, strategy, symbol, direction, entry_time, entry_price,
            stop_price, target_price, probability, sqs, rr, exit_reason,
            order_id, sl_order_id, tp_order_id
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'active', %s, %s, %s)
        RETURNING id
        """

        conn = self._get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(query, (
                    user_id, strategy, symbol, direction, entry_time, entry_price,
                    stop_price, target_price, probability, sqs, rr,
                    order_id, sl_order_id, tp_order_id
                ))
                trade_id = cur.fetchone()[0]
            conn.commit()
            return trade_id
        except Exception as e:
            print(f"⚠️ Error recording trade: {e}")
            conn.rollback()
            return -1
        finally:
            conn.close()

    def update_trade_exit(
        self,
        user_id: str,
        strategy: str,
        trade_id: int,
        exit_price: float,
        exit_reason: str,  # 'target_hit', 'stop_hit', 'timeout_win', 'timeout_lost', 'timeout_breakeven', 'manual_win', 'manual_lost', 'manual_breakeven'
        pnl: float,
        exit_time: Optional[datetime] = None
    ) -> bool:
        """
        Actualiza trade con resultado de salida.
        Además actualiza strategy_state con el resultado.

        Args:
            user_id: ID del usuario (ej: "hufsa")
            strategy: Nombre de la estrategia (ej: "archer_dual")
            trade_id: ID del trade a actualizar
            exit_price: Precio de salida
            exit_reason: Razón del cierre con sufijo win/lost:
                - 'target_hit': TP tocó (ganancia)
                - 'stop_hit': SL tocó (pérdida)
                - 'timeout_win': cerrado por timeout con ganancia
                - 'timeout_lost': cerrado por timeout con pérdida
                - 'timeout_breakeven': cerrado por timeout sin ganancia ni pérdida
                - 'manual_win': cerrado manualmente con ganancia
                - 'manual_lost': cerrado manualmente con pérdida
                - 'manual_breakeven': cerrado manualmente sin ganancia ni pérdida
            pnl: PnL en USDT (positivo = ganancia, negativo = pérdida)
            exit_time: Timestamp de salida (si None, usa NOW())

        Returns:
            bool: True si se actualizó exitosamente
        """
        if exit_time is None:
            exit_time = datetime.now()

        # Primero obtener entry_price para calcular pnl_pct
        query_get_entry = """
        SELECT entry_price, user_id, strategy FROM trade_history WHERE id = %s
        """

        query_update = """
        UPDATE trade_history
        SET exit_time = %s,
            exit_price = %s,
            exit_reason = %s,
            pnl_pct = %s,
            pnl_usdt = %s,
            updated_at = NOW()
        WHERE id = %s
        RETURNING user_id, strategy
        """

        conn = self._get_conn()
        try:
            with conn.cursor() as cur:
                # Get entry price
                cur.execute(query_get_entry, (trade_id,))
                result = cur.fetchone()
                if not result:
                    print(f"⚠️ Trade {trade_id} not found")
                    return False

                entry_price = float(result[0])
                db_user_id = result[1]
                db_strategy = result[2]

                # Calculate pnl_pct
                pnl_pct = ((exit_price - entry_price) / entry_price) * 100 if entry_price > 0 else 0
                pnl_usdt = pnl

                # Update trade
                cur.execute(query_update, (
                    exit_time, exit_price, exit_reason, pnl_pct, pnl_usdt, trade_id
                ))

                # Update strategy state (legacy - usa strategy_name concatenado)
                strategy_name = f"{db_user_id}_{db_strategy}"
                self._update_strategy_state(cur, strategy_name, pnl_pct, exit_time)

            conn.commit()
            return True
        except Exception as e:
            print(f"⚠️ Error updating trade exit: {e}")
            conn.rollback()
            return False
        finally:
            conn.close()

    def _update_strategy_state(self, cursor, strategy_name: str, pnl_pct: float, trade_time: datetime):
        """Helper para actualizar estado de estrategia"""
        # Get current state
        cursor.execute("""
            SELECT cumulative_pnl_pct, peak_pnl_pct, consecutive_wins, consecutive_losses,
                   total_trades, winning_trades, losing_trades
            FROM strategy_state
            WHERE strategy_name = %s
        """, (strategy_name,))

        state = cursor.fetchone()
        if not state:
            return

        cum_pnl, peak_pnl, cons_wins, cons_losses, total, wins, losses = state

        # Convert Decimal to float for arithmetic operations
        cum_pnl = float(cum_pnl) if cum_pnl is not None else 0.0
        peak_pnl = float(peak_pnl) if peak_pnl is not None else 0.0

        # Update cumulative
        new_cum_pnl = cum_pnl + pnl_pct

        # Update peak
        new_peak = max(peak_pnl, new_cum_pnl)

        # Update drawdown
        new_dd = new_peak - new_cum_pnl

        # Update consecutive
        if pnl_pct > 0:
            new_cons_wins = cons_wins + 1
            new_cons_losses = 0
            new_wins = wins + 1
            new_losses = losses
        else:
            new_cons_wins = 0
            new_cons_losses = cons_losses + 1
            new_wins = wins
            new_losses = losses + 1

        # Update max DD
        cursor.execute("""
            SELECT max_drawdown_pct FROM strategy_state WHERE strategy_name = %s
        """, (strategy_name,))
        current_max_dd = cursor.fetchone()[0]
        current_max_dd = float(current_max_dd) if current_max_dd is not None else 0.0
        new_max_dd = max(current_max_dd, new_dd)

        # Save
        cursor.execute("""
            UPDATE strategy_state
            SET cumulative_pnl_pct = %s,
                peak_pnl_pct = %s,
                current_drawdown_pct = %s,
                max_drawdown_pct = %s,
                total_trades = %s,
                winning_trades = %s,
                losing_trades = %s,
                consecutive_wins = %s,
                consecutive_losses = %s,
                last_trade_time = %s,
                updated_at = NOW()
            WHERE strategy_name = %s
        """, (
            new_cum_pnl, new_peak, new_dd, new_max_dd,
            total + 1, new_wins, new_losses,
            new_cons_wins, new_cons_losses,
            trade_time, strategy_name
        ))

    # ═══════════════════════════════════════════════════════════════════
    # 2. CIRCUIT BREAKER
    # ═══════════════════════════════════════════════════════════════════

    def should_activate_circuit_breaker(
        self,
        user_id: str,
        strategy: str,
        max_drawdown_threshold: float = -30.0,  # -30%
        max_consecutive_losses: int = 5
    ) -> Tuple[bool, Optional[str]]:
        """
        Activa circuit breaker si:
        1. Drawdown actual > threshold (-30%)
        2. Consecutivas pérdidas > threshold (5)

        Args:
            user_id: ID del usuario
            strategy: Nombre de la estrategia

        Returns:
            (should_block, reason)
        """
        # Para mantener compatibilidad con strategy_state existente (legacy)
        strategy_name = f"{user_id}_{strategy}"

        query = """
        SELECT
            current_drawdown_pct,
            consecutive_losses,
            circuit_breaker_active,
            circuit_breaker_since,
            cumulative_pnl_pct,
            peak_pnl_pct
        FROM strategy_state
        WHERE strategy_name = %s
        """

        conn = self._get_conn()
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
                cur.execute(query, (strategy_name,))
                state = cur.fetchone()

                if not state:
                    return False, None

                current_dd = float(state['current_drawdown_pct'])
                cons_losses = state['consecutive_losses']
                breaker_active = state['circuit_breaker_active']
                cum_pnl = float(state['cumulative_pnl_pct'])
                peak_pnl = float(state['peak_pnl_pct'])

                # Check if should activate
                should_activate = (
                    current_dd > abs(max_drawdown_threshold) or
                    cons_losses >= max_consecutive_losses
                )

                if should_activate and not breaker_active:
                    # ACTIVATE CIRCUIT BREAKER
                    cur.execute("""
                        UPDATE strategy_state
                        SET circuit_breaker_active = TRUE,
                            circuit_breaker_since = NOW(),
                            updated_at = NOW()
                        WHERE strategy_name = %s
                    """, (strategy_name,))
                    conn.commit()

                    reason = (
                        f"🚨 CIRCUIT BREAKER ACTIVATED: {strategy_name}\n"
                        f"   Current DD: {current_dd:.2f}% (threshold: {max_drawdown_threshold}%)\n"
                        f"   Consecutive Losses: {cons_losses} (threshold: {max_consecutive_losses})\n"
                        f"   Cumulative PnL: {cum_pnl:.2f}%\n"
                        f"   Trading PAUSED until recovery"
                    )
                    return True, reason

                elif breaker_active:
                    # Check if should deactivate (recovery)
                    recovery_target = peak_pnl - (abs(max_drawdown_threshold) * 0.5)  # Recuperar 50% del DD

                    if cum_pnl >= recovery_target and cons_losses == 0:
                        # DEACTIVATE
                        cur.execute("""
                            UPDATE strategy_state
                            SET circuit_breaker_active = FALSE,
                                circuit_breaker_since = NULL,
                                updated_at = NOW()
                            WHERE strategy_name = %s
                        """, (strategy_name,))
                        conn.commit()

                        print(f"✅ CIRCUIT BREAKER RESET: {strategy_name} recovered to {cum_pnl:.2f}%")
                        return False, None
                    else:
                        # Still blocked
                        since = state['circuit_breaker_since']
                        duration = datetime.now() - since if since else timedelta(0)
                        reason = (
                            f"⏸️ CIRCUIT BREAKER ACTIVE: {strategy_name} (for {duration.total_seconds()/3600:.1f}h)\n"
                            f"   Current PnL: {cum_pnl:.2f}% | Recovery target: {recovery_target:.2f}%\n"
                            f"   Consecutive Losses: {cons_losses} | Waiting for win streak"
                        )
                        return True, reason

                return False, None

        except Exception as e:
            print(f"⚠️ Error checking circuit breaker: {e}")
            return False, None
        finally:
            conn.close()

    # ═══════════════════════════════════════════════════════════════════
    # 3. SYMBOL PERFORMANCE TRACKER
    # ═══════════════════════════════════════════════════════════════════

    def get_symbol_stats(
        self,
        user_id: str,
        strategy: str,
        symbol: str,
        lookback_days: int = 30
    ) -> Dict:
        """
        Obtiene estadísticas recientes de un símbolo.

        Args:
            user_id: ID del usuario
            strategy: Nombre de la estrategia
            symbol: Símbolo del trade
            lookback_days: Días hacia atrás para análisis

        Returns dict con:
        - trades: Número de trades
        - win_rate: % de trades ganadores
        - cumulative_pnl: PnL acumulado
        - avg_pnl: PnL promedio por trade
        - status: 'new', 'excellent', 'good', 'neutral', 'poor', 'toxic'
        """
        query = """
        SELECT
            COUNT(*) as total_trades,
            SUM(CASE WHEN pnl_pct > 0 THEN 1 ELSE 0 END) as wins,
            SUM(pnl_pct) as cumulative_pnl,
            AVG(pnl_pct) as avg_pnl,
            MAX(entry_time) as last_trade
        FROM trade_history
        WHERE user_id = %s
          AND strategy = %s
          AND symbol = %s
          AND exit_reason IN ('target_hit', 'stop_hit', 'timeout')
          AND entry_time > NOW() - INTERVAL '%s days'
        """

        conn = self._get_conn()
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
                cur.execute(query, (user_id, strategy, symbol, lookback_days))
                result = cur.fetchone()

                if not result or result['total_trades'] == 0:
                    return {
                        'status': 'new',
                        'trades': 0,
                        'win_rate': 0,
                        'cumulative_pnl': 0,
                        'avg_pnl': 0
                    }

                trades = result['total_trades']
                wins = result['wins']
                win_rate = (wins / trades * 100) if trades > 0 else 0
                cum_pnl = float(result['cumulative_pnl'] or 0)
                avg_pnl = float(result['avg_pnl'] or 0)

                # Classify status
                if trades < 5:
                    status = 'new'
                elif win_rate >= 60 and cum_pnl > 10:
                    status = 'excellent'
                elif win_rate >= 55 and cum_pnl > 5:
                    status = 'good'
                elif win_rate >= 48 and cum_pnl >= -5:
                    status = 'neutral'
                elif win_rate < 42 or cum_pnl < -15:
                    status = 'toxic'
                else:
                    status = 'poor'

                return {
                    'status': status,
                    'trades': trades,
                    'win_rate': win_rate,
                    'cumulative_pnl': cum_pnl,
                    'avg_pnl': avg_pnl
                }

        except Exception as e:
            print(f"⚠️ Error getting symbol stats: {e}")
            return {'status': 'new', 'trades': 0, 'win_rate': 0, 'cumulative_pnl': 0, 'avg_pnl': 0}
        finally:
            conn.close()

    def should_block_symbol(
        self,
        user_id: str,
        strategy: str,
        symbol: str,
        min_trades: int = 10,
        min_win_rate: float = 42.0,
        max_loss_pct: float = -15.0
    ) -> Tuple[bool, Optional[str]]:
        """
        Bloquea símbolo si:
        1. Tiene suficientes trades (>=10) Y
        2. Win rate muy bajo (<42%) O PnL muy negativo (<-15%)

        Args:
            user_id: ID del usuario
            strategy: Nombre de la estrategia
            symbol: Símbolo del trade

        Returns:
            (should_block, reason)
        """
        stats = self.get_symbol_stats(user_id, strategy, symbol, lookback_days=60)

        if stats['status'] == 'new':
            return False, None  # Dar oportunidad

        if stats['trades'] >= min_trades:
            if stats['win_rate'] < min_win_rate:
                reason = (
                    f"⛔ SYMBOL BLACKLIST: {symbol}\n"
                    f"   Win Rate: {stats['win_rate']:.1f}% < {min_win_rate}% (over {stats['trades']} trades)\n"
                    f"   Cumulative PnL: {stats['cumulative_pnl']:.2f}%\n"
                    f"   Status: {stats['status'].upper()}"
                )
                return True, reason

            if stats['cumulative_pnl'] < max_loss_pct:
                reason = (
                    f"⛔ SYMBOL BLACKLIST: {symbol}\n"
                    f"   Cumulative PnL: {stats['cumulative_pnl']:.2f}% < {max_loss_pct}%\n"
                    f"   Win Rate: {stats['win_rate']:.1f}% (over {stats['trades']} trades)\n"
                    f"   Status: {stats['status'].upper()}"
                )
                return True, reason

        return False, None

    # ═══════════════════════════════════════════════════════════════════
    # UTILITY METHODS
    # ═══════════════════════════════════════════════════════════════════

    def get_strategy_state(self, strategy_name: str) -> Dict:
        """Obtiene estado actual de la estrategia"""
        query = "SELECT * FROM strategy_state WHERE strategy_name = %s"

        conn = self._get_conn()
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
                cur.execute(query, (strategy_name,))
                result = cur.fetchone()
                return dict(result) if result else {}
        finally:
            conn.close()

    def reset_circuit_breaker(self, strategy_name: str):
        """Reset manual del circuit breaker (usar con cuidado)"""
        conn = self._get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE strategy_state
                    SET circuit_breaker_active = FALSE,
                        circuit_breaker_since = NULL,
                        consecutive_losses = 0,
                        updated_at = NOW()
                    WHERE strategy_name = %s
                """, (strategy_name,))
            conn.commit()
            print(f"✅ Circuit breaker manually reset for {strategy_name}")
        except Exception as e:
            print(f"⚠️ Error resetting circuit breaker: {e}")
            conn.rollback()
        finally:
            conn.close()

    def get_symbol_performance_report(self, user_id: str, strategy: str, top_n: int = 10) -> str:
        """
        Genera reporte de mejores/peores símbolos.

        Args:
            user_id: ID del usuario
            strategy: Nombre de la estrategia
            top_n: Número de símbolos a mostrar
        """
        query = """
        SELECT
            symbol,
            COUNT(*) as trades,
            SUM(CASE WHEN pnl_pct > 0 THEN 1 ELSE 0 END) * 100.0 / COUNT(*) as win_rate,
            SUM(pnl_pct) as cumulative_pnl,
            AVG(pnl_pct) as avg_pnl
        FROM trade_history
        WHERE user_id = %s
          AND strategy = %s
          AND exit_reason IN ('target_hit', 'stop_hit', 'timeout')
          AND entry_time > NOW() - INTERVAL '60 days'
        GROUP BY symbol
        HAVING COUNT(*) >= 5
        ORDER BY cumulative_pnl DESC
        """

        conn = self._get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(query, (user_id, strategy))
                results = cur.fetchall()

                if not results:
                    return "No data available"

                report = f"\n{'='*70}\n"
                report += f"SYMBOL PERFORMANCE REPORT - {user_id.upper()}/{strategy.upper()}\n"
                report += f"{'='*70}\n\n"

                # Best performers
                report += "🏆 TOP PERFORMERS:\n"
                report += f"{'Symbol':<12} | {'Trades':<7} | {'WR':<7} | {'Cum PnL':<10} | {'Avg PnL'}\n"
                report += "-" * 70 + "\n"
                for row in results[:top_n]:
                    symbol, trades, wr, cum_pnl, avg_pnl = row
                    report += f"{symbol:<12} | {int(trades):<7} | {wr:6.1f}% | {cum_pnl:9.2f}% | {avg_pnl:7.2f}%\n"

                # Worst performers
                report += "\n💔 WORST PERFORMERS:\n"
                report += f"{'Symbol':<12} | {'Trades':<7} | {'WR':<7} | {'Cum PnL':<10} | {'Avg PnL'}\n"
                report += "-" * 70 + "\n"
                for row in results[-top_n:][::-1]:
                    symbol, trades, wr, cum_pnl, avg_pnl = row
                    report += f"{symbol:<12} | {int(trades):<7} | {wr:6.1f}% | {cum_pnl:9.2f}% | {avg_pnl:7.2f}%\n"

                report += f"{'='*70}\n"
                return report

        except Exception as e:
            return f"Error generating report: {e}"
        finally:
            conn.close()
