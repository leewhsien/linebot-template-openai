# -*- coding: utf-8 -*-
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#  You may obtain a copy of the License at
#
#       https://www.apache.org/licenses/LICENSE-2.0
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and limitations under the License.

import openai
import os
import sys
import json
import requests
import aiohttp
from urllib.parse import quote

from fastapi import Request, FastAPI, HTTPException
from linebot import AsyncLineBotApi, WebhookParser
from linebot.aiohttp_async_http_client import AiohttpAsyncHttpClient
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage

# 環境設定
OPENAI_API_KEY = os.getenv('OPENAI_API_KEY', None)
LINE_CHANNEL_SECRET = os.getenv('LINE_CHANNEL_SECRET', None)
LINE_CHANNEL_ACCESS_TOKEN = os.getenv('LINE_CHANNEL_ACCESS_TOKEN', None)
LINE_USER_ID = os.getenv('LINE_ADMIN_USER_ID', None)  # 管理者 ID
BACKEND_API_URL = "https://510.org.tw/api/unit_status?name="

# 初始化
app = FastAPI()
session = aiohttp.ClientSession()
async_http_client = AiohttpAsyncHttpClient(session)
line_bot_api = AsyncLineBotApi(LINE_CHANNEL_ACCESS_TOKEN, async_http_client)
parser = WebhookParser(LINE_CHANNEL_SECRET)

# 用戶身份與答對累計
user_roles = {}
user_message_count = {}
user_unit_names = {}

# LINE 提醒 API
NOTIFY_URL = "https://api.line.me/v2/bot/message/push"

# 身份確認訊息
identity_prompt = "您好，請問有什麼需要幫忙的地方嗎？"

# FAQ 系統提示
system_content_common = """
你是一位客服專員，專門協助回答台灣一起夢想公益協會的問題。
當你提到「客服表單」，請一律在回答中自然附上：https://forms.gle/HkvmUzFGRwfVWs1n9
"""

system_content_agency = system_content_common + """
📦 微型社福常見問題：

【申請募款合作常見問題】
1. 檔案上傳當機：請確認檔案未超過2MB，可用 https://www.ilovepdf.com/zh-tw/compress_pdf 壓縮
2. 無國稅局財報：提供理監事會議通過財報，由專人確認
3. 財報無法拆分：可用 https://www.ilovepdf.com/zh-tw/split_pdf 拆分
4. 無正職勞保：下載 https://drive.google.com/file/d/19yVOO4kT0CT4TK_204HGqgQRM8cBroG0/view?usp=drive_link 之 0人聲明文件

【已募款合作常見問題】
1. 檔案上傳當機：請確認檔案未超過2MB，可用 https://www.ilovepdf.com/zh-tw/compress_pdf 壓縮
2. 本月未收到款項：可能未於每月9號前收到收據，或10號前未上傳款項使用報告

【其他服務申請】
- 志工招募：https://510.org.tw/volunteer_applications
- 心靈沈靜活動：https://510.org.tw/peace_mind
- 小聚活動：https://510.org.tw/event_applications
- 合作申請：https://510.org.tw/collaboration_apply
- 定期定額募款申請：https://510.org.tw/agency_applications
"""

def call_openai_chat_api(user_message, role):
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

# 管理者通知

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

async def get_user_profile(user_id):
    try:
        profile = await line_bot_api.get_profile(user_id)
        return profile.display_name
    except Exception as e:
        print(f"取得用戶名稱失敗：{e}")
        return "未知用戶"

# 後台 API 查詢

def query_backend_api(unit_name):
    try:
        url = BACKEND_API_URL + quote(unit_name)
        response = requests.get(url)
        if response.status_code == 200:
            return response.json()
        else:
            return None
    except Exception as e:
        print(f"後台 API 查詢失敗: {e}")
        return None

# 判斷是否屬於查詢資料類問題

def is_data_check_question(message):
    keywords = ["上傳成功", "資料有上傳嗎", "幫我查看", "幫我確認", "幫我看一下"]
    return any(keyword in message for keyword in keywords)

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

            # 初始化身份
            if user_id not in user_roles:
                user_roles[user_id] = "微型社福"
                await line_bot_api.reply_message(event.reply_token, TextSendMessage(text=identity_prompt))
                return 'OK'

            # 紀錄用戶填寫單位名稱
            if user_id not in user_unit_names:
                await line_bot_api.reply_message(event.reply_token, TextSendMessage(text="請提供您的單位全名（完整協會名稱）。"))
                user_unit_names[user_id] = None
                return 'OK'

            if user_unit_names[user_id] is None:
                user_unit_names[user_id] = user_message.strip()
                await line_bot_api.reply_message(event.reply_token, TextSendMessage(text="收到，之後即可隨時詢問相關問題。"))
                return 'OK'

            user_message_count[user_id] = user_message_count.get(user_id, 0) + 1

            # 新增資料查詢判斷邏輯
            if is_data_check_question(user_message):
                unit_name = user_unit_names[user_id]
                result = query_backend_api(unit_name)
                if result:
                    upload_status = result.get("upload_status", "無法取得狀態")
                    last_upload_time = result.get("last_upload_time", "無紀錄")
                    reply = f"單位：{unit_name}\n上傳狀態：{upload_status}\n最後上傳時間：{last_upload_time}"
                else:
                    reply = f"查無 {unit_name} 的後台資料，請確認是否有誤。"
                await line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply))
                return 'OK'

            # 其餘正常 AI 回答
            role = user_roles.get(user_id, "微型社福")
            response = call_openai_chat_api(user_message, role)

            if user_message_count[user_id] >= 3:
                response += "\n\n如果沒有解決到您的問題，請輸入『需要幫忙』，我將請專人回覆您。"

            if "需要幫忙" in user_message:
                notify_admin(user_id, display_name, user_message)

            await line_bot_api.reply_message(event.reply_token, TextSendMessage(text=response))

    return 'OK'

# 啟動服務 (Render 版)
if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 10000))
    print(f"Starting server on port {port}...")
    uvicorn.run("main:app", host="0.0.0.0", port=port)
