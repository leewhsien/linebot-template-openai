# -*- coding: utf-8 -*-
import os
import openai
from fastapi import FastAPI, Request, HTTPException
from linebot.v3.webhook import WebhookHandler
from linebot.v3.messaging import AsyncLineBotApi
from linebot.v3.http_client.aiohttp import AiohttpAsyncHttpClient
from linebot.exceptions import InvalidSignatureError
from linebot.models import TextSendMessage, MessageEvent, TextMessage

app = FastAPI()

# 環境變數
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
LINE_CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET")
LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")

openai.api_key = OPENAI_API_KEY
handler = WebhookHandler(LINE_CHANNEL_SECRET)
line_bot_api = AsyncLineBotApi(
    channel_access_token=LINE_CHANNEL_ACCESS_TOKEN,
    http_client=AiohttpAsyncHttpClient()
)

@app.post("/callback")
async def callback(request: Request):
    signature = request.headers.get("X-Line-Signature", "")
    body = await request.body()

    try:
        handler.handle(body.decode("utf-8"), signature)
    except InvalidSignatureError:
        raise HTTPException(status_code=400, detail="Invalid signature")

    return "OK"

@handler.add(MessageEvent, message=TextMessage)
async def handle_message(event: MessageEvent):
    user_message = event.message.text

    # 呼叫 GPT-4 回覆
    response = openai.ChatCompletion.create(
        model="gpt-4",
        messages=[
            {"role": "user", "content": user_message}
        ]
    )
    reply = response.choices[0].message.content.strip()

    await line_bot_api.reply_message(
        event.reply_token,
        TextSendMessage(text=reply)
    )
