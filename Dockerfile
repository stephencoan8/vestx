FROM python:3.11-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application
COPY . .

# Create directory for database
RUN mkdir -p instance

# Expose port
EXPOSE 5000

# Set environment variables
ENV FLASK_APP=main.py
ENV PYTHONUNBUFFERED=1

# Run application
CMD ["gunicorn", "main:app", "-k", "gthread", "--workers", "2", "--threads", "4", "--timeout", "120", "-b", "0.0.0.0:5000"]
