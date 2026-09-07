FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 WATER_DB=/data/water.db
WORKDIR /app
COPY server/requirements.txt .
RUN pip install --no-cache-dir --index-url https://mirrors.aliyun.com/pypi/simple/ -r requirements.txt
COPY server/app.py server/ws_endpoint.py ./
USER 10001:10001
HEALTHCHECK --interval=10s --timeout=3s --start-period=10s --retries=3 CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8790/water/api/health', timeout=2)"
CMD ["python", "app.py"]
