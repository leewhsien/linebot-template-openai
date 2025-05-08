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

# 環境變數設定
OPENAI_API_KEY = os.getenv('OPENAI_API_KEY', None)
LINE_CHANNEL_SECRET = os.getenv('LINE_CHANNEL_SECRET', None)
LINE_CHANNEL_ACCESS_TOKEN = os.getenv('LINE_CHANNEL_ACCESS_TOKEN', None)
LINE_USER_ID = os.getenv('LINE_USER_ID', None)  # 你的個人 LINE User ID

# 初始化 LINE Bot
app = FastAPI()
session = aiohttp.ClientSession()
async_http_client = AiohttpAsyncHttpClient(session)
line_bot_api = AsyncLineBotApi(LINE_CHANNEL_ACCESS_TOKEN, async_http_client)
parser = WebhookParser(LINE_CHANNEL_SECRET)

# LINE Notify URL
NOTIFY_URL = "https://api.line.me/v2/bot/message/push"

def call_openai_chat_api(user_message):
    """ 呼叫 OpenAI API 進行回應 """
    openai.api_key = OPENAI_API_KEY

    system_content = """
    你是一位客服專員，專門協助回答台灣一起夢想公益協會的問題。請根據以下資訊回覆使用者的問題：

    公司名稱：台灣一起夢想公益協會（簡稱「一起夢想」）
    成立年份：2012年
    官網：https://510.org.tw/
    客服專線：(02)6604-2510
    客服時間：週一至週五，上午9:00至下午6:00
    客服信箱：service@510.org.tw
    門市地址：台北市忠孝東路四段220號11樓

    📌 服務簡介：
    一起夢想是台灣首個專注服務「微型社福」的非營利組織，致力於支持全台正職人數6人以下的社會福利機構，協助其穩定運作，專心照顧弱勢族群。

    📦 服務項目：
    1. 募款支持：
       - 定期定額捐款：https://510.org.tw/agency_applications
       - 捐款查詢、捐款收據申請，請聯繫客服信箱或專線。

    2. 後勤支持：
       - 月報繳交與延遲處理：https://510.org.tw/agency_applications
       - 資料上傳與補件通知。

    3. 志工招募與活動報名：
       - 志工招募：https://510.org.tw/volunteer_applications
       - 心靈沈靜活動：https://510.org.tw/peace_mind
       - 各地小聚報名：https://510.org.tw/event_applications

    4. 社群連結：
       - Facebook: https://www.facebook.com/510org/
       - IG: https://www.instagram.com/510dream/
       - YouTube: https://www.youtube.com/channel/UC123456789

    🔍 常見問題 (FAQ)：

    1. 為什麼這個月沒有收到定期定額款項？
       - 如果單據已確實寄送，但一起夢想收到時間已超過每月10日，將無法趕上該月的帳務處理，款項將延至下月撥款。

    2. 月報遲交怎麼辦？
       - 敬請留意月報繳交時間，並盡快補上傳。若屢次逾期或未提交，恐影響後續合作安排，請務必配合。

    3. 是否提供單次募款或募款專案？
       - 目前我們專注於「定期定額」捐款，暫不提供單次募款或募款專案。如需更多資金募集建議，請聯繫客服。

    4. 月報、單據、資料上傳有收到了嗎？
       - 若資料有問題或未收到，我們會主動通知您，謝謝您的關心與協助！
    """

    response = openai.ChatCompletion.create(
        model="gpt-3.5-turbo",
        messages=[
            {"role": "system", "content": system_content},
            {"role": "user", "content": user_message},
        ]
    )

    return response.choices[0].message['content']

def notify_admin(user_id, message):
    """通知管理員"""
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}"
    }

    import datetime
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    notification_message = (
        f"🔔 收到未知問題通知\n"
        f"時間：{timestamp}\n"
        f"用戶 ID：{user_id}\n"
        f"訊息內容：{message}"
    )

    data = {
        "to": LINE_USER_ID,
        "messages": [{"type": "text", "text": notification_message}]
    }

    response = requests.post(NOTIFY_URL, headers=headers, json=data)
    if response.status_code != 200:
        print(f"通知發送失敗：{response.status_code} - {response.text}")

@app.post("/callback")
async def handle_callback(request: Request):
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
            user_message = event.message.text

            print(f"用戶 ID：{user_id}")
            print(f"收到訊息：{user_message}")

            # 呼叫 OpenAI API
            response_message = call_openai_chat_api(user_message)

            # 若無法回答，發送通知
            if "抱歉，我無法回答這個問題" in response_message:
                notify_admin(user_id, user_message)

            # 回覆用戶
            await line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text=response_message)
            )

    return 'OK'

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=port)
