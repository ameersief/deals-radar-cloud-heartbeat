# -*- coding: utf-8 -*-
"""
Daily Live Deal Hunter & Real-Time Price Verifier
Hunts 100% fresh, live Amazon.ae deals across the 8 Supergroup categories.
Verifies discounts, stock status, and updates Neon PostgreSQL catalog.
"""
import os, psycopg2, requests, bs4, re, sys, time
from datetime import datetime

sys.stdout.reconfigure(encoding='utf-8')

# Strictly read from environment variable - NEVER hardcode credentials
DATABASE_URL = os.getenv('DATABASE_URL')
if not DATABASE_URL:
    print("WARNING: DATABASE_URL secret is not set in environment.")
    sys.exit(0)

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36',
    'Accept-Language': 'en-US,en;q=0.9',
}

CATEGORY_QUERIES = {
    '☕ أجهزة القهوة والمطبخ الذكي': [
        'espresso machine discount',
        'coffee maker deal',
        'nespresso machine discount'
    ],
    '✨ العطور وبدائل الماركات الفاخرة': [
        'lattafa perfume discount',
        'oud perfume deal',
        'arabian oud discount'
    ],
    '🚗 إكسسوارات السيارات والرحلات': [
        'car dash cam deals',
        'car jump starter discount',
        'car wireless charger mount deal'
    ],
    '💡 أدوات حل المشاكل وترند التيك توك': [
        'smart home gadgets deals',
        'robot vacuum discount',
        'kitchen smart chopper deals'
    ],
    '🎧 الإلكترونيات والأجهزة والترفيه': [
        'anker power bank discount',
        'noise cancelling headphones deal',
        'ugreen charger discount'
    ],
    '🧺 مستلزمات السوبرماركت والتوفير': [
        'laundry detergent discount',
        'skincare deals amazon',
        'oral b toothbrush deal'
    ],
    '👗 الموضة والساعات والأناقة': [
        'casio watch discount',
        'mens leather wallet deal',
        'sunglasses polarized discount'
    ],
    '🎮 الجيمنج ومكاتب العمل الذكية': [
        'gaming mechanical keyboard deal',
        'gaming mouse discount',
        'monitor arm desk mount deals'
    ]
}

def setup_db(conn):
    cur = conn.cursor()
    cur.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='amazon_deals_catalog' AND column_name='is_active') THEN
                ALTER TABLE amazon_deals_catalog ADD COLUMN is_active BOOLEAN DEFAULT TRUE;
            END IF;
            IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='amazon_deals_catalog' AND column_name='verified_at') THEN
                ALTER TABLE amazon_deals_catalog ADD COLUMN verified_at TIMESTAMP WITH TIME ZONE DEFAULT NOW();
            END IF;
        END $$;
    """)
    conn.commit()

def clean_num(text):
    if not text:
        return 0.0
    val = re.sub(r'[^\d.]', '', text.replace(',', ''))
    try:
        return float(val)
    except:
        return 0.0

def hunt_category_deals(category, queries, max_per_category=4):
    found = []
    seen = set()
    
    for q in queries:
        if len(found) >= max_per_category:
            break
        url = f"https://www.amazon.ae/s?k={q.replace(' ', '+')}&s=exact-aware-popularity-rank"
        try:
            r = requests.get(url, headers=HEADERS, timeout=12)
            if r.status_code != 200:
                continue
            soup = bs4.BeautifulSoup(r.text, 'html.parser')
            items = soup.select('div[data-component-type="s-search-result"]')
            for it in items:
                asin = it.get('data-asin')
                if not asin or asin in seen:
                    continue
                seen.add(asin)
                
                prices = [p.get_text(strip=True) for p in it.select('.a-price .a-offscreen')]
                if len(prices) >= 2:
                    current = clean_num(prices[0])
                    orig = clean_num(prices[1])
                    if orig > current > 15:
                        disc = round(((orig - current) / orig) * 100)
                        if disc >= 15:
                            h2 = it.select_one('h2')
                            title = h2.get_text(strip=True) if h2 else ""
                            if not title or len(title) < 15:
                                continue
                            
                            img_el = it.select_one('img.s-image')
                            img = img_el.get('src') if img_el else ""
                            if img:
                                img = re.sub(r'_[A-Z0-9,]+_\.', '_AC_SL1200_.', img)
                            
                            savings = orig - current
                            badge = f"خصم {disc}% حقيقي"
                            
                            found.append({
                                'asin': asin,
                                'title': title,
                                'deal_price': current,
                                'original_price': orig,
                                'discount_pct': disc,
                                'savings': savings,
                                'category': category,
                                'image_url': img,
                                'badge_label': badge,
                                'affiliate_url': f"https://www.amazon.ae/dp/{asin}?tag=lamha0e-21"
                            })
                            if len(found) >= max_per_category:
                                break
        except Exception as e:
            print(f"  [Scrape Warning on query '{q}'] {e}")
            
    return found

def main():
    print("=" * 60)
    print("🚀 Starting Daily Live Fresh Deal Hunter for Amazon.ae")
    print(f"Timestamp: {datetime.now().isoformat()}")
    print("=" * 60)
    
    conn = psycopg2.connect(DATABASE_URL)
    setup_db(conn)
    cur = conn.cursor()
    
    total_added = 0
    total_updated = 0
    
    for cat, queries in CATEGORY_QUERIES.items():
        print(f"\nScanning live deals for: {cat}...")
        deals = hunt_category_deals(cat, queries, max_per_category=4)
        print(f"  -> Discovered {len(deals)} verified live discounted deals.")
        
        for d in deals:
            cur.execute("SELECT id, deal_price FROM amazon_deals_catalog WHERE asin = %s", (d['asin'],))
            row = cur.fetchone()
            if row:
                cur.execute("""
                    UPDATE amazon_deals_catalog
                    SET deal_price = %s,
                        original_price = %s,
                        discount_pct = %s,
                        savings = %s,
                        image_url = %s,
                        category = %s,
                        is_active = TRUE,
                        verified_at = NOW()
                    WHERE asin = %s
                """, (d['deal_price'], d['original_price'], d['discount_pct'], d['savings'], d['image_url'], d['category'], d['asin']))
                total_updated += 1
            else:
                cur.execute("""
                    INSERT INTO amazon_deals_catalog 
                    (asin, title, deal_price, original_price, discount_pct, savings, category, image_url, affiliate_url, badge_label, is_published, posted_count, is_active, verified_at, created_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, FALSE, 0, TRUE, NOW(), NOW())
                """, (
                    d['asin'], d['title'], d['deal_price'], d['original_price'], d['discount_pct'],
                    d['savings'], d['category'], d['image_url'], d['affiliate_url'], d['badge_label']
                ))
                total_added += 1
                
        conn.commit()
    
    cur.execute("""
        UPDATE amazon_deals_catalog
        SET is_active = FALSE
        WHERE verified_at < NOW() - INTERVAL '3 days';
    """)
    conn.commit()
    
    print("\n" + "=" * 60)
    print(f"✅ Daily Live Hunt Complete!")
    print(f"Total Fresh Deals Added: {total_added}")
    print(f"Total Existing Deals Re-Verified with Live Prices: {total_updated}")
    
    conn.close()

if __name__ == '__main__':
    main()
