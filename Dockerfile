FROM python:3.14-slim
WORKDIR /app
COPY requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY catalog_app ./catalog_app
RUN useradd --create-home studio && mkdir /app/data && chown studio:studio /app/data
USER studio
EXPOSE 8023
CMD ["python", "-m", "uvicorn", "catalog_app.main:app", "--host", "0.0.0.0", "--port", "8023"]
