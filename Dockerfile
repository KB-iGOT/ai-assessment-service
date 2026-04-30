FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    build-essential \
    libpq-dev \
    libpango-1.0-0 \
    libpangoft2-1.0-0 \
    libjpeg62-turbo \
    libopenjp2-7 \
    libffi-dev \
    libgobject-2.0-0 \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

# Copy packaging files + source BEFORE pip install
COPY pyproject.toml .
COPY src ./src
COPY README.md .

# Install the app
RUN pip install --no-cache-dir .

# Copy remaining files (scripts, ui, etc.)
COPY . .

# Create directory for course data
RUN mkdir -p interactive_courses_data

# Expose port for FastAPI
EXPOSE 8000

ENV PYTHONPATH=/app/src

CMD ["uvicorn", "assessment.api:app", "--host", "0.0.0.0", "--port", "8000"]

