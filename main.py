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
identity_prompt = "您好，請問有什麼需要幫忙的地方嗎？"

# FAQ
system_content_common = """
你是一位客服專員，專門協助回答台灣一起夢想公益協會的問題。
當你提到「客服表單」，請一律在回答中自然附上：https://forms.gle/HkvmUzFGRwfVWs1n9
"""

system_content_agency = system_content_common + """
📦 微型社福常見問題 FAQ：

申請募款合作常見問題：
1. 檔案上傳到一半，網頁一直顯示圈圈或當機，該怎麼辦？
   - 請確認檔案大小未超過2MB，可用 https://www.ilovepdf.com/zh-tw/compress_pdf 壓縮。

2. 沒有申報給國稅局的資料怎麼辦？
   - 請提供理監事會議通過之財報資料，將由專人與您確認。

3. 財報是一整份無法拆分檔案怎麼辦？
   - 可用 https://www.ilovepdf.com/zh-tw/split_pdf 拆分後重新上傳。

4. 沒有正職人員無法提供勞保證明怎麼辦？
   - 請下載「正職 0 人聲明文件」(https://drive.google.com/file/d/19yVOO4kT0CT4TK_204HGqgQRM8cBroG0/view?usp=drive_link)，蓋章後掃描上傳。

已募款合作單位常見問題：
1. 上傳到一半，網頁顯示圈圈或當機？
   - 檔案可能過大，請壓縮至 2MB 以下。

2. 為什麼沒收到本月款項？
   - 撥款日為每月15日，遇假日順延。常見原因：未於9日前收到收據，或未於10日前上傳使用報告。

📦 微型社福可申請服務：
- 志工招募：https://510.org.tw/volunteer_applications
- 心靈活動：https://510.org.tw/peace_mind
- 小聚報名：https://510.org.tw/event_applications
- 合作申請：https://510.org.tw/collaboration_apply
- 定期定額捐款申請：https://510.org.tw/agency_applications
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

            # 新增：辨識是否是詢問「上傳成功沒」的語意
            check_keywords = ["有上傳成功嗎", "有成功上傳嗎", "幫我看一下有沒有傳好", "有沒有正確", "請幫我查看"]
            if any(kw in user_message for kw in check_keywords):
                await line_bot_api.reply_message(
                    event.reply_token,
                    TextSendMessage(text="請問您是哪一間微型社福的夥伴呢？我們會協助您到後台確認，再回覆您！")
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
