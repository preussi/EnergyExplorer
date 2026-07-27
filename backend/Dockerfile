FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

# Bake the (small ~46 KB) polytope into the image so the container is
# self-contained on Kubernetes (no data volume). Samples are generated from the
# polytope on demand — no large samples file is shipped.
COPY data ./data

ENV DATA_DIR=/app/data

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
