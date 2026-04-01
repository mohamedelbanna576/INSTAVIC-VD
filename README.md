# INSTAVIC VD — Premium Instagram Downloader

A modern, responsive web application that allows users to download videos from public Instagram accounts. Featuring a sleek **Cosmic Dark** UI with glassmorphism and advanced reliability features.

![Python](https://img.shields.io/badge/Python-3.10+-blue)
![FastAPI](https://img.shields.io/badge/FastAPI-0.115-green)
![License](https://img.shields.io/badge/License-Personal_Use-yellow)

---

## Features

- **Premium UI/UX** — Deep cosmic dark theme with glassmorphism, glowing accents, and Phosphor icon set.
- **Single Video Download** — Paste a link to any public Instagram Reel or Video Post, get an instant download.
- **Bulk Profile Download** — Paste a public profile URL and download all available videos at once.
- **Persistent Identity** — Connect your Instagram `sessionid` to bypass anonymous rate limits. The identity is saved locally in `config.json` and survives server restarts.
- **Proxy Rotation** — Shield your home IP from being blocked by rotating requests through a random list in `proxies.txt`.
- **Real-time Progress** — Live progress bar and counter during bulk downloads via Server-Sent Events.
- **ZIP Packaging** — Download all videos from a profile as a single ZIP file.
- **Auto-Detection** — Automatically detects whether a URL is a single post or a profile.
- **Responsive Design** — Works beautifully on desktop and mobile.

---

## 🚀 Resilience Upgrades

### 1. Connect Your Account (Bypass Rate Limits)
If you see "Rate limit reached" or "Download timed out", it's because Instagram blocks anonymous traffic.
1. Log in to Instagram.com in your browser.
2. Open **Developer Tools** (F12) -> **Application** -> **Cookies**.
3. Copy the `sessionid` value.
4. Click **Connect Account** in the app and paste the value.
5. You are now authenticated! This cookie is saved locally and only used for your downloads.

### 2. Proxy Support
To use proxies:
1. Create a `proxies.txt` file in the root folder.
2. Add one proxy per line (e.g., `http://user:pass@host:port`).
3. The app will randomly select a proxy for each new download task.

---

## Setup & Run (Step-by-Step)

### 1. Open a Terminal / Command Prompt
Navigate to the project folder:
```bash
cd "c:\Users\moham\Desktop\INSTAVIC VD"
```

### 2. Create a Virtual Environment (Recommended)
```bash
python -m venv venv
```
Activate it:
- **Windows (Command Prompt):** `venv\Scripts\activate`
- **Windows (PowerShell):** `.\venv\Scripts\Activate.ps1`
- **macOS / Linux:** `source venv/bin/activate`

### 3. Install Dependencies
```bash
pip install -r requirements.txt
```

### 4. Run the Server
```bash
python app.py
```

### 5. Open in Browser
Navigate to: `http://127.0.0.1:8000`

---

## Project Structure

```
INSTAVIC VD/
├── app.py                 # FastAPI backend server
├── config.json            # [INTERNAL] Auto-created; stores your session cookie
├── proxies.txt            # [OPTIONAL] Add your list of rotating proxies here
├── requirements.txt       # Python dependencies
├── README.md              # This file
├── static/
│   ├── index.html         # Frontend HTML (Cosmic Dark Redesign)
│   ├── style.css          # Premium Glassmorphic CSS
│   └── script.js          # Modern Frontend logic
└── downloads/             # Auto-created; stores downloaded videos
```

---

## ⚠️ Disclaimer

This tool is for **personal and educational use only**. Downloading content from Instagram may violate their Terms of Service. Please respect content creators' rights and only download content you have permission to use. The developer is not responsible for any misuse of this tool.

---

## Tech Stack

- **Backend:** Python, FastAPI, Uvicorn, Instaloader, yt-dlp
- **Frontend:** HTML5, Tailwind CSS, Vanilla JavaScript, Phosphor Icons
- **Real-time:** Server-Sent Events (SSE)
