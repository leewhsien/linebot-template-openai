# -*- coding: utf-8 -*-
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#  You may obtain a copy of the License at
#
#       https://www.apache.org/licenses/LICENSE-2.0
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and limitations under the License.

import openai
import os
import sys
import json
import requests
import aiohttp

from fastapi import Request, FastAPI, HTTPException
from linebot import AsyncLineBotApi, WebhookParser
from linebot.aiohttp_async_http_client import AiohttpAsyncHttpClient
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage

# 環境變數設定
OPENAI_API_KEY = os.getenv('OPENAI_API_KEY', None)
LINE_CHANNEL_SECRET = os.getenv('LINE_CHANNEL_SECRET', None)
LINE_CHANNEL_ACCESS_TOKEN = os.getenv('LINE_CHANNEL_ACCESS_TOKEN', None)
LINE_USER_ID = "Ue23b0c54b12a040a3e20ee43f51b8ef9"  # 直接設定你的 LINE User ID

# 初始化 LINE Bot
app = FastAPI()
session = aiohttp.ClientSession()
async_http_client = AiohttpAsyncHttpClient(session)
line_bot_api = AsyncLineBotApi(LINE_CHANNEL_ACCESS_TOKEN, async_http_client)
parser = WebhookParser(LINE_CHANNEL_SECRET)

# LINE Notify URL
NOTIFY_URL = "https://api.line.me/v2/bot/message/push"

def call_openai_chat_api(user_message):
    """ 呼叫 OpenAI API 進行回應 """
    openai.api_key = OPENAI_API_KEY

    system_content = """
你是一位客服專員，專門協助回答台灣一起夢想公益協會的問題。請根據以下資訊回覆使用者的問題：

公司名稱：台灣一起夢想公益協會（簡稱「一起夢想」）
成立年份：2012年
官網：https://510.org.tw/
客服專線：(02)6604-2510
客服時間：週一至週五，上午10:00至下午6:00
客服信箱：service@510.org.tw
門市地址：台北市忠孝東路四段220號11樓

📦 常見問題 FAQ（協會上傳/後台操作類）：

1. 檔案上傳到一半網頁當機怎麼辦？
   - 請確認檔案大小未超過 2MB。若超過，可使用免費線上壓縮工具後再重新上傳。

2. 財報資料無法提供給國稅局怎麼辦？
   - 請提供理監事會議通過的財報相關資料，將由專人與您確認。

3. 財報是整份無法拆分怎麼辦？
   - 可使用免費線上服務拆分檔案，再重新上傳。

4. 沒有正職人員無法提供勞保證明怎麼辦？
   - 請下載「正職 0 人聲明文件」，加蓋協會大章後掃描上傳。

📦 常見問題 FAQ（捐款與收據相關）：

5. 為什麼這個月沒有收到款項？
   - 撥款日為每月 15 日（遇假日順延）。可能原因為：(1) 一起夢想未於 9 號前收到收據；(2) 未於 10 號上傳款項使用報告。

6. 如何查詢我的捐款資料？
   - 可至徵信查詢區（https://510.org.tw/donation_information）輸入資料，系統會寄送紀錄至您提供的 email。

7. 捐款期數怎麼設定？能提前終止嗎？
   - 2023/10/11 前捐款：固定 36 期，到期自動終止。
   - 之後捐款：依信用卡到期日為期。
   - 若要變更，請填寫客服表單申請「變更總捐款期數」。

8. 想調整每月捐款金額怎麼做？
   - 請填寫客服表單（https://forms.gle/HkvmUzFGRwfVWs1n9）申請「變更捐款金額」。

9. 更換信用卡怎麼做？
   - 步驟一：填客服表單申請「變更扣款信用卡」，我們會終止原訂單。
   - 步驟二：收到 email 連結後，請重新設定新的信用卡資訊。

10. 為什麼授權失敗？
   - 可能原因包括：信用卡失效、額度不足、金融卡餘額不足等。
   - 可填表單申請「再次授權當月扣款」。

11. 是否會提供捐款收據？
   - 電子收據會寄至 email；定期定額於每月 1 號扣款當下寄出，單筆捐款則立即寄出。
   - 年度收據於隔年 2 月前寄出；如未收到，可填寫表單申請補寄（電子或紙本）。

12. 捐款如何報稅？
   - 方法一：自行列印電子收據報稅。
   - 方法二：由我們代為申報，請於每年 2/5 前填寫申請表單。

13. 想取消定期定額捐款？
   - 方式一：至徵信查詢區取得資料後於 email 中取消。
   - 方式二：填寫客服表單申請取消。

📦 其他常見服務：

14. 志工招募資訊：https://510.org.tw/volunteer_applications
15. 心靈沈靜活動報名：https://510.org.tw/peace_mind
16. 小聚活動報名：https://510.org.tw/event_applications
17. 申請合作成為微型社福：https://510.org.tw/collaboration_apply
18. 申請定期定額捐款支持：https://510.org.tw/agency_applications
"""

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
    """通知管理員"""
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}"
    }

    notification_message = (
        f"🔔 收到未知問題通知\n"
        f"用戶名稱：{display_name}\n"
        f"用戶 ID：{user_id}\n"
        f"訊息內容：{message}"
    )

    data = {
        "to": LINE_USER_ID,
        "messages": [{"type": "text", "text": notification_message}]
    }

    requests.post(NOTIFY_URL, headers=headers, json=data)

async def get_user_profile(user_id):
    """取得用戶名稱"""
    try:
        profile = await line_bot_api.get_profile(user_id)
        return profile.display_name
    except Exception as e:
        print(f"取得用戶名稱失敗：{e}")
        return "未知用戶"

@app.post("/callback")
async def handle_callback(request: Request):
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
            user_message = event.message.text
            display_name = await get_user_profile(user_id)

            print(f"用戶名稱：{display_name}")
            print(f"用戶 ID：{user_id}")
            print(f"收到訊息：{user_message}")

            response_message = call_openai_chat_api(user_message)

            if "抱歉" in response_message:
                notify_admin(user_id, display_name, user_message)

            await line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text=response_message)
            )

    return 'OK'

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
