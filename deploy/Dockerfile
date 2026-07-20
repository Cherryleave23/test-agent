# 端侧 1 企业 1 实例镜像（MOD-deploy，G6）
FROM python:3.11-slim
WORKDIR /app
COPY deploy/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY src/ ./src/
COPY deploy/ ./deploy/
COPY scripts/ ./scripts/
ENV PYTHONPATH=/app
CMD ["python", "src/main.py", "deploy/enterprise.yaml"]
