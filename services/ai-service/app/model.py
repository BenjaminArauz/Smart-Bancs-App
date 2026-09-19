"""
Carga y ejecución del modelo de riesgo.

El modelo (IsolationForest + scaler) se entrena offline con
`train_model.py` y se serializa a `model/risk_model.joblib`. Este
módulo solo lo carga en memoria una vez (al levantar el proceso) y
expone una función de scoring síncrona y barata en CPU (microsegundos
por transacción) — clave para que el servicio de IA pueda responder
rápido y no se vuelva, a su vez, un cuello de botella para quien lo
consuma.
"""

from __future__ import annotations

from pathlib import Path

import joblib
import numpy as np

MODEL_PATH = Path(__file__).parent.parent / "model" / "risk_model.joblib"

# Umbrales de negocio para traducir el score continuo [0, 1] a una
# categoría accionable por el sistema de transacciones (p. ej. para
# decidir si se marca la transacción para revisión manual).
LOW_THRESHOLD = 0.4
MEDIUM_THRESHOLD = 0.7


class RiskModel:
    def __init__(self, model_path: Path = MODEL_PATH):
        if not model_path.exists():
            raise FileNotFoundError(
                f"No se encontró el artefacto del modelo en {model_path}. "
                "Ejecuta `python train_model.py` antes de levantar el servicio."
            )
        bundle = joblib.load(model_path)
        self._model = bundle["model"]
        self._scaler = bundle["scaler"]
        self._feature_order = bundle["feature_order"]
        self.model_version = bundle["model_version"]

    def score(self, features: dict) -> tuple[float, str]:
        ordered = np.array([[features[name] for name in self._feature_order]], dtype=float)
        raw_score = -self._model.decision_function(ordered).reshape(-1, 1)
        risk_score = float(np.clip(self._scaler.transform(raw_score)[0, 0], 0.0, 1.0))
        return risk_score, self._risk_level(risk_score)

    @staticmethod
    def _risk_level(risk_score: float) -> str:
        if risk_score < LOW_THRESHOLD:
            return "low"
        if risk_score < MEDIUM_THRESHOLD:
            return "medium"
        return "high"
