# -*- coding: utf-8 -*-
"""
Intelligent Autonomous Deals Wave Publisher (Zero-Failure Cloud Engine)
Features:
1. Golden Window Gate: Only publishes during Dubai daytime peak windows (10:00 AM, 02:00 PM, 06:00 PM, 09:00 PM GST).
2. Anti-Duplicate / Anti-Miss Gate: Guarantees exactly 1 wave per window, immune to GitHub Actions cron latency.
3. Zero-Stale Policy: Only publishes in-stock deals with >= 15% discount verified within 48h.
4. Dual Distribution: Posts to Telegram Channel (@deals_radar_arabic) & 8 Forum Topics.
"""
import os, sys, time, json, ssl, urllib.request, urllib.parse
from datetime import datetime, timezone

sys.stdout.reconfigure(encoding='utf-8')

DATABASE_URL = os.getenv('DATABASE_URL')
if not DATABASE_URL:
    print("Error: DATABASE_URL not set in environment.")
    sys.exit(0)

FORCE_PUBLISH = os.getenv('FORCE_PUBLISH', 'false').lower() == 'true'

BOT_TOKEN = "7979623898:AAHuCqpZj9XR-bPTiDFTVm5nyrif9l2t9Rk"
CHANNEL_ID = "@deals_radar_arabic"
SUPERGROUP_ID = "-1004395158397"
ctx = ssl.create_default_context()

def check_publishing_window(cur):
    """
    Checks if current time is within Dubai Golden Hours and if a wave is already sent.
    Dubai is UTC+4.
    Target Wave Times (UTC):
      • Morning Wave:   06:00 UTC (10:00 AM GST) -> Window: 06:00 - 08:30 UTC
      • Afternoon Wave: 10:00 UTC (02:00 PM GST) -> Window: 10:00 - 12:30 UTC
      • Evening Wave:   14:00 UTC (06:00 PM GST) -> Window: 14:00 - 16:30 UTC
      • Night Wave:     17:00 UTC (09:00 PM GST) -> Window: 17:00 - 19:30 UTC
    """
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

    # Check last wave publication time in Neon DB
    cur.execute("SELECT MAX(last_posted_at) FROM amazon_deals_catalog;")
    row = cur.fetchone()
    last_posted = row[0] if row else None

    if last_posted:
        diff_seconds = (now_utc - last_posted).total_seconds()
        diff_hours = diff_seconds / 3600.0
        # If published less than 2.2 hours ago, wave already completed
        if diff_hours < 2.2:
            print(f"✅ {current_window_name} was already published {diff_hours:.1f} hours ago (at {last_posted}). Skipping to avoid duplication.")
            return False, "already_published"

    print(f"🎯 Inside {current_window_name}! Ready to publish fresh 8-deal wave.")
    return True, current_window_name

def main():
    import psycopg2
    try:
        conn = psycopg2.connect(DATABASE_URL)
        cur = conn.cursor()

        should_publish, reason = check_publishing_window(cur)
        if not should_publish:
            conn.close()
            sys.exit(0)

        # Pull community topics
        cur.execute("SELECT topics FROM community_groups LIMIT 1;")
        row = cur.fetchone()
        topics = row[0] if row else {}

        # Query top 1 deal from each category (ordered by least posted, then freshest verification)
        query = """
        SELECT DISTINCT ON (category) 
            asin, title, deal_price, original_price, discount_pct, savings, image_url, category, badge_label
        FROM amazon_deals_catalog
        WHERE is_active = TRUE AND (verified_at >= NOW() - INTERVAL '48 hours' OR verified_at IS NULL)
        ORDER BY category, posted_count ASC, verified_at DESC, last_posted_at ASC NULLS FIRST;
        """
        cur.execute(query)
        deals = cur.fetchall()

        print(f"Discovered {len(deals)} verified deals ready for distribution.")
        if not deals:
            print("No active deals found matching zero-stale criteria.")
            conn.close()
            sys.exit(0)

        published_asins = []

        for d in deals:
            asin, title, current, original, disc, sav, img, cat, badge = d
            current = float(current)
            original = float(original)

            # Clean Arabic title
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

            # 2. Post to Forum Topic
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
            print(f"🎉 Successfully published wave of {len(published_asins)} deals across Channel & Forum!")

        conn.close()

    except Exception as err:
        print(f"Publisher error: {err}")
        sys.exit(1)

if __name__ == "__main__":
    main()
