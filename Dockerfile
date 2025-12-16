FROM python:3.11-slim

# ---- 시스템 패키지 + Chrome 실행에 필요한 라이브러리 ----
RUN apt-get update && apt-get install -y \
    build-essential \
    wget \
    unzip \
    gnupg \
    curl \
    ca-certificates \
    fonts-liberation \
    libnss3 \
    libatk-bridge2.0-0 \
    libatk1.0-0 \
    libcups2 \
    libdrm2 \
    libxkbcommon0 \
    libxcomposite1 \
    libxdamage1 \
    libxfixes3 \
    libxrandr2 \
    libgbm1 \
    libasound2 \
    libx11-6 \
    libx11-xcb1 \
    libxcb1 \
    libxext6 \
    libxi6 \
    libxtst6 \
    libglib2.0-0 \
    libgtk-3-0 \
    --no-install-recommends \
 && rm -rf /var/lib/apt/lists/*

# ---- Google Chrome 설치 ----
RUN wget -q -O - https://dl.google.com/linux/linux_signing_key.pub \
    | gpg --dearmor > /usr/share/keyrings/google-linux-keyring.gpg \
 && echo "deb [arch=amd64 signed-by=/usr/share/keyrings/google-linux-keyring.gpg] http://dl.google.com/linux/chrome/deb/ stable main" \
    > /etc/apt/sources.list.d/google-chrome.list \
 && apt-get update \
 && apt-get install -y google-chrome-stable \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# ---- 의존성 ----
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ---- 코드 복사 ----
COPY . .

ENV PYTHONUNBUFFERED=1

# 기본 엔트리포인트는 compose에서 덮어쓸 거라 CMD만 둠
CMD ["bash"]
