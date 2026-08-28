# Edge-gateway image: the SDK + all bridges/adapters in one small container.
# Build from the REPO ROOT:  docker build -f deploy/edge/Dockerfile -t iotcore-edge .
FROM python:3.11-slim

WORKDIR /app
COPY sdk/ sdk/
COPY adapters/ adapters/
COPY schema/ schema/
RUN pip install --no-cache-dir -r adapters/requirements.txt

# spool dir for disk-backed store-and-forward — mount a volume here
RUN mkdir -p /var/spool/iotcore
ENV PYTHONUNBUFFERED=1

# which bridge/adapter to run is chosen per service in docker-compose.yml
CMD ["python", "-m", "adapters.simulator"]
