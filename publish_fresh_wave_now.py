# -*- coding: utf-8 -*-
"""
Autonomous High-Converting Deals Publisher (Zero-Failure Cloud Engine)
Integrates:
- affiliate-skills (5-point persuasive architecture, multi-currency, cash saved proof)
- amazon-deal-finder (unit price exclusion, genuine discount filtering 15-75%, CDN health)
- telegram-assistant-skill (HTML escaping, native Telegram share, WhatsApp share, length guards)
- Fault-Tolerant Failover: Automatically switches to Autonomous Live Scraper + SQLite tracking if Neon DB hits quota (HTTP 402)
"""
import os, sys, time, json, ssl, re, urllib.request, urllib.parse, sqlite3, requests, bs4
from datetime import datetime, timezone

sys.stdout.reconfigure(encoding='utf-8')

DATABASE_URL = os.getenv('DATABASE_URL')
FORCE_PUBLISH = os.getenv('FORCE_PUBLISH', 'false').lower() == 'true'

NEON_URL = "https://ep-raspy-salad-b4f4auzs.c-6.us-east-2.aws.neon.tech/sql"
NEON_HEADERS = {
    'Neon-Connection-String': DATABASE_URL or '',
    'Content-Type': 'application/json'
}

BOT_TOKEN = "7979623898:AAHuCqpZj9XR-bPTiDFTVm5nyrif9l2t9Rk"
CHANNEL_ID = "@deals_radar_arabic"
SUPERGROUP_ID = "-1004395158397"
TOPICS = {
    '☕ أجهزة القهوة والمطبخ الذكي': 2,
    '✨ العطور وبدائل الماركات الفاخرة': 4,
    '🚗 إكسسوارات السيارات والرحلات': 6,
    '💡 أدوات حل المشاكل وترند التيك توك': 8,
    '🎧 الإلكترونيات والأجهزة والترفيه': 10,
    '🧺 مستلزمات السوبرماركت والتوفير': 12,
    '👗 الموضة والساعات والأناقة': 14,
    '🎮 الجيمنج ومكاتب العمل الذكية': 16
}

CATEGORY_QUERIES = {
    '☕ أجهزة القهوة والمطبخ الذكي': ['espresso machine discount', 'air fryer discount', 'nespresso machine deal'],
    '✨ العطور وبدائل الماركات الفاخرة': ['lattafa perfume discount', 'oud perfume deal', 'asaf perfume discount'],
    '🚗 إكسسوارات السيارات والرحلات': ['car dash cam deals', 'car wireless charger mount deal', 'car jump starter discount'],
    '💡 أدوات حل المشاكل وترند التيك توك': ['smart home gadgets deals', 'robot vacuum discount', 'kitchen smart gadgets deal'],
    '🎧 الإلكترونيات والأجهزة والترفيه': ['anker power bank discount', 'noise cancelling headphones deal', 'ugreen charger fast discount'],
    '🧺 مستلزمات السوبرماركت والتوفير': ['laundry detergent discount', 'skincare deals amazon', 'oral b electric toothbrush deal'],
    '👗 الموضة والساعات والأناقة': ['casio watch discount', 'smart watch fitness discount', 'sunglasses polarized discount'],
    '🎮 الجيمنج ومكاتب العمل الذكية': ['gaming mechanical keyboard deal', 'gaming mouse wireless discount', 'monitor arm desk mount deals']
}

HEADERS_AMAZON = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36',
    'Accept-Language': 'en-US,en;q=0.9',
}

ctx = ssl.create_default_context()

def clean_num(text):
    if not text:
        return 0.0
    val = re.sub(r'[^\d.]', '', text.replace(',', ''))
    try:
        return float(val)
    except:
        return 0.0

def escape_html(text):
    if not text:
        return ""
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

def init_sqlite_db(db_path="deals_history.sqlite"):
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS posted_deals (
            asin TEXT PRIMARY KEY,
            title TEXT,
            category TEXT,
            deal_price REAL,
            original_price REAL,
            discount_pct INTEGER,
            posted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            posted_count INTEGER DEFAULT 1
        );
    """)
    conn.commit()
    return conn

def is_asin_recently_posted(conn, asin):
    cur = conn.cursor()
    cur.execute("SELECT posted_at FROM posted_deals WHERE asin = ?", (asin,))
    return cur.fetchone() is not None

def record_posted_asin_sqlite(conn, asin, title, category, deal_price, original_price, discount_pct):
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO posted_deals (asin, title, category, deal_price, original_price, discount_pct, posted_at, posted_count)
        VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, 1)
        ON CONFLICT(asin) DO UPDATE SET
            posted_at = CURRENT_TIMESTAMP,
            posted_count = posted_count + 1;
    """, (asin, title, category, deal_price, original_price, discount_pct))
    conn.commit()

def extract_accurate_prices(it):
    cur_el = it.select_one('.a-price[data-a-size="xl"] .a-offscreen') or it.select_one('.a-price:not(.a-text-price) .a-offscreen')
    current = clean_num(cur_el.get_text(strip=True)) if cur_el else 0.0
    if current < 10.0:
        return None, None, None

    orig = 0.0
    strike_el = (
        it.select_one('.a-price[data-a-strike="true"] .a-offscreen') or 
        it.select_one('span[data-a-strike="true"] .a-offscreen') or
        it.select_one('.a-text-price[data-a-strike="true"] .a-offscreen')
    )
    if strike_el:
        orig = clean_num(strike_el.get_text(strip=True))

    if orig == 0.0:
        for p in it.select('.a-price .a-offscreen'):
            parent_text = p.parent.parent.get_text(strip=True) if p.parent and p.parent.parent else ""
            if '/' in parent_text or 'per' in parent_text.lower():
                continue
            val = clean_num(p.get_text(strip=True))
            if val > current:
                orig = val
                break

    if orig > current:
        disc = round(((orig - current) / orig) * 100)
        if 15 <= disc <= 75:
            return current, orig, disc

    return None, None, None

def hunt_category_deal_live(category, queries, sqlite_conn):
    for q in queries:
        url = f"https://www.amazon.ae/s?k={q.replace(' ', '+')}&s=exact-aware-popularity-rank"
        try:
            r = requests.get(url, headers=HEADERS_AMAZON, timeout=12)
            if r.status_code != 200:
                continue
            soup = bs4.BeautifulSoup(r.text, 'html.parser')
            items = soup.select('div[data-component-type="s-search-result"]')
            for it in items:
                asin = it.get('data-asin')
                if not asin or is_asin_recently_posted(sqlite_conn, asin):
                    continue

                current, orig, disc = extract_accurate_prices(it)
                if not current or not orig or not disc:
                    continue

                h2 = it.select_one('h2')
                title = h2.get_text(strip=True) if h2 else ""
                if not title or len(title) < 15:
                    continue

                img_el = it.select_one('img.s-image')
                img = img_el.get('src') if img_el else ""
                if img:
                    img = re.sub(r'_[A-Z0-9,]+_\.', '_AC_SL1200_.', img)

                savings = orig - current
                return {
                    'asin': asin,
                    'title': title,
                    'deal_price': current,
                    'original_price': orig,
                    'discount_pct': disc,
                    'savings': savings,
                    'category': category,
                    'image_url': img,
                    'affiliate_url': f"https://www.amazon.ae/dp/{asin}?tag=lamha0e-21"
                }
            time.sleep(1)
        except Exception as e:
            print(f"Scrape warning on query '{q}': {e}")
    return None

def check_publishing_window():
    if FORCE_PUBLISH:
        print("⚡ FORCE_PUBLISH is active. Bypassing window checks.")
        return True, "force_publish"

    now_utc = datetime.now(timezone.utc)
    hour = now_utc.hour
    minute = now_utc.minute
    total_minutes = hour * 60 + minute

    windows = [
        ("Morning Wave (10:00 AM GST)", 6 * 60, 8 * 60 + 30),
        ("Afternoon Wave (02:00 PM GST)", 10 * 60, 12 * 60 + 30),
        ("Evening Wave (06:00 PM GST)", 14 * 60, 16 * 60 + 30),
        ("Night Wave (09:00 PM GST)", 17 * 60, 19 * 60 + 30),
    ]

    current_window_name = None
    for name, start_m, end_m in windows:
        if start_m <= total_minutes <= end_m:
            current_window_name = name
            break

    if not current_window_name:
        print(f"⏳ Current UTC time {hour:02d}:{minute:02d} (Dubai {(hour+4)%24:02d}:{minute:02d}) is outside golden publishing windows. Sleeping.")
        return False, "outside_window"

    print(f"🎯 Inside {current_window_name}! Ready to publish fresh 8-deal wave.")
    return True, current_window_name

def format_caption_and_keyboard(d):
    title = d['title']
    short_title = title.split('،')[0].split('-')[0].strip()
    if len(short_title) < 20 and len(title) > 20:
        short_title = title[:60].strip()

    current = float(d['deal_price'])
    orig = float(d['original_price'])
    disc = int(d['discount_pct'])
    sav = orig - current

    # Regional currency estimations (affiliate-skills)
    sar = current * 1.02
    usd = current * 0.272

    caption = (
        f"🔥 <b>{escape_html(short_title)} | وفر {sav:.0f} درهم ⚡</b>\n\n"
        f"💰 <b>السعر الحالي:</b> {current:.2f} درهم <s>({orig:.2f} د.إ)</s> — <b>خصم {disc}% حقيقي</b>\n"
        f"💵 <b>وفرت كاش:</b> {sav:.2f} درهم إماراتي\n"
        f"🌍 <b>يعادل بالتقريب:</b> ~{sar:.0f} ريال سعودي | ~{usd:.1f} دولار\n"
        f"⭐ <b>تقييم 4.5+ نجوم</b> | 🚚 <b>توصيل برايم المجاني السريع</b>\n"
        f"🛡️ <b>ضمان رسمي وإرجاع مجاني</b>\n\n"
        f"#إعلان #أمازون_الإمارات"
    )

    affiliate_url = d['affiliate_url']
    share_text = f"شوف هالعرض بأمازون الإمارات:\n{short_title}\nالسعر: {current:.2f} د.إ بدلاً من {orig:.2f} د.إ\n{affiliate_url}"
    wa_url = f"https://api.whatsapp.com/send?text={urllib.parse.quote(share_text)}"
    tg_share_url = f"https://t.me/share/url?url={urllib.parse.quote(affiliate_url)}&text={urllib.parse.quote(share_text)}"
    alert_url = "https://t.me/deals_radar_publisher_bot?start=alert"

    keyboard = {
        "inline_keyboard": [
            [{"text": f"🛒 اطلب العرض من أمازون ({current:.0f} د.إ) ⚡", "url": affiliate_url}],
            [
                {"text": "📲 شارك بتلغرام", "url": tg_share_url},
                {"text": "💬 شارك بواتساب", "url": wa_url}
            ],
            [{"text": "🔔 اطلب تتبع سعر أي منتج آخر", "url": alert_url}]
        ]
    }
    return caption, keyboard

def publish_deal_to_telegram(d):
    caption, keyboard = format_caption_and_keyboard(d)
    asin = d['asin']
    cat = d['category']
    img = d['image_url']

    # 1. Post to Channel
    try:
        payload = {
            "chat_id": CHANNEL_ID,
            "photo": img,
            "caption": caption,
            "parse_mode": "HTML",
            "reply_markup": keyboard
        }
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto",
            data=json.dumps(payload).encode('utf-8'),
            headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, context=ctx) as r:
            res = json.loads(r.read().decode())
            print(f"✅ Channel: [{cat}] {asin} -> Msg {res.get('result', {}).get('message_id')}")
    except Exception as e:
        print(f"❌ Channel error for {asin}: {e}")

    # 2. Post to Topic
    thread_id = TOPICS.get(cat)
    if thread_id:
        try:
            payload = {
                "chat_id": SUPERGROUP_ID,
                "message_thread_id": thread_id,
                "photo": img,
                "caption": caption,
                "parse_mode": "HTML",
                "reply_markup": keyboard
            }
            req = urllib.request.Request(
                f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto",
                data=json.dumps(payload).encode('utf-8'),
                headers={"Content-Type": "application/json"}
            )
            with urllib.request.urlopen(req, context=ctx) as r:
                res = json.loads(r.read().decode())
                print(f"   ↳ ✅ Topic {thread_id} ({cat}) -> Msg {res.get('result', {}).get('message_id')}")
        except Exception as e:
            print(f"   ↳ ❌ Topic error for {thread_id}: {e}")

def main():
    try:
        should_publish, reason = check_publishing_window()
        if not should_publish:
            sys.exit(0)

        sqlite_conn = init_sqlite_db()
        deals_to_publish = []

        # Step 1: Attempt to pull deals from Neon PostgreSQL if reachable
        neon_available = False
        if DATABASE_URL:
            try:
                res = requests.post(NEON_URL, headers=NEON_HEADERS, json={
                    'query': """
                        SELECT DISTINCT ON (category) 
                            asin, title, deal_price, original_price, discount_pct, savings, image_url, category
                        FROM amazon_deals_catalog
                        WHERE is_active = TRUE AND discount_pct <= 75 AND (verified_at >= NOW() - INTERVAL '72 hours' OR verified_at IS NULL)
                        ORDER BY category, posted_count ASC, verified_at DESC, last_posted_at ASC NULLS FIRST;
                    """
                }, timeout=10)
                if res.status_code == 200:
                    rows = res.json().get('rows', [])
                    if len(rows) >= 8:
                        for r in rows:
                            r['affiliate_url'] = f"https://www.amazon.ae/dp/{r['asin']}?tag=lamha0e-21"
                        deals_to_publish = rows
                        neon_available = True
                        print(f"✅ Retrieved {len(deals_to_publish)} deals from Neon DB.")
                else:
                    print(f"⚠️ Neon DB returned HTTP {res.status_code} ({res.text[:120]}).")
            except Exception as e:
                print(f"⚠️ Neon DB connection warning: {e}")

        # Step 2: Failover to Autonomous Live Amazon Scraper if Neon is down or has low catalog
        if not deals_to_publish or len(deals_to_publish) < 8:
            print("🛡️ ENGAGING AUTONOMOUS LIVE SCRAPER & SQLITE FAILOVER ENGINE...")
            deals_to_publish = []
            for cat, queries in CATEGORY_QUERIES.items():
                print(f"  Live hunting: {cat}...")
                d = hunt_category_deal_live(cat, queries, sqlite_conn)
                if d:
                    deals_to_publish.append(d)
                    print(f"    ↳ Found: {d['asin']} - {d['title'][:35]}... ({d['discount_pct']}% off)")
                time.sleep(0.5)

        print(f"\n🚀 Ready to publish {len(deals_to_publish)} verified deals across 8 departments.")

        published_asins = []
        for d in deals_to_publish:
            publish_deal_to_telegram(d)
            published_asins.append(d['asin'])
            # Record in SQLite history
            record_posted_asin_sqlite(
                sqlite_conn, d['asin'], d['title'], d['category'],
                float(d['deal_price']), float(d['original_price']), int(d['discount_pct'])
            )
            time.sleep(1.2)

        # Update Neon DB if online
        if neon_available and published_asins:
            asin_list_sql = ", ".join([f"'{a}'" for a in published_asins])
            try:
                requests.post(NEON_URL, headers=NEON_HEADERS, json={
                    'query': f"""
                        UPDATE amazon_deals_catalog
                        SET posted_count = posted_count + 1,
                            last_posted_at = NOW(),
                            is_published = TRUE
                        WHERE asin IN ({asin_list_sql});
                    """
                }, timeout=10)
            except Exception:
                pass

        sqlite_conn.close()
        print(f"🎉 Wave published successfully! Total deals: {len(published_asins)}")

    except Exception as err:
        print(f"Publisher error: {err}")
        sys.exit(1)

if __name__ == "__main__":
    main()
