# 標準函式庫導入
import json
import logging
import os
import sqlite3
import tempfile
from io import BytesIO

# 第三方套件導入
from dotenv import load_dotenv
from flask import Flask, request, abort, send_from_directory
import google.generativeai as genai
# --- Google Drive API 相關導入 ---
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload
# --- END Google Drive API 相關導入 ---
from linebot.v3.exceptions import InvalidSignatureError
from linebot.v3.messaging import (
    ApiClient,
    Configuration,
    MessagingApi,
    MessagingApiBlob
)
from linebot.v3.messaging.models import (
    FlexBubble,
    FlexBox,
    FlexButton,
    FlexMessage,
    FlexText,
    ImageMessage,
    LocationAction,
    QuickReply,
    QuickReplyItem,
    ReplyMessageRequest,
    TextMessage,
    URIAction
)
from linebot.v3.webhook import WebhookHandler, WebhookParser
from linebot.v3.webhooks import (
    ImageMessageContent,
    MessageEvent,
    TextMessageContent
)
from PIL import Image
import requests

# 本地應用程式/函式庫特定導入 (若有)

load_dotenv()

app = Flask(__name__)

# 從環境變數讀取 LINE Bot 設定
LINE_CHANNEL_SECRET = os.environ.get("LINE_CHANNEL_SECRET")
LINE_CHANNEL_ACCESS_TOKEN = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN")

if not LINE_CHANNEL_SECRET or not LINE_CHANNEL_ACCESS_TOKEN:
    raise RuntimeError("Missing essential environment variables")

print(f"LINE_CHANNEL_SECRET: {LINE_CHANNEL_SECRET}")
print(f"LINE_CHANNEL_ACCESS_TOKEN: {LINE_CHANNEL_ACCESS_TOKEN}")
#print(f"GOOGLE_MAP_API_KEY: {GOOGLE_MAP_API_KEY}")

# --- 資料庫路徑設定 ---
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_FILENAME = "medicine.db"
DB_PATH = os.path.join(BASE_DIR, DB_FILENAME) # 確保 DB_PATH 指向容器內的預期路徑
# --- END 資料庫路徑設定 ---

# --- Google Drive 下載函式 ---
def download_db_from_google_drive():
    print("Attempting to download database from Google Drive...")
    google_creds_json_str = os.getenv("GOOGLE_CREDENTIALS_JSON")
    file_id = os.getenv("GOOGLE_DRIVE_FILE_ID")

    if not google_creds_json_str:
        print("Error: GOOGLE_CREDENTIALS_JSON secret not found.")
        return False
    if not file_id:
        print("Error: GOOGLE_DRIVE_FILE_ID secret not found.")
        return False

    try:
        # 將 JSON 字串轉換為字典
        creds_info = json.loads(google_creds_json_str)
        # 從服務帳戶資訊建立憑證
        creds = service_account.Credentials.from_service_account_info(
            creds_info,
            scopes=['https://www.googleapis.com/auth/drive.readonly'] # 只需要唯讀權限
        )
        drive_service = build('drive', 'v3', credentials=creds)

        request_dl = drive_service.files().get_media(fileId=file_id)
        # fh = io.BytesIO() # 記憶體中處理
        # downloader = MediaIoBaseDownload(fh, request_dl)
        
        # 直接寫入檔案
        with open(DB_PATH, 'wb') as fh:
            downloader = MediaIoBaseDownload(fh, request_dl)
            done = False
            while done is False:
                status, done = downloader.next_chunk()
                print(f"Download {int(status.progress() * 100)}%.")
        print(f"Database '{DB_FILENAME}' downloaded successfully to '{DB_PATH}'.")
        return True
    except Exception as e:
        print(f"Error downloading database from Google Drive: {e}")
        return False
# --- END Google Drive 下載函式 ---

# --- 在應用程式啟動時執行資料庫下載 ---
DOWNLOAD_SUCCESS = False # 初始化
# 檢查是否在 Hugging Face Space 中運行，或密鑰是否普遍可用
# 在 HF Spaces 中，密鑰是環境變數。在本地，會使用 .env。
if os.getenv("GOOGLE_CREDENTIALS_JSON") and os.getenv("GOOGLE_DRIVE_FILE_ID"):
    print("Found Google Drive credentials, attempting download...")
    DOWNLOAD_SUCCESS = download_db_from_google_drive()
else:
    print("Warning: GOOGLE_CREDENTIALS_JSON or GOOGLE_DRIVE_FILE_ID not found in environment. Skipping DB download.")
    print("If running locally, ensure they are in your .env file or environment.")
    print("If on Hugging Face, ensure secrets are set in Space settings.")

if not DOWNLOAD_SUCCESS:
    print("CRITICAL: Database download failed or was skipped. The application might not function as expected if the database is required at startup.")
# --- END 應用程式啟動時執行資料庫下載 ---

configuration = Configuration(access_token=LINE_CHANNEL_ACCESS_TOKEN)
parser = WebhookParser(LINE_CHANNEL_SECRET)
handler = WebhookHandler(LINE_CHANNEL_SECRET)

genai.configure(api_key=GOOGLE_API_KEY)
text_system_prompt = "你是一個專業的中文藥物安全衛教AI，運行於Linebot平台，負責為台灣用戶提供用藥查詢、衛教提醒、藥品辨識與互動諮詢。所有回應必須以繁體中文呈現，語氣需保持專業、中立、清晰，嚴禁使用非正式語彙或網路用語。你的回答僅限於台灣現行合法藥品、常見用藥安全及一般衛教知識，絕不涉及診斷、處方或違法用途。遇重要藥品資訊或警語時，務必標示資料來源（如衛福部、健保署或官方藥物資料庫）；無法查證時，需說明資訊有限並提醒用戶諮詢藥師。遇到模糊、非藥物相關、或疑似緊急情境（如中毒、嚴重過敏），請直接回覆：「請儘速就醫或聯絡藥師，Linebot無法提供緊急醫療協助。」回答時，優先給出簡明結論，再補充必要說明，遇複雜內容可分點陳述，藥品名稱、注意事項及用法用量需明顯標註。若用戶詢問非本功能範圍問題，請回覆：「本Linebot僅提供藥物安全與衛生教育資訊。」並簡要列舉可查詢主題（如用藥禁忌、藥物交互作用、藥品保存方式等）。所有資訊僅反映截至2025年6月之官方資料，若遇新藥、召回或重大警訊，應提醒用戶查閱衛福部或官方藥事機構。"
chat = genai.GenerativeModel(model_name="gemini-1.5-flash")

logging.basicConfig(level=logging.INFO)
app.logger.setLevel(logging.INFO)

@app.route("/images/<filename>")
def serve_image(filename):
    return send_from_directory(static_tmp_path, filename)

@app.route("/")
def home():
    return {"message": "Line Webhook Server"}

@app.route("/callback", methods=["POST"])
def callback():
    
    # 檢查資料庫是否已成功下載 (可選，但建議)
    if not DOWNLOAD_SUCCESS and not os.path.exists(DB_PATH):
        print("Database not available, aborting callback.")
        abort(500) # 或返回一個提示用戶稍後再試的訊息

    signature = request.headers.get("X-Line-Signature", "")
    body = request.get_data(as_text=True)

    try:
        events = parser.parse(body, signature)
    except InvalidSignatureError:
        abort(400)
    except Exception as e:
        print("Webhook parse error:", e)
        abort(400)

    with ApiClient(configuration) as api_client:
        messaging_api = MessagingApi(api_client)
        blob_api = MessagingApiBlob(api_client)

        for event in events:
            if event.type == "message":
                # 文字訊息
                if event.message.type == "text":
                    user_input = event.message.text.strip()
                    print("📨 收到訊息：", user_input)

                    # AI 問答
                    if user_input.startswith("AI "):
                       
                        prompt = text_system_prompt + "\n" + user_input[3:].strip()
                        try:
                            response = chat.generate_content(prompt)
                            reply_text = response.text
                        except Exception as e:
                            reply_text = f"請重新輸入：{e}"

                        reply_request = ReplyMessageRequest(
                            reply_token=event.reply_token,
                            messages=[TextMessage(text=reply_text)]
                        )
                        messaging_api.reply_message(reply_message_request=reply_request)

                    # 查詢藥品
                    elif user_input == "查詢藥品":
                        reply_text = "請輸入藥品名稱，例如：口服感冒藥"
                        reply_request = ReplyMessageRequest(
                            reply_token=event.reply_token,
                            messages=[TextMessage(text=reply_text)]
                        )
                        messaging_api.reply_message(reply_message_request=reply_request)

                    # 查詢藥局
                    elif user_input == "查詢藥局":
                        quick_reply = QuickReply(
                            items=[QuickReplyItem(action=LocationAction(label="傳送我的位置"))]
                        )
                        reply_request = ReplyMessageRequest(
                            reply_token=event.reply_token,
                            messages=[TextMessage(text="請點選下方按鈕傳送你的位置，我才能幫你找附近藥局喔～", quick_reply=quick_reply)]
                        )
                        messaging_api.reply_message(reply_message_request=reply_request)

                    # 其他：查詢藥品資料庫
                    else:
                        medicine_name = user_input.lower()

                        try:
                            conn = sqlite3.connect(DB_PATH)
                            cursor = conn.cursor()
                            query = """
                                SELECT DISTINCT 中文品名, 英文品名, 適應症
                                FROM drugs
                                WHERE LOWER(中文品名) = ?
                                   OR LOWER(英文品名) = ?
                                LIMIT 1
                            """
                            cursor.execute(query, (medicine_name, medicine_name))
                            row = cursor.fetchone()
                            conn.close()
                            
                            if row:
                                zh_name, en_name, indication = row
                                # 這裡可根據需求回傳資料庫內容
                                result = f"藥品名稱：{zh_name}\n英文名稱：{en_name}\n適應症：{indication}"
                                # 副作用可另外查表或給固定提醒（但不交由AI生成）
                                # result += "\n副作用：...(依資料庫內容填寫)"
                                return result
                            else:
                                return "未找到相關藥品，請重新輸入"
                        except Exception:
                            return "系統繁忙，請稍後再試"

                    reply_request = ReplyMessageRequest(
                            reply_token=event.reply_token,
                            messages=[TextMessage(text=result.strip())]
                        )
                    messaging_api.reply_message(reply_message_request=reply_request)

                        # try:
                        #     conn = sqlite3.connect(DB_PATH)
                        #     cursor = conn.cursor()
                        #     query = """
                        #         SELECT DISTINCT 中文品名, 英文品名, 適應症
                        #         FROM drugs
                        #         WHERE LOWER(中文品名) LIKE ?
                        #         LIMIT 3
                        #     """
                        #     like_param = f'%{medicine_name}%'
                        #     cursor.execute(query, (like_param,))
                        #     rows = cursor.fetchall()
                        #     conn.close()
                            
                        #     if rows:
                        #         zh_name, en_name, indication = rows[0]
                        #         # 副作用由 AI 產生
                        #         prompt = text_system_prompt + "\n" + (
                        #             f"請用簡短條列式，僅列出副作用，針對藥品「{zh_name}」(英文名：{en_name})，"
                        #             "請用繁體中文回答，若無法判斷請推測。"
                        #         )
                        #         try:
                        #             ai_resp = chat.generate_content(prompt)
                        #             side_effects = ai_resp.text.strip()
                        #         except Exception as e:
                        #             side_effects = f"系統繁忙，請稍後再試：{e}"
                        #         reply_text = (
                        #             f"🔹 中文品名：{zh_name}\n"
                        #             f"📌 英文品名：{en_name}\n"
                        #             f"📄 適應症：{indication}\n"
                        #             f"⚠️ 副作用：{side_effects}"
                        #         )
                        #     else:
                        #         # 全部請AI生成
                        #         prompt = text_system_prompt + "\n" + (
                        #             f"請用以下格式，幫我介紹藥品「{medicine_name}」，若無法查到請盡量推測：\n"
                        #             "🔹 中文品名：\n"
                        #             "📌 英文品名：\n"
                        #             "📄 適應症：\n"
                        #             "⚠️ 副作用："
                        #         )
                        #         try:
                        #             ai_resp = chat.generate_content(prompt)
                        #             reply_text = ai_resp.text
                        #         except Exception as e:
                        #             reply_text = f"系統繁忙，請稍後再試：{e}"

                        # except Exception as e:
                        #     reply_text = f"⚠️ 查詢資料時發生錯誤：{str(e)}"

                        
                # 處理位置訊息（查詢附近藥局）
                elif event.message.type == "location":
                    user_lat = event.message.latitude
                    user_lng = event.message.longitude

                    nearby_url = (
                        f"https://maps.googleapis.com/maps/api/place/nearbysearch/json?"
                        f"location={user_lat},{user_lng}&radius=1000&type=pharmacy&key={GOOGLE_MAP_API_KEY}"
                    )
                    nearby_res = requests.get(nearby_url).json()

                    if not nearby_res.get('results'):
                        reply_request = ReplyMessageRequest(
                            reply_token=event.reply_token,
                            messages=[TextMessage(text="附近找不到藥局")]
                        )
                        messaging_api.reply_message(reply_message_request=reply_request)
                        continue

                    place = nearby_res['results'][0]
                    place_id = place['place_id']
                    name = place.get('name', '藥局名稱未知')
                    location = place['geometry']['location']
                    dest_lat, dest_lng = location['lat'], location['lng']

                    details_url = (
                        f"https://maps.googleapis.com/maps/api/place/details/json?"
                        f"place_id={place_id}&fields=name,formatted_phone_number&key={GOOGLE_MAP_API_KEY}"
                    )
                    details_res = requests.get(details_url).json()
                    phone = details_res.get('result', {}).get('formatted_phone_number', '電話不詳')

                    dist_url = (
                        f"https://maps.googleapis.com/maps/api/distancematrix/json?"
                        f"origins={user_lat},{user_lng}&destinations={dest_lat},{dest_lng}&key={GOOGLE_MAP_API_KEY}"
                    )
                    dist_res = requests.get(dist_url).json()
                    distance = dist_res['rows'][0]['elements'][0]['distance']['text']

                    map_url = f"https://www.google.com/maps/search/?api=1&query={dest_lat},{dest_lng}"

                    bubble = FlexBubble(
                        body=FlexBox(
                            layout="vertical",
                            contents=[
                                FlexText(text=name, weight="bold", size="lg"),
                                FlexText(text=f"電話：{phone}", size="sm", color="#555555"),
                                FlexText(text=f"距離：{distance}", size="sm", color="#777777"),
                            ],
                        ),
                        footer=FlexBox(
                            layout="vertical",
                            contents=[
                                FlexButton(
                                    style="link",
                                    height="sm",
                                    action=URIAction(label="地圖導航", uri=map_url),
                                )
                            ],
                        ),
                    )

                    flex_message = FlexMessage(
                        alt_text="附近藥局推薦",
                        contents=bubble
                    )

                    reply_request = ReplyMessageRequest(
                        reply_token=event.reply_token,
                        messages=[flex_message]
                    )
                    messaging_api.reply_message(reply_message_request=reply_request)

                # 圖片訊息：用 Gemini AI 以藥品格式解釋圖片
                elif event.message.type == "image":
                    try:
                        content = blob_api.get_message_content(message_id=event.message.id)
                        with tempfile.NamedTemporaryFile(dir=static_tmp_path, suffix=".jpg", delete=False) as tf:
                            tf.write(content)
                            filename = os.path.basename(tf.name)
                        image_url = f"https://{base_url}/images/{filename}"
                        image = Image.open(tf.name)

                        # Gemini 圖片說明（指定格式，四欄都AI產生）
                        prompt = text_system_prompt + "\n" + (
                            "請根據這張圖片判斷藥品資訊，並用以下格式回答，若無法判斷請盡量推測：\n"
                            "🔹 中文品名：\n"
                            "📌 英文品名：\n"
                            "📄 適應症：\n"
                            "⚠️ 副作用："
                        )
                        response = chat.generate_content([image, prompt])
                        description = response.text

                        reply_request = ReplyMessageRequest(
                            reply_token=event.reply_token,
                            messages=[
                                ImageMessage(
                                    original_content_url=image_url,
                                    preview_image_url=image_url
                                ),
                                TextMessage(text=description)
                            ]
                        )
                        messaging_api.reply_message(reply_message_request=reply_request)
                    except Exception as e:
                        reply_request = ReplyMessageRequest(
                            reply_token=event.reply_token,
                            messages=[TextMessage(text=f"圖片處理失敗：{e}")]
                        )
                        messaging_api.reply_message(reply_message_request=reply_request)

    return "OK"

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 7860)) # 讀取環境變數 PORT，預設為 7860
    app.run(host="0.0.0.0", port=port, debug=False)