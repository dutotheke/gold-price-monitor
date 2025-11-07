#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import requests
from bs4 import BeautifulSoup
import csv
import sys
from datetime import datetime
from pathlib import Path

URL = "https://baotinmanhhai.vn/"
HEADERS = {"User-Agent": "Mozilla/5.0"}
LAST_FILE = Path("gold_last.csv")  # file lưu giá gần nhất


# ---------------- scraping ----------------
def fetch_html():
    r = requests.get(URL, headers=HEADERS, timeout=15)
    r.raise_for_status()
    return r.text


def parse_gold_table(html):
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table", class_="gold-table-content")
    if not table:
        raise ValueError("Không tìm thấy bảng giá vàng (class='gold-table-content')")
    items = []
    tbody = table.find("tbody")
    if not tbody:
        return items
    for row in tbody.find_all("tr"):
        cols = row.find_all("td")
        if len(cols) >= 3:
            product = cols[0].get_text(strip=True)
            buy = cols[1].get_text(strip=True).replace(".", "")
            sell = cols[2].get_text(strip=True).replace(".", "")
            items.append({
                "product": product,
                "buy": buy if buy else None,
                "sell": sell if sell else None
            })
    return items


# ---------------- file & compare ----------------
def load_last_prices():
    """Đọc file gold_last.csv nếu có."""
    if not LAST_FILE.exists():
        return []
    items = []
    with open(LAST_FILE, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            items.append(row)
    return items


def save_prices(items, file_path):
    with open(file_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["product", "buy", "sell"])
        writer.writeheader()
        writer.writerows(items)


def has_changes(new_items, old_items):
    """So sánh danh sách giá mới và cũ."""
    if not old_items:
        return True  # lần đầu chạy
    if len(new_items) != len(old_items):
        return True
    for n, o in zip(new_items, old_items):
        if (n["product"] != o["product"]) or (n["buy"] != o["buy"]) or (n["sell"] != o["sell"]):
            return True
    return False


# ---------------- telegram helpers ----------------
def send_telegram_message(bot_token, chat_id, text, parse_mode="HTML"):
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {"chat_id": chat_id, "text": text, "parse_mode": parse_mode}
    r = requests.post(url, json=payload, timeout=20)
    r.raise_for_status()
    return r.json()


def send_telegram_document(bot_token, chat_id, filepath, caption=None):
    url = f"https://api.telegram.org/bot{bot_token}/sendDocument"
    with open(filepath, "rb") as f:
        files = {"document": (os.path.basename(filepath), f)}
        data = {"chat_id": chat_id}
        if caption:
            data["caption"] = caption
        r = requests.post(url, data=data, files=files, timeout=60)
    r.raise_for_status()
    return r.json()


# ---------------- formatting ----------------
def format_message(items, scraped_at=None):
    if scraped_at is None:
        scraped_at = datetime.now()
    header = (
        f"<b>🏅 GIÁ VÀNG HÔM NAY — BẢO TÍN MẠNH HẢI</b>\n"
        f"🕓 {scraped_at.strftime('%Y-%m-%d %H:%M:%S')}\n\n"
    )
    lines = []
    for it in items:
        buy = f"{int(it['buy']):,}" if it['buy'] and it['buy'].isdigit() else (it['buy'] or "-")
        sell = f"{int(it['sell']):,}" if it['sell'] and it['sell'].isdigit() else (it['sell'] or "-")
        name = (
            it['product']
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
        )
        lines.append(f"• <b>{name}</b>\n    Mua: {buy} — Bán: {sell}")
    return header + "\n".join(lines)


# ---------------- main ----------------
def main():
    BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not BOT_TOKEN or not CHAT_ID:
        print("⚠️ Thiếu TELEGRAM_BOT_TOKEN hoặc TELEGRAM_CHAT_ID.")
        return

    print(f"🔎 Đang tải trang {URL}")
    html = fetch_html()
    new_items = parse_gold_table(html)
    old_items = load_last_prices()

    if not has_changes(new_items, old_items):
        print("✅ Không có thay đổi, không gửi Telegram.")
        return

    now = datetime.now()
    msg = format_message(new_items, now)
    print("📤 Gửi thông báo tới Telegram...")

    send_telegram_message(BOT_TOKEN, CHAT_ID, msg)
    file_name = f"gold_{now.strftime('%Y%m%d_%H%M%S')}.csv"
    save_prices(new_items, file_name)
    send_telegram_document(BOT_TOKEN, CHAT_ID, file_name, caption="Bảng giá chi tiết CSV")

    # cập nhật file lưu lần cuối
    save_prices(new_items, LAST_FILE)
    print("✅ Đã gửi và lưu trạng thái mới.")


if __name__ == "__main__":
    main()