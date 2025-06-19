import os
from fastapi import FastAPI, Request, HTTPException
from linebot import LineBotApi, WebhookParser
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage

app = FastAPI()

LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
LINE_CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET")

line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
parser = WebhookParser(LINE_CHANNEL_SECRET)

@app.post("/callback")
async def callback(request: Request):
    signature = request.headers.get("X-Line-Signature", "")
    body = (await request.body()).decode()

    try:
        events = parser.parse(body, signature)
    except InvalidSignatureError:
        raise HTTPException(status_code=400, detail="Invalid signature")

    for event in events:
        if isinstance(event, MessageEvent) and isinstance(event.message, TextMessage):
            msg = event.message.text.strip()

            if "我是" in msg or "我們是" in msg:
                reply = (
                    "請協助填寫以下資訊：\n"
                    "1、單位名稱：\n"
                    "2、服務縣市：\n"
                    "3、聯絡人職稱＋姓名＋電話：\n"
                    "4、服務對象（可複選）：弱勢孩童、邊緣少年、中年困境、孤獨長者、無助動物\n"
                    "5、服務類別（可複選）：民生照顧、教育陪伴、醫療照護、身心障礙、理念推廣、原住民、新住民、有物資需求、有志工需求"
                )
            else:
                reply = (
                    "您好，我是一起夢想的客服小編，我會盡力回答您的問題。\n"
                    "如果沒有幫上忙，請輸入『需要幫忙』，我會請專人來協助您。"
                )

            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text=reply)
            )

    return "OK"

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 10000))
    uvicorn.run("main:app", host="0.0.0.0", port=port)
