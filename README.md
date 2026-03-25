# 🛍️ PriceBot Srbija

**Telegram bot za pronalaženje najjeftinijih cijena u Srbiji**

Pomažem korisnicima da uštede novac pretragom cijena na omiljenim sajtovima i praćenjem oglasa sa notifikacijama.

---

## ✨ Funkcionalnosti

### 🆓 Besplatni plan
- **1 AI pretraga dnevno** – Gemini 2.0 Flash sa Google Search
- **Praćenje 1 oglasa** – max 5 dana
- **Web scraping** – Polovniautomobili.com, Halooglasi.com, Kupujemprodajem.com

### 💎 Premium plan (3€/mj)
- ✅ Neograničene AI pretrage
- ✅ Neograničeno praćenje oglasa
- ✅ Bez vremenskog ograničenja
- ✅ Praćenje akcija i popusta

---

## 🚀 Instalacija

### 1. Kloniraj repozitorijum
```bash
git clone https://github.com/YOUR_USERNAME/TelegramBot.git
cd TelegramBot
```

### 2. Instaliraj zavisnosti
```bash
pip install -r requirements.txt
```

### 3. Kreiraj `.env` fajl
```env
TELEGRAM_TOKEN=your_telegram_token_here
GOOGLE_API_KEY=your_google_api_key_here
STRIPE_LINK=https://buy.stripe.com/your_link_here
```

### 4. Pokreni bota
```bash
python bot.py
```

---

## 📝 Kako funkcionira

### 🔍 Pretraži cijenu
1. Korisnik pošalje `/start` ili klikne `🔍 Pretraži cijenu`
2. Bot poziva Gemini 2.0 Flash sa Google Search toolom
3. Vraća top 3-5 najjeftinijih opcija sortirano po cijeni

Primer odgovora:
```
🛒 iPhone 15 Pro 128GB • ananas.rs: 89.990 RSD 🔗 https://...
🛒 iPhone 15 Pro 128GB • cenoteka.rs: 91.500 RSD 🔗 https://...
🛒 iPhone 15 Pro 128GB • gigatron.rs: 92.000 RSD 🔗 https://...
```

### 🔔 Prati oglas
1. Korisnik klikne `🔔 Prati oglas`
2. Izabere kategoriju (🚗 Auto, 📱 Tehnika, 🏠 Stan, 🛍️ Ostalo)
3. Upiše naziv i max cijenu: `iPhone 17 700€`
4. Bot scrapa sajt svakih 12 sati
5. Kad nađe novi oglas ispod limitne cijene, šalje notifikaciju

---

## 🗄️ Baza podataka

SQLite baza (`pricebot.db`) sa sledećim tabelama:

### `users`
```sql
CREATE TABLE users (
    id INTEGER PRIMARY KEY,
    user_id INTEGER UNIQUE,
    username TEXT,
    plan TEXT DEFAULT 'free',  -- 'free' | 'premium'
    search_count INTEGER DEFAULT 0,
    search_date TEXT,
    created_at TEXT
)
```

### `tracked_ads`
```sql
CREATE TABLE tracked_ads (
    id INTEGER PRIMARY KEY,
    user_id INTEGER,
    category TEXT,
    search_term TEXT,
    max_price REAL,
    site TEXT,
    is_premium BOOLEAN,
    known_urls TEXT,
    created_at TEXT,
    expires_at TEXT,
    is_active BOOLEAN DEFAULT 1,
    FOREIGN KEY (user_id) REFERENCES users(user_id)
)
```

---

## 🛠️ Tehnologija

| Alat | Verzija | Svrha |
|------|---------|-------|
| Python | 3.10+ | Glavna aplikacija |
| python-telegram-bot | 21.0+ | Telegram API |
| httpx | 0.24+ | HTTP zahtjevi |
| BeautifulSoup4 | 4.12+ | Web scraping |
| SQLite | - | Baza podataka |
| Google Gemini 2.0 Flash | - | AI pretrage |

---

## 📁 Struktura projekta

```
TelegramBot/
├── bot.py                 # Glavni bot fajl
├── database.py           # SQLite operacije
├── scraper.py            # Web scraping za oglase
├── requirements.txt      # Python zavisnosti
├── .env                  # API ključevi (ne commitujem!)
├── .gitignore           # Git ignore liste
├── README.md            # Ovaj fajl
└── pricebot.db          # SQLite baza (generiše se)
```

---

## 🔐 Sigurnost

- **API ključevi** su u `.env` fajlu (**.gitignore**)
- **Telegram token** i **Google API ključ** se ne commituju
- Korisničke poruke se ne loguju
- Baza se čuva lokalno

---

## 💳 Stripe integracija

Premium plan se aktivira preko Stripe link-a. Za testiranje:

1. Idi na [https://buy.stripe.com/your_link_here](https://buy.stripe.com/your_link_here)
2. Obavi plaćanje
3. Korisnik se automatski upgrade-uje u bazi

> **Napomena:** Webhook integracija sa Telegram-om nije implementirana. Za production trebalo bi da dodamo Stripe webhook.

---

## 📊 Primjer korištenja

```
User: /start

Bot: 👋 Dobrodošao u PriceBot Srbija! Pomažem ti da uštediš novac na kupovini!
     🆓 Besplatno:
     • 1 pretraga cijene dnevno (AI)
     • Praćenje 1 oglasa max 5 dana
     💎 Premium (3€/mj): [Opcije]

User: koliko košta iPhone 15

Bot: 🔍 Pretražujem cijene, molim sačekaj...
     [nakon 2-3 sekunde]
     🛒 iPhone 15 128GB • ananas.rs: 89.990 RSD 🔗 https://...
     🛒 iPhone 15 128GB • cenoteka.rs: 91.500 RSD 🔗 https://...
     🛒 iPhone 15 128GB • gigatron.rs: 92.000 RSD 🔗 https://...
```

---

## 🐛 Troubleshooting

### Bot se ne pokreće
```bash
# Provjeri da li su sve zavisnosti instalirane
pip install -r requirements.txt --upgrade

# Provjeri .env fajl
cat .env
```

### Greška pri pretrazi: "Došlo je do greške pri pretrazi"
- Provjeri Google API ključ
- Provjeri da li je Google Search uključen u Gemini konzoli
- Vidi logove: `python bot.py 2>&1 | grep ERROR`

### Praćenje oglasa ne radi
- Provjeri `.db` fajl: `sqlite3 pricebot.db ".tables"`
- Provjeri bot logove za greške pri scrapingu

---

## 📞 Podrška

Za greške ili prijedloge, otvori **Issue** na GitHub-u.

---

## 📄 Licenca

MIT License – Slobodno koristi i modificiraj kod.

---

**Napravljeno sa ❤️ za Srbiju** 🇷🇸
