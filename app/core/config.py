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
    temp_dir: Path = Path(os.getenv("TEMP_DIR", "./data/temp"))

    # Tamaño máximo por archivo subido en la webapp; 0 = sin límite.
    max_upload_mb: int = int(os.getenv("MAX_UPLOAD_MB", "500"))


settings = Settings()


def ensure_dirs() -> None:
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    settings.uploads_dir.mkdir(parents=True, exist_ok=True)
    settings.outputs_dir.mkdir(parents=True, exist_ok=True)
    settings.temp_dir.mkdir(parents=True, exist_ok=True)