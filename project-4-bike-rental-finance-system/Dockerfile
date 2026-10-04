# The demo dashboard: synthetic data, no Google, no keys, no login.
#   docker build -t fleet-ledger-demo .
#   docker run --rm -p 8000:8000 fleet-ledger-demo
# On Render the platform sets PORT; locally it defaults to 8000.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    DATA_SOURCE=demo \
    HOST=0.0.0.0 \
    PORT=8000

WORKDIR /app
COPY pyproject.toml ./
COPY src ./src
RUN pip install ".[dashboard]"

COPY dashboard ./dashboard
COPY demo ./demo

RUN useradd --system --no-create-home app
USER app

EXPOSE 8000
CMD ["sh", "dashboard/start.sh", "--demo"]
