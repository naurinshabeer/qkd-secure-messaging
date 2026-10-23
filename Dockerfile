FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt ./
RUN python -m pip install --no-cache-dir -r requirements.txt
COPY app.py ./
COPY qkd_demo ./qkd_demo
COPY web ./web
CMD ["python", "-m", "qkd_demo.coordinator", "--host", "0.0.0.0"]
