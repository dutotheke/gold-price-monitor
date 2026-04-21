from __future__ import annotations

import os
import re
import time
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

GIST_FILE_NAME = "gold_price_snapshot.txt"  # file snapshot trên Gist
LAST_DATA_FILE = "last_price.txt"           # fallback local (dev)
SNAPSHOT_PATH = "gold_snapshot.txt"         # file trung gian compare -> notify
SCREENSHOT_PATH = "gold_table.png"          # ảnh gửi Telegram


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


def normalize_text(s: str) -> str:
    s = (s or "").replace("\u00a0", " ").strip()
    s = re.sub(r"\s+", " ", s)
    return s


def parse_vnd(value: str) -> Optional[int]:
    """
    Chuyển '15.170.000' => 15170000
    Nếu trống / '-' / '—' => None
    """
    value = (value or "").strip()
    if value in ("", "-", "—"):
        return None
    digits = re.sub(r"[^\d]", "", value)
    return int(digits) if digits else None


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
    GitHub Actions step output via $GITHUB_OUTPUT
    """
    out = os.getenv("GITHUB_OUTPUT")
    if not out:
        log(f"(local) output {name}={value}")
        return
    with open(out, "a", encoding="utf-8") as f:
        f.write(f"{name}={value}\n")


# -----------------------------
# Crawler (HTML mới)
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


def _extract_price_from_block(block) -> Optional[int]:
    """
    Lấy giá đầu tiên có số trong 1 block.
    Ưu tiên các <span> chứa số, fallback toàn bộ text của block.
    """
    if block is None:
        return None

    # Ưu tiên span vì layout mới chứa đúng giá trong span
    for el in block.select("span"):
        txt = normalize_text(el.get_text(" ", strip=True))
        if re.search(r"\d", txt):
            val = parse_vnd(txt)
            if val is not None:
                return val

    # fallback: lấy toàn bộ text
    txt = normalize_text(block.get_text(" ", strip=True))
    return parse_vnd(txt)


def parse_gold_table(page_html: str) -> List[GoldItem]:
    """
    Hỗ trợ 2 layout:
    1) Layout mới: div/grid dưới block id^=gold_price_table-
    2) Layout cũ: table/tbody/tr/td
    """
    soup = BeautifulSoup(page_html, "html.parser")

    # =========================================================
    # A. Layout mới: div/grid
    # =========================================================
    root = soup.select_one('div[id^="gold_price_table-"]')
    items: List[GoldItem] = []

    if root:
        seen = set()

        for row in root.select("div.grid"):
            # Lấy các child trực tiếp của row
            children = [c for c in row.find_all(recursive=False) if getattr(c, "name", None)]
            if len(children) < 3:
                continue

            # Tên sản phẩm: ưu tiên h3/span, fallback img alt
            name = ""
            name_el = row.select_one("h3 span") or row.select_one("h3")
            if name_el:
                name = normalize_text(name_el.get_text(" ", strip=True))

            if not name:
                img = row.select_one("img[alt]")
                if img:
                    name = normalize_text(img.get("alt", ""))

            # Bỏ header row hoặc row rác
            if not name:
                continue

            # Layout hiện tại:
            # child[1] = Bán ra
            # child[2] = Mua vào
            sell = _extract_price_from_block(children[1])
            buy = _extract_price_from_block(children[2])

            # Nếu cả 2 đều không có số thì bỏ
            if buy is None and sell is None:
                continue

            # chống duplicate theo name
            key = normalize_text(name).lower()
            if key in seen:
                continue
            seen.add(key)

            items.append(GoldItem(name=name, buy=buy, sell=sell))

        if items:
            return items

    # =========================================================
    # B. Fallback layout cũ: table
    # =========================================================
    table = soup.select_one(".gold-table-content") or soup.select_one("table")
    if table:
        # xác định cột theo header nếu có
        header_cells = [
            normalize_text(x.get_text(" ", strip=True)).lower()
            for x in table.select("thead th")
        ]

        idx_name = 0
        idx_buy = 1
        idx_sell = 2

        if header_cells:
            for i, h in enumerate(header_cells):
                if "mua" in h:
                    idx_buy = i
                elif "bán" in h or "ban" in h:
                    idx_sell = i

        for tr in table.select("tbody tr"):
            tds = tr.find_all("td")
            if len(tds) < 3:
                continue

            name = normalize_text(tds[idx_name].get_text(" ", strip=True))
            buy = parse_vnd(tds[idx_buy].get_text(" ", strip=True)) if idx_buy < len(tds) else None
            sell = parse_vnd(tds[idx_sell].get_text(" ", strip=True)) if idx_sell < len(tds) else None

            if not name or (buy is None and sell is None):
                continue

            items.append(GoldItem(name=name, buy=buy, sell=sell))

        if items:
            return items

    raise RuntimeError("Không parse được bảng giá vàng từ HTML hiện tại")


def get_gold_price() -> List[GoldItem]:
    return parse_gold_table(fetch_gold_page())


# -----------------------------
# Gist snapshot
# -----------------------------
def get_gist_token() -> Optional[str]:
    # hỗ trợ cả 2 tên secret, tuỳ bạn đặt
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
# Telegram: sendPhoto
# -----------------------------
def send_telegram_photo(
    bot_token: str,
    chat_id: str,
    photo_path: str,
    caption: str,
    retries: int = TELEGRAM_RETRIES,
) -> None:
    url = f"https://api.telegram.org/bot{bot_token}/sendPhoto"
    last_error: Optional[Exception] = None

    for attempt in range(1, retries + 1):
        try:
            log(f"Gửi Telegram photo (attempt {attempt}/{retries})...")
            with open(photo_path, "rb") as f:
                files = {"photo": f}
                data = {"chat_id": chat_id, "caption": caption}
                r = requests.post(
                    url,
                    data=data,
                    files=files,
                    timeout=REQUEST_TIMEOUT,
                    proxies={"http": None, "https": None},
                )
            log(f"Telegram response: {r.status_code} — {r.text}")
            r.raise_for_status()
            return
        except Exception as e:
            last_error = e
            log(f"❌ Lỗi gửi Telegram photo: {e}")
            if attempt < retries:
                log(f"👉 Thử lại sau {TELEGRAM_RETRY_DELAY}s...")
                time.sleep(TELEGRAM_RETRY_DELAY)

    raise RuntimeError(f"Gửi Telegram photo thất bại sau {retries} lần") from last_error


# -----------------------------
# Screenshot: Playwright (lazy import)
# -----------------------------
def capture_gold_table_screenshot(out_path: str = SCREENSHOT_PATH) -> str:
    """
    Chụp bảng giá vàng theo cách ổn định:
    - requests lấy HTML
    - BeautifulSoup bóc đúng block hiện tại
    - render local bằng Playwright
    - screenshot element
    """
    from playwright.sync_api import sync_playwright

    html_text = fetch_gold_page(BAOTINMANHHAI_URL)
    soup = BeautifulSoup(html_text, "html.parser")

    # Ưu tiên layout mới
    root = soup.select_one('div[id^="gold_price_table-"]')

    # fallback layout cũ
    if not root:
        root = soup.select_one(".gold-table-content")
    if not root:
        root = soup.select_one(".table-responsive.gold-table")
    if not root:
        root = soup.select_one("table")

    if not root:
        raise RuntimeError("Không tìm thấy block bảng giá vàng để chụp")

    target_html = str(root)

    doc = f"""
<!doctype html>
<html lang="vi">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>Giá vàng Bảo Tín Mạnh Hải</title>
  <style>
    html, body {{
      margin: 0;
      padding: 16px;
      background: #ffffff;
      font-family: Arial, Helvetica, sans-serif;
    }}

    * {{
      box-sizing: border-box;
    }}

    img {{
      max-width: 36px;
      height: auto;
    }}

    svg {{
      max-width: 100%;
      height: auto;
    }}

    table {{
      border-collapse: collapse;
      width: 100%;
      font-size: 18px;
    }}

    th, td {{
      border: 1px solid #d0d0d0;
      padding: 10px 12px;
      vertical-align: middle;
    }}

    thead th {{
      background: #f2f2f2;
      font-weight: 700;
      text-align: left;
    }}

    tbody tr:nth-child(even) {{
      background: #fafafa;
    }}

    /* hỗ trợ layout div/grid mới */
    [id^="gold_price_table-"] {{
      width: 100%;
    }}

    .grid {{
      display: grid !important;
    }}

    .flex {{
      display: flex !important;
    }}

    .items-center {{
      align-items: center !important;
    }}

    .justify-center {{
      justify-content: center !important;
    }}

    .justify-end {{
      justify-content: flex-end !important;
    }}

    .flex-col {{
      flex-direction: column !important;
    }}

    .gap-4 {{
      gap: 16px !important;
    }}

    .gap-2 {{
      gap: 8px !important;
    }}

    .text-center {{
      text-align: center !important;
    }}

    .font-semibold {{
      font-weight: 600 !important;
    }}

    .font-bold {{
      font-weight: 700 !important;
    }}

    .border {{
      border: 1px solid #ddd !important;
    }}

    .border-b-0 {{
      border-bottom: 0 !important;
    }}

    .w-full {{
      width: 100% !important;
    }}

    .p-2 {{
      padding: 8px !important;
    }}

    .px-3 {{
      padding-left: 12px !important;
      padding-right: 12px !important;
    }}

    .px-4 {{
      padding-left: 16px !important;
      padding-right: 16px !important;
    }}

    .py-3 {{
      padding-top: 12px !important;
      padding-bottom: 12px !important;
    }}

    .py-5\\.5 {{
      padding-top: 22px !important;
      padding-bottom: 22px !important;
    }}

    .text-sm {{
      font-size: 14px !important;
    }}

    .text-lg {{
      font-size: 18px !important;
    }}

    .text-xl {{
      font-size: 20px !important;
    }}

    .odd\\:bg-white:nth-child(odd) {{
      background: #fff !important;
    }}

    .even\\:bg-soft-almond:nth-child(even) {{
      background: #faf7f2 !important;
    }}

    /* ép grid gần giống layout thật */
    .grid-cols-\\[1fr_1fr_1fr\\] {{
      grid-template-columns: 2.5fr 1fr 1fr !important;
    }}

    .md\\:grid-cols-\\[minmax\\(200px\\,3fr\\)_1\\.5fr_1\\.5fr_1fr_1\\.5fr\\] {{
      grid-template-columns: minmax(200px,3fr) 1.5fr 1.5fr 1fr 1.5fr !important;
    }}
  </style>
</head>
<body>
  {target_html}
</body>
</html>
""".strip()

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--disable-remote-fonts"],
        )
        page = browser.new_page(
            viewport={"width": 1600, "height": 2200},
            device_scale_factor=2,
        )
        page.set_default_timeout(60_000)
        page.set_content(doc, wait_until="domcontentloaded")

        # ưu tiên block mới
        locator = page.locator('[id^="gold_price_table-"]').first
        if locator.count() == 0:
            locator = page.locator(".gold-table-content").first
        if locator.count() == 0:
            locator = page.locator("table").first
        if locator.count() == 0:
            locator = page.locator("body").first

        locator.wait_for(state="visible", timeout=60_000)
        locator.screenshot(path=out_path, timeout=60_000)

        browser.close()

    log(f"✅ Đã tạo screenshot bảng vàng: {out_path}")
    return out_path

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
    2) chụp screenshot bảng vàng
    3) sendPhoto
    4) update gist snapshot ONLY after send success
    """
    bot_token = (os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()
    chat_id = (os.getenv("TELEGRAM_CHAT_ID") or "").strip()
    if not bot_token or not chat_id:
        raise RuntimeError("Thiếu TELEGRAM_BOT_TOKEN hoặc TELEGRAM_CHAT_ID")

    snapshot_text = load_file(SNAPSHOT_PATH).strip()
    if not snapshot_text:
        raise RuntimeError(f"Không có snapshot text ở {SNAPSHOT_PATH}")

    img_path = capture_gold_table_screenshot(SCREENSHOT_PATH)

    caption = f"🪙 Giá vàng Bảo Tín Mạnh Hải\n⏱ {datetime.now().strftime('%H:%M %d/%m/%Y')}"
    send_telegram_photo(bot_token, chat_id, img_path, caption)

    # Chỉ lưu snapshot sau khi gửi ảnh thành công
    save_last_snapshot(snapshot_text)
    log("✅ Notify done: sent screenshot + updated snapshot")


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
