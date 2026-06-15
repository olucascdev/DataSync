FROM python:3.12-slim

ENV DEBIAN_FRONTEND=noninteractive

# Dependências de sistema:
# - Chrome (browser automation)
# - libGL + libglib (OpenCV/Camelot)
# - ghostscript (Camelot PDF processing)
RUN apt-get update && apt-get install -y --no-install-recommends \
    wget gnupg ca-certificates \
    fonts-liberation \
    libasound2 libatk-bridge2.0-0 libgtk-3-0 \
    libnss3 libxss1 libxtst6 xdg-utils \
    libgl1 libglib2.0-0 \
    ghostscript \
    xvfb \
    && wget -q -O - https://dl.google.com/linux/linux_signing_key.pub \
       | gpg --dearmor -o /usr/share/keyrings/google-chrome.gpg \
    && echo "deb [arch=amd64 signed-by=/usr/share/keyrings/google-chrome.gpg] \
       http://dl.google.com/linux/chrome/deb/ stable main" \
       > /etc/apt/sources.list.d/google-chrome.list \
    && apt-get update && apt-get install -y --no-install-recommends google-chrome-stable \
    && rm -rf /var/lib/apt/lists/*

ENV CHROME_BIN=/usr/bin/google-chrome-stable

WORKDIR /app
RUN mkdir -p /app/downloads /app/logs /app/data/chrome-profile

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Roda sob display virtual (Xvfb) para que o Chrome opere em modo
# não-headless. Necessário porque o Cloudflare Turnstile (captcha) na
# tela de login bloqueia navegadores headless.
CMD ["xvfb-run", "-a", "--server-args=-screen 0 1920x1080x24", "python", "main.py"]
