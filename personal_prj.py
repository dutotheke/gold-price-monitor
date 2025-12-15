from __future__ import annotations

import os
import re
import time
import html
import hashlib
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

GIST_FILE_NAME = "gold_price_snapshot.txt"  # giữ nguyên: lưu text trên Gist


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
    Nếu trống / '-' / '—' / không phải số => None.
    """
    value = (value or "").strip()
    if value in ("", "-", "—"):
        return None
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
# NEW: Canonical snapshot + hash
# -----------------------------
def normalize_name(s: str) -> str:
    """
    Chuẩn hoá tên: strip + gom whitespace + thay NBSP.
    Tránh cảnh báo giả do khác nhau bởi khoảng trắng/NBSP.
    """
    s = (s or "").replace("\u00a0", " ").strip()
    s = re.sub(r"\s+", " ", s)
    return s


def canonical_snapshot(items: List[GoldItem]) -> str:
    """
    Snapshot text ổn định để lưu lên Gist:
    - normalize name
    - None -> '' cho buy/sell
    - sort theo name để chống reorder HTML
    """
    rows = []
    for it in items:
        name = normalize_name(it.name)
        buy = "" if it.buy is None else str(int(it.buy))
        sell = "" if it.sell is None else str(int(it.sell))
        rows.append((name, buy, sell))

    rows.sort(key=lambda x: x[0])

    # Lưu đúng format bạn đang dùng trên gist
    return "\n".join([f"{n} | {b} | {s}" for n, b, s in rows]).strip()


def sha256_text(s: str) -> str:
    return hashlib.sha256((s or "").encode("utf-8")).hexdigest()


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


def parse_gold_table(page_html: str) -> List[GoldItem]:
    """
    Parse HTML để lấy bảng 'GIÁ VÀNG HÔM NAY'.
    Selector tương đối “phòng hờ” để tránh vỡ khi layout đổi nhẹ.
    """
    soup = BeautifulSoup(page_html, "html.parser")

    heading = soup.find(
        string=lambda t: isinstance(t, str)
        and "GIÁ VÀNG HÔM NAY" in t.upper()
    )
    table = None
    if heading:
        section = heading.find_parent()
        if section:
            table = section.find("table")

    if table is None:
        table = soup.find("table")

    if table is None:
        raise RuntimeError("Không tìm thấy bảng giá vàng trong HTML.")

    items: List[GoldItem] = []
    for tr in table.select("tbody tr"):
        tds = [td.get_text(strip=True) for td in tr.find_all("td")]
        if len(tds) < 2:
            continue

        name = normalize_name(tds[0])
        buy = parse_vnd(tds[1]) if len(tds) >= 2 else None
        sell = parse_vnd(tds[2]) if len(tds) >= 3 else None

        if not name or (buy is None and sell is None):
            continue

        items.append(GoldItem(name=name, buy=buy, sell=sell))

    return items


def get_gold_price() -> List[GoldItem]:
    page_html = fetch_gold_page()
    items = parse_gold_table(page_html)
    if not items:
        raise RuntimeError("Không parse được bất kỳ dòng giá vàng nào.")
    return items


# -----------------------------
# Lưu / tải snapshot bằng file (fallback)
# -----------------------------
def load_last_data_from_file(path: str = LAST_DATA_FILE) -> str:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read() or ""
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
    Đọc snapshot TEXT từ Gist.
    Nếu có lỗi => trả về chuỗi rỗng.
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
        return file_obj.get("content") or ""
    except Exception as e:
        log(f"⚠️ Lỗi khi đọc Gist: {e}, fallback snapshot rỗng.")
        return ""


def save_last_data_to_gist(token: str, gist_id: str, text: str) -> None:
    """
    Cập nhật snapshot TEXT lên Gist (đã tồn tại).
    """
    url = f"https://api.github.com/gists/{gist_id}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
    }
    payload = {"files": {GIST_FILE_NAME: {"content": text}}}
    log(f"Cập nhật snapshot lên Gist: {gist_id}")
    resp = requests.patch(url, headers=headers, json=payload, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    log("✅ Đã lưu snapshot lên Gist.")


def load_last_snapshot() -> str:
    gist_token = os.getenv("GIST_TOKEN")
    gist_id = os.getenv("GIST_ID")

    if gist_token and gist_id:
        return load_last_data_from_gist(gist_token, gist_id)

    log("ℹ️ Không có GIST_TOKEN hoặc GIST_ID, dùng snapshot local (file).")
    return load_last_data_from_file()


def save_last_snapshot(text: str) -> None:
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
    header = (
        "🪙 <b>Cập nhật giá vàng Bảo Tín Mạnh Hải</b>\n"
        f"⏱ {datetime.now().strftime('%H:%M %d/%m/%Y')}\n\n"
    )

    rows: List[tuple[str, str, str]] = []
    rows.append(("LOẠI VÀNG", "MUA VÀO", "BÁN RA"))

    for item in items:
        name = normalize_name(item.name)
        buy_s = format_vnd(item.buy)
        sell_s = format_vnd(item.sell)
        rows.append((name, buy_s, sell_s))

    col1_width = max(len(r[0]) for r in rows)
    col2_width = max(len(r[1]) for r in rows)
    col3_width = max(len(r[2]) for r in rows)

    lines: List[str] = []
    for name, buy_s, sell_s in rows:
        lines.append(
            name.ljust(col1_width)
            + "  "
            + buy_s.rjust(col2_width)
            + "  "
            + sell_s.rjust(col3_width)
        )

    table_text_escaped = html.escape("\n".join(lines))

    return (
        header
        + "<pre><code>"
        + table_text_escaped
        + "</code></pre>"
        + "\nNguồn: baotinmanhhai.vn/gia-vang-hom-nay"
    )


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

    # NEW: canonical snapshot (text) -> hash để so sánh
    snapshot_text = canonical_snapshot(items)
    snapshot_hash = sha256_text(snapshot_text)

    # last snapshot là TEXT, nhưng so sánh bằng hash
    last_text = load_last_snapshot()
    last_hash = sha256_text(canonical_snapshot([
        # Không re-parse lại text cũ (không cần). Chỉ canonicalize theo string.
        # Ở đây đơn giản: canonicalize string bằng normalize newline/whitespace.
        # => làm theo hướng nhẹ: canonicalize trực tiếp trên last_text.
    ]) if False else (last_text.replace("\u00a0", " ").replace("\r\n", "\n").strip()))

    # So sánh hash
    if snapshot_hash != last_hash:
        log(f"🔔 Phát hiện thay đổi (hash): {last_hash[:8]} -> {snapshot_hash[:8]}")
        msg = build_message(items)
        try:
            send_telegram_message(bot_token, chat_id, msg, parse_mode="HTML")
            save_last_snapshot(snapshot_text)  # vẫn lưu TEXT lên Gist/file
            log("✅ Đã gửi Telegram (có thay đổi).")
        except Exception as e:
            log(f"❌ Gửi Telegram thất bại: {e}")
    else:
        log("⏳ Không có thay đổi, không gửi Telegram.")


if __name__ == "__main__":
    main()
