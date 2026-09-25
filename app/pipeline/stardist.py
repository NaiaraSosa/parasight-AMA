from __future__ import annotations
import threading
import numpy as np
from csbdeep.data import PercentileNormalizer
from stardist.models import StarDist2D

_MODEL = None
_MODEL_LOCK = threading.Lock()
_MODEL_NAME = "2D_versatile_fluo"

def _get_stardist_model(model_name: str = _MODEL_NAME):
    """
    Obtiene o crea el modelo StarDist.

    StarDist es un modelo de deep learning especializado en segmentación
    de objetos pequeños y redondos (núcleos, parásitos, etc.).
    Se carga una sola vez para eficiencia.

    Returns:
        Modelo StarDist2D cargado y listo para usar.
    """
    global _MODEL
    if _MODEL is None:
        with _MODEL_LOCK:
            if _MODEL is None:
                _MODEL = StarDist2D.from_pretrained(model_name)
    return _MODEL


def segment_parasites(
    img2d: np.ndarray,
    prob_thresh: float = 0.5,
    nms_thresh: float = 0.3,
    model_name: str = _MODEL_NAME,
):
    """
    Segmenta parásitos en una imagen 2D usando el modelo StarDist.

    StarDist está optimizado para detectar objetos pequeños y redondos
    como parásitos intracelulares. Usa un enfoque basado en distancias
    radiales para contornos precisos.

    Args:
        img2d: Imagen 2D (Y, X) con parásitos para segmentar.
        prob_thresh: Umbral de probabilidad (default: 0.5). Valores más altos
            requieren mayor confianza para detectar parásitos.
        nms_thresh: Umbral para Non-Maximum Suppression (default: 0.3).
            Controla solapamiento entre detecciones.
        model_name: Nombre del modelo pre-entrenado a usar.

    Returns:
        Tuple de (labels, details):
        - labels: Array 2D int32 con máscaras. Cada parásito tiene ID único.
        - details: Dict con información detallada de cada detección.
    """
    model = _get_stardist_model(model_name=model_name)
    normalizer = PercentileNormalizer()
    labels, details = model.predict_instances(
        img2d,
        prob_thresh=prob_thresh,
        nms_thresh=nms_thresh,
        normalizer=normalizer,
    )
    return labels.astype(np.int32, copy=False), details
