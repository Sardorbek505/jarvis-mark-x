FROM python:3.11-slim

WORKDIR /app

# Install dependencies first (better layer caching)
COPY telegram_bot/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

# Copy project
COPY . .

# Config directory (mounted as volume in production)
RUN mkdir -p /app/config

# Default: start both bot and miniapp server
CMD ["sh", "-c", "python -m telegram_bot.bot"]
