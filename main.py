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
import urllib.parse

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

# 用戶答對累計
user_message_count = {}

# LINE 提醒 API
NOTIFY_URL = "https://api.line.me/v2/bot/message/push"

# 一開始的問候
identity_prompt = "您好，請問有什麼需要幫忙的地方嗎？"

# FAQ 系統內容
system_content_common = """
你是一位客服專員，專門協助回答台灣一起夢想公益協會的問題。
當你提到「客服表單」，請一律在回答中自然附上：https://forms.gle/HkvmUzFGRwfVWs1n9
若使用者連續輸入三則以上訊息後仍未解決問題，請於回答後附註：「如果沒有解決到您的問題，請輸入『需要幫忙』，我將請專人回覆您。」

📦 微型社福 FAQ（整併版）：

【申請募款合作常見問題】

1. 檔案上傳到一半，網頁一直顯示圈圈或當機，該怎麼辦？
- 應該是因為上傳檔案太大，請確認檔案大小未超過2mb，若超過者可利用 免費線上服務 (https://www.ilovepdf.com/zh-tw/compress_pdf) 壓縮檔案大小。

2. 我沒有申報給國稅局的資料，該怎麼辦？
- 請提供理監事會議通過之財報相關資料，後續會由專人與您確認。

3. 我的財報是一整份，無法拆分檔案怎麼辦?
- 可利用 免費線上服務 (https://www.ilovepdf.com/zh-tw/split_pdf) 進行檔案拆分後，再重新上傳資料至後台。

4. 協會目前沒有正職，因此沒有勞保投保證明，該怎麼辦？
- 請下載 正職0人聲明文件 (https://drive.google.com/file/d/19yVOO4kT0CT4TK_204HGqgQRM8cBroG0/view?usp=drive_link)，用協會大章印後掃描上傳，謝謝！

【已募款合作單位常見問題】

5. 為什麼這個月沒有收到款項？
- 一起夢想每月撥款一次為每月15號，遇假日順延；若未收到款項可能是因(1)一起夢想未於9號前收到協會的捐款收據，(2)協會未於10號前上傳款項使用報告。

【其他服務申請】

6. 志工招募：https://510.org.tw/volunteer_applications
7. 心靈沈靜活動：https://510.org.tw/peace_mind
8. 小聚活動：https://510.org.tw/event_applications
9. 申請合作：https://510.org.tw/collaboration_apply
10. 申請定期定額捐款支持：https://510.org.tw/agency_applications
"""

# 呼叫 ChatGPT
def call_openai_chat_api(user_message):
    openai.api_key = OPENAI_API_KEY
    content = system_content_common
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

# 呼叫 API 查詢單位資料上傳狀態
def call_unit_status_api(unit_name):
    try:
        base_url = "https://510.org.tw/api/unit_status"
        encoded_name = urllib.parse.quote(unit_name)
        url = f"{base_url}?name={encoded_name}"
        response = requests.get(url)
        if response.status_code == 200:
            return response.json()
        else:
            return None
    except Exception as e:
        print(f"API 呼叫失敗：{e}")
        return None

# 判斷是否屬於「資料有沒有上傳」相關問題
def is_upload_related_question(user_message):
    keywords = [
        "上傳了嗎", "資料有成功嗎", "已經上傳了", "幫我確認有沒有成功", 
        "幫我看一下有沒有正確", "上傳成功了嗎", "資料送出了嗎", "有上傳成功嗎"
    ]
    return any(keyword in user_message for keyword in keywords)

# 管理員通知
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

# 取得用戶名稱
async def get_user_profile(user_id):
    try:
        profile = await line_bot_api.get_profile(user_id)
        return profile.display_name
    except Exception as e:
        print(f"取得用戶名稱失敗：{e}")
        return "未知用戶"

# Webhook
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

            # 先判斷是否屬於查詢上傳問題
            if is_upload_related_question(user_message):
                reply = f"好的，請問您是「哪一個微型社福單位」？請提供完整單位名稱。"
                await line_bot_api.reply_message(
                    event.reply_token,
                    TextSendMessage(text=reply)
                )
                return 'OK'

            # 當用戶回報單位名稱時
            if "社團法人" in user_message:
                api_result = call_unit_status_api(user_message)
                if api_result:
                    reply = f"✅ 您的單位：{api_result['name']}\n上傳狀態：{api_result['upload_status']}\n最後上傳時間：{api_result['last_upload_time']}"
                else:
                    reply = "抱歉，系統查不到您提供的單位資料，請再次確認單位全名。"
                await line_bot_api.reply_message(
                    event.reply_token,
                    TextSendMessage(text=reply)
                )
                return 'OK'

            # 進入 AI 回答流程
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

# 啟動服務
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
