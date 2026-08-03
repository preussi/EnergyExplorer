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
# Per-user dataset sessions (a few KB each: the recipe to regenerate the cloud,
# plus the polytope itself for uploads). Mount this to keep sessions across
# container rebuilds — without a mount they live only as long as the container.
ENV SESSION_DIR=/app/sessions

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
