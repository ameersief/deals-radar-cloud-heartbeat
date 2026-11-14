# -*- coding: utf-8 -*-
"""
Autonomous Telegram Publisher (Fallback & Direct Cloud Engine)
Pulls top verified deals across the 8 categories from Neon DB
and publishes them to Telegram Channel and Forum Topics.
"""
import os, psycopg2, json, urllib.request, urllib.parse, ssl, sys, time

sys.stdout.reconfigure(encoding='utf-8')

DATABASE_URL = os.getenv('DATABASE_URL')
if not DATABASE_URL:
    print("Error: DATABASE_URL not set in environment.")
    sys.exit(0)

BOT_TOKEN = "7979623898:AAHuCqpZj9XR-bPTiDFTVm5nyrif9l2t9Rk"
CHANNEL_ID = "@deals_radar_arabic"
SUPERGROUP_ID = "-1004395158397"
ctx = ssl.create_default_context()

try:
    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()

    cur.execute("SELECT topics FROM community_groups LIMIT 1")
    row = cur.fetchone()
    topics = row[0] if row else {}

    query = """
    SELECT DISTINCT ON (category) 
        asin, title, deal_price, original_price, discount_pct, savings, image_url, category, badge_label
    FROM amazon_deals_catalog
    WHERE is_active = TRUE AND (verified_at >= NOW() - INTERVAL '48 hours' OR verified_at IS NULL)
    ORDER BY category, posted_count ASC, verified_at DESC, last_posted_at ASC NULLS FIRST;
    """
    cur.execute(query)
    deals = cur.fetchall()

    print(f"Discovered {len(deals)} verified deals to publish.")

    published_asins = []

    for d in deals:
        asin, title, current, original, disc, sav, img, cat, badge = d
        current = float(current)
        original = float(original)
        
        short_title = title.split('،')[0].split('-')[0].strip()
        if len(short_title) < 20 and len(title) > 20:
            short_title = title[:60].strip()
            
        caption = (
            f"🔥 <b>{short_title}</b>\n\n"
            f"💰 <b>السعر:</b> {current:.2f} درهم <s>({original:.2f} د.إ)</s> — <b>خصم {disc}% حقيقي</b> ⚡\n"
            f"⭐ <b>تقييم 4.5+ نجوم</b> | 🚚 <b>توصيل برايم المجاني السريع</b>"
        )
        
        affiliate_url = f"https://www.amazon.ae/dp/{asin}?tag=lamha0e-21"
        wa_text = f"شوف هالعرض بأمازون الإمارات:\n{short_title}\nالسعر: {current:.2f} د.إ بدلاً من {original:.2f} د.إ\n{affiliate_url}"
        wa_url = f"https://api.whatsapp.com/send?text={urllib.parse.quote(wa_text)}"
        alert_url = "https://t.me/deals_radar_publisher_bot?start=alert"
        
        keyboard = {
            "inline_keyboard": [
                [{"text": f"🛒 اطلب العرض من أمازون ({current:.0f} د.إ) ⚡", "url": affiliate_url}],
                [{"text": "📲 مشاركة عبر الواتساب", "url": wa_url}],
                [{"text": "🔔 اطلب تتبع سعر أي منتج آخر", "url": alert_url}]
            ]
        }
        
        # 1. Post to Main Channel
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
            
        # 2. Post to Topic in Supergroup
        thread_id = topics.get(cat)
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
                
        published_asins.append(asin)
        time.sleep(1.2)

    if published_asins:
        cur.execute("""
            UPDATE amazon_deals_catalog
            SET posted_count = posted_count + 1,
                last_posted_at = NOW(),
                is_published = TRUE
            WHERE asin = ANY(%s)
        """, (published_asins,))
        conn.commit()
        print(f"✅ Successfully updated {len(published_asins)} published deals in Neon DB.")

    conn.close()

except Exception as err:
    print(f"Publisher error: {err}")
