FROM python:3.12-slim

WORKDIR /app

# Install dependencies first for better layer caching.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the project.
COPY . .

# Default: print the analysis report. Override to write deliverables, e.g.
#   docker run --rm -v "$PWD/deliverables:/app/deliverables" IMAGE --deliverables
ENTRYPOINT ["python", "-m", "supplynet"]
