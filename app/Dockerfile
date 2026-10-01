FROM python:3.12-slim

# Répertoire de travail
WORKDIR /app

# Dépendances système minimales
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copier les dépendances Python en premier pour profiter du cache Docker
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copier le code de l'application
COPY . .

# Créer les répertoires de cache et de tokens, et un utilisateur non-root.
# chown limité à ces trois répertoires : le code applicatif (COPY . .) reste
# appartenant à root et non modifiable par l'utilisateur d'exécution.
RUN mkdir -p /app/.cache /app/.garmin /app/.data && \
    useradd -m appuser && \
    chown -R appuser /app/.cache /app/.garmin /app/.data

COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

USER appuser

# Exposer le port Streamlit
EXPOSE 8501

# Vérification de santé
HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
    CMD curl -f http://localhost:8501/_stcore/health || exit 1

ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]
CMD ["streamlit", "run", "main.py", \
     "--server.port=8501", \
     "--server.address=0.0.0.0", \
     "--server.headless=true", \
     "--browser.gatherUsageStats=false"]
