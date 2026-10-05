from __future__ import annotations
from pathlib import Path
from typing import Union
import numpy as np
import tifffile
from czifile import CziFile


PathLike = Union[str, Path]
IMAGE_EXTS = {".tif", ".tiff", ".czi"}

def load_image(path: PathLike) -> np.ndarray:
    """
    Carga una imagen desde archivo TIFF o CZI y devuelve un array NumPy crudo.

    Args:
        path: Ruta al archivo de imagen (str o Path).

    Returns:
        Array NumPy con la imagen cargada. Las dimensiones dependen del archivo:
        - TIFF: Puede ser 2D o multidimensional; los TIFF RGB se rechazan
        - CZI: Típicamente multidimensional con canales, Z-stacks, tiempo

    Raises:
        ValueError: Si la extensión no es soportada o hay error de carga.
        FileNotFoundError: Si el archivo no existe.
        Exception: Errores específicos de las librerías de carga.
    """
    p = Path(path)
    ext = p.suffix.lower()

    if ext not in IMAGE_EXTS:
        raise ValueError(f"Extension no soportada: {ext}")

    if ext in {".tif", ".tiff"}:
        if p.name.lower().startswith("markers_counter window"):
            raise ValueError(
                "Exportacion anotada de ImageJ no compatible: usa la imagen "
                "de microscopia cruda."
            )
        with tifffile.TiffFile(str(p)) as tif:
            series = tif.series[0]
            image = series.asarray()
            axes = series.axes or ""

        # TIFF RGB exports (for example ImageJ Markers_Counter images) are
        # annotated visualizations, not the single-channel microscopy input
        # expected by the models. Treat them as invalid instead of silently
        # analysing only the red channel.
        if "S" in axes:
            samples = image.shape[axes.index("S")]
            if samples in {3, 4}:
                raise ValueError(
                    "TIFF RGB no compatible: usa la imagen de microscopia cruda "
                    "de un solo canal."
                )
        return image

    if ext == ".czi":
        with CziFile(str(p)) as czi:
            return czi.asarray()

    raise ValueError(f"No se pudo cargar el archivo: {p}")

def extract_2d_frame(
    arr: np.ndarray,
    channel: int = 0,
    z: int = 0,
    t: int = 0,
) -> np.ndarray:
    """
    Extrae un frame 2D (Y, X) desde un array multidimensional de imagen.

    Diseñado para manejar formatos comunes de microscopía que pueden tener
    múltiples dimensiones: canales (C), profundidad Z, tiempo (T), etc.
    Intenta inferir automáticamente el layout dimensional y seleccionar
    el frame apropiado.

    Args:
        arr: Array NumPy multidimensional cargado desde imagen.
        channel: Índice del canal a seleccionar (default: 0).
        z: Índice Z-stack a seleccionar (default: 0).
        t: Índice de tiempo a seleccionar (default: 0).

    Returns:
        Array 2D (Y, X) listo para procesamiento. Si ya es 2D, lo devuelve tal cual.

    Notes:
        - Asume layouts comunes: (Y,X,C), (C,Y,X), (Z,Y,X), (T,Z,C,Y,X), etc.
        - Para arrays con >3 dimensiones, recorta iterativamente al índice 0
          hasta llegar a 3D, luego aplica lógica de 3D.
        - Usa np.squeeze() para eliminar dimensiones singleton.
    """
    x = np.asarray(arr)
    x = np.squeeze(x)

    if x.ndim == 2:
        return x

    if x.ndim == 3:
        # (Y, X, C) - último eje pequeño = canales
        if x.shape[-1] <= 4:
            return x[:, :, channel if channel < x.shape[-1] else 0]
        # (C, Y, X) - primer eje pequeño = canales
        if x.shape[0] <= 4:
            return x[channel if channel < x.shape[0] else 0, :, :]
        # (Z, Y, X) o similar - asume primer eje = Z
        return x[z if z < x.shape[0] else 0, :, :]

    # Para ndim >= 4: recorta ejes "extra" al índice 0 hasta llegar a 3D o 2D
    while x.ndim > 3:
        x = x[0]

    return extract_2d_frame(x, channel=channel, z=z, t=t)


def load_image_2d(path: PathLike) -> np.ndarray:
    """
    Carga un archivo de imagen y devuelve un frame 2D listo para el pipeline.

    Combina load_image() y extract_2d_frame() para proporcionar una interfaz
    simple que siempre devuelve una imagen 2D (Y, X) independientemente del
    formato multidimensional del archivo original.

    Args:
        path: Ruta al archivo de imagen (str o Path).

    Returns:
        Array 2D NumPy (Y, X) con dtype apropiado para procesamiento.
        Típicamente uint16 para imágenes de microscopía.

    Raises:
        ValueError: Si la extensión no es soportada o hay error de carga.
        FileNotFoundError: Si el archivo no existe.
    """
    raw = load_image(path)
    img2d = extract_2d_frame(raw)
    if img2d.ndim != 2 or img2d.size == 0:
        raise ValueError(f"La imagen no contiene un frame 2D valido: {Path(path).name}")
    if not np.issubdtype(img2d.dtype, np.number):
        raise ValueError(f"La imagen no contiene datos numericos: {Path(path).name}")
    return img2d
