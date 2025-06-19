# -*- coding: utf-8 -*-
import openai
import os
import json
import requests
import aiohttp
import urllib.parse
import unicodedata

from fastapi import Request, FastAPI, HTTPException
from linebot import AsyncLineBotApi, WebhookParser
from linebot.aiohttp_async_http_client import AiohttpAsyncHttpClient
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage, SourceUser

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
LINE_CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET")
LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
ADMIN_USER_ID = os.getenv("LINE_ADMIN_ID")  # 從環境變數讀取管理員 ID

app = FastAPI()
session = aiohttp.ClientSession()
http_client = AiohttpAsyncHttpClient(session)
line_bot_api = AsyncLineBotApi(LINE_CHANNEL_ACCESS_TOKEN, http_client)
parser = WebhookParser(LINE_CHANNEL_SECRET)

user_roles = {}
user_message_count = {}
user_orgname = {}
user_custom_info = {}
manual_mode_users = {}

identity_prompt = "您好，我是一起夢想的客服小編，我會盡力回答您的問題。\n如果沒有幫上忙，請輸入『需要幫忙』，我會請專人來協助您。"

system_prompt = """你是一位客服專員，專門協助回答台灣一起夢想公益協會的問題。
若使用者連續輸入三則以上訊息後仍未解決問題，請於回答後附註：\n「如果沒有解決到您的問題，請輸入『需要幫忙』，我將請專人回覆您。」"""

faq_response_map = {
    "邀請": "🙏 感謝您的邀請，如有合作機會我們會主動與您聯繫。",
    "收據": "📨 收據會在月底前彙整寄出，如有問題請隨時告知。",
    "月報會遲交": "📌 請於每月10號前上傳月報，逾期將順延至次月撥款。",
    "沒有收到款項": "💰 撥款日為每月15號（假日順延），若未收到請確認是否已完成月報與收據。",
    "資料已上傳": "📁 我將為您查詢最近的上傳紀錄，請稍候..."
}

onboarding_message = """請協助填寫以下資訊：\n1、單位名稱：\n2、服務縣市：\n3、聯絡人職稱＋姓名＋電話：\n4、服務對象（可複選）：弱勢孩童、邊緣少年、中年困境、孤獨長者、無助動物\n5、服務類別（可複選）：民生照顧、教育陪伴、醫療照護、身心障礙、理念推廣、原住民、新住民、有物資需求、有志工需求"

async def get_user_profile(user_id):
    try:
        profile = await line_bot_api.get_profile(user_id)
        return profile.display_name
    except:
        return "未知用戶"

def normalize_org_name(name):
    return unicodedata.normalize('NFKC', name.strip())

def call_openai_chat_api(user_message):
    try:
        openai.api_key = OPENAI_API_KEY
        response = openai.ChatCompletion.create(
            model="gpt-3.5-turbo",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message}
            ]
        )
        return response.choices[0].message['content']
    except:
        return "目前無法處理您的請求，請稍後再試。"

async def handle_status_check(user_id, org_name, event):
    norm_name = normalize_org_name(org_name)
    encoded_name = urllib.parse.quote(norm_name)
    url = f"https://510.org.tw/api/unit_status?name={encoded_name}"
    try:
        r = requests.get(url)
        if r.status_code == 200:
            status = r.json().get("upload_status", "")
            if status == "已完成":
                msg = f"✅ 我們查詢到 {norm_name} 已成功完成上傳作業。"
            else:
                msg = f"目前查詢結果顯示 {norm_name} 尚未完成上傳，請再確認。"
        else:
            msg = "⚠️ 查詢過程發生錯誤，請稍後再試。"
    except:
        msg = "⚠️ 查詢時出現問題，請稍後再試。"
    await line_bot_api.reply_message(event.reply_token, TextSendMessage(text=msg))

async def notify_admin(text):
    if ADMIN_USER_ID:
        await line_bot_api.push_message(ADMIN_USER_ID, TextSendMessage(text=text))

@app.post("/callback")
async def callback(request: Request):
    signature = request.headers['X-Line-Signature']
    body = (await request.body()).decode()
    try:
        events = parser.parse(body, signature)
    except InvalidSignatureError:
        raise HTTPException(status_code=400, detail="Invalid signature")

    for event in events:
        if isinstance(event, MessageEvent) and isinstance(event.message, TextMessage):
            user_id = event.source.user_id
            user_message = event.message.text.strip()
            display_name = await get_user_profile(user_id)

            if manual_mode_users.get(user_id):
                return 'OK'

            if user_id not in user_roles:
                user_roles[user_id] = "微型社福"
                await line_bot_api.reply_message(event.reply_token, TextSendMessage(text=identity_prompt))
                return 'OK'

            if user_message.startswith("我們是") or user_message.startswith("我是"):
                org = user_message.replace("我們是", "").replace("我是", "").strip()
                if any(c in org for c in ["協會", "基金會", "機構"]):
                    user_orgname[user_id] = org
                    msg = onboarding_message
                    await line_bot_api.reply_message(event.reply_token, TextSendMessage(text=msg))
                    return 'OK'

            if user_id not in user_orgname:
                await line_bot_api.reply_message(event.reply_token, TextSendMessage(text=onboarding_message))
                return 'OK'

            if user_message in faq_response_map:
                await line_bot_api.reply_message(event.reply_token, TextSendMessage(text=faq_response_map[user_message]))
                return 'OK'

            if any(k in user_message for k in ["上傳了嗎", "上傳狀況", "資料是否上傳", "月報"]):
                org_name = user_orgname.get(user_id)
                if org_name:
                    await handle_status_check(user_id, org_name, event)
                else:
                    await line_bot_api.reply_message(event.reply_token, TextSendMessage(
                        text="我還無法確定您是代表哪一個單位，請告訴我您是哪一個社福單位，才能幫您查詢資料喔！"
                    ))
                return 'OK'

            if user_message == "需要幫忙":
                manual_mode_users[user_id] = True
                await notify_admin(f"🔔 使用者需要協助：{display_name} ({user_id})\n訊息內容：{user_message}")
                await line_bot_api.reply_message(event.reply_token, TextSendMessage(text="我已經通知專人協助，請耐心等候。"))
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
