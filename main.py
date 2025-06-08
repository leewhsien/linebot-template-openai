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

# 用戶身份與答對累計
user_roles = {}
user_message_count = {}

# LINE 提醒 API
NOTIFY_URL = "https://api.line.me/v2/bot/message/push"

# 問候與請確認身份
identity_prompt = "您好，請問您是哪一間微型社福的夥伴呢？"

# FAQ
system_content_common = """
你是一位客服專員，專門協助回答台灣一起夢想公益協會的問題。
當你提到「客服表單」，請一律在回答中自然附上：https://forms.gle/HkvmUzFGRwfVWs1n9
若使用者連續輸入三則以上訊息後仍未解決問題，請於回答後附註：
「如果沒有解決到您的問題，請輸入『需要幫忙』，我將請專人回覆您。」
"""

system_content_agency = system_content_common + """
📦 微型社福 FAQ（協會上傳/後台操作類）：
1. 檔案上傳到一半網頁當機怎麼辦？
   - 請確認檔案大小未超過 2MB。若超過，可使用免費線上壓縮工具後再重新上傳。
2. 財報資料無法提供給國稅局怎麼辦？
   - 請提供理監事會議通過的財報相關資料，將由專人與您確認。
3. 財報是整份無法拆分怎麼辦？
   - 可使用免費線上服務拆分檔案，再重新上傳。
4. 沒有正職人員無法提供勞保證明怎麼辦？
   - 請下載「正職 0 人聲明文件」，加蓋協會大章後掃描上傳。
5. 為什麼這個月沒有收到款項？
   - 撥款日為每月 15 日（遇假日順延）。可能原因為：
     (1) 一起夢想未於 9 號前收到收據；
     (2) 未於 10 號上傳款項使用報告。

📦 微型社福可申請之服務：
14. 志工招募資訊：https://510.org.tw/volunteer_applications
15. 心靈沈靜活動報名：https://510.org.tw/peace_mind
16. 小聚活動報名：https://510.org.tw/event_applications
17. 微型社福申請合作頁面：https://510.org.tw/collaboration_apply
18. 申請定期定額捐款支持：https://510.org.tw/agency_applications
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
            role = user_roles.get(user_id, "微型社福")
            response = call_openai_chat_api(user_message, role)

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
    uvicorn.run("main:app", host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
