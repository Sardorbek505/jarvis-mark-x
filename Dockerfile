FROM python:3.11-slim

WORKDIR /app

# Install dependencies first (better layer caching)
COPY telegram_bot/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

# Copy project
COPY . .

# Config directory (mounted as volume in production)
RUN mkdir -p /app/config

# Run the webhook app (bot + Mini App + WebSocket) on $PORT.
# Portable across Koyeb / Fly / Railway / Render — each injects $PORT.
CMD ["sh", "-c", "python -m uvicorn telegram_bot.render_app:app --host 0.0.0.0 --port ${PORT:-8000}"]
