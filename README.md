# Gold Price Monitor – Realtime Price Tracker (Bảo Tín Mạnh Hải)

Script Python tự động:

- Crawl giá vàng **Bảo Tín Mạnh Hải**
- So sánh với snapshot trước đó (lưu trên **GitHub Gist**)
- Chỉ gửi **Telegram alert khi có thay đổi**
- Trình bày thông tin dưới dạng **bảng căn cột đẹp**, đọc dễ dàng
- Chạy tự động qua **GitHub Actions (cron)**

## 1. Tính năng chính

### ✔ Crawl dữ liệu giá vàng  
Lấy bảng “Giá vàng hôm nay” từ trang:

```
https://baotinmanhhai.vn/gia-vang-hom-nay
```

### ✔ Lưu snapshot tránh spam  
Sử dụng **GitHub Gist** để lưu dữ liệu snapshot giữa các lần chạy.

### ✔ Thông báo Telegram dạng bảng  
Tin nhắn gửi tới Telegram được format dạng bảng cố định bằng `<pre>`.

### ✔ Vận hành hoàn toàn tự động qua GitHub Actions  
Cron job chạy mỗi 10 phút.

## 2. Kiến trúc

```
personal_prj.py
    ├─ Crawl giá vàng
    ├─ Parse HTML
    ├─ Format bảng Telegram
    ├─ Snapshot diff (Gist / file local)
    └─ Gửi alert Telegram
```

## 3. Yêu cầu

```
requests
beautifulsoup4
```

Cài đặt:

```bash
pip install -r requirements.txt
```

## 4. Hướng dẫn thiết lập

### 4.1. Tạo Telegram Bot

1. Mở Telegram → tìm `@BotFather`
2. Tạo bot và lấy BOT TOKEN
3. Lấy CHAT ID từ:
   ```
   https://api.telegram.org/bot<token>/getUpdates
   ```

### 4.2. Tạo GitHub Gist dùng lưu snapshot

Tạo secret Gist và lấy `GIST_ID`.

### 4.3. Tạo Personal Access Token (Classic)

Tạo token classic tại:

```
https://github.com/settings/tokens/new
```

Tick đúng scope:

```
gist
```

### 4.4. Thêm secrets vào repo

| Secret | Value |
|--------|--------|
| TELEGRAM_BOT_TOKEN | Bot token |
| TELEGRAM_CHAT_ID | Chat ID |
| GIST_TOKEN | Classic PAT |
| GIST_ID | ID của Gist |

## 5. GitHub Actions workflow

Ví dụ workflow:

```yaml
name: Run gold price monitor

on:
  schedule:
    - cron: "*/10 * * * *"
  workflow_dispatch:

jobs:
  run-monitor:
    runs-on: ubuntu-latest

    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"

      - name: Install dependencies
        run: |
          python -m pip install --upgrade pip
          pip install -r requirements.txt

      - name: Run gold price monitor
        env:
          TELEGRAM_BOT_TOKEN: ${{ secrets.TELEGRAM_BOT_TOKEN }}
          TELEGRAM_CHAT_ID: ${{ secrets.TELEGRAM_CHAT_ID }}
          GIST_TOKEN: ${{ secrets.GIST_TOKEN }}
          GIST_ID: ${{ secrets.GIST_ID }}
        run: |
          python personal_prj.py
```

## 6. Chạy thử trên máy local

```bash
python personal_prj.py
```

## 7. Output mẫu trên Telegram

```
🪙 Cập nhật giá vàng Bảo Tín Mạnh Hải
⏱ 08:10 22/11/2025

LOẠI VÀNG                      MUA VÀO       BÁN RA
Nhẫn ép vỉ Kim Gia Bảo         14.780.000   15.080.000
Vàng miếng SJC                 14.890.000   15.030.000

Nguồn: baotinmanhhai.vn/gia-vang-hom-nay
```

## 8. License

MIT License.
