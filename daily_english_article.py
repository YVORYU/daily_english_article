#!/usr/bin/env python3
"""
Daily English Article Pusher

Fetch English news articles (CET-6 / IELTS level) daily, translate
to Chinese, and push to Feishu via Webhook or App Bot API.

Push modes:
  webhook  -- Use Feishu custom bot webhook (simple, requires enterprise)
  app      -- Use Feishu App Bot API (requires App ID/Secret/open_id)

Usage:
  python daily_english_article.py

Configuration:
  Copy .env.example to .env and fill in your settings.
"""

import os
import sys
import json
import re
import time
import hashlib
import hmac
import base64
import logging
import traceback
from datetime import datetime, date
from pathlib import Path
from typing import Optional, Dict
from html import unescape
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv

# ============================================================
#  Logging
# ============================================================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
)
log = logging.getLogger(__name__)

# ============================================================
#  Constants
# ============================================================

BNE_BASE_URL = "https://breakingnewsenglish.com/"

BNE_LEVEL_PAGES = [
    (6, "https://breakingnewsenglish.com/news-for-kids.html"),
    (5, "https://breakingnewsenglish.com/english-news-readings.html"),
]

HTTP_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}

FEISHU_TOKEN_URL = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
FEISHU_SEND_MSG_URL = "https://open.feishu.cn/open-apis/im/v1/messages"

# ============================================================
#  Configuration
# ============================================================

def load_config() -> dict:
    script_dir = Path(__file__).parent.absolute()
    load_dotenv(script_dir / ".env")
    load_dotenv(Path.cwd() / ".env")

    push_mode = os.environ.get("FEISHU_PUSH_MODE", "webhook").strip().lower()

    config = {
        "push_mode": push_mode,
        "feishu_webhook_url": os.environ.get("FEISHU_WEBHOOK_URL", "").strip(),
        "feishu_webhook_secret": os.environ.get("FEISHU_WEBHOOK_SECRET", "").strip(),
        "feishu_app_id": os.environ.get("FEISHU_APP_ID", "").strip(),
        "feishu_app_secret": os.environ.get("FEISHU_APP_SECRET", "").strip(),
        "feishu_receiver_id": os.environ.get("FEISHU_RECEIVER_ID", "").strip(),
        "translation_provider": os.environ.get("TRANSLATION_PROVIDER", "tencent").strip().lower(),
        "translation_api_key": os.environ.get("TRANSLATION_API_KEY", "").strip(),
        "translation_api_url": os.environ.get("TRANSLATION_API_URL", "").strip(),
        "tencent_secret_id": os.environ.get("TENCENT_SECRET_ID", "").strip(),
        "tencent_secret_key": os.environ.get("TENCENT_SECRET_KEY", "").strip(),
        "data_dir": os.environ.get("DATA_DIR", "./data").strip(),
    }

    if push_mode == "webhook":
        if not config["feishu_webhook_url"]:
            log.error("FEISHU_PUSH_MODE=webhook but FEISHU_WEBHOOK_URL is empty")
            log.error("Create a .env file. See .env.example for reference.")
            sys.exit(1)
    elif push_mode == "app":
        missing = []
        for key, label in [
            ("feishu_app_id", "FEISHU_APP_ID"),
            ("feishu_app_secret", "FEISHU_APP_SECRET"),
            ("feishu_receiver_id", "FEISHU_RECEIVER_ID"),
        ]:
            if not config[key]:
                missing.append(label)
        if missing:
            log.error("FEISHU_PUSH_MODE=app but missing: %s", ", ".join(missing))
            log.error("Create a .env file. See .env.example for reference.")
            sys.exit(1)
    else:
        log.error("Invalid FEISHU_PUSH_MODE: %s (must be 'webhook' or 'app')", push_mode)
        sys.exit(1)

    return config


# ============================================================
#  Utility
# ============================================================

def clean_html_text(text: str) -> str:
    text = re.sub(r'<[^>]+>', '', text)
    text = unescape(text)
    text = re.sub(r'\n\s*\n', '\n\n', text)
    return text.strip()


# ============================================================
#  Translation
# ============================================================

def translate_text(text: str, config: dict) -> str:
    if not text or len(text.strip()) == 0:
        return ""

    provider = config["translation_provider"]
    api_key = config["translation_api_key"]
    api_url = config["translation_api_url"]
    tencent_sid = config.get("tencent_secret_id", "")
    tencent_skey = config.get("tencent_secret_key", "")

    log.info("Translating via %s ...", provider)

    if provider == "tencent":
        return _translate_tencent(text, tencent_sid, tencent_skey)
    elif provider == "google":
        return _translate_google(text, api_key)
    elif provider == "libre":
        return _translate_libre(text, api_url)
    elif provider == "deepl":
        return _translate_deepl(text, api_key, api_url)
    elif provider == "openai":
        return _translate_openai(text, api_key, api_url)
    else:
        log.warning("Unknown provider %s, falling back to tencent", provider)
        return _translate_tencent(text, tencent_sid, tencent_skey)


# ---------- Tencent Cloud TMT ----------

def _translate_tencent(text: str, secret_id: str, secret_key: str) -> str:
    """Translate via Tencent Cloud Machine Translation API (TMT).

    Free tier: 5 million chars/month.
    https://console.cloud.tencent.com/tmt
    """
    if not secret_id or not secret_key:
        log.error("Tencent translation requires TENCENT_SECRET_ID and TENCENT_SECRET_KEY")
        return ""

    host = "tmt.tencentcloudapi.com"
    service = "tmt"

    translated = []
    for para in text.split('\n\n'):
        para = para.strip()
        if not para:
            translated.append("")
            continue
        try:
            payload = {
                "SourceText": para,
                "Source": "en",
                "Target": "zh",
                "ProjectId": 0,
            }
            body = json.dumps(payload)
            timestamp = int(time.time())
            date_str = datetime.utcfromtimestamp(timestamp).strftime("%Y-%m-%d")

            # TC3-HMAC-SHA256 signature
            canonical_request = (
                "POST\n/\n\n"
                f"content-type:application/json; charset=utf-8\n"
                f"host:{host}\n"
                f"x-tc-action:texttranslate\n\n"
                "content-type;host;x-tc-action\n"
                + hashlib.sha256(body.encode("utf-8")).hexdigest()
            )
            string_to_sign = (
                f"TC3-HMAC-SHA256\n{timestamp}\n{date_str}/{service}/tc3_request\n"
                + hashlib.sha256(canonical_request.encode("utf-8")).hexdigest()
            )
            k_date = hmac.new(
                f"TC3{secret_key}".encode("utf-8"),
                date_str.encode("utf-8"), hashlib.sha256,
            ).digest()
            k_service = hmac.new(k_date, service.encode("utf-8"), hashlib.sha256).digest()
            k_signing = hmac.new(k_service, b"tc3_request", hashlib.sha256).digest()
            signature = hmac.new(k_signing, string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()

            authorization = (
                f"TC3-HMAC-SHA256 "
                f"Credential={secret_id}/{date_str}/{service}/tc3_request, "
                f"SignedHeaders=content-type;host;x-tc-action, "
                f"Signature={signature}"
            )

            headers = {
                "Authorization": authorization,
                "Content-Type": "application/json; charset=utf-8",
                "Host": host,
                "X-TC-Action": "TextTranslate",
                "X-TC-Timestamp": str(timestamp),
                "X-TC-Version": "2018-03-21",
                "X-TC-Region": "ap-beijing",
            }

            resp = requests.post(f"https://{host}", headers=headers, data=body.encode("utf-8"), timeout=15)
            if resp.status_code == 200:
                result = resp.json().get("Response", {})
                if "Error" in result:
                    log.warning("Tencent TMT error: %s %s",
                                result["Error"].get("Code", ""), result["Error"].get("Message", ""))
                    translated.append("")
                else:
                    target = result.get("TargetText", "")
                    translated.append(target.replace("\n", "") if target else "")
            else:
                log.warning("Tencent TMT HTTP %d: %s", resp.status_code, resp.text[:200])
                translated.append("")
        except Exception as e:
            log.warning("Tencent TMT error: %s", e)
            translated.append("")

        time.sleep(0.1)

    return "\n\n".join(translated)


# ---------- MyMemory (free, no key) ----------

def _translate_google(text: str, api_email: str = "") -> str:
    """Translate via MyMemory Translation API (free, no API key).

    5000 chars/day anonymous, unlimited with email.
    Pass email via TRANSLATION_API_KEY.
    """
    api_email = api_email or ""
    translated = []
    for para in text.split('\n\n'):
        para = para.strip()
        if not para:
            translated.append("")
            continue
        try:
            chunks = _split_text(para, max_len=480)
            para_result = []
            for chunk in chunks:
                params = {"q": chunk, "langpair": "en|zh-CN"}
                if api_email:
                    params["de"] = api_email
                resp = requests.get(
                    "https://api.mymemory.translated.net/get",
                    params=params, timeout=15,
                )
                if resp.status_code == 200:
                    data = resp.json()
                    t = data.get("responseData", {}).get("translatedText", "")
                    if t and "MYMEMORY WARNING" not in t.upper():
                        para_result.append(t)
                    else:
                        matches = data.get("matches", [])
                        para_result.append(matches[0]["translation"] if matches else "")
                else:
                    log.warning("MyMemory HTTP %d", resp.status_code)
                    para_result.append("")
                time.sleep(0.3)
            translated.append("".join(para_result))
        except Exception as e:
            log.warning("MyMemory error: %s", e)
            translated.append("")

    return "\n\n".join(translated)


def _split_text(text: str, max_len: int = 480) -> list:
    if len(text) <= max_len:
        return [text]
    chunks = []
    sentences = re.split(r'(?<=[.!?])\s+', text)
    current = ""
    for s in sentences:
        if len(current) + len(s) + 1 > max_len and current:
            chunks.append(current.strip())
            current = s
        else:
            current = (current + " " + s).strip()
    if current.strip():
        chunks.append(current.strip())
    return chunks


# ---------- LibreTranslate (free, public servers) ----------

LIBRETRANSLATE_SERVERS = [
    "https://translate.argosopentech.com/translate",
    "https://libretranslate.de/translate",
    "https://libretranslate.pussthecat.org/translate",
    "https://translate.terraprint.co/translate",
]


def _translate_libre(text: str, api_url: str) -> str:
    max_chunk = 1500
    chunks = []
    current = ""
    for para in text.split('\n'):
        para = para.strip()
        if not para:
            chunks.append("")
            continue
        if len(current) + len(para) > max_chunk and current:
            chunks.append(current.strip())
            current = para + "\n"
        else:
            current += para + "\n"
    if current.strip():
        chunks.append(current.strip())

    servers = [api_url] if api_url else LIBRETRANSLATE_SERVERS
    translated = []
    for chunk in chunks:
        if not chunk.strip():
            translated.append("")
            continue
        done = False
        for server_url in servers:
            try:
                resp = requests.post(
                    server_url,
                    json={"q": chunk, "source": "en", "target": "zh", "format": "text"},
                    timeout=20, headers={"Content-Type": "application/json"},
                )
                if resp.status_code == 200:
                    result = resp.json().get("translatedText", "")
                    if result:
                        translated.append(result)
                        done = True
                        break
                    log.warning("LibreTranslate returned empty from %s", server_url)
                else:
                    log.warning("LibreTranslate %d from %s", resp.status_code, server_url)
            except (requests.RequestException, requests.Timeout) as e:
                log.warning("LibreTranslate server %s error: %s", server_url, e)
        if not done:
            log.warning("All LibreTranslate servers failed for this chunk")
            translated.append("")
        time.sleep(0.3)

    return "\n".join(translated)


# ---------- DeepL ----------

def _translate_deepl(text: str, api_key: str, api_url: str) -> str:
    url = api_url or "https://api-free.deepl.com/v2/translate"
    if not api_key:
        log.warning("DeepL key missing, fallback to libre")
        return _translate_libre(text, "")
    try:
        resp = requests.post(
            url,
            data={"auth_key": api_key, "text": text, "source_lang": "EN", "target_lang": "ZH"},
            timeout=30,
        )
        if resp.status_code == 200:
            return "\n".join(t["text"] for t in resp.json().get("translations", []))
        log.warning("DeepL %d: %s", resp.status_code, resp.text[:200])
        return ""
    except Exception as e:
        log.warning("DeepL error: %s", e)
        return ""


# ---------- OpenAI ----------

def _translate_openai(text: str, api_key: str, api_url: str) -> str:
    url = api_url or "https://api.openai.com/v1/chat/completions"
    if not api_key:
        log.warning("OpenAI key missing, fallback to libre")
        return _translate_libre(text, "")
    try:
        resp = requests.post(
            url,
            json={
                "model": "gpt-3.5-turbo",
                "messages": [
                    {"role": "system", "content": "You are a professional translator. Translate English to Chinese. Preserve paragraph breaks. Output only the translation."},
                    {"role": "user", "content": text},
                ],
                "temperature": 0.3,
            },
            timeout=60,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        )
        if resp.status_code == 200:
            return resp.json()["choices"][0]["message"]["content"].strip()
        log.warning("OpenAI %d: %s", resp.status_code, resp.text[:200])
        return ""
    except Exception as e:
        log.warning("OpenAI error: %s", e)
        return ""


# ============================================================
#  Feishu Client
# ============================================================

class FeishuWebhookClient:
    def __init__(self, webhook_url: str, webhook_secret: str = ""):
        self.webhook_url = webhook_url
        self.webhook_secret = webhook_secret

    def _gen_sign(self, timestamp: str):
        if not self.webhook_secret:
            return timestamp, ""
        string_to_sign = f"{timestamp}\n{self.webhook_secret}"
        hmac_code = hmac.new(string_to_sign.encode("utf-8"), digestmod=hashlib.sha256).digest()
        return timestamp, base64.b64encode(hmac_code).decode("utf-8")

    def send_post_message(self, title: str, content_lines: list):
        log.info("Sending via Webhook to: %s", self.webhook_url[:60] + "...")
        payload = {
            "msg_type": "post",
            "content": {"post": {"zh_cn": {"title": title, "content": content_lines}}},
        }
        if self.webhook_secret:
            timestamp = str(int(time.time()))
            _, sign = self._gen_sign(timestamp)
            payload["timestamp"] = timestamp
            payload["sign"] = sign

        resp = requests.post(self.webhook_url, json=payload, timeout=15,
                             headers={"Content-Type": "application/json"})
        data = resp.json()
        if data.get("code") != 0:
            raise RuntimeError(f"Webhook send failed: code={data.get('code')} msg={data.get('msg', 'unknown')}")
        log.info("Webhook message sent! code=0")
        return data


class FeishuAppClient:
    def __init__(self, app_id: str, app_secret: str):
        self.app_id = app_id
        self.app_secret = app_secret
        self.token = None
        self.token_expire_at = 0

    def _get_token(self) -> str:
        if time.time() < self.token_expire_at:
            return self.token
        resp = requests.post(
            FEISHU_TOKEN_URL,
            json={"app_id": self.app_id, "app_secret": self.app_secret},
            timeout=10,
        )
        data = resp.json()
        if data.get("code") != 0:
            raise RuntimeError(f"Token error: {data.get('msg', 'unknown')}")
        self.token = data["tenant_access_token"]
        self.token_expire_at = time.time() + data.get("expire", 7200) - 300
        return self.token

    def send_post_message(self, receiver_id: str, title: str, content_lines: list):
        token = self._get_token()
        params = {"receive_id_type": "open_id"}
        if receiver_id.startswith("oc_"):
            params = {"receive_id_type": "chat_id"}
        payload = {
            "receive_id": receiver_id,
            "msg_type": "post",
            "content": json.dumps({"zh_cn": {"title": title, "content": content_lines}}),
        }
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json; charset=utf-8"}
        resp = requests.post(FEISHU_SEND_MSG_URL, params=params, json=payload, headers=headers, timeout=15)
        data = resp.json()
        if data.get("code") != 0:
            raise RuntimeError(f"Send failed: {data.get('msg', 'unknown')}")
        log.info("App message sent! id=%s", data.get("data", {}).get("message_id"))
        return data


# ============================================================
#  Article Fetcher
# ============================================================

EXERCISE_PATTERNS = [
    r'^\d+\.\s+[A-Z]',
    r'^\d+\)\s+',
    r'^\d+\)\s+[a-d][\.\)]',
    r'_{3,}',
    r'[a-d]\)\s+[a-z]',
    r'^[A-D]\.\s+',
    r'Students\s+(walk|talk|go|read|think|write|sit|stand|work|share)',
    r'In pairs\s*/\s*groups',
    r'Spend one minute',
    r'Rank these',
    r'Have a chat',
    r'Change partners',
    r'Complete this table',
    r'Guess if .* below are true',
    r'Synonym Match|Phrase Match|Vocabulary',
    r'WORD SEARCH|Gap fill|Listening',
    r'Before reading|After reading|Comprehension',
    r'Warm-ups?\s*:|Discussion Questions',
    r'Role [Pp]lay|Multiple choice',
    r'Copyright|All rights reserved',
    r'Buy my \d',
    r'e-Book|eBook',
    r'PRINT\s*$',
    r'STUDENT\s+[AB]',
    r'Write five GOOD questions',
    r'Write five questions',
    r'Interview other students',
    r'\(\d+\)\s_+',
    r'____+',
]

SKIP_KEYWORDS = [
    'phrase matching', 'listening fill', 'student survey', 'free writing',
    'warm-up', 'before reading', 'after reading', 'true / false',
    'synonym match', 'multiple choice', 'gap fill', 'role play',
    'word search', 'comprehension', 'dictation', 'spelling',
    'try the same news', 'easier levels', 'buy my ',
    'see a sample', 'mini lesson', 'speed reading',
    '5-speed listening', 'graded readings', 'graded news',
    'copyright', 'all rights reserved', 'teachers',
    'student a', 'student b', 'write five', 'word pairs',
    'missing words', 'put the text back', 'text reconstruction',
]

NON_ARTICLE_PATTERNS = [
    r'^".+"\s*\n\s*\w+.*\(\d{4}\)',
    r'^\u201c.+\u201d\s*\n\s*\w+.*\(\d{4}\)',
    r'\(\d{4}\)\s*$',
    r'Teaching Current Events',
    r'autobiography',
    r'^News\s*$',
    r'\$US\s+\d',
    r'Take a look',
    r'Make sure you try all',
]

EXERCISE_SECTION_IDS = frozenset([
    'warm-ups', 'gap-fill', 'before-reading-listening',
    'listening-guess-the-answers', 'listening-fill-gaps',
    'comprehension-questions', 'multiple-choice-quiz',
    'role-play', 'after-reading-listening', 'newspapers-survey',
    'newspapers-discussion', 'discussion-write-your-own',
    'language-cloze', 'spelling', 'put-the-text-back',
])


class ArticleFetcher:
    """Fetch articles from Breaking News English.
    Priority: Level 6 (upper-intermediate) -> Level 5 (intermediate).
    """

    def __init__(self, data_dir: str):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)

    def get_latest_article(self, sent_ids: Optional[set] = None) -> Optional[Dict]:
        log.info("Fetching latest articles...")
        articles = []
        seen_slugs = set()

        for level, page_url in BNE_LEVEL_PAGES:
            try:
                log.info("Fetching level %d listing: %s", level, page_url)
                resp = requests.get(page_url, headers=HTTP_HEADERS, timeout=15)
                resp.encoding = 'utf-8'
                soup = BeautifulSoup(resp.text, 'html.parser')
            except Exception as e:
                log.warning("Failed to fetch level %d page: %s", level, e)
                continue

            for link in soup.find_all('a', href=True):
                href = link['href'].strip()
                text = link.get_text(strip=True)
                match = re.match(r'^(\d{4})/(\d{6})-.+\.html$', href)
                if match:
                    base_slug = re.sub(r'-\d+\.html$', '.html', href)
                    if base_slug in seen_slugs:
                        continue
                    seen_slugs.add(base_slug)
                    full_url = urljoin(BNE_BASE_URL, href)
                    try:
                        pub_date = datetime.strptime(match.group(2), "%y%m%d").date()
                    except ValueError:
                        continue
                    level_match = re.search(r'-(\d+)\.html$', href)
                    articles.append({
                        "url": full_url,
                        "title": text or href,
                        "pub_date": pub_date,
                        "slug": href,
                        "level": int(level_match.group(1)) if level_match else level,
                    })

        if not articles:
            log.warning("No articles found on any listing page")
            return None

        articles.sort(key=lambda x: x["pub_date"], reverse=True)
        seen = set()
        unique = []
        for art in articles:
            base_key = re.match(r'(\d{4}/\d{6})', art["slug"])
            if base_key and base_key.group(1) not in seen:
                seen.add(base_key.group(1))
                unique.append(art)
        log.info("Found %d unique recent articles", len(unique))

        for art in unique[:10]:
            base_url = re.sub(r'-\d+\.html$', '.html', art["url"])
            level5_url = re.sub(r'-\d+\.html$', '-5.html', art["url"])
            if art["level"] >= 6:
                candidates = [("base_url_l6", base_url, 6), ("level_5", level5_url, 5)]
            else:
                candidates = [("found_url", art["url"], art["level"])]

            for label, url_to_try, level in candidates:
                data = self._fetch_article_content(url_to_try, level)
                if data:
                    data["level"] = level
                    data["source_url"] = url_to_try
                    data["pub_date"] = art["pub_date"]
                    candidate_id = ArticleTracker.generate_id(url_to_try, data["title"])
                    if sent_ids and candidate_id in sent_ids:
                        log.info("Already sent (ID: %s), trying next article.", candidate_id)
                        continue
                    log.info("Got article at Level %d: %s", level, data["title"])
                    return data

        log.warning("Could not find any unsent article")
        return None

    @staticmethod
    def _is_exercise_content(text: str) -> bool:
        text_lower = text.lower()
        for kw in SKIP_KEYWORDS:
            if kw in text_lower:
                return True
        for pattern in EXERCISE_PATTERNS:
            if re.search(pattern, text, re.IGNORECASE):
                return True
        return False

    @staticmethod
    def _is_non_article(text: str) -> bool:
        for pattern in NON_ARTICLE_PATTERNS:
            if re.search(pattern, text, re.IGNORECASE):
                return True
        return False

    def _fetch_article_content(self, url: str, level: int) -> Optional[Dict]:
        log.info("Fetching (L%d): %s", level, url)
        try:
            resp = requests.get(url, headers=HTTP_HEADERS, timeout=20)
            resp.encoding = 'utf-8'
            soup = BeautifulSoup(resp.text, 'html.parser')
        except Exception as e:
            log.warning("Failed: %s", e)
            return None

        # Title
        title_tag = soup.find('h1') or soup.find('title')
        title = ""
        if title_tag:
            title = clean_html_text(title_tag.get_text())
            for pat in [
                r'^Breaking News English\s*(Lesson)?\s*:?\s*',
                r'\s*[-&]\s*Level\s*\d+.*$',
                r'\s*[-|]\s*Breaking News English.*$',
                r'\s*[-|]\s*Easy English.*$',
                r'^The\s+Reading\s*/\s*Listening\s*[-\s]*',
            ]:
                title = re.sub(pat, '', title, flags=re.IGNORECASE).strip()
            if len(title) > 80:
                for sep in [' - ', ' | ', '\u2013', '\u2014']:
                    if sep in title:
                        title = title.split(sep)[0].strip()
                        break

        # Body text
        article_paragraphs = self._extract_paragraphs(soup)

        # Final cleanup
        cleaned = []
        for para in article_paragraphs:
            if self._is_exercise_content(para) or self._is_non_article(para):
                break
            cleaned.append(para)

        article_text = '\n\n'.join(cleaned)
        if not article_text or len(article_text.strip()) < 80:
            log.warning("Content too short (len=%d)", len(article_text.strip()) if article_text else 0)
            return None

        # Sources
        sources = []
        for p in soup.find_all('p'):
            ptext = p.get_text()
            if re.search(r'(apnews\.com|reuters\.com|bbc\.com|cnn\.com|nytimes\.com|theguardian\.com)', ptext, re.I):
                sources.extend(re.findall(r'https?://[^\s<>"]+', ptext))
        src_section = soup.find(string=re.compile(r'^Sources?$', re.IGNORECASE))
        if src_section:
            for sibling in src_section.parent.find_next_siblings():
                if sibling.name == 'p':
                    sources.extend(re.findall(r'https?://[^\s<>"]+', sibling.get_text()))

        return {"title": title, "text": article_text.strip(), "sources": sources}

    def _extract_paragraphs(self, soup) -> list:
        """Extract article paragraphs from HTML, filtering out exercises."""
        paragraphs = []

        # Strategy 1: Extract from <article> tag
        article_tag = soup.find('article')
        if article_tag:
            for p in article_tag.find_all('p'):
                text = clean_html_text(p.get_text())
                if not text:
                    continue
                sub_paras = [s.strip() for s in re.split(r'\n\s*\n', text) if s.strip()]
                for sp in sub_paras:
                    if len(sp) > 50 and not self._is_exercise_content(sp):
                        paragraphs.append(sp)
            if len(paragraphs) >= 2:
                return paragraphs

        # Strategy 2: Find "The Reading / Listening" header
        paragraphs = []
        reading_header = soup.find(string=re.compile(r'Reading\s*/\s*Listening', re.IGNORECASE))
        if reading_header:
            container = reading_header.parent
            for ancestor in container.parents:
                if ancestor.name in ('header', 'div', 'section'):
                    container = ancestor
                    break

            section = container
            for ancestor in container.parents:
                if ancestor.name == 'div' and ancestor.get('class') and 'section' in ' '.join(ancestor.get('class', [])):
                    section = ancestor
                    break

            art_in_section = section.find('article')
            if art_in_section:
                for p in art_in_section.find_all('p'):
                    text = clean_html_text(p.get_text())
                    if not text:
                        continue
                    sub_paras = [s.strip() for s in re.split(r'\n\s*\n', text) if s.strip()]
                    for sp in sub_paras:
                        if len(sp) > 50 and not self._is_exercise_content(sp):
                            paragraphs.append(sp)
            else:
                for element in container.next_siblings:
                    if hasattr(element, 'name') and element.name == 'p':
                        text = clean_html_text(element.get_text())
                        if text and len(text) > 100 and not self._is_exercise_content(text):
                            sub_paras = [s.strip() for s in re.split(r'\n\s*\n', text) if s.strip()]
                            for sp in sub_paras:
                                if len(sp) > 50 and not self._is_exercise_content(sp):
                                    paragraphs.append(sp)
                    if hasattr(element, 'name') and element.name in ('div', 'h2', 'h3', 'h4'):
                        div_text = element.get_text(strip=True).lower()
                        if any(kw in div_text for kw in SKIP_KEYWORDS):
                            break

            if len(paragraphs) >= 2:
                return paragraphs

        # Strategy 3: Long standalone <p> tags outside exercise sections
        paragraphs = []
        for p in soup.find_all('p'):
            in_bad_section = False
            for ancestor in p.parents:
                if hasattr(ancestor, 'get') and ancestor.get('id', '') in EXERCISE_SECTION_IDS:
                    in_bad_section = True
                    break
                parent_classes = ' '.join(ancestor.get('class', [])) if hasattr(ancestor, 'get') else ''
                if 'content-container' in parent_classes or 'lesson-excerpt' in parent_classes:
                    in_bad_section = True
                    break
                if ancestor.name == 'article':
                    break
                if hasattr(ancestor, 'get') and ancestor.get('id', '') == 'secondary':
                    in_bad_section = True
                    break
            if in_bad_section:
                continue
            text = clean_html_text(p.get_text())
            if text and len(text) > 200 and not self._is_exercise_content(text) and not self._is_non_article(text):
                paragraphs.append(text)
            if len(paragraphs) >= 3:
                break

        return paragraphs


# ============================================================
#  Message Builder
# ============================================================

class MessageBuilder:
    @staticmethod
    def _text(text: str) -> list:
        return [[{"tag": "text", "text": text}]]

    @staticmethod
    def _blank() -> list:
        return [[{"tag": "text", "text": ""}]]

    @staticmethod
    def _hr() -> list:
        return [[{"tag": "text", "text": "----------------------------------------"}]]

    @classmethod
    def build(cls, article: dict, translated_text: str, translated_title: str = "") -> list:
        title = article["title"]
        text = article["text"]
        level = article.get("level", 5)
        source_url = article.get("source_url", "")
        pub_date = article.get("pub_date", date.today())
        sources = article.get("sources", [])
        today_str = pub_date.strftime("%Y-%m-%d")

        content = []
        content.extend(cls._blank())
        content.extend(cls._text("早上好！今日精读文章已为你准备好。"))
        content.extend(cls._blank())
        content.extend(cls._hr())

        content.extend(cls._blank())
        content.extend(cls._text(f"Topic: {title}"))
        if translated_title:
            content.extend(cls._text(f"主题: {translated_title}"))
        content.extend(cls._text(f"Date: {today_str}  |  Level: {level}"))
        content.extend(cls._blank())
        content.extend(cls._hr())

        content.extend(cls._blank())
        content.extend(cls._text("[ English Original ]"))
        content.extend(cls._blank())
        for para in [p.strip() for p in text.split('\n\n') if p.strip()]:
            content.extend(cls._text(para))
            content.extend(cls._blank())
        if sources:
            content.extend(cls._text("Source: " + " | ".join(sources[:2])))
            content.extend(cls._blank())
        content.extend(cls._hr())

        content.extend(cls._blank())
        content.extend(cls._text("[ Chinese Translation ]"))
        content.extend(cls._blank())
        trans_paras = [p.strip() for p in translated_text.split('\n\n') if p.strip()] if translated_text else []
        if trans_paras:
            for para in trans_paras[:10]:
                content.extend(cls._text(para))
                content.extend(cls._blank())
        else:
            content.extend(cls._text("[Translation unavailable - please check TRANSLATION_PROVIDER setting]"))
            content.extend(cls._blank())
        content.extend(cls._hr())

        content.extend(cls._blank())
        content.extend(cls._text(f"Original: {source_url}"))
        content.extend(cls._blank())
        content.extend(cls._text("Keep reading every day. English will become your strength."))
        content.extend(cls._blank())

        return content


# ============================================================
#  Article Tracker
# ============================================================

class ArticleTracker:
    def __init__(self, data_dir: str):
        self.file_path = Path(data_dir) / "sent_articles.json"
        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        self.sent = self._load()

    def _load(self) -> set:
        if self.file_path.exists():
            try:
                data = json.loads(self.file_path.read_text(encoding='utf-8'))
                return set(data.get("sent", []))
            except (json.JSONDecodeError, KeyError):
                return set()
        return set()

    def _save(self):
        self.file_path.write_text(
            json.dumps({"sent": list(self.sent)}, ensure_ascii=False, indent=2),
            encoding='utf-8',
        )

    def is_sent(self, article_id: str) -> bool:
        return article_id in self.sent

    def mark_sent(self, article_id: str):
        self.sent.add(article_id)
        self._save()

    @staticmethod
    def generate_id(url: str, title: str) -> str:
        return hashlib.md5(f"{url}|{title}".encode()).hexdigest()[:12]


# ============================================================
#  Main
# ============================================================

def run():
    config = load_config()
    data_dir = config["data_dir"]
    Path(data_dir).mkdir(parents=True, exist_ok=True)

    fetcher = ArticleFetcher(data_dir)
    tracker = ArticleTracker(data_dir)

    push_mode = config["push_mode"]
    if push_mode == "webhook":
        feishu = FeishuWebhookClient(config["feishu_webhook_url"], config["feishu_webhook_secret"])
    else:
        feishu = FeishuAppClient(config["feishu_app_id"], config["feishu_app_secret"])

    # 1. Fetch
    log.info("=" * 50)
    log.info("Fetching latest article (push mode: %s)...", push_mode)
    article = fetcher.get_latest_article(tracker.sent)
    if not article:
        log.error("Failed to fetch article.")
        sys.exit(1)
    log.info("Article: %s (Level %d)", article["title"], article.get("level", 0))

    # 2. Dedup (safety net; get_latest_article already skips sent articles)
    article_id = ArticleTracker.generate_id(article.get("source_url", ""), article["title"])
    if tracker.is_sent(article_id):
        log.info("Already sent (ID: %s), skipping.", article_id)
        return

    # 3. Translate
    log.info("Translating...")
    translation = translate_text(article["text"], config)
    translated_title = translate_text(article["title"], config)
    if not translation or not translation.strip():
        log.warning("Translation failed! Check TRANSLATION_PROVIDER setting.")
    if not translated_title or not translated_title.strip():
        log.warning("Title translation failed.")

    # 4. Build message
    log.info("Building message...")
    content_lines = MessageBuilder.build(article, translation, translated_title)

    slug = re.sub(r'[^a-zA-Z0-9]+', '_', article["title"])[:40]
    msg_file = Path(data_dir) / f"article_{article_id}_{slug}.json"
    msg_file.write_text(
        json.dumps({
            "article_id": article_id, "title": article["title"],
            "pub_date": str(article.get("pub_date", "")),
            "level": article.get("level", 0), "push_mode": push_mode,
            "content_lines": content_lines,
        }, ensure_ascii=False, indent=2),
        encoding='utf-8',
    )
    log.info("Saved to: %s", msg_file)

    # 5. Send
    title_line = f"Daily English Reading | {article['title']}"
    log.info("Sending to Feishu...")
    try:
        if push_mode == "webhook":
            feishu.send_post_message(title_line, content_lines)
        else:
            feishu.send_post_message(config["feishu_receiver_id"], title_line, content_lines)
        tracker.mark_sent(article_id)
        log.info("Done! ID: %s", article_id)
    except Exception as e:
        log.error("Send failed: %s", e)
        log.error(traceback.format_exc())
        sys.exit(1)


if __name__ == "__main__":
    print("Daily English Article Pusher")
    print("=" * 40)
    try:
        run()
    except KeyboardInterrupt:
        log.info("Interrupted")
        sys.exit(0)
    except Exception as e:
        log.error("Error: %s", e)
        log.error(traceback.format_exc())
        sys.exit(1)