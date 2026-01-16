"""
Logger configuration - Console only (stdout)
All logs go to stdout, which can be redirected to uvicorn.log via nohup
"""
import logging


def setup_logger(
    name: str = "crypto-listener-rest",
    level: int = logging.INFO
) -> logging.Logger:
    """
    Configura un logger que escribe solo a stdout (consola).
    Usa nohup para redirigir a uvicorn.log:
        nohup uvicorn main:app --host 127.0.0.1 --port 8000 > uvicorn.log 2>&1 &

    Args:
        name: Nombre del logger
        level: Nivel de logging (default: INFO)

    Returns:
        Logger configurado
    """
    logger = logging.getLogger(name)
    logger.setLevel(level)

    # Evitar duplicación de handlers si ya está configurado
    if logger.handlers:
        return logger

    # Formato de log con timestamp, nivel, y mensaje
    formatter = logging.Formatter(
        fmt='%(asctime)s | %(levelname)-8s | %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )

    # Solo console output (stdout) - se redirige a uvicorn.log con nohup
    console_handler = logging.StreamHandler()
    console_handler.setLevel(level)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    return logger


def get_logger(name: str = "crypto-listener-rest") -> logging.Logger:
    """
    Obtiene el logger existente o crea uno nuevo si no existe.

    Args:
        name: Nombre del logger (default: crypto-listener-rest)

    Returns:
        Logger instance
    """
    logger = logging.getLogger(name)

    # Si el logger no tiene handlers, configurarlo
    if not logger.handlers:
        logger = setup_logger(name)

    return logger


# Inicialización global del logger por defecto
_default_logger = None

def init_default_logger():
    """Inicializa el logger por defecto al importar el módulo"""
    global _default_logger
    if _default_logger is None:
        _default_logger = setup_logger()
    return _default_logger


# Auto-inicializar al importar
init_default_logger()
