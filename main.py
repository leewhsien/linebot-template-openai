# -*- coding: utf-8 -*-
import openai
import os
import json
import requests
import aiohttp
import urllib.parse
import unicodedata
import re

from fastapi import Request, FastAPI, HTTPException
from linebot import AsyncLineBotApi, WebhookParser
from linebot.aiohttp_async_http_client import AiohttpAsyncHttpClient
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
LINE_CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET", "")
LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN", "")
ADMIN_USER_ID = "Ue23b0c54b12a040a3e20ee43f51b8ef9"

app = FastAPI()
session = aiohttp.ClientSession()
async_http_client = AiohttpAsyncHttpClient(session)
line_bot_api = AsyncLineBotApi(LINE_CHANNEL_ACCESS_TOKEN, async_http_client)
parser = WebhookParser(LINE_CHANNEL_SECRET)

user_roles = {}
user_orgname = {}
user_message_count = {}
user_has_provided_info = {}

onboarding_message = (
    "請協助填寫以下資訊：\n"
    "1、單位名稱：\n"
    "2、服務縣市：\n"
    "3、聯絡人職稱＋姓名＋電話：\n"
    "4、服務對象（可複選）：弱勢孩童、邊緣少年、中年困境、孤獨長者、無助動物\n"
    "5、服務類別（可複選）：民生照顧、教育陪伴、醫療照護、身心障礙、理念推廣、原住民、新住民、有物資需求、有志工需求"
)

completion_message = (
    "已收到您的資訊，並完成建檔\n"
    "很榮幸認識您與貴單位\n"
    "一起夢想支持微型社福的腳步持續邁進\n"
    "期待未來多多交流、一起努力🤜🏻🤛🏻"
)

system_content = """
你是一位客服專員，專門協助回答台灣一起夢想公益協會的問題。
若使用者連續輸入三則以上訊息後仍未解決問題，請於回答後附註：
「如果沒有解決到您的問題，請輸入『需要幫忙』，我將請專人回覆您。」
"""

faq_response_map = {
    "邀請": "🙏 感謝您的邀請，如有合作機會我們會主動與您聯繫。",
    "收據": "📨 收據會在月底前彙整寄出，如有問題請隨時告知。",
    "月報會遲交": "📌 請於每月10號前上傳月報，逾期將順延至次月撥款。",
    "沒有收到款項": "💰 撥款日為每月15號（假日順延），若未收到請確認是否已完成月報與收據。",
    "資料已上傳": "📁 我們將為您確認最近一次的資料是否已成功上傳。"
}

def normalize_org_name(name):
    return unicodedata.normalize("NFKC", name.strip())

def call_openai_chat_api(user_message):
    openai.api_key = OPENAI_API_KEY
    try:
        response = openai.ChatCompletion.create(
            model="gpt-3.5-turbo",
            messages=[
                {"role": "system", "content": system_content},
                {"role": "user", "content": user_message}
            ]
        )
        return response.choices[0].message['content']
    except Exception as e:
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
    url = f"https://510.org.tw/api/unit_status?name={encoded_name}"
    try:
        r = requests.get(url, timeout=10)
        if r.status_code == 200:
            data = r.json()
            status = data.get("upload_status", "")
            if status == "已完成":
                text = f"✅ 我們查詢到 {norm_name} 已成功完成上傳作業。"
            else:
                text = f"目前查詢結果顯示 {norm_name} 尚未完成上傳，請再確認。"
        else:
            text = "查詢過程發生異常，請稍後再試。"
    except:
        text = "查詢時發生錯誤，請稍後再試。"
    await line_bot_api.reply_message(event.reply_token, TextSendMessage(text=text))

def message_looks_like_profile(msg):
    return all(key in msg for key in ["單位名稱", "服務縣市", "聯絡人", "服務對象", "服務類別"])

@app.post("/callback")
async def callback(request: Request):
    signature = request.headers["X-Line-Signature"]
    body = (await request.body()).decode()
    try:
        events = parser.parse(body, signature)
    except InvalidSignatureError:
        raise HTTPException(status_code=400, detail="Invalid signature")

    for event in events:
        if isinstance(event, MessageEvent) and isinstance(event.message, TextMessage):
            user_id = event.source.user_id
            text = event.message.text.strip()

            if user_id not in user_roles:
                user_roles[user_id] = "微型社福"
                user_has_provided_info[user_id] = False
                await line_bot_api.reply_message(event.reply_token, TextSendMessage(
                    text="您好，我是一起夢想的客服小編，我會盡力回答您的問題。\n如果沒有幫上忙，請輸入『需要幫忙』，我會請專人來協助您。"
                ))
                return "OK"

            if not user_has_provided_info.get(user_id, False):
                if message_looks_like_profile(text):
                    user_has_provided_info[user_id] = True
                    await line_bot_api.reply_message(event.reply_token, TextSendMessage(text=completion_message))
                    display_name = await get_user_profile(user_id)
                    await line_bot_api.push_message(ADMIN_USER_ID, TextSendMessage(
                        text=f"有新用戶加入並完成建檔：\n用戶名稱：{display_name}\n用戶ID：{user_id}\n內容：\n{text}"))
                    return "OK"
                else:
                    await line_bot_api.reply_message(event.reply_token, TextSendMessage(text=onboarding_message))
                    return "OK"

            if text == "需要幫忙":
                await line_bot_api.reply_message(event.reply_token, TextSendMessage(text="我已經通知專人協助，請耐心等候。"))
                await line_bot_api.push_message(ADMIN_USER_ID, TextSendMessage(text=f"⚠️ 用戶 {user_id} 請求協助：\n「需要幫忙」"))
                return "OK"

            if text in faq_response_map:
                await line_bot_api.reply_message(event.reply_token, TextSendMessage(text=faq_response_map[text]))
                return "OK"

            # 若詢問查詢但尚未填寫單位名稱
            if any(kw in text for kw in ["月報", "資料", "查詢"]) and not user_orgname.get(user_id):
                await line_bot_api.reply_message(event.reply_token, TextSendMessage(
                    text="請告訴我您是哪一個單位，我才能幫您查詢。"))
                return "OK"

            # 若已提供單位，直接查詢
            if any(kw in text for kw in ["月報", "資料", "查詢"]) and user_orgname.get(user_id):
                await handle_status_check(user_id, user_orgname[user_id], event)
                return "OK"

            # 回覆單位名稱
            if text.startswith("我是") or text.startswith("我們是"):
                org = text.replace("我是", "").replace("我們是", "").strip()
                user_orgname[user_id] = org
                await line_bot_api.reply_message(event.reply_token, TextSendMessage(text="收到單位名稱，請問有什麼需要幫忙的嗎？"))
                return "OK"

            # 自動記憶若單純發送機構名稱
            if normalize_org_name(text).endswith("協會") or normalize_org_name(text).endswith("基金會"):
                user_orgname[user_id] = text.strip()
                await line_bot_api.reply_message(event.reply_token, TextSendMessage(text="好的，我記下來了，請問接下來需要我幫您什麼？"))
                return "OK"

            # 未知訊息處理
            user_message_count[user_id] = user_message_count.get(user_id, 0) + 1
            reply = call_openai_chat_api(text)

            if user_message_count[user_id] >= 3:
                reply += "\n\n如果沒有解決到您的問題，請輸入『需要幫忙』，我將請專人回覆您。"
                await line_bot_api.push_message(ADMIN_USER_ID, TextSendMessage(
                    text=f"⚠️ 用戶 {user_id} 連續輸入無法識別內容：\n「{text}」"))

            await line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply))
    return "OK"

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 10000))
    uvicorn.run("main:app", host="0.0.0.0", port=port)
