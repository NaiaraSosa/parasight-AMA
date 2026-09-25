from __future__ import annotations
import threading
import numpy as np
from cellpose import models, io as cellpose_io

cellpose_io.logger_setup()  # configura logging para Cellpose

_MODEL = None
_MODEL_LOCK = threading.Lock()

def _get_cellpose_model():
    """
    Obtiene o crea el modelo Cellpose.

    Cellpose es un modelo de deep learning para segmentación de células.
    Se carga una sola vez para eficiencia.

    Returns:
        Modelo Cellpose cargado y listo para usar.
    """
    global _MODEL
    if _MODEL is None:
        with _MODEL_LOCK:
            if _MODEL is None:
                _MODEL = models.CellposeModel(gpu=True)
    return _MODEL


def segment_cells(
    img2d: np.ndarray,
    flow_threshold: float = 0.0,    
    cellprob_threshold: float = 0.0,
    min_size: int = 200,
    tile_norm_blocksize: int = 0.0,
) -> np.ndarray:
    """
    Segmenta células en una imagen 2D usando el modelo Cellpose.

    Cellpose es un algoritmo de deep learning pre-entrenado para identificar
    y segmentar células individuales en imágenes de microscopía. Usa un
    modelo general que funciona bien con diversos tipos de células.

    Args:
        img2d: Imagen 2D (Y, X) con células para segmentar.
        flow_threshold: Umbral para flujos (default: 0.0). Valores más altos
            hacen la segmentación más conservadora (menos células detectadas).
        cellprob_threshold: Umbral para probabilidad de célula (default: 0.0).
            Valores más altos requieren mayor confianza para detectar células.
        tile_norm_blocksize: Tamaño de bloque para normalización por tiles
            (default: 0 = sin tiling). Útil para imágenes grandes.
        gpu: Usar GPU si disponible (default: True).

    Returns:
        Array 2D int32 con máscaras de segmentación. Cada célula tiene un
        ID único (1, 2, 3, ...), fondo = 0.
    """
    model = _get_cellpose_model()

    masks, flows, styles= model.eval(
        img2d,
        flow_threshold=flow_threshold,
        cellprob_threshold=cellprob_threshold,
        min_size=min_size,
        normalize={"tile_norm_blocksize": tile_norm_blocksize},
    )
    return masks.astype(np.int32, copy=False)