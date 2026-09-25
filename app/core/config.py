# lee .env, paths, límites, etc.

import os
from pathlib import Path
from pydantic import BaseModel

class Settings(BaseModel):
    app_host: str = os.getenv("APP_HOST", "127.0.0.1")
    app_port: int = int(os.getenv("APP_PORT", "8000"))

    data_dir: Path = Path(os.getenv("DATA_DIR", "./data"))
    uploads_dir: Path = Path(os.getenv("UPLOADS_DIR", "./data/uploads"))
    outputs_dir: Path = Path(os.getenv("OUTPUTS_DIR", "./data/outputs"))
    # Archivos de trabajo por job mientras se procesa (p. ej. el ZIP descomprimido);
    # se borran al terminar el job. TEMP_DIR es el nombre anterior de la variable.
    processing_dir: Path = Path(
        os.getenv("PROCESSING_DIR") or os.getenv("TEMP_DIR") or "./data/processing"
    )

    # Tamaño máximo por archivo subido en la webapp; 0 = sin límite.
    max_upload_mb: int = int(os.getenv("MAX_UPLOAD_MB", "500"))


settings = Settings()


# README.txt que se deja en cada carpeta, para quien administre el servidor
# y mire el disco sin conocer la app.
_READMES = {
    "uploads_dir": (
        "Parasight-AMA: archivos originales subidos por los usuarios, una carpeta por job.\n"
        "NO BORRAR a mano: sin esto no se puede reprocesar un job. Ver ../README.txt.\n"
    ),
    "outputs_dir": (
        "Parasight-AMA: resultados de cada job (previews, CSV, results_<job>.zip,\n"
        "status.json/result.json). Los enlaces /jobs/<job> dependen de esto.\n"
        "NO BORRAR a mano. Ver ../README.txt.\n"
    ),
    "processing_dir": (
        "Parasight-AMA: archivos de trabajo de los jobs en curso (p. ej. ZIP\n"
        "descomprimido). La app borra processing/<job> al terminar cada job.\n"
        "Se puede borrar el contenido si no hay ningún job procesando.\n"
    ),
}


def _write_readme(directory: Path, text: str) -> None:
    path = directory / "README.txt"
    try:
        if not path.exists() or path.read_text(encoding="utf-8") != text:
            path.write_text(text, encoding="utf-8")
    except OSError:
        pass  # no es crítico (p. ej. carpeta de solo lectura)


def _data_readme() -> str:
    # Nombres reales de las carpetas (pueden venir de la configuración).
    up, out, proc = (d.name + "/" for d in (settings.uploads_dir, settings.outputs_dir, settings.processing_dir))
    width = max(len(up), len(out), len(proc)) + 2
    return (
        "Parasight-AMA: datos de la webapp.\n"
        "\n"
        f"  {up:<{width}}archivos originales subidos, una carpeta por job   -> NO BORRAR\n"
        f"  {out:<{width}}resultados (previews, CSV, ZIP), uno por job        -> NO BORRAR\n"
        f"  {proc:<{width}}archivos de trabajo de los jobs en curso; se borran solos\n"
        f"  {'':<{width}}al terminar cada job. Solo queda algo si el servicio se cortó.\n"
        "\n"
        "Para liberar espacio, borrar jobs viejos con deploy/parasight-cleanup.sh\n"
        "(borra uploads/<job> y outputs/<job> juntos), no a mano por carpeta.\n"
    )


def ensure_dirs() -> None:
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    settings.uploads_dir.mkdir(parents=True, exist_ok=True)
    settings.outputs_dir.mkdir(parents=True, exist_ok=True)
    settings.processing_dir.mkdir(parents=True, exist_ok=True)
    _write_readme(settings.data_dir, _data_readme())
    for attr, text in _READMES.items():
        _write_readme(getattr(settings, attr), text)