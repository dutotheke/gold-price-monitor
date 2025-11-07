import os
import requests
from bs4 import BeautifulSoup

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
    print("⚠️ Thiếu TELEGRAM_BOT_TOKEN hoặc TELEGRAM_CHAT_ID.")
    exit(1)

def get_gold_price():
    url = "https://baotinmanhhai.vn/"
    headers = {"User-Agent": "Mozilla/5.0"}
    resp = requests.get(url, headers=headers, timeout=20)
    soup = BeautifulSoup(resp.text, "html.parser")

    rows = soup.select("table tbody tr")
    data = []
    for r in rows:
        cols = [c.get_text(strip=True) for c in r.find_all("td")]
        if len(cols) >= 3:
            data.append(cols)
    return data

def load_last_data():
    try:
        with open("last_price.txt", "r", encoding="utf-8") as f:
            return f.read().strip()
    except FileNotFoundError:
        return ""

def save_last_data(text):
    with open("last_price.txt", "w", encoding="utf-8") as f:
        f.write(text)

def send_telegram_message(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message}
    requests.post(url, json=payload, timeout=20, proxies={"http": None, "https": None})

def main():
    data = get_gold_price()
    text = "\n".join([" | ".join(row) for row in data])

    last_text = load_last_data()
    if text != last_text:
        msg = f"🪙 Cập nhật giá vàng mới:\n\n{text}"
        send_telegram_message(msg)
        save_last_data(text)
        print("✅ Đã gửi Telegram (có thay đổi).")
    else:
        print("⏳ Không có thay đổi.")

if __name__ == "__main__":
    main()
