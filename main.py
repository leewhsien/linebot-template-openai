from fastapi import FastAPI, Request
from linebot import AsyncLineBotApi, WebhookParser
from linebot.models import TextSendMessage, MessageEvent, TextMessage
from linebot.exceptions import InvalidSignatureError

import os

app = FastAPI()

line_bot_api = AsyncLineBotApi(os.getenv("LINE_CHANNEL_ACCESS_TOKEN"))
parser = WebhookParser(os.getenv("LINE_CHANNEL_SECRET"))

@app.post("/callback")
async def callback(request: Request):
    signature = request.headers.get("X-Line-Signature", "")
    body = (await request.body()).decode()

    try:
        events = parser.parse(body, signature)
    except InvalidSignatureError:
        return "Invalid signature", 400

    for event in events:
        if isinstance(event, MessageEvent) and isinstance(event.message, TextMessage):
            user_message = event.message.text.strip()

            if "我是" in user_message or "我們是" in user_message:
                await line_bot_api.reply_message(event.reply_token, TextSendMessage(
                    text="""請協助填寫以下資訊：
1、單位名稱：
2、服務縣市：
3、聯絡人職稱＋姓名＋電話：
4、服務對象（可複選）：弱勢孩童、邊緣少年、中年困境、孤獨長者、無助動物
5、服務類別（可複選）：民生照顧、教育陪伴、醫療照護、身心障礙、理念推廣、原住民、新住民、有物資需求、有志工需求"""
                ))
            else:
                await line_bot_api.reply_message(event.reply_token, TextSendMessage(
                    text="您好，我是一起夢想的客服小編，我會盡力回答您的問題。如果沒有幫上忙，請輸入『需要幫忙』，我會請專人來協助您。"
                ))
    return "OK"
