# -*- coding: utf-8 -*-
import os
import openai
import json
import requests
import unicodedata
import aiohttp
import urllib.parse

from fastapi import FastAPI, Request, HTTPException
from linebot import AsyncLineBotApi, WebhookParser
from linebot.aiohttp_async_http_client import AiohttpAsyncHttpClient
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage

# 初始化變數
OPENAI_API_KEY = os.getenv('OPENAI_API_KEY')
LINE_CHANNEL_SECRET = os.getenv('LINE_CHANNEL_SECRET')
LINE_CHANNEL_ACCESS_TOKEN = os.getenv('LINE_CHANNEL_ACCESS_TOKEN')
ADMIN_USER_ID = "Ue23b0c54b12a040a3e20ee43f51b8ef9"

app = FastAPI()
session = aiohttp.ClientSession()
async_http_client = AiohttpAsyncHttpClient(session)
line_bot_api = AsyncLineBotApi(LINE_CHANNEL_ACCESS_TOKEN, async_http_client)
parser = WebhookParser(LINE_CHANNEL_SECRET)

# 使用者狀態與記憶
user_roles = {}
user_message_count = {}
user_orgname = {}
user_basic_info_completed = {}

# 啟動訊息
identity_prompt = "您好，我是一起夢想的客服小編，我會盡力回答您的問題。\n如果沒有幫上忙，請輸入『需要幫忙』，我會請專人來協助您。"

# 用於新用戶加入後要求填寫的內容
onboarding_message = """請協助填寫以下資訊：
1、單位名稱：
2、服務縣市：
3、聯絡人職稱＋姓名＋電話：
4、服務對象（可複選）：弱勢孩童、邊緣少年、中年困境、孤獨長者、無助動物
5、服務類別（可複選）：民生照顧、教育陪伴、醫療照護、身心障礙、理念推廣、原住民、新住民、有物資需求、有志工需求"""

# OpenAI 系統前置內容
system_content_agency = """
你是一位客服專員，專門協助回答台灣一起夢想公益協會的問題。
若使用者連續輸入三則以上訊息後仍未解決問題，請於回答後附註：
「如果沒有解決到您的問題，請輸入『需要幫忙』，我將請專人回覆您。」
"""

# FAQ 關鍵字對應回覆
faq_response_map = {
    "邀請": "🙏 感謝您的邀請，如有合作機會我們會主動與您聯繫。",
    "收據": "📨 收據會在月底前彙整寄出，如有問題請隨時告知。",
    "月報會遲交": "📌 請於每月10號前上傳月報，逾期將順延至次月撥款。",
    "沒有收到款項": "💰 撥款日為每月15號（假日順延），若未收到請確認是否已完成月報與收據。",
    "資料已上傳": "📁 我們會幫您查詢最近一次的資料上傳狀況，請稍候。"
}

# 名稱格式標準化
def normalize_org_name(name):
    return unicodedata.normalize("NFKC", name.strip())

# 呼叫 OpenAI API
def call_openai_chat_api(user_message):
    openai.api_key = OPENAI_API_KEY
    try:
        response = openai.ChatCompletion.create(
            model="gpt-3.5-turbo",
            messages=[
                {"role": "system", "content": system_content_agency},
                {"role": "user", "content": user_message}
            ]
        )
        return response.choices[0].message['content']
    except Exception as e:
        return "目前無法處理您的請求，請稍後再試。"

# 取得使用者名稱
async def get_user_profile(user_id):
    try:
        profile = await line_bot_api.get_profile(user_id)
        return profile.display_name
    except:
        return "未知用戶"

# 查詢單位上傳狀態
async def handle_status_check(user_id, org_name, event):
    norm_name = normalize_org_name(org_name)
    api_url = f"https://510.org.tw/api/unit_status?name={urllib.parse.quote(norm_name)}"
    try:
        res = requests.get(api_url, timeout=10)
        if res.status_code == 200:
            data = res.json()
            if data.get("upload_status") == "已完成":
                msg = f"✅ 我們查詢到 {norm_name} 已成功完成上傳作業。"
            else:
                msg = f"📌 查詢結果顯示 {norm_name} 尚未完成上傳，請再確認。"
        else:
            msg = "❗查詢過程發生異常，請稍後再試。"
    except:
        msg = "❗查詢時發生錯誤，請稍後再試。"
    await line_bot_api.reply_message(event.reply_token, TextSendMessage(text=msg))

# 發送通知給管理員
async def notify_admin(user_id, message):
    display_name = await get_user_profile(user_id)
    text = f"⚠️ 有使用者需要協助\n使用者ID: {user_id}\n暱稱: {display_name}\n內容: {message}"
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
            message = event.message.text.strip()

            # 新用戶首次對話
            if user_id not in user_roles:
                user_roles[user_id] = "微型社福"
                user_basic_info_completed[user_id] = False
                await line_bot_api.reply_message(event.reply_token, TextSendMessage(text=identity_prompt))
                return 'OK'

            # 尚未填完基本資料
            if not user_basic_info_completed.get(user_id, False):
                if message.startswith("我是") or message.startswith("我們是"):
                    org_name = normalize_org_name(message.replace("我是", "").replace("我們是", "").strip())
                    user_orgname[user_id] = org_name
                    await line_bot_api.reply_message(event.reply_token, TextSendMessage(text=onboarding_message))
                    return 'OK'
                elif message.startswith("1、") or "單位名稱" in message:
                    user_basic_info_completed[user_id] = True
                    await line_bot_api.reply_message(event.reply_token, TextSendMessage(
                        text="已收到您的資訊，並完成建檔\n很榮幸認識您與貴單位\n一起夢想支持微型社福的腳步持續邁進\n期待未來多多交流、一起努力🤜🏻🤛🏻"))
                    await notify_admin(user_id, "新用戶加入並填妥基本資料")
                    return 'OK'
                else:
                    await line_bot_api.reply_message(event.reply_token, TextSendMessage(text=onboarding_message))
                    return 'OK'

            # 詢問上傳狀態
            if "月報" in message or "上傳" in message or "查詢" in message:
                org_name = user_orgname.get(user_id)
                if org_name:
                    await handle_status_check(user_id, org_name, event)
                else:
                    await line_bot_api.reply_message(event.reply_token, TextSendMessage(text="請先告訴我您是哪一個單位喔～"))
                return 'OK'

            # FAQ 快速回答
            for key in faq_response_map:
                if key in message:
                    await line_bot_api.reply_message(event.reply_token, TextSendMessage(text=faq_response_map[key]))
                    return 'OK'

            # 請求專人協助
            if "需要幫忙" in message:
                await line_bot_api.reply_message(event.reply_token, TextSendMessage(text="我已經通知專人協助，請耐心等候"))
                await notify_admin(user_id, message)
                return 'OK'

            # 聊天邏輯
            user_message_count[user_id] = user_message_count.get(user_id, 0) + 1
            reply = call_openai_chat_api(message)
            if user_message_count[user_id] >= 3:
                reply += "\n\n如果沒有解決到您的問題，請輸入『需要幫忙』，我將請專人回覆您。"
            await line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply))
    return 'OK'

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=int(os.getenv("PORT", 10000)))
