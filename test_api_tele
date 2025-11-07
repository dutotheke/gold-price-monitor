import requests, os
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
url = f"https://api.telegram.org/bot{TOKEN}/getUpdates"
print(requests.get(url).text)
