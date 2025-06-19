
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

# 資料結構
user_roles = {}
user_message_count = {}
user_orgname = {}
user_last_active = {}
user_is_in_human_mode = {}
user_last_messages = {}
user_basic_info = {}

# 開場白
identity_prompt = "您好，我是一起夢想的客服小編，我會盡力回答您的問題。如果沒有幫上忙，請輸入『需要幫忙』，我會請專人來協助您。"

# FAQ 關鍵字對應回覆
faq_response_map = {
    "邀請": "🙏 非常感謝您的邀請！如有合作機會，我們會再聯繫您。",
    "收據": "📨 收據會在月底彙整後寄出，若有問題我們會另行通知。",
    "月報會遲交": "📌 月報需在每月10號之前上傳，逾期款項將順延至次月15號撥付。",
    "沒有收到款項": "💰 撥款日為每月15號（遇假日順延），若未收到可能資料未齊，請再確認。",
    "資料已上傳": "📁 請問您上傳的是哪一項資料呢？我可以幫您查詢是否成功。"
}

# 查詢後台資料狀態
async def handle_status_check(user_id, org_name, event):
    name = unicodedata.normalize('NFKC', org_name.strip())
    api_url = f"https://510.org.tw/api/unit_status?name={urllib.parse.quote(name)}"
    try:
        response = requests.get(api_url)
        if response.status_code == 200:
            data = response.json()
            status = data.get("upload_status", "")
            time_info = data.get("last_upload_time", "")
            reply = f"✅ 單位「{name}」的上傳狀態為：{status}，最後上傳時間：{time_info}"
        else:
            reply = "⚠️ 查詢過程發生異常，請稍後再試。"
    except:
        reply = "⚠️ 查詢時發生錯誤，請稍後再試。"
    await line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply))

# 呼叫 ChatGPT
def call_openai_chat_api(user_message):
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
        return "目前無法處理您的請求，請稍後再試。"

# webhook 入口
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

            # 自動切回機器人模式
            if user_is_in_human_mode.get(user_id) and user_last_active.get(user_id):
                if now - user_last_active[user_id] > timedelta(minutes=30):
                    user_is_in_human_mode[user_id] = False
                    await line_bot_api.reply_message(
                        event.reply_token,
                        TextSendMessage(text="⏳ 超過 30 分鐘未互動，已切換回機器人。請問我可以幫您什麼？")
                    )
                    return "OK"

            user_last_active[user_id] = now

            # 人工模式中，暫不回應
            if user_is_in_human_mode.get(user_id):
                return "OK"

            # 進入人工模式
            if "需要幫忙" in msg:
                user_is_in_human_mode[user_id] = True
                await line_bot_api.reply_message(event.reply_token, TextSendMessage(text="✅ 已通知專人，請稍候回覆～"))
                return "OK"

            # 初次使用者
            if user_id not in user_roles:
                user_roles[user_id] = "微型社福"
                if "協會" not in display_name:
                    await line_bot_api.reply_message(event.reply_token, TextSendMessage(
                        text="以下是我們需要的基本資料，請您回覆以下項目：

1、單位名稱：
2、服務縣市：
3、聯絡人職稱＋姓名＋電話：
4、服務對象（可複選）：弱勢孩童、邊緣少年、中年困境、孤獨長者、無助動物
5、服務類別（可複選）：民生照顧、教育陪伴、醫療照護、身心障礙、理念推廣、原住民、新住民、有物資需求、有志工需求"
                    ))
                    return "OK"
                await line_bot_api.reply_message(event.reply_token, TextSendMessage(text=identity_prompt))
                return "OK"

            # 填寫完基本資料（包含五個關鍵詞）
            if all(k in msg for k in ["單位名稱", "服務縣市", "聯絡人", "服務對象", "服務類別"]):
                await line_bot_api.reply_message(event.reply_token, TextSendMessage(
                    text="✅ 已收到您的資訊，並完成建檔
很榮幸認識您與貴單位
一起夢想支持微型社福的腳步持續邁進
期待未來多多交流、一起努力🤜🏻🤛🏻"
                ))
                print(f"📝 有新用戶填妥基本資料：{user_id}")
                return "OK"

            # 提供單位名稱
            if msg.startswith("我們是") or msg.startswith("我是"):
                user_orgname[user_id] = msg.replace("我們是", "").replace("我是", "").strip()
                await line_bot_api.reply_message(event.reply_token, TextSendMessage(text="感謝提供，請問有什麼需要幫忙的？"))
                return "OK"

            # 月報查詢語意
            if any(kw in msg for kw in ["月報上傳了嗎", "月報有上傳", "我上傳了"]):
                org_name = user_orgname.get(user_id)
                if org_name:
                    await handle_status_check(user_id, org_name, event)
                else:
                    await line_bot_api.reply_message(event.reply_token, TextSendMessage(text="請問您是哪個單位？我才能幫您查詢喔！"))
                return "OK"

            # FAQ 直接回覆
            for keyword, reply in faq_response_map.items():
                if keyword in msg:
                    await line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply))
                    return "OK"

            # ChatGPT 回覆邏輯（含重複問題偵測）
            user_message_count[user_id] = user_message_count.get(user_id, 0) + 1
            user_last_messages.setdefault(user_id, []).append(msg)
            if len(user_last_messages[user_id]) > 3:
                user_last_messages[user_id] = user_last_messages[user_id][-3:]

            response = call_openai_chat_api(msg)
            if user_message_count[user_id] >= 3 and any(old in msg for old in user_last_messages[user_id]):
                response += "

如果沒有解決到您的問題，請輸入『需要幫忙』，我將請專人回覆您。"

            await line_bot_api.reply_message(event.reply_token, TextSendMessage(text=response))
    return "OK"

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 10000))
    uvicorn.run("main:app", host="0.0.0.0", port=port)
