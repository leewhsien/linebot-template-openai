# -*- coding: utf-8 -*-
import openai
import os
import sys
import json
import requests
import aiohttp

from fastapi import Request, FastAPI, HTTPException
from linebot import AsyncLineBotApi, WebhookParser
from linebot.aiohttp_async_http_client import AiohttpAsyncHttpClient
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage

# 環境設定
OPENAI_API_KEY = os.getenv('OPENAI_API_KEY', None)
LINE_CHANNEL_SECRET = os.getenv('LINE_CHANNEL_SECRET', None)
LINE_CHANNEL_ACCESS_TOKEN = os.getenv('LINE_CHANNEL_ACCESS_TOKEN', None)
LINE_USER_ID = "Ue23b0c54b12a040a3e20ee43f51b8ef9"

# API URL（請注意：這是你們的正式網址）
UNIT_STATUS_API_URL = "https://510.org.tw/api/unit_status"

# 初始化
app = FastAPI()
session = aiohttp.ClientSession()
async_http_client = AiohttpAsyncHttpClient(session)
line_bot_api = AsyncLineBotApi(LINE_CHANNEL_ACCESS_TOKEN, async_http_client)
parser = WebhookParser(LINE_CHANNEL_SECRET)

# 用戶身份與答對累計
user_roles = {}
user_message_count = {}
user_unit_name = {}

# LINE 提醒 API
NOTIFY_URL = "https://api.line.me/v2/bot/message/push"

# 問候語
identity_prompt = "您好，請問有什麼需要幫忙的地方嗎？"

# 基礎系統提示
system_content_common = """
你是一位客服專員，專門協助回答台灣一起夢想公益協會的問題。
當你提到「客服表單」，請一律在回答中自然附上：https://forms.gle/HkvmUzFGRwfVWs1n9
若使用者連續輸入三則以上訊息後仍未解決問題，請於回答後附註：
「如果沒有解決到您的問題，請輸入『需要幫忙』，我將請專人回覆您。」
"""

# FAQ 本體
system_content_agency = system_content_common + """
📦 微型社福常見問題 FAQ：
1. 檔案上傳到一半網頁當機怎麼辦？請確認檔案大小未超過 2MB，或使用線上壓縮工具壓縮檔案。
2. 財報資料無法提供給國稅局怎麼辦？請提供理監事會議通過的財報資料，將有專人與您確認。
3. 財報無法拆分？請使用線上分割工具拆分後再上傳。
4. 沒有正職人員無法提供勞保證明？請下載「正職0人聲明文件」後加蓋協會大章上傳。
5. 為什麼沒有收到款項？可能為收據與款項使用報告未依時完成上傳。

📦 其他服務申請：
- 志工招募：https://510.org.tw/volunteer_applications
- 心靈沈靜活動：https://510.org.tw/peace_mind
- 小聚活動：https://510.org.tw/event_applications
- 合作申請：https://510.org.tw/collaboration_apply
- 募款合作：https://510.org.tw/agency_applications
"""

# 呼叫 OpenAI 文字模型
def call_openai_chat_api(user_message):
    openai.api_key = OPENAI_API_KEY
    try:
        response = openai.ChatCompletion.create(
            model="gpt-3.5-turbo",
            messages=[
                {"role": "system", "content": system_content_agency},
                {"role": "user", "content": user_message},
            ]
        )
        return response.choices[0].message['content']
    except Exception as e:
        print(f"OpenAI API Error: {e}")
        return "抱歉，目前無法處理您的請求，請稍後再試。"

# 呼叫 API 查詢上傳狀態
def query_unit_status(unit_name):
    try:
        resp = requests.get(UNIT_STATUS_API_URL, params={"name": unit_name})
        data = resp.json()
        if data.get("upload_status") == "已完成":
            return f"✅ {unit_name} 的資料已上傳完成，最後上傳時間為 {data.get('last_upload_time')}。"
        elif data.get("upload_status") == "尚未完成":
            return f"⚠ {unit_name} 的資料尚未完成上傳。"
        else:
            return f"目前查詢不到 {unit_name} 的上傳狀態，請洽客服確認。"
    except Exception as e:
        print(f"API 查詢錯誤: {e}")
        return "目前系統暫時無法查詢資料，請稍後再試。"

# 通知管理者
def notify_admin(user_id, display_name, message):
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}"
    }
    data = {
        "to": LINE_USER_ID,
        "messages": [{"type": "text", "text": f"🔔 收到未知問題通知\n用戶名稱：{display_name}\n用戶 ID：{user_id}\n訊息內容：{message}"}]
    }
    requests.post(NOTIFY_URL, headers=headers, json=data)

# 取得使用者名稱
async def get_user_profile(user_id):
    try:
        profile = await line_bot_api.get_profile(user_id)
        return profile.display_name
    except:
        return "未知用戶"

# 主 webhook
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

            user_message_count[user_id] = user_message_count.get(user_id, 0) + 1

            # 檢查是否第一次互動
            if user_id not in user_roles:
                user_roles[user_id] = "微型社福"
                user_unit_name[user_id] = user_message  # 把第一句視為單位名稱
                await line_bot_api.reply_message(event.reply_token, TextSendMessage(text=identity_prompt))
                return 'OK'

            # 檢查是否是「查詢上傳狀態」
            if any(kw in user_message for kw in ["上傳", "資料上傳", "月報有上傳了嗎", "檔案有上傳嗎"]):
                unit_name = user_unit_name.get(user_id, "")
                reply_text = query_unit_status(unit_name)
            else:
                reply_text = call_openai_chat_api(user_message)

            if user_message_count[user_id] >= 3:
                reply_text += "\n\n如果沒有解決到您的問題，請輸入『需要幫忙』，我將請專人回覆您。"

            if "需要幫忙" in user_message:
                notify_admin(user_id, display_name, user_message)

            await line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_text))

    return 'OK'

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 10000))
    print(f"Starting server on port {port}...")
    uvicorn.run("main:app", host="0.0.0.0", port=port)
