# 使用官方 Python 映像作為基礎
FROM python:3.9-slim

# 設定工作目錄
WORKDIR /app

# 將 requirements.txt 複製到工作目錄
COPY requirements.txt .

# 安裝依賴套件
RUN pip install --no-cache-dir -r requirements.txt

# 將專案中的所有檔案複製到工作目錄
COPY . .

# 新增：更改 /app 目錄的權限，使其對所有使用者可寫
RUN chmod -R 777 /app

# 設定環境變數 PORT (Hugging Face Spaces 通常會提供這個環境變數)
# 您的 app.py 應該從 os.getenv("PORT", "7860") 讀取端口
# 如果您的 app.py 中寫死了端口，例如 5000，這裡可以改為 EXPOSE 5000
# 但最好是讓 Flask 監聽 $PORT
ENV PORT 7860

# 開放應用程式運行的端口 (與上面 ENV PORT 一致，或者您 Flask 監聽的端口)
EXPOSE 7860

# 執行應用程式的指令
# 確保您的 app.py 中有 if __name__ == "__main__": app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 7860)))
CMD ["python", "app.py"]