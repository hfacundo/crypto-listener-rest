"""
Logger configuration with RotatingFileHandler
Rotates logs when they reach 5MB, keeping up to 5 backup files
"""
import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path


def setup_logger(
    name: str = "crypto-listener-rest",
    log_file: str = "logs/crypto-listener.log",
    max_bytes: int = 5 * 1024 * 1024,  # 5MB
    backup_count: int = 1,
    level: int = logging.INFO
) -> logging.Logger:
    """
    Configura un logger con RotatingFileHandler y console output.

    Args:
        name: Nombre del logger
        log_file: Ruta del archivo de log (se crea el directorio si no existe)
        max_bytes: Tamaño máximo del archivo antes de rotar (default: 5MB)
        backup_count: Número de archivos de backup a mantener (default: 1)
        level: Nivel de logging (default: INFO)

    Returns:
        Logger configurado

    Archivos generados:
        - crypto-listener.log (activo)
        - crypto-listener.log.1 (backup, se elimina al rotar)
    """
    # Crear directorio de logs si no existe
    log_path = Path(log_file)
    log_path.parent.mkdir(parents=True, exist_ok=True)

    # Crear logger
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

    # Handler 1: RotatingFileHandler (rota a 5MB)
    file_handler = RotatingFileHandler(
        filename=log_file,
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding='utf-8'
    )
    file_handler.setLevel(level)
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    # Handler 2: Console output (para nohup y debug)
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
