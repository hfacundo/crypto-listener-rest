-- Migration: Add manual_operations_audit table
-- Description: Audit log for all manual trading operations (close, adjust SL/TP, etc.)
-- Date: 2025-01-18
-- Author: Claude Code

-- Create audit table for manual operations
CREATE TABLE IF NOT EXISTS manual_operations_audit (
    id SERIAL PRIMARY KEY,
    timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    user_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    operation TEXT NOT NULL,  -- 'close_position', 'set_sl', 'set_tp', 'adjust_sl_tp', etc.
    params JSONB NOT NULL,    -- Request parameters
    result JSONB,             -- Response data
    success BOOLEAN NOT NULL,
    error TEXT,
    request_id TEXT,
    ip_address TEXT
);

-- Create indexes for common queries
CREATE INDEX IF NOT EXISTS idx_audit_user_time ON manual_operations_audit(user_id, timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_audit_symbol_time ON manual_operations_audit(symbol, timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_audit_operation ON manual_operations_audit(operation, timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_audit_request_id ON manual_operations_audit(request_id);
CREATE INDEX IF NOT EXISTS idx_audit_success ON manual_operations_audit(success, timestamp DESC);

-- Add comment to table
COMMENT ON TABLE manual_operations_audit IS 'Audit log for manual trading operations via REST API';
COMMENT ON COLUMN manual_operations_audit.operation IS 'Operation type: close_position, set_sl, set_tp, adjust_sl_tp, adjust_sl_tp_flexible, trailing_stop, batch_adjust';
COMMENT ON COLUMN manual_operations_audit.params IS 'JSON of request parameters (user_id, symbol, stop_loss, take_profit, etc.)';
COMMENT ON COLUMN manual_operations_audit.result IS 'JSON of operation result (order_id, success, error, etc.)';
COMMENT ON COLUMN manual_operations_audit.request_id IS 'X-Request-ID header from HTTP request for traceability';
