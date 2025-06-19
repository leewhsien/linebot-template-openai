
# -*- coding: utf-8 -*-
import openai
import os
import requests
import urllib.parse
import unicodedata
import aiohttp
from datetime import datetime, timedelta
from fastapi import FastAPI, Request, HTTPException
from linebot import AsyncLineBotApi, WebhookParser
from linebot.aiohttp_async_http_client import AiohttpAsyncHttpClient
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage

app = FastAPI()
session = aiohttp.ClientSession()
http_client = AiohttpAsyncHttpClient(session)
line_bot_api = AsyncLineBotApi(os.getenv("LINE_CHANNEL_ACCESS_TOKEN"), http_client)
parser = WebhookParser(os.getenv("LINE_CHANNEL_SECRET"))

LINE_ADMIN_USER_ID = os.getenv("LINE_ADMIN_USER_ID", "Ue23b0c54b12a040a3e20ee43f51b8ef9")

# 狀態記錄
user_roles = {}
user_message_count = {}
user_orgname = {}
user_last_active = {}
user_is_in_human_mode = {}
user_last_messages = {}

# 開場白
identity_prompt = "您好，我是一起夢想的客服小編，我會盡力回答您的問題。如果沒有幫上忙，請輸入『需要幫忙』，我會請專人來協助您。"

# FAQ 關鍵字對應回覆
faq_response_map = {
    "邀請": "🙏 感謝您的邀請，如有合作機會我們會主動與您聯繫。",
    "收據": "📨 收據會在月底前彙整寄出，如有問題請隨時告知。",
    "月報會遲交": "📌 請於每月10號前上傳月報，逾期將順延至次月撥款。",
    "沒有收到款項": "💰 撥款日為每月15號（假日順延），若未收到請確認是否已完成月報與收據。",
    "資料已上傳": "📁 我來幫您查詢最近一次的資料是否成功上傳。"
}

# call ChatGPT，失敗時轉人工
async def call_openai_chat_api(user_message, user_id, display_name):
    openai.api_key = os.getenv("OPENAI_API_KEY")
    base_prompt = "你是一位客服專員，專門協助回答台灣一起夢想公益協會的問題。"
    try:
        result = openai.ChatCompletion.create(
            model="gpt-3.5-turbo",
            messages=[
                {"role": "system", "content": base_prompt},
                {"role": "user", "content": user_message},
            ]
        )
        return result.choices[0].message["content"]
    except Exception as e:
        user_is_in_human_mode[user_id] = True
        if LINE_ADMIN_USER_ID:
            alert = f"⚠️ ChatGPT 回覆失敗：\n用戶：{display_name}\nID：{user_id}\n問題：{user_message}"
            await line_bot_api.push_message(LINE_ADMIN_USER_ID, TextSendMessage(text=alert))
        return "您的問題或許需要專人協助，已通知一起夢想的夥伴，請耐心等候。"

# 查詢單位是否上傳
async def handle_status_check(user_id, org_name, event):
    name = unicodedata.normalize('NFKC', org_name.strip())
    api_url = f"https://510.org.tw/api/unit_status?name={urllib.parse.quote(name)}"
    try:
        response = requests.get(api_url)
        if response.status_code == 200:
            data = response.json()
            status = data.get("upload_status", "")
            time_info = data.get("last_upload_time", "")
            reply = f"✅ 查詢結果：{name} 的上傳狀態為「{status}」，時間：{time_info or '未提供'}"
        else:
            reply = "⚠️ 查詢過程發生錯誤，請稍後再試。"
    except:
        reply = "⚠️ 查詢時發生例外錯誤，請稍後再試。"
    await line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply))

@app.post("/callback")
async def callback(request: Request):
    signature = request.headers["X-Line-Signature"]
    body = (await request.body()).decode()
    try:
        events = parser.parse(body, signature)
    except InvalidSignatureError:
        raise HTTPException(status_code=400, detail="Invalid signature")

    now = datetime.utcnow()

    for event in events:
        if isinstance(event, MessageEvent) and isinstance(event.message, TextMessage):
            user_id = event.source.user_id
            msg = event.message.text.strip()
            display_name = (await line_bot_api.get_profile(user_id)).display_name

            # 自動切回機器人
            if user_is_in_human_mode.get(user_id) and user_last_active.get(user_id):
                if now - user_last_active[user_id] > timedelta(minutes=30):
                    user_is_in_human_mode[user_id] = False
                    await line_bot_api.reply_message(event.reply_token, TextSendMessage(text="⏳ 已超過 30 分鐘，系統已切回自動回覆。請問我可以幫您什麼？"))
                    return "OK"
            user_last_active[user_id] = now

            # 若已切換人工則不回覆
            if user_is_in_human_mode.get(user_id):
                return "OK"

            # 進入人工模式
            if "需要幫忙" in msg:
                user_is_in_human_mode[user_id] = True
                await line_bot_api.reply_message(event.reply_token, TextSendMessage(text="✅ 我已經通知專人協助，請耐心等候"))
                if LINE_ADMIN_USER_ID:
                    alert = f"🔔 有用戶需要協助：\n用戶：{display_name}\nID：{user_id}\n訊息：{msg}"
                    await line_bot_api.push_message(LINE_ADMIN_USER_ID, TextSendMessage(text=alert))
                return "OK"

            # 初次對話
            if user_id not in user_roles:
                user_roles[user_id] = "微型社福"
                await line_bot_api.reply_message(event.reply_token, TextSendMessage(text=identity_prompt))
                return "OK"

            # 自動判斷是否單位名稱（含關鍵詞）
            if any(kw in msg for kw in ["協會", "基金會", "發展中心", "機構", "庇護工場", "社福", "單位"]):
                user_orgname[user_id] = msg.strip()

            # 要求填寫單位資訊
            if not user_orgname.get(user_id):
                await line_bot_api.reply_message(event.reply_token, TextSendMessage(
                    text="請協助填寫以下資訊：
1、單位名稱：
2、服務縣市：
3、聯絡人職稱＋姓名＋電話：
4、服務對象（可複選）：弱勢孩童、邊緣少年、中年困境、孤獨長者、無助動物
5、服務類別（可複選）：民生照顧、教育陪伴、醫療照護、身心障礙、理念推廣、原住民、新住民、有物資需求、有志工需求"
                ))
                return "OK"

            # 月報查詢
            if any(kw in msg for kw in ["月報上傳", "月報有上傳", "我有上傳月報", "幫我查月報"]):
                org_name = user_orgname.get(user_id)
                if org_name:
                    await handle_status_check(user_id, org_name, event)
                else:
                    await line_bot_api.reply_message(event.reply_token, TextSendMessage(text="請問您是哪一個單位？我才能幫您查詢喔！"))
                return "OK"

            # FAQ 回覆
            for keyword, reply in faq_response_map.items():
                if keyword in msg:
                    await line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply))
                    return "OK"

            # ChatGPT 回覆（含相似判斷）
            user_message_count[user_id] = user_message_count.get(user_id, 0) + 1
            user_last_messages.setdefault(user_id, []).append(msg)
            if len(user_last_messages[user_id]) > 3:
                user_last_messages[user_id] = user_last_messages[user_id][-3:]

            reply = await call_openai_chat_api(msg, user_id, display_name)
            if user_message_count[user_id] >= 3 and any(old in msg for old in user_last_messages[user_id]):
                reply += "

如果沒有解決到您的問題，請輸入『需要幫忙』，我將請專人回覆您。"
            await line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply))
    return "OK"

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 10000))
    uvicorn.run("main:app", host="0.0.0.0", port=port)
