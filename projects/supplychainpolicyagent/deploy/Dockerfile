# 部署镜像：全功能（角色由 SERVICE_ROLE 环境变量控制）
#   build context = 仓库根目录:  docker build -f deploy/Dockerfile .
FROM python:3.11-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app
# sentence-transformers / pymilvus 运行时依赖
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgomp1 curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# pymysql 连 MySQL 8.x (caching_sha2_password) 需要 cryptography 做 RSA 认证
RUN pip install --no-cache-dir cryptography==43.0.1

COPY config ./config
COPY db ./db
COPY api ./api
COPY core ./core
COPY schemas ./schemas
COPY service ./service
COPY data ./data
COPY deploy/mysql-init ./deploy/mysql-init
COPY main.py ./

# 前端构建产物（浏览器端可用；不存在时不覆盖）
COPY frontend/dist ./frontend/dist

EXPOSE 8080 8600

# 默认角色：quote（历史兼容）；policy/embed 等通过 compose 覆盖 command/env
ENV SERVICE_ROLE=quote
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"]