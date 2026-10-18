FROM python:3.11-slim

# system deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    wget \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

# copy project
COPY . /app

ENV PYTHONUNBUFFERED=1
ENV LOG_DIR=/app/logs

CMD ["bash","-lc","conda activate tg_algo && python multi_agent_orchestrator.py"]
