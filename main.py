# -*- coding: utf-8 -*-
import openai
import os
import sys
import json
import requests
import aiohttp
import urllib.parse

import unicodedata

from fastapi import Request, FastAPI, HTTPException
from linebot import AsyncLineBotApi, WebhookParser
from linebot.aiohttp_async_http_client import AiohttpAsyncHttpClient
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage

OPENAI_API_KEY = os.getenv('OPENAI_API_KEY', None)
LINE_CHANNEL_SECRET = os.getenv('LINE_CHANNEL_SECRET', None)
LINE_CHANNEL_ACCESS_TOKEN = os.getenv('LINE_CHANNEL_ACCESS_TOKEN', None)
LINE_USER_ID = "Ue23b0c54b12a040a3e20ee43f51b8ef9"

app = FastAPI()
session = aiohttp.ClientSession()
async_http_client = AiohttpAsyncHttpClient(session)
line_bot_api = AsyncLineBotApi(LINE_CHANNEL_ACCESS_TOKEN, async_http_client)
parser = WebhookParser(LINE_CHANNEL_SECRET)

user_roles = {}
user_message_count = {}
user_orgname = {}

NOTIFY_URL = "https://api.line.me/v2/bot/message/push"

identity_prompt = "您好，請問有什麼需要幫忙的地方嗎？"

system_content_common = """
你是一位客服專員，專門協助回答台灣一起夢想公益協會的問題。
若使用者連續輸入三則以上訊息後仍未解決問題，請於回答後附註：
「如果沒有解決到您的問題，請輸入『需要幫忙』，我將請專人回覆您。」
"""

system_content_agency = system_content_common + """
📦 微型社福常見問題 FAQ（協會上傳/後台操作類）：

申請募款合作常見問題：

1. 檔案上傳到一半網頁當機怎麼辦？請確認檔案大小未超過 2MB。可使用 https://www.ilovepdf.com/zh-tw/compress_pdf 壓縮後上傳。
2. 財報資料無法提供給國稅局怎麼辦？請提供理監事會議通過的財報資料，將有專人確認。
3. 財報整份無法拆分？請用 https://www.ilovepdf.com/zh-tw/split_pdf 拆分上傳。
4. 無正職人員無法提供勞保？請下載正職0人聲明文件：https://drive.google.com/file/d/19yVOO4kT0CT4TK_204HGqgQRM8cBroG0/view?usp=drive_link

已募款合作單位常見問題：

1. 撥款日每月15號，9號前需收到收據、10號前需上傳款項使用報告。

📦 微型社福能申請之服務：

- 志工招募資訊：https://510.org.tw/volunteer_applications
- 心靈沈靜活動：https://510.org.tw/peace_mind
- 小聚活動：https://510.org.tw/event_applications
- 合作申請頁面：https://510.org.tw/collaboration_apply
- 申請定期定額：https://510.org.tw/agency_applications
"""

# 新增名稱標準化
def normalize_org_name(name):
    name = name.strip()
    name = unicodedata.normalize('NFKC', name)
    return name

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
        return "目前無法處理您的請求，請稍後再試。"

async def get_user_profile(user_id):
    try:
        profile = await line_bot_api.get_profile(user_id)
        return profile.display_name
    except:
        return "未知用戶"

async def handle_status_check(user_id, org_name, event):
    norm_name = normalize_org_name(org_name)
    encoded_name = urllib.parse.quote(norm_name)
    api_url = f"https://510.org.tw/api/unit_status?name={encoded_name}"
    try:
        response = requests.get(api_url, timeout=10)
        if response.status_code == 200:
            data = response.json()
            upload_status = data.get("upload_status", "")
            if upload_status == "已完成":
                reply_text = f"✅ 我們查詢到 {norm_name} 已成功完成上傳作業。"
            else:
                reply_text = f"目前查詢結果顯示 {norm_name} 尚未完成上傳，請再確認。"
        else:
            reply_text = f"查詢過程發生異常，請稍後再試。"
    except:
        reply_text = f"查詢時發生錯誤，請稍後再試。"
    await line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_text))

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

            # 初次進來
            if user_id not in user_roles:
                user_roles[user_id] = "微型社福"
                await line_bot_api.reply_message(event.reply_token, TextSendMessage(text=identity_prompt))
                return 'OK'

            # 是否是提供單位名稱
            if user_message.startswith("我們是") or user_message.startswith("我是"):
                org_name = user_message.replace("我們是", "").replace("我是", "").strip()
                user_orgname[user_id] = org_name
                await line_bot_api.reply_message(event.reply_token, TextSendMessage(text="感謝提供，請問有什麼需要幫忙的？"))
                return 'OK'

            # 詢問月報上傳
            if "月報有上傳" in user_message or "月報上傳了嗎" in user_message:
                org_name = user_orgname.get(user_id)
                if org_name:
                    await handle_status_check(user_id, org_name, event)
                else:
                    await line_bot_api.reply_message(event.reply_token, TextSendMessage(text="請先告訴我您是哪一個單位喔！"))
                return 'OK'

            user_message_count[user_id] = user_message_count.get(user_id, 0) + 1
            response = call_openai_chat_api(user_message)
            if user_message_count[user_id] >= 3:
                response += "\n\n如果沒有解決到您的問題，請輸入『需要幫忙』，我將請專人回覆您。"
            await line_bot_api.reply_message(event.reply_token, TextSendMessage(text=response))
    return 'OK'

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 10000))
    uvicorn.run("main:app", host="0.0.0.0", port=port)
