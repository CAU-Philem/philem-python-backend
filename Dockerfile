FROM python:3.11-slim

# 시스템 패키지
RUN apt-get update && apt-get install -y \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 의존성
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 코드 복사
COPY . .

ENV PYTHONUNBUFFERED=1

# 기본 엔트리포인트는 덮어쓸 거라 CMD만 둠
CMD ["bash"]