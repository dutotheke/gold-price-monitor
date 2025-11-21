from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional

import requests
from bs4 import BeautifulSoup


# -----------------------------
# Cấu hình / hằng số
# -----------------------------
BAOTINMANHHAI_URL = "https://baotinmanhhai.vn/gia-vang-hom-nay"
LAST_DATA_FILE = "last_price.txt"

REQUEST_TIMEOUT = 20  # giây
TELEGRAM_RETRIES = 3
TELEGRAM_RETRY_DELAY = 3  # giây

GIST_FILE_NAME = "gold_price_snapshot.txt"  # tên file trong Gist


# -----------------------------
# Model dữ liệu
# -----------------------------
@dataclass
class GoldItem:
    name: str
    buy: Optional[int]  # một số loại có thể chỉ có giá mua hoặc bán
    sell: Optional[int]
    unit: str = "đồng/chỉ"


# -----------------------------
# Utils
# -----------------------------
def log(msg: str) -> None:
    """Log đơn giản, có timestamp."""
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}")


def parse_vnd(value: str) -> Optional[int]:
    """
    Chuyển chuỗi kiểu '14.780.000' => 14780000 (int).
    Nếu trống hoặc không phải số => None.
    """
    digits = re.sub(r"[^\d]", "", value)
    if not digits:
        return None
    return int(digits)


def format_vnd(value: Optional[int]) -> str:
    """Format int về dạng '14.780.000' (giống website)."""
    if value is None:
        return "-"
    return f"{value:,.0f}".replace(",", ".")


# -----------------------------
# Crawler
# -----------------------------
def fetch_gold_page(url: str = BAOTINMANHHAI_URL) -> str:
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0 Safari/537.36"
        )
    }
    log(f"Đang tải trang giá vàng: {url}")
    resp = requests.get(url, headers=headers, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    return resp.text


def parse_gold_table(html: str) -> List[GoldItem]:
    """
    Parse HTML để lấy bảng 'GIÁ VÀNG HÔM NAY'.
    Selector tương đối “phòng hờ” để tránh vỡ khi layout đổi nhẹ.
    """
    soup = BeautifulSoup(html, "html.parser")

    # Thử tìm heading 'GIÁ VÀNG HÔM NAY' rồi từ đó tìm table gần nhất
    heading = soup.find(
        string=lambda t: isinstance(t, str)
        and "GIÁ VÀNG HÔM NAY" in t.upper()
    )
    table = None
    if heading:
        section = heading.find_parent()
        if section:
            table = section.find("table")

    # Fallback: lấy table đầu tiên nếu không tìm được theo heading
    if table is None:
        table = soup.find("table")

    if table is None:
        raise RuntimeError("Không tìm thấy bảng giá vàng trong HTML.")

    items: List[GoldItem] = []
    for tr in table.select("tbody tr"):
        tds = [td.get_text(strip=True) for td in tr.find_all("td")]
        # Dựa theo layout hiện tại: LOẠI VÀNG | MUA VÀO | BÁN RA
        if len(tds) < 2:
            continue

        name = tds[0]
        buy = parse_vnd(tds[1]) if len(tds) >= 2 else None
        sell = parse_vnd(tds[2]) if len(tds) >= 3 else None

        # Loại bỏ các dòng header hoặc rỗng
        if not name or (buy is None and sell is None):
            continue

        items.append(GoldItem(name=name, buy=buy, sell=sell))

    return items


def get_gold_price() -> List[GoldItem]:
    html = fetch_gold_page()
    items = parse_gold_table(html)
    if not items:
        raise RuntimeError("Không parse được bất kỳ dòng giá vàng nào.")
    return items


# -----------------------------
# Lưu / tải snapshot bằng file (fallback)
# -----------------------------
def load_last_data_from_file(path: str = LAST_DATA_FILE) -> str:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read().strip()
    except FileNotFoundError:
        return ""


def save_last_data_to_file(text: str, path: str = LAST_DATA_FILE) -> None:
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


# -----------------------------
# Lưu / tải snapshot bằng Gist
# -----------------------------
def load_last_data_from_gist(token: str, gist_id: str) -> str:
    """
    Đọc nội dung snapshot từ Gist.
    Nếu có lỗi (404, network, thiếu file) => trả về chuỗi rỗng.
    """
    url = f"https://api.github.com/gists/{gist_id}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
    }
    try:
        log(f"Đọc snapshot từ Gist: {gist_id}")
        resp = requests.get(url, headers=headers, timeout=REQUEST_TIMEOUT)
        if resp.status_code == 404:
            log("⚠️ Không tìm thấy Gist với GIST_ID, xem như snapshot rỗng.")
            return ""
        resp.raise_for_status()
        data = resp.json()
        file_obj = data.get("files", {}).get(GIST_FILE_NAME)
        if not file_obj:
            log(f"⚠️ Không thấy file {GIST_FILE_NAME} trong Gist, xem như rỗng.")
            return ""
        content = file_obj.get("content") or ""
        return content.strip()
    except Exception as e:
        log(f"⚠️ Lỗi khi đọc Gist: {e}, fallback snapshot rỗng.")
        return ""


def save_last_data_to_gist(token: str, gist_id: str, text: str) -> None:
    """
    Cập nhật nội dung snapshot vào Gist (đã tồn tại).
    Hàm này giả định Gist đã được tạo sẵn và GIST_ID chính xác.
    """
    url = f"https://api.github.com/gists/{gist_id}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
    }
    payload = {
        "files": {
            GIST_FILE_NAME: {
                "content": text,
            }
        }
    }
    log(f"Cập nhật snapshot lên Gist: {gist_id}")
    resp = requests.patch(url, headers=headers, json=payload, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    log("✅ Đã lưu snapshot lên Gist.")


def load_last_snapshot() -> str:
    """
    Wrapper: ưu tiên đọc từ Gist nếu có GIST_TOKEN + GIST_ID,
    nếu không thì fallback sang file local.
    """
    gist_token = os.getenv("GIST_TOKEN")
    gist_id = os.getenv("GIST_ID")

    if gist_token and gist_id:
        return load_last_data_from_gist(gist_token, gist_id)

    log("ℹ️ Không có GIST_TOKEN hoặc GIST_ID, dùng snapshot local (file).")
    return load_last_data_from_file()


def save_last_snapshot(text: str) -> None:
    """
    Wrapper: ưu tiên lưu vào Gist nếu có GIST_TOKEN + GIST_ID,
    nếu không thì lưu vào file local.
    """
    gist_token = os.getenv("GIST_TOKEN")
    gist_id = os.getenv("GIST_ID")

    if gist_token and gist_id:
        try:
            save_last_data_to_gist(gist_token, gist_id, text)
            return
        except Exception as e:
            log(f"⚠️ Lỗi lưu Gist, fallback sang file local: {e}")

    save_last_data_to_file(text)


# -----------------------------
# Telegram
# -----------------------------
def send_telegram_message(
    bot_token: str,
    chat_id: str,
    text: str,
    parse_mode: str = "HTML",
    retries: int = TELEGRAM_RETRIES,
) -> None:
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": parse_mode,
        "disable_web_page_preview": True,
    }

    last_error: Optional[Exception] = None

    for attempt in range(1, retries + 1):
        try:
            log(f"Gửi Telegram (attempt {attempt}/{retries})...")
            r = requests.post(
                url,
                json=payload,
                timeout=REQUEST_TIMEOUT,
                proxies={"http": None, "https": None},
            )
            log(f"Telegram response: {r.status_code} — {r.text}")
            r.raise_for_status()
            return
        except Exception as e:
            last_error = e
            log(f"❌ Lỗi gửi Telegram: {e}")
            if attempt < retries:
                log(f"👉 Thử lại sau {TELEGRAM_RETRY_DELAY}s...")
                time.sleep(TELEGRAM_RETRY_DELAY)

    raise RuntimeError(f"Gửi Telegram thất bại sau {retries} lần") from last_error


# -----------------------------
# Build message hiển thị
# -----------------------------
def build_message(items: List[GoldItem]) -> str:
    """
    Chuyển list GoldItem thành text gửi Telegram.
    Có thể custom thêm (lọc theo loại, sort, highlight…).
    """
    lines: List[str] = []

    lines.append("🪙 <b>Cập nhật giá vàng Bảo Tín Mạnh Hải</b>")
    lines.append(f"⏱ Thời điểm crawl: {datetime.now().strftime('%H:%M %d/%m/%Y')}")
    lines.append("")
    lines.append("<pre>LOẠI VÀNG                      MUA VÀO       BÁN RA</pre>")

    for item in items:
        name = item.name[:28]  # tránh quá dài
        buy_s = format_vnd(item.buy)
        sell_s = format_vnd(item.sell)
        line = f"{name:<28} {buy_s:>10}  {sell_s:>10}"
        lines.append(line)

    lines.append("")
    lines.append("Nguồn: baotinmanhhai.vn/gia-vang-hom-nay")

    return "\n".join(lines)


# -----------------------------
# Main
# -----------------------------
def main() -> None:
    print("🔁 Cron job chạy lúc", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))

    bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")

    if not bot_token or not chat_id:
        log("⚠️ Thiếu TELEGRAM_BOT_TOKEN hoặc TELEGRAM_CHAT_ID. Thoát.")
        return

    try:
        items = get_gold_price()
    except Exception as e:
        log(f"❌ Lỗi lấy giá vàng: {e}")
        return

    # Snapshot text để so sánh với lần trước
    snapshot_lines = [
        f"{it.name} | {it.buy or ''} | {it.sell or ''}"
        for it in items
    ]
    snapshot_text = "\n".join(snapshot_lines)

    last_text = load_last_snapshot()

    if snapshot_text != last_text:
        msg = build_message(items)
        try:
            send_telegram_message(bot_token, chat_id, msg, parse_mode="HTML")
            save_last_snapshot(snapshot_text)
            log("✅ Đã gửi Telegram (có thay đổi).")
        except Exception as e:
            log(f"❌ Gửi Telegram thất bại: {e}")
    else:
        log("⏳ Không có thay đổi, không gửi Telegram.")


if __name__ == "__main__":
    main()
