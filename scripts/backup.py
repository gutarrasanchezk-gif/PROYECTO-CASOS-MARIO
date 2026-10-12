"""Create a consistent SQLite backup without overwriting an existing file."""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
import tempfile
from contextlib import closing
from pathlib import Path


def backup_database(source: Path, destination: Path) -> Path:
    source = source.expanduser().resolve()
    destination = destination.expanduser().resolve()
    if not source.is_file() or source.stat().st_size == 0:
        raise ValueError("La base de origen no existe o está vacía.")
    if destination == source:
        raise ValueError("El respaldo debe tener una ruta distinta a la base de origen.")
    if destination.exists():
        raise ValueError("El destino ya existe; elige otro nombre para conservarlo.")

    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary_fd, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent
    )
    os.close(temporary_fd)
    temporary = Path(temporary_name)
    try:
        # mode=ro prevents a typo in the source path from creating a new database.
        # SQLite's backup API includes committed changes from an active WAL.
        with closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True, timeout=30)) as original:
            with closing(sqlite3.connect(temporary, timeout=30)) as copied:
                original.backup(copied, pages=128, sleep=0.05)
                if copied.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
                    raise ValueError("La verificación del respaldo detectó un error.")
                copied.commit()
        # Hard linking publishes the complete backup atomically and refuses to
        # overwrite a destination another process may have created meanwhile.
        os.link(temporary, destination)
        return destination
    finally:
        temporary.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Respalda SQLite de forma consistente, incluso mientras el programa está en uso."
    )
    parser.add_argument("source", type=Path, help="Ruta de la base SQLite existente.")
    parser.add_argument("destination", type=Path, help="Archivo nuevo que recibirá el respaldo.")
    args = parser.parse_args()
    try:
        destination = backup_database(args.source, args.destination)
    except (ValueError, OSError, sqlite3.Error) as error:
        print(f"No se pudo crear el respaldo: {error}", file=sys.stderr)
        return 1
    print(f"Respaldo creado: {destination}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
