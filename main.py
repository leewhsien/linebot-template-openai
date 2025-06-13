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
user_organization_name = {}

# LINE 提醒 API
NOTIFY_URL = "https://api.line.me/v2/bot/message/push"

# 開場
identity_prompt = "您好，請問有什麼需要幫忙的地方嗎？"

# system prompt
system_content_common = """
你是一位客服專員，專門協助回答台灣一起夢想公益協會的問題。
當你提到「客服表單」，請一律在回答中自然附上：https://forms.gle/HkvmUzFGRwfVWs1n9
"""

system_content_agency = system_content_common + """
📦 微型社福 FAQ（協會上傳/後台操作類）：

【申請募款合作常見問題】
1. 檔案上傳到一半網頁當機怎麼辦？
請確認檔案大小未超過 2MB，若超過可使用 https://www.ilovepdf.com/zh-tw/compress_pdf 壓縮。
2. 財報資料無法提供給國稅局怎麼辦？
提供理監事會議通過的財報資料，由專人協助確認。
3. 財報無法拆分怎麼辦？
可使用 https://www.ilovepdf.com/zh-tw/split_pdf 進行拆分後上傳。
4. 沒有正職人員無法提供勞保證明怎麼辦？
下載正職0人聲明書：https://drive.google.com/file/d/19yVOO4kT0CT4TK_204HGqgQRM8cBroG0/view?usp=drive_link 
加蓋大章掃描上傳。

【已募款合作單位常見問題】
1. 檔案上傳失敗？
同樣檢查是否超過 2MB 檔案大小，並使用壓縮工具。
2. 為什麼本月沒有收到撥款？
(1) 未於9號前上傳收據 (2) 未於10號前上傳款項使用報告

【服務申請入口】
- 志工招募資訊：https://510.org.tw/volunteer_applications
- 心靈沈靜活動報名：https://510.org.tw/peace_mind
- 小聚活動報名：https://510.org.tw/event_applications
- 微型社福申請合作頁面：https://510.org.tw/collaboration_apply
- 申請定期定額捐款支持：https://510.org.tw/agency_applications
"""

# 呼叫 OpenAI API
async def call_openai_chat_api(user_message):
    openai.api_key = OPENAI_API_KEY
    content = system_content_agency
    try:
        response = await openai.ChatCompletion.acreate(
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

# 後台 API 串接查詢
async def query_backend(unit_name):
    try:
        encoded_name = quote(unit_name)
        url = f"https://510.org.tw/api/unit_status?name={encoded_name}"
        async with session.get(url, timeout=10) as resp:
            if resp.status == 200:
                data = await resp.json()
                return data
            else:
                return None
    except Exception as e:
        print("API 查詢失敗：", e)
        return None

# 自動判斷是否要進行後台查詢
async def check_need_query(user_id, user_message):
    keywords = ["資料有上傳成功嗎", "已經上傳資料", "幫我查看資料", "月報有上傳", "資料正確嗎"]
    if any(keyword in user_message for keyword in keywords):
        unit_name = user_organization_name.get(user_id)
        if not unit_name:
            return "請問您是哪一個微型社福單位呢？（請提供全名）"
        result = await query_backend(unit_name)
        if not result:
            return "查詢後台時發生問題，請稍後再試，或填寫客服表單：https://forms.gle/HkvmUzFGRwfVWs1n9"
        if result['upload_status'] == "已完成":
            return f"✅ 後台顯示：您的資料已於 {result['last_upload_time']} 完成上傳。"
        else:
            return "目前後台尚未查到完整上傳紀錄，若有問題請填寫客服表單。"
    return None

# LINE 通知管理員
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

# webhook主程式
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
                await line_bot_api.reply_message(
                    event.reply_token,
                    TextSendMessage(text=identity_prompt)
                )
                return 'OK'

            # 判斷是否為單位名稱輸入
            if user_id not in user_organization_name:
                user_organization_name[user_id] = user_message
                await line_bot_api.reply_message(
                    event.reply_token,
                    TextSendMessage(text="好的！已記錄您的單位，請問有什麼需要協助的？")
                )
                return 'OK'

            # 先判斷是否為需要後台查詢的問題
            backend_response = await check_need_query(user_id, user_message)
            if backend_response:
                await line_bot_api.reply_message(event.reply_token, TextSendMessage(text=backend_response))
                return 'OK'

            # 進入一般 OpenAI 問答
            user_message_count[user_id] = user_message_count.get(user_id, 0) + 1
            response = await call_openai_chat_api(user_message)

            if user_message_count[user_id] >= 3:
                response += "\n\n如果沒有解決到您的問題，請輸入『需要幫忙』，我將請專人回覆您。"

            if "需要幫忙" in user_message:
                notify_admin(user_id, display_name, user_message)

            await line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text=response)
            )

    return 'OK'

# Render 部署啟動
if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 10000))
    print(f"Starting server on port {port}...")
    uvicorn.run("main:app", host="0.0.0.0", port=port)
