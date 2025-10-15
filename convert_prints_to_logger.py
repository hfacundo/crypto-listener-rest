#!/usr/bin/env python3
"""
Script para convertir print() statements a logger calls
Uso: python convert_prints_to_logger.py <archivo.py>

Este script detecta automáticamente el nivel de log apropiado:
- logger.error() para mensajes con ❌, "Error", "Failed"
- logger.warning() para mensajes con ⚠️, "Warning"
- logger.debug() para mensajes con "DEBUG"
- logger.info() para todo lo demás
"""

import re
import sys
from pathlib import Path


def detect_log_level(print_content: str) -> str:
    """
    Detecta el nivel de log apropiado basado en el contenido del print

    Args:
        print_content: Contenido entre print()

    Returns:
        Nivel de log: 'error', 'warning', 'debug', o 'info'
    """
    content_lower = print_content.lower()

    # Emojis y palabras clave por nivel
    error_keywords = ['❌', 'error', 'failed', 'exception', 'traceback', 'fail:']
    warning_keywords = ['⚠️', 'warning', 'warn', 'no se encontr']
    debug_keywords = ['debug', '🔍']

    # Prioridad: error > warning > debug > info
    if any(kw in content_lower for kw in error_keywords):
        return 'error'
    elif any(kw in content_lower for kw in warning_keywords):
        return 'warning'
    elif any(kw in content_lower for kw in debug_keywords):
        return 'debug'
    else:
        return 'info'


def convert_print_to_logger(line: str) -> str:
    """
    Convierte una línea con print() a logger call

    Args:
        line: Línea de código con print()

    Returns:
        Línea modificada con logger
    """
    # Pattern para detectar print statements
    # Captura: indentación, print(...), y contenido
    pattern = r'^(\s*)print\((.*)\)(.*)$'

    match = re.match(pattern, line)
    if not match:
        return line

    indent = match.group(1)
    content = match.group(2)
    rest = match.group(3)  # Cualquier cosa después del print (raro pero posible)

    # Detectar nivel de log
    log_level = detect_log_level(content)

    # Construir nueva línea
    new_line = f"{indent}logger.{log_level}({content}){rest}\n"

    return new_line


def add_logger_import(content: str) -> str:
    """
    Agrega el import del logger si no existe

    Args:
        content: Contenido completo del archivo

    Returns:
        Contenido con import agregado
    """
    # Verificar si ya existe
    if 'from app.utils.logger_config import get_logger' in content:
        return content

    if 'logger = get_logger(' in content:
        return content

    # Buscar donde insertar (después de los imports)
    lines = content.split('\n')

    # Encontrar la última línea de imports
    last_import_idx = 0
    for i, line in enumerate(lines):
        if line.strip().startswith(('import ', 'from ')):
            last_import_idx = i

    # Insertar después del último import
    insert_idx = last_import_idx + 1

    # Agregar líneas de logger
    logger_lines = [
        '',
        '# ========== LOGGING CONFIGURATION ==========',
        'from app.utils.logger_config import get_logger',
        'logger = get_logger(__name__)',
        '# ===========================================',
        ''
    ]

    # Insertar
    for offset, logger_line in enumerate(logger_lines):
        lines.insert(insert_idx + offset, logger_line)

    return '\n'.join(lines)


def convert_file(file_path: Path, dry_run: bool = False) -> dict:
    """
    Convierte un archivo completo

    Args:
        file_path: Ruta del archivo
        dry_run: Si True, solo muestra cambios sin escribir

    Returns:
        Dict con estadísticas de conversión
    """
    if not file_path.exists():
        return {"error": f"File not found: {file_path}"}

    # Leer archivo
    with open(file_path, 'r', encoding='utf-8') as f:
        original_content = f.read()

    lines = original_content.split('\n')

    # Convertir cada línea
    converted_lines = []
    stats = {
        'total_prints': 0,
        'info': 0,
        'error': 0,
        'warning': 0,
        'debug': 0
    }

    for line in lines:
        if 'print(' in line and not line.strip().startswith('#'):
            stats['total_prints'] += 1
            converted = convert_print_to_logger(line)

            # Contar por nivel
            if 'logger.info' in converted:
                stats['info'] += 1
            elif 'logger.error' in converted:
                stats['error'] += 1
            elif 'logger.warning' in converted:
                stats['warning'] += 1
            elif 'logger.debug' in converted:
                stats['debug'] += 1

            converted_lines.append(converted.rstrip('\n'))
        else:
            converted_lines.append(line)

    new_content = '\n'.join(converted_lines)

    # Agregar import del logger
    new_content = add_logger_import(new_content)

    # Escribir archivo o mostrar diff
    if dry_run:
        print(f"\n{'='*60}")
        print(f"DRY RUN: {file_path}")
        print(f"{'='*60}")
        print(f"Total prints to convert: {stats['total_prints']}")
        print(f"  - info:    {stats['info']}")
        print(f"  - error:   {stats['error']}")
        print(f"  - warning: {stats['warning']}")
        print(f"  - debug:   {stats['debug']}")
        print(f"\nRun without --dry-run to apply changes")
    else:
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(new_content)
        print(f"✅ Converted {file_path}: {stats['total_prints']} prints replaced")

    return stats


def main():
    """Main entry point"""
    if len(sys.argv) < 2:
        print("Usage: python convert_prints_to_logger.py <file.py> [--dry-run]")
        print("\nOptions:")
        print("  --dry-run    Show what would be changed without modifying files")
        print("\nExamples:")
        print("  python convert_prints_to_logger.py main.py --dry-run")
        print("  python convert_prints_to_logger.py app/futures.py")
        sys.exit(1)

    file_path = Path(sys.argv[1])
    dry_run = '--dry-run' in sys.argv

    stats = convert_file(file_path, dry_run=dry_run)

    if 'error' in stats:
        print(f"❌ {stats['error']}")
        sys.exit(1)


if __name__ == '__main__':
    main()
