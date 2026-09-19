FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY train_model.py .
COPY app ./app

# El modelo se entrena en build time: en un proyecto real este artefacto
# vendría de un pipeline de CI/CD o de un registro de modelos (ver 3.3.2),
# pero para este despliegue self-contained alcanza con generarlo aquí.
RUN python train_model.py

EXPOSE 8001

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8001"]
