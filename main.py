# -*- coding: utf-8 -*-
import openai
import os
import sys
import json
import requests
import aiohttp
import re

from fastapi import Request, FastAPI, HTTPException
from linebot import AsyncLineBotApi, WebhookParser
from linebot.aiohttp_async_http_client import AiohttpAsyncHttpClient
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage

# 環境設定
OPENAI_API_KEY = os.getenv('OPENAI_API_KEY', None)
LINE_CHANNEL_SECRET = os.getenv('LINE_CHANNEL_SECRET', None)
LINE_CHANNEL_ACCESS_TOKEN = os.getenv('LINE_CHANNEL_ACCESS_TOKEN', None)
LINE_USER_ID = "Ue23b0c54b12a040a3e20ee43f51b8ef9"  # 管理者 ID

# 初始化
app = FastAPI()
session = aiohttp.ClientSession()
async_http_client = AiohttpAsyncHttpClient(session)
line_bot_api = AsyncLineBotApi(LINE_CHANNEL_ACCESS_TOKEN, async_http_client)
parser = WebhookParser(LINE_CHANNEL_SECRET)

user_roles = {}
user_message_count = {}

NOTIFY_URL = "https://api.line.me/v2/bot/message/push"

identity_prompt = "您好，請問有什麼需要幫忙的地方嗎？"

system_content_common = """
你是一位客服專員，專門協助回答台灣一起夢想公益協會的問題。
當你提到「客服表單」，請一律在回答中自然附上：https://forms.gle/HkvmUzFGRwfVWs1n9
"""

system_content_agency = system_content_common + """
\n📦 微型社福 FAQ：
- 檔案上傳錯誤、財報處理、無正職證明等上傳協助
- 款項未撥常見原因
- 志工、小聚、申請合作服務入口：https://510.org.tw/
"""

# 簡單判斷是否為上傳查詢的關鍵字
def is_upload_status_inquiry(message):
    keywords = [
        "上傳了嗎", "有沒有上傳成功", "資料有上傳嗎", 
        "幫我確認上傳", "確認有沒有上傳"
    ]
    return any(keyword in message for keyword in keywords)

# 呼叫 API 取得上傳狀態
def query_upload_status(unit_name):
    try:
        api_url = f"https://510.org.tw/api/unit_status?name={unit_name}"
        response = requests.get(api_url, timeout=10)
        if response.status_code == 200:
            data = response.json()
            status = data.get("upload_status", "查無資料")
            last_time = data.get("last_upload_time", "")
            if status == "已完成":
                return f"✅ 資料已完成上傳。\n最後上傳時間：{last_time}"
            else:
                return f"⚠ 目前尚未完成資料上傳。"
        else:
            return "⚠ 查詢失敗，請稍後再試。"
    except Exception as e:
        return f"⚠ 取得資料時發生錯誤：{e}"

# 呼叫 OpenAI 產生回覆
def call_openai_chat_api(user_message):
    openai.api_key = OPENAI_API_KEY
    content = system_content_agency
    try:
        response = openai.ChatCompletion.create(
            model="gpt-3.5-turbo",
            messages=[
                {"role": "system", "content": content},
                {"role": "user", "content": user_message},
            ]
        )
        return response.choices[0].message['content']
    except Exception as e:
        print(f"OpenAI API Error: {e}")
        return "抱歉，目前無法處理您的請求，請稍後再試。"

# 取得 LINE 使用者名稱
async def get_user_profile(user_id):
    try:
        profile = await line_bot_api.get_profile(user_id)
        return profile.display_name
    except Exception:
        return "未知用戶"

# 通知管理員
def notify_admin(user_id, display_name, message):
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}"
    }
    data = {
        "to": LINE_USER_ID,
        "messages": [{
            "type": "text",
            "text": f"🔔 收到未知問題通知\n用戶名稱：{display_name}\n用戶 ID：{user_id}\n訊息內容：{message}"
        }]
    }
    requests.post(NOTIFY_URL, headers=headers, json=data)

@app.post("/callback")
async def callback(request: Request):
    signature = request.headers['X-Line-Signature']
    body = await request.body()
    body = body.decode()

    try:
        events = parser.parse(body, signature)
    except InvalidSignatureError:
        raise HTTPException(status_code=400, detail="Invalid signature")

    for event in events:
        if isinstance(event, MessageEvent) and isinstance(event.message, TextMessage):
            user_id = event.source.user_id
            user_message = event.message.text.strip()
            display_name = await get_user_profile(user_id)

            if user_id not in user_roles:
                user_roles[user_id] = "微型社福"
                await line_bot_api.reply_message(
                    event.reply_token,
                    TextSendMessage(text=identity_prompt)
                )
                return 'OK'

            user_message_count[user_id] = user_message_count.get(user_id, 0) + 1

            # 判斷是否為上傳查詢
            if is_upload_status_inquiry(user_message):
                response = query_upload_status(user_message)
            else:
                response = call_openai_chat_api(user_message)

            if user_message_count[user_id] >= 3:
                response += "\n\n如果沒有解決到您的問題，請輸入『需要幫忙』，我將請專人回覆您。"

            if "需要幫忙" in user_message:
                notify_admin(user_id, display_name, user_message)

            await line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text=response)
            )

    return 'OK'

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 10000))
    print(f"Starting server on port {port}...")
    uvicorn.run("main:app", host="0.0.0.0", port=port)
