# PartnerFlow Lab, hosted: one container runs the API (private) and the dashboard (public).
FROM python:3.11-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
RUN mkdir -p data && chmod +x deploy/start_hosted.sh
EXPOSE 7860
CMD ["sh", "deploy/start_hosted.sh"]
