"""
Entrenamiento del modelo de riesgo/fraude — punto 3.3 del reto.

Genera un dataset sintético con la MISMA forma de features que produce
el ETL de la sección 3.2 (`etl/transform.py`): monto, hora del día, día
de la semana y si es fin de semana. No usamos un dataset real (no
existe uno etiquetado disponible), pero el pipeline de entrenamiento es
real y reproducible: se puede reemplazar `build_synthetic_dataset()` por
una carga de `etl/output/transactions_clean.jsonl` + una fuente de
etiquetas de fraude sin tocar el resto del script.

Modelo: IsolationForest (no supervisado). Es la elección correcta para
detección de fraude en su fase inicial porque:
  - No requiere transacciones ya etiquetadas como "fraude" (que en la
    práctica son escasísimas al día 1 de un producto nuevo).
  - Aprende la noción de "comportamiento normal" y marca como riesgosas
    las transacciones que se alejan de ese patrón (monto alto a horas
    inusuales, etc.).

El score de anomalía crudo (decision_function) se reescala a [0, 1]
con un MinMaxScaler ajustado sobre el mismo set de entrenamiento, y se
guarda junto al modelo para que el servicio de inferencia reproduzca
exactamente el mismo escalado.

Uso:
    python train_model.py
"""

from __future__ import annotations

import random
from pathlib import Path

import joblib
import numpy as np
from sklearn.ensemble import IsolationForest
from sklearn.preprocessing import MinMaxScaler

MODEL_VERSION = "risk-model-v1"
MODEL_PATH = Path(__file__).parent / "model" / "risk_model.joblib"
FEATURE_ORDER = ["amount", "hour_of_day", "day_of_week", "is_weekend"]

random.seed(42)
np.random.seed(42)


def build_synthetic_dataset(n_normal: int = 4000, n_anomalous: int = 200) -> np.ndarray:
    """Simula transacciones normales (horario/monto habitual de un banco) y
    un pequeño porcentaje de transacciones atípicas (montos altos, horas
    inusuales), imitando la proporción real de fraude en producción (<5%).
    """
    # Transacciones normales: montos bajos/medios, concentradas en horario laboral
    normal_amount = np.random.gamma(shape=2.0, scale=60.0, size=n_normal)
    normal_hour = np.clip(np.random.normal(loc=14, scale=4, size=n_normal), 0, 23).astype(int)
    normal_dow = np.random.randint(0, 7, size=n_normal)
    normal_is_weekend = (normal_dow >= 5).astype(int)

    # Transacciones atípicas: montos altos y horario de madrugada
    anomalous_amount = np.random.gamma(shape=2.0, scale=900.0, size=n_anomalous)
    anomalous_hour = np.clip(np.random.normal(loc=3, scale=2, size=n_anomalous), 0, 23).astype(int)
    anomalous_dow = np.random.randint(0, 7, size=n_anomalous)
    anomalous_is_weekend = (anomalous_dow >= 5).astype(int)

    amount = np.concatenate([normal_amount, anomalous_amount])
    hour = np.concatenate([normal_hour, anomalous_hour])
    dow = np.concatenate([normal_dow, anomalous_dow])
    is_weekend = np.concatenate([normal_is_weekend, anomalous_is_weekend])

    return np.column_stack([amount, hour, dow, is_weekend]).astype(float)


def train() -> None:
    X = build_synthetic_dataset()

    model = IsolationForest(
        n_estimators=200,
        contamination=0.05,  # asumimos ~5% de comportamiento atípico, ajustable con datos reales
        random_state=42,
    )
    model.fit(X)

    # decision_function: valores altos = normal, valores bajos = anómalo.
    # Invertimos el signo para que "risk_score" alto = más riesgo, y
    # escalamos a [0, 1] para que sea interpretable por el API/negocio.
    raw_scores = -model.decision_function(X).reshape(-1, 1)
    scaler = MinMaxScaler()
    scaler.fit(raw_scores)

    MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(
        {
            "model": model,
            "scaler": scaler,
            "feature_order": FEATURE_ORDER,
            "model_version": MODEL_VERSION,
        },
        MODEL_PATH,
    )
    print(f"Modelo entrenado y guardado en {MODEL_PATH} (version={MODEL_VERSION})")


if __name__ == "__main__":
    train()
