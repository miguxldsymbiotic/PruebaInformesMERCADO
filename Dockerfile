FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1

# Install system dependencies for WeasyPrint, Kaleido, and Playwright
RUN apt-get update && apt-get install -y \
    libnss3 \
    libnspr4 \
    libatk1.0-0 \
    libatk-bridge2.0-0 \
    libcups2 \
    libdrm2 \
    libxkbcommon0 \
    libxcomposite1 \
    libxdamage1 \
    libxext6 \
    libxfixes3 \
    libxrandr2 \
    libgbm1 \
    libasound2 \
    libpangocairo-1.0-0 \
    libpango-1.0-0 \
    libharfbuzz0b \
    libfribidi0 \
    libcairo2 \
    libgdk-pixbuf-2.0-0 \
    shared-mime-info \
    fonts-liberation \
    && rm -rf /var/lib/apt/lists/*

# Setup non-root user for Hugging Face (UID 1000)
RUN useradd -m -u 1000 user
USER user
ENV HOME=/home/user \
    PATH=/home/user/.local/bin:$PATH

WORKDIR $HOME/app

# Copy requirements and install
COPY --chown=user requirements_deploy.txt .
RUN pip install --no-cache-dir --user -r requirements_deploy.txt

# Install Playwright browsers (specifically chromium for the reports)
RUN python -m playwright install chromium

# Copy application code
COPY --chown=user . .

# Azure Container Apps uses port 8080 by default (configurable)
EXPOSE 8080

# We point to app/app.py as the entry point
CMD ["python", "-m", "shiny", "run", "app/app.py", "--host", "0.0.0.0", "--port", "8080"]
