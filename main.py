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

OPENAI_API_KEY = os.getenv('OPENAI_API_KEY', None)
LINE_CHANNEL_SECRET = os.getenv('LINE_CHANNEL_SECRET', None)
LINE_CHANNEL_ACCESS_TOKEN = os.getenv('LINE_CHANNEL_ACCESS_TOKEN', None)
LINE_USER_ID = "Ue23b0c54b12a040a3e20ee43f51b8ef9"

API_URL = "https://510.org.tw/api/unit_status?name="

# 初始化
app = FastAPI()
session = aiohttp.ClientSession()
async_http_client = AiohttpAsyncHttpClient(session)
line_bot_api = AsyncLineBotApi(LINE_CHANNEL_ACCESS_TOKEN, async_http_client)
parser = WebhookParser(LINE_CHANNEL_SECRET)

user_roles = {}
user_message_count = {}
user_unit_names = {}

NOTIFY_URL = "https://api.line.me/v2/bot/message/push"
identity_prompt = "您好，請問有什麼需要幫忙的地方嗎？"

system_content_common = """
你是一位客服專員，專門協助回答台灣一起夢想公益協會的問題。
若使用者連續輸入三則以上訊息後仍未解決問題，請於回答後附註：
「如果沒有解決到您的問題，請輸入『需要幫忙』，我將請專人回覆您。」
"""

system_content_agency = system_content_common + """
📦 微型社福 FAQ：
- 檔案上傳錯誤、財報處理、無正職證明等上傳協助
- 款項未撥常見原因
- 志工、小聚、申請合作服務入口：https://510.org.tw/
"""

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

async def get_user_profile(user_id):
    try:
        profile = await line_bot_api.get_profile(user_id)
        return profile.display_name
    except Exception as e:
        print(f"取得用戶名稱失敗：{e}")
        return "未知用戶"

def is_greeting(text):
    greetings = ["安安", "嗨", "哈囉", "你好", "您好", "早安", "午安", "晚安"]
    return any(greet in text for greet in greetings)

def check_api_upload(unit_name):
    try:
        url = API_URL + requests.utils.quote(unit_name)
        response = requests.get(url, timeout=10)
        data = response.json()

        if data.get("upload_status") == "已完成":
            return f"✅ 我們查詢到 {unit_name} 的資料已經上傳完成囉！"
        elif data.get("upload_status") == "未完成":
            return f"⚠ 目前查詢到 {unit_name} 的上傳資料尚未完成，請洽客服確認。"
        else:
            return f"目前查不到 {unit_name} 的上傳狀態，請洽客服確認。"
    except Exception as e:
        print("API 查詢錯誤", e)
        return "抱歉，目前系統暫時無法查詢，請稍後再試。"

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

            # 先判斷是否為單位名稱輸入階段
            if user_id not in user_unit_names:
                if is_greeting(user_message):
                    await line_bot_api.reply_message(
                        event.reply_token,
                        TextSendMessage(text="請問貴單位的全名是？（例如：社團法人新竹市身心障礙者聯合就業協會）")
                    )
                    return 'OK'
                else:
                    user_unit_names[user_id] = user_message
                    await line_bot_api.reply_message(
                        event.reply_token,
                        TextSendMessage(text="感謝提供，請問有什麼需要幫忙的？")
                    )
                    return 'OK'

            # 關鍵判斷：是否詢問上傳狀態
            if any(keyword in user_message for keyword in ["月報有上傳", "上傳了嗎", "資料上傳了沒", "上傳狀態"]):
                unit_name = user_unit_names.get(user_id)
                reply = check_api_upload(unit_name)
            else:
                reply = call_openai_chat_api(user_message)

            if user_message_count[user_id] >= 3:
                reply += "\n\n如果沒有解決到您的問題，請輸入『需要幫忙』，我將請專人回覆您。"

            await line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text=reply)
            )

    return 'OK'

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 10000))
    print(f"Starting server on port {port}...")
    uvicorn.run("main:app", host="0.0.0.0", port=port)
