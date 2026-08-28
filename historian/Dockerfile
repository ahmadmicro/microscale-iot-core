# Historian image (sink + API — service command chosen in compose).
# Build from the REPO ROOT:  docker build -f historian/Dockerfile -t iotcore-historian .
FROM python:3.11-slim

WORKDIR /app
COPY sdk/ sdk/
COPY historian/ historian/
RUN pip install --no-cache-dir -r historian/requirements.txt
ENV PYTHONUNBUFFERED=1

CMD ["uvicorn", "historian.api:app", "--host", "0.0.0.0", "--port", "8090"]
