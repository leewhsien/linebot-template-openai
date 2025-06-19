from fastapi import FastAPI, Request, HTTPException
from linebot import LineBotApi, WebhookParser
from linebot.models import MessageEvent, TextMessage, TextSendMessage
from linebot.exceptions import InvalidSignatureError
import os
import datetime

app = FastAPI()

# 載入環境變數
LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
LINE_CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET")
ADMIN_USER_ID = os.getenv("LINE_ADMIN_USER_ID", "Uxxxxxxxxxxxxxxxxxxxxx")

line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
parser = WebhookParser(LINE_CHANNEL_SECRET)

# 使用者狀態儲存
user_roles = {}           # user_id -> "微型社福"
user_message_count = {}   # user_id -> 問答次數
user_orgname = {}         # user_id -> 單位名稱
user_basic_info = {}      # user_id -> 是否已提供基本資料
manual_override = set()   # 需要人工處理的 user_id

# FAQ 關鍵字對應回覆
faq_response_map = {
    "邀請": "🙏 感謝您的邀請，如有合作機會我們會主動與您聯繫。",
    "收據": "📨 收據會在月底前彙整寄出，如有問題請隨時告知。",
    "月報會遲交": "📌 請於每月10號前上傳月報，逾期將順延至次月撥款。",
    "沒有收到款項": "💰 撥款日為每月15號（假日順延），若未收到請確認是否已完成月報與收據。",
    "資料已上傳": "📁 我來幫您查詢最近月份是否成功上傳。"
}

# 初次歡迎詞
identity_prompt = """親愛的夥伴您好：
麻煩請回覆以下基本資訊，並拍下您的名片，讓我們提供您最適合的協助 👍

1、單位名稱：
2、服務縣市：
3、聯絡人職稱＋姓名＋電話：
4、服務對象（可複選）：弱勢孩童、邊緣少年、中年困境、孤獨長者、無助動物
5、服務類別（可複選）：民生照顧、教育陪伴、醫療照護、身心障礙、理念推廣、原住民、新住民、有物資需求、有志工需求

🌝 有經費需求，想了解「定期定額」 的服務，請輸入：「我要申請定期定額」

🌝 有財務評估、記帳報稅需求，想了解「財務健檢」服務
🔍 免費財務評估，解答疑惑，澄清現況
🔍 提供專業健檢報告及優化建議
🔍 由我們的專業會計團隊幫助您記帳報稅
立即申請：https://dream510.pse.is/4wyefg

若您有設計或法律需求，請填寫以下表單：
律師諮詢表單：https://dream510.pse.is/58hsw5
視覺設計表單：https://dream510.pse.is/57n4nf

👍 期待與您攜手前行，記得加入臉書社團獲得第一手消息：https://godreamer.pse.is/3qu2vc
"""

# 用戶資料完成後的回覆
basic_info_complete_msg = """已收到您的資訊，並完成建檔
很榮幸認識您與貴單位
一起夢想支持微型社福的腳步持續邁進
期待未來多多交流、一起努力🤜🏻🤛🏻"""

# 回傳給 OpenAI 模型（可自行擴充串接）
def call_openai_chat_api(user_input):
    # 模擬回應，可串接 OpenAI Chat API
    return f"🤖 我目前無法完全理解您的問題，但我會持續學習來更好地幫助您。"

# 獲取用戶名稱（顯示用）
async def get_user_profile(user_id):
    try:
        profile = line_bot_api.get_profile(user_id)
        return profile.display_name
    except:
        return "夥伴"

# 模擬查詢月報狀態（實際串接後台邏輯）
async def handle_status_check(user_id, org_name, event):
    # 可加上實際查詢邏輯
    await line_bot_api.reply_message(
        event.reply_token,
        TextSendMessage(text=f"📄 已為您查詢：{org_name} 最近一次月報已成功上傳。")
    )

# 通知管理員
def notify_admin(message):
    try:
        line_bot_api.push_message(ADMIN_USER_ID, TextSendMessage(text=message))
    except:
        pass

@app.post("/callback")
async def callback(request: Request):
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
            user_message = event.message.text.strip()
            display_name = await get_user_profile(user_id)

            # 若是需要人工協助
            if user_id in manual_override:
                return 'OK'

            # 新用戶流程
            if user_id not in user_basic_info or not user_basic_info.get(user_id, False):
                if any(keyword in user_message for keyword in ["單位名稱", "聯絡人", "服務縣市", "服務對象", "服務類別"]):
                    user_basic_info[user_id] = True
                    await line_bot_api.reply_message(event.reply_token, TextSendMessage(text=basic_info_complete_msg))
                    notify_admin(f"📥 有新用戶加入並填妥基本資料：\n{display_name}（{user_id}）")
                    return 'OK'
                else:
                    await line_bot_api.reply_message(event.reply_token, TextSendMessage(text=identity_prompt))
                    return 'OK'

            # 處理 FAQ 自動回覆
            for keyword, response in faq_response_map.items():
                if keyword in user_message:
                    await line_bot_api.reply_message(event.reply_token, TextSendMessage(text=response))
                    return 'OK'

            # 「月報有上傳嗎？」自動辨識單位
            if "月報" in user_message or "上傳" in user_message:
                org_name = user_orgname.get(user_id)
                if org_name:
                    await handle_status_check(user_id, org_name, event)
                else:
                    await line_bot_api.reply_message(event.reply_token, TextSendMessage(text="請先提供您的單位名稱，我才能幫您查詢喔～"))
                return 'OK'

            # 儲存單位名稱
            if user_message.startswith("我是") or user_message.startswith("我們是"):
                org_name = user_message.replace("我們是", "").replace("我是", "").strip()
                user_orgname[user_id] = org_name
                await line_bot_api.reply_message(event.reply_token, TextSendMessage(text="感謝提供，請問有什麼需要幫忙的？"))
                return 'OK'

            # 「需要幫忙」轉人工
            if user_message == "需要幫忙":
                manual_override.add(user_id)
                notify_admin(f"⚠️ 用戶需要人工協助：\n{display_name}（{user_id}）")
                await line_bot_api.reply_message(event.reply_token, TextSendMessage(text="我已經通知專人協助，請耐心等候 🙏"))
                return 'OK'

            # 累計問題次數（偵測是否需要轉人工）
            user_message_count[user_id] = user_message_count.get(user_id, 0) + 1
            reply = call_openai_chat_api(user_message)

            if user_message_count[user_id] >= 3:
                reply += "\n\n如果沒有解決到您的問題，請輸入『需要幫忙』，我會請專人來協助您。"

            # 偵測回覆無效則通知管理員
            if "無法完全理解" in reply:
                notify_admin(f"❓ 機器人未能回覆：{display_name}（{user_id}）\n訊息：{user_message}")
                reply = "您的問題或許需要專人協助，已通知一起夢想的夥伴，請耐心等候 🙇‍♀️"

            await line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply))

    return 'OK'

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 10000))
    uvicorn.run("main:app", host="0.0.0.0", port=port)
