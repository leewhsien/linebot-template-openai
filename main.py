# -*- coding: utf-8 -*-
import openai
import os
import json
import requests
import aiohttp
import urllib.parse
import unicodedata
import re
from datetime import datetime, timedelta

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
manual_override = {}
manual_override_time = {}

user_id = event.source.user_id
text = event.message.text.strip()

# ✅ 若該用戶目前處於人工接管狀態
if manual_override.get(user_id, False):
    now = datetime.now()

    # 自動解除：15分鐘後恢復機器人功能
    if user_id in manual_override_time and now - manual_override_time[user_id] > timedelta(minutes=15):
        manual_override[user_id] = False
    else:
        # 若使用者說了解、謝謝等 → 手動解除
        if any(kw in text.lower() for kw in ["謝謝", "了解", "知道了", "收到", "ok", "好喔", "好的"]):
            manual_override[user_id] = False
            await line_bot_api.reply_message(event.reply_token, TextSendMessage(
                text="很高興幫上忙，接下來有問題我會繼續協助您！"
            ))
        else:
            return "OK"  # 暫停機器人回覆

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
faq_keywords_map = [
    {
        "keywords": ["沒有收到款項", "這個月沒撥款", "還沒有收到款項", "撥款了嗎", "還沒有入帳"],
        "reply": "📨 一起夢想每月撥款一次於每月15號（遇假日順延）；若未收到款項可能是因：\n(1) 一起夢想未於10號前收到協會的捐款收據\n(2) 協會未於10號前上傳款項使用報告\n款項將於下個月15號一併撥款喔"
    },
    {
        "keywords": ["邀請參加", "共襄盛舉", "歡迎蒞臨", "歡迎參加"],
        "reply": "🙏 非常感謝您熱情的邀請與好意！因為目前大家都在持續服務微型社福的夥伴們，實在不便抽身參與此一活動，也祝福活動一切順利圓滿，再次感謝您的邀請與用心。"
    },
    {
        "keywords":["月報未上傳", "月報會遲交", "月報已上傳"],
        "reply": "月報需在每月10號前上傳，如逾期，款項將於下個月15號一併撥款。"
    },
     {
        "keywords":["收據已寄出", "收據有收到嗎"],
        "reply": "📨 謝謝您，由於紙本單據眾多，無法一一幫忙查詢，請見諒；如收據有問題會另外通知。"
    },
    {
        "keywords":["資料已上傳", "財報已上傳，請查收"],
        "reply": "謝謝您，由於服務單位眾多，無法一一幫忙查詢，請見諒；如有任何問題會再另行通知，謝謝。"
    },
     {
        "keywords":["募款沒有募滿", "填補沒有填滿"],
        "reply": "📌 因為我們填補水庫近期較緊縮，因此填補優先針對：餘款+新募得款項低於目標金額的單位進行填補，希望可以盡量幫到所有單位~"
    },
    {
        "keywords":["資料已上傳，請查收", "財報已上傳，請查收"],
        "reply": "謝謝您，由於服務單位眾多，無法一一幫忙查詢，請見諒；如有任何問題會再另行通知，謝謝。"
    },
     {
        "keywords":["檔案上傳到一半，網頁一直顯示圈圈或當機", "檔案上傳不了"],
        "reply": "請確認檔案大小是否超過 2MB。可使用 https://www.ilovepdf.com/zh-tw/compress_pdf 壓縮後上傳"
    },
    {
        "keywords":["我的財報是一整份，無法拆分檔案怎麼辦"],
        "reply": "可利用 https://www.ilovepdf.com/zh-tw/split_pdf 進行檔案拆分後，再重新上傳資料至後台"
    },
     {
        "keywords":["協會目前沒有正職", "都是兼職", "都是志工"],
        "reply": "請下載請下載 https://drive.google.com/file/d/19yVO04kT0CT4TK_204HGqQRM8cBroG0/view?usp=drive_link 並用協會大章印後掃描上傳，謝謝"
    },
]

def get_faq_reply(user_text):
    user_text = user_text.lower()
    for faq in faq_keywords_map:
        for keyword in faq["keywords"]:
            if keyword in user_text:
                return faq["reply"]
    return None

def normalize_org_name(name):
    return unicodedata.normalize("NFKC", name.strip())

def message_looks_like_profile(msg):
    status, info = parse_registration_info(msg)
    return status == "success"
    
# 👉 建議放在這裡：message_looks_like_profile() 上面

def parse_registration_info(text):
    lines = text.strip().split("\n")
    info = {
        "unit": None,
        "city": None,
        "contact": None,
        "targets": None,
        "services": None
    }

    for line in lines:
        if not info["unit"] and "協會" in line:
            info["unit"] = line.strip()
        elif not info["city"] and any(city in line for city in ["新北", "台北", "台中", "台南", "高雄", "基隆", "新竹", "嘉義", "花蓮", "台東", "南投", "宜蘭", "雲林", "彰化", "苗栗", "屏東", "澎湖", "金門", "連江"]):
            info["city"] = line.strip()
        elif not info["contact"] and (
            any(c in line for c in ["理事", "總幹事", "社工", "志工", "牧師", "老師", "理事長", "秘書長", "主任", "負責人"]) or
            re.search(r"\d{4,}", line) or
            len(line.strip()) >= 5
        ):
            info["contact"] = line.strip()
        elif not info["targets"] and any(k in line for k in ["弱勢孩童", "邊緣少年", "中年困境", "孤獨長者", "無助動物"]):
            info["targets"] = line.strip()
        elif not info["services"] and any(k in line for k in ["民生照顧", "教育陪伴", "醫療照護", "身心障礙", "理念推廣", "原住民", "新住民", "有物資需求", "有志工需求"]):
            info["services"] = line.strip()

    if all(v is not None for v in info.values()):
        return "success", info
    else:
        return "incomplete", info

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
    except:
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
                text = f"✅ 查詢結果：{norm_name} 已完成上傳。"
            else:
                text = f"⚠️ 查詢結果：{norm_name} 尚未完成上傳，請確認。"
        else:
            text = "❗ 查詢過程異常，請稍後再試。"
    except:
        text = "❗ 查詢時發生錯誤，請稍後再試。"
    await line_bot_api.reply_message(event.reply_token, TextSendMessage(text=text))

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
            profile_name = await get_user_profile(user_id)

            if user_id not in user_roles:
                user_roles[user_id] = "微型社福"
                user_has_provided_info[user_id] = False

                profile_name = await get_user_profile(user_id)
                await line_bot_api.push_message(ADMIN_USER_ID, TextSendMessage(
                    text=f"🆕 有新用戶首次傳訊息：\n用戶名稱：{profile_name}\nID：{user_id}\n訊息內容：{text}"
                ))

                await line_bot_api.reply_message(event.reply_token, TextSendMessage(
                    text="您好，我是一起夢想的客服小編，我會盡力回答您的問題。\n請先協助填寫基本資料：\n" + onboarding_message
                ))
                return "OK"
                

            def get_faq_reply(user_text):
                user_text = user_text.lower()
                for faq in faq_keywords_map:
                    for keyword in faq["keywords"]:
                        if keyword in user_text:
                            return faq["reply"]
                return None
            

            if not user_has_provided_info.get(user_id, False):
                if message_looks_like_profile(text):
                    user_has_provided_info[user_id] = True
                    for line in text.split("\n"):
                        if "單位名稱" in line:
                            user_orgname[user_id] = line.replace("單位名稱", "").replace("：", "").strip()
                    await line_bot_api.reply_message(event.reply_token, TextSendMessage(text=completion_message))
                    await line_bot_api.push_message(ADMIN_USER_ID, TextSendMessage(
                        text=f"🎉 有新用戶完成建檔：\n用戶名稱：{profile_name}\n內容：\n{text}"
                    ))
                    return "OK"
                else:
                    await line_bot_api.reply_message(event.reply_token, TextSendMessage(text=onboarding_message))
                    return "OK"

            if text == "需要幫忙":
                await line_bot_api.reply_message(event.reply_token, TextSendMessage(
                    text="我已經通知專人協助，請耐心等候。"
                ))
                await line_bot_api.push_message(ADMIN_USER_ID, TextSendMessage(
                    text=f"⚠️ 用戶請求協助：\n用戶名稱：{profile_name}\n訊息：需要幫忙"
                ))
                return "OK"

            for keyword, reply_text in faq_keywords_map.items():
                if keyword in text:
                    await line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_text))
                    return "OK"
                    
            if not any(k in text for k in faq_keywords_map.keys()) and                 "上傳" not in text and "資料" not in text and "月報" not in text and                 not text.startswith("我是") and not text.startswith("我們是"):

                await line_bot_api.reply_message(
                    event.reply_token,
                    TextSendMessage(
                        text="對不起，我們專注在協助回答台灣一起夢想公益協會的相關問題；您所提的問題可能需要專人協助，已通知一起夢想的夥伴，請耐心等候。"
                    )
                )

                await line_bot_api.push_message(
                    ADMIN_USER_ID,
                    TextSendMessage(
                        text=f"⚠️ 收到與主題偏離的訊息：\n用戶名稱：{profile_name}\n訊息內容：{text}"
                    )
                )
                return "OK"

            if "上傳" in text or "資料" in text or "月報" in text:
                org = user_orgname.get(user_id)
                if org:
                    await handle_status_check(user_id, org, event)
                else:
                    await line_bot_api.reply_message(event.reply_token, TextSendMessage(
                        text="請告訴我您是哪一個單位，我才能幫您查詢。"
                    ))
                return "OK"
                
            if not any(k in text for k in faq_keywords_map.keys()) and                 "上傳" not in text and "資料" not in text and "月報" not in text and                 not text.startswith("我是") and not text.startswith("我們是"):

                await line_bot_api.reply_message(
                    event.reply_token,
                    TextSendMessage(
                        text="對不起，我們專注在協助回答台灣一起夢想公益協會的相關問題；您所提的問題可能需要專人協助，已通知一起夢想的夥伴，請耐心等候。"
                    )
                )

                await line_bot_api.push_message(
                    ADMIN_USER_ID,
                    TextSendMessage(
                        text=f"⚠️ 收到與主題偏離的訊息：\n用戶名稱：{profile_name}\n訊息內容：{text}"
                    )
                )
                return "OK"

            if text.startswith("我們是") or text.startswith("我是"):
                org = text.replace("我們是", "").replace("我是", "").strip()
                user_orgname[user_id] = org
                await line_bot_api.reply_message(event.reply_token, TextSendMessage(
                    text="好的，我已記下您的單位，請問有什麼需要幫忙的？"
                ))
                return "OK"

            user_message_count[user_id] = user_message_count.get(user_id, 0) + 1

            reply = call_openai_chat_api(text)

            if user_message_count[user_id] >= 3:
                reply += "\n\n如果沒有解決到您的問題，請輸入『需要幫忙』，我將請專人回覆您。"

            await line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply))
            return "OK"
            
            if not any(k in text for k in faq_keywords_map.keys()) and                 "上傳" not in text and "資料" not in text and "月報" not in text and                 not text.startswith("我是") and not text.startswith("我們是"):

                await line_bot_api.reply_message(
                    event.reply_token,
                    TextSendMessage(
                        text="對不起，我們專注在協助回答台灣一起夢想公益協會的相關問題；您所提的問題可能需要專人協助，已通知一起夢想的夥伴，請耐心等候。"
                    )
                )

                await line_bot_api.push_message(
                    ADMIN_USER_ID,
                    TextSendMessage(
                        text=f"⚠️ 收到與主題偏離的訊息：\n用戶名稱：{profile_name}\n訊息內容：{text}"
                    )
                )
                return "OK"

    return "OK"

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 10000))
    uvicorn.run("main:app", host="0.0.0.0", port=port)
