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
REQUEST_TIMEOUT = 20  # giây

TELEGRAM_RETRIES = 3
TELEGRAM_RETRY_DELAY = 3  # giây

GIST_FILE_NAME = "gold_price_snapshot.txt"
LAST_DATA_FILE = "last_price.txt"          # fallback local (dev)
SNAPSHOT_PATH = "gold_snapshot.txt"        # file trung gian giữa compare -> notify


# -----------------------------
# Model dữ liệu
# -----------------------------
@dataclass
class GoldItem:
    name: str
    buy: Optional[int]
    sell: Optional[int]
    unit: str = "đồng/chỉ"


# -----------------------------
# Utils
# -----------------------------
def log(msg: str) -> None:
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}")


def parse_vnd(value: str) -> Optional[int]:
    value = (value or "").strip()
    if value in ("", "-", "—"):
        return None
    digits = re.sub(r"[^\d]", "", value)
    return int(digits) if digits else None


def format_vnd(value: Optional[int]) -> str:
    if value is None:
        return "-"
    return f"{value:,.0f}".replace(",", ".")


def normalize_text(s: str) -> str:
    s = (s or "").replace("\u00a0", " ").strip()
    s = re.sub(r"\s+", " ", s)
    return s


def canonical_snapshot(items: List[GoldItem]) -> str:
    """
    Snapshot ổn định để lưu lên Gist:
    - normalize name
    - None -> '' cho buy/sell
    - sort theo name để chống reorder HTML
    """
    rows = []
    for it in items:
        name = normalize_text(it.name)
        buy = "" if it.buy is None else str(int(it.buy))
        sell = "" if it.sell is None else str(int(it.sell))
        rows.append((name, buy, sell))

    rows.sort(key=lambda x: x[0])
    return "\n".join([f"{n} | {b} | {s}" for n, b, s in rows]).strip()


def canonicalize_text_blob(s: str) -> str:
    return (s or "").replace("\u00a0", " ").replace("\r\n", "\n").strip()


def sha256_text(s: str) -> str:
    return hashlib.sha256((s or "").encode("utf-8")).hexdigest()


def save_file(path: str, content: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        f.write(content or "")


def load_file(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read() or ""
    except FileNotFoundError:
        return ""


def write_output(name: str, value: str) -> None:
    """
    GitHub Actions step output: dùng file $GITHUB_OUTPUT
    """
    out = os.getenv("GITHUB_OUTPUT")
    if not out:
        log(f"(local) output {name}={value}")
        return
    with open(out, "a", encoding="utf-8") as f:
        f.write(f"{name}={value}\n")


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
    soup = BeautifulSoup(page_html, "html.parser")

    heading = soup.find(
        string=lambda t: isinstance(t, str) and "GIÁ VÀNG HÔM NAY" in t.upper()
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

        name = normalize_text(tds[0])
        buy = parse_vnd(tds[1]) if len(tds) >= 2 else None
        sell = parse_vnd(tds[2]) if len(tds) >= 3 else None

        if not name or (buy is None and sell is None):
            continue

        items.append(GoldItem(name=name, buy=buy, sell=sell))

    if not items:
        raise RuntimeError("Không parse được bất kỳ dòng giá vàng nào.")
    return items


def get_gold_price() -> List[GoldItem]:
    return parse_gold_table(fetch_gold_page())


# -----------------------------
# Gist snapshot
# -----------------------------
def get_gist_token() -> Optional[str]:
    # hỗ trợ cả 2 tên secret (tuỳ repo bạn đặt)
    return (os.getenv("GIST_TOKEN") or os.getenv("GITHUB_TOKEN_GIST") or "").strip() or None


def load_last_data_from_gist(token: str, gist_id: str) -> str:
    url = f"https://api.github.com/gists/{gist_id}"
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}
    resp = requests.get(url, headers=headers, timeout=REQUEST_TIMEOUT)
    if resp.status_code == 404:
        return ""
    resp.raise_for_status()
    data = resp.json()
    file_obj = data.get("files", {}).get(GIST_FILE_NAME)
    return (file_obj or {}).get("content") or ""


def save_last_data_to_gist(token: str, gist_id: str, text: str) -> None:
    url = f"https://api.github.com/gists/{gist_id}"
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}
    payload = {"files": {GIST_FILE_NAME: {"content": text}}}
    resp = requests.patch(url, headers=headers, json=payload, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()


def load_last_snapshot() -> str:
    gist_token = get_gist_token()
    gist_id = (os.getenv("GIST_ID") or "").strip()

    if gist_token and gist_id:
        try:
            return load_last_data_from_gist(gist_token, gist_id)
        except Exception as e:
            log(f"⚠️ Lỗi đọc Gist: {e}, fallback file local.")
            return load_file(LAST_DATA_FILE)

    log("ℹ️ Không có Gist token/GIST_ID, dùng snapshot local (file).")
    return load_file(LAST_DATA_FILE)


def save_last_snapshot(text: str) -> None:
    gist_token = get_gist_token()
    gist_id = (os.getenv("GIST_ID") or "").strip()

    if gist_token and gist_id:
        save_last_data_to_gist(gist_token, gist_id, text)
        return

    save_file(LAST_DATA_FILE, text)


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

    rows: List[tuple[str, str, str]] = [("LOẠI VÀNG", "MUA VÀO", "BÁN RA")]
    for item in items:
        name = normalize_text(item.name)
        rows.append((name, format_vnd(item.buy), format_vnd(item.sell)))

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
# 2-phase commands
# -----------------------------
def cmd_compare() -> None:
    """
    1) crawl+parse -> snapshot_text
    2) load last snapshot from gist/file
    3) compare hash
    4) write snapshot_text to SNAPSHOT_PATH
    5) output changed=true/false
    """
    items = get_gold_price()
    snapshot_text = canonical_snapshot(items)
    save_file(SNAPSHOT_PATH, snapshot_text)

    last_text = load_last_snapshot()
    new_hash = sha256_text(snapshot_text)
    old_hash = sha256_text(canonicalize_text_blob(last_text))

    changed = "true" if new_hash != old_hash else "false"
    log(f"Compare hash: {old_hash[:8]} -> {new_hash[:8]} changed={changed}")
    write_output("changed", changed)


def cmd_notify() -> None:
    """
    1) read snapshot_text from SNAPSHOT_PATH
    2) re-crawl to build message (or parse snapshot -> items)
       -> ở đây re-crawl để đảm bảo message là dữ liệu mới nhất
    3) send telegram
    4) update gist snapshot ONLY after send success
    """
    bot_token = (os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()
    chat_id = (os.getenv("TELEGRAM_CHAT_ID") or "").strip()
    if not bot_token or not chat_id:
        raise RuntimeError("Thiếu TELEGRAM_BOT_TOKEN hoặc TELEGRAM_CHAT_ID")

    snapshot_text = load_file(SNAPSHOT_PATH).strip()
    if not snapshot_text:
        raise RuntimeError(f"Không có snapshot text ở {SNAPSHOT_PATH}")

    # Re-crawl để build message đẹp (giữ nguyên format hiện tại của bạn)
    items = get_gold_price()
    msg = build_message(items)

    send_telegram_message(bot_token, chat_id, msg, parse_mode="HTML")

    # Chỉ lưu snapshot sau khi gửi thành công
    save_last_snapshot(snapshot_text)
    log("✅ Notify done: sent telegram + updated snapshot")


def main() -> None:
    import sys
    mode = (sys.argv[1] if len(sys.argv) > 1 else "").strip().lower()
    if mode == "compare":
        cmd_compare()
    elif mode == "notify":
        cmd_notify()
    else:
        raise SystemExit("Usage: python personal_prj.py compare|notify")


if __name__ == "__main__":
    main()
