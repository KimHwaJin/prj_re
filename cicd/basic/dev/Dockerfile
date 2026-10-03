# 사내 플랫폼 기반 이미지는 유지한다. Python 3.11이 필요하다.
FROM harbor-srep01.skhynix.com/aix-harbor/python-sqlite3-oracle:gaia-f22.0-v0.3
ENV WORK_DIR=/app \
    LD_LIBRARY_PATH=/home/instant_client/instantclient_23_9 \
    APP_ENV=dev \
    PYTHONPATH=/app/src:/app \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt ./
# requirements.txt는 pyproject.toml + uv.lock에서 생성. SDK는 사내 base에서 제공.
RUN python -c "import sys; assert sys.version_info[:2] == (3, 11), 'Python 3.11 base image required'" && \
    python -m pip install --no-cache-dir -r requirements.txt
COPY . ./
EXPOSE 5000
ENTRYPOINT ["python", "app.py"]
