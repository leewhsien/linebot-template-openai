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

# API 位置（Ken提供的正式機網址）
UNIT_STATUS_API_URL = "https://510.org.tw/api/unit_status?name="

# 初始化
app = FastAPI()
session = aiohttp.ClientSession()
async_http_client = AiohttpAsyncHttpClient(session)
line_bot_api = AsyncLineBotApi(LINE_CHANNEL_ACCESS_TOKEN, async_http_client)
parser = WebhookParser(LINE_CHANNEL_SECRET)

user_message_count = {}

# FAQ (整併版)
system_content = """
你是一位客服專員，專門協助回答台灣一起夢想公益協會的問題。
當你提到「客服表單」，請一律在回答中自然附上：https://forms.gle/HkvmUzFGRwfVWs1n9
若使用者連續輸入三則以上訊息後仍未解決問題，請於回答後附註：
「如果沒有解決到您的問題，請輸入『需要幫忙』，我將請專人回覆您。」

📦 微型社福常見問題 FAQ：

申請募款合作常見問題：
1. 檔案上傳到一半，網頁一直顯示圈圈或當機，該怎麼辦?
應該是因為上傳檔案太大，請確認檔案大小未超過2mb，若超過者可利用 https://www.ilovepdf.com/zh-tw/compress_pdf 壓縮檔案大小。
2. 我沒有申報給國稅局的資料，該怎麼辦?
請提供理監事會議通過之財報相關資料，後續會由專人與您確認。
3. 我的財報是一整份，無法拆分檔案怎麼辦?
可利用 https://www.ilovepdf.com/zh-tw/split_pdf 進行檔案拆分後再重新上傳。
4. 協會目前沒有正職，因此沒有勞保投保證明，該怎麼辦？
請下載「正職0人聲明文件」 (https://drive.google.com/file/d/19yVOO4kT0CT4TK_204HGqgQRM8cBroG0/view?usp=drive_link)，用協會大章印後掃描上傳。

已募款合作單位常見問題：
1. 檔案上傳到一半，網頁一直顯示圈圈或當機，該怎麼辦?
請確認檔案大小未超過2mb，若超過可利用 https://www.ilovepdf.com/zh-tw/compress_pdf 壓縮檔案大小。
2. 為什麼我沒有收到這個月款項？
撥款日為每月15日（遇假日順延）；可能原因為(1)未於9日前收到收據，(2)未於10日前上傳款項使用報告。

📦 微型社福能申請之服務：
- 志工招募：https://510.org.tw/volunteer_applications
- 心靈沈靜：https://510.org.tw/peace_mind
- 小聚活動：https://510.org.tw/event_applications
- 申請合作：https://510.org.tw/collaboration_apply
- 募款定期定額申請：https://510.org.tw/agency_applications
"""

def call_openai_chat_api(user_message):
    openai.api_key = OPENAI_API_KEY
    try:
        response = openai.ChatCompletion.create(
            model="gpt-3.5-turbo",
            messages=[
                {"role": "system", "content": system_content},
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
    requests.post("https://api.line.me/v2/bot/message/push", headers=headers, json=data)

async def get_user_profile(user_id):
    try:
        profile = await line_bot_api.get_profile(user_id)
        return profile.display_name
    except Exception as e:
        print(f"取得用戶名稱失敗：{e}")
        return "未知用戶"

def check_if_status_query(user_message):
    keywords = [
        "上傳成功了嗎", "有收到資料嗎", "上傳了沒", "幫我確認上傳", "幫我看有沒有完成", 
        "上傳狀態", "資料狀態", "完成申請了嗎"
    ]
    return any(kw in user_message for kw in keywords)

def call_unit_status_api(unit_name):
    try:
        encoded_name = requests.utils.quote(unit_name)
        full_url = UNIT_STATUS_API_URL + encoded_name
        res = requests.get(full_url, timeout=5)
        if res.status_code == 200:
            data = res.json()
            upload_status = data.get("upload_status", "無資料")
            last_upload_time = data.get("last_upload_time", "無資料")
            return f"✅ 資料狀態：{upload_status}\n最後上傳時間：{last_upload_time}"
        else:
            return "⚠️ 查詢資料時發生錯誤，請稍後再試。"
    except Exception as e:
        print(f"API 呼叫失敗: {e}")
        return "⚠️ 系統異常，請稍後再試。"

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

            # 新增判斷：是否為查詢單位上傳狀態
            if check_if_status_query(user_message):
                await line_bot_api.reply_message(
                    event.reply_token,
                    TextSendMessage(text="請告訴我您的單位名稱（全名），我將幫您查詢目前資料狀態。")
                )
                return 'OK'

            # 如果上一輪問完單位名稱
            if user_message.startswith("我是") or user_message.startswith("單位名稱"):
                unit_name = user_message.replace("我是", "").replace("單位名稱", "").strip()
                status_result = call_unit_status_api(unit_name)
                await line_bot_api.reply_message(
                    event.reply_token,
                    TextSendMessage(text=status_result)
                )
                return 'OK'

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
