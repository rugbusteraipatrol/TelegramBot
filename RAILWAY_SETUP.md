# 🚂 Railway Setup za PriceBot Srbija

## Problem
Bot daje grešku pri pretrazi na Railway-u jer **nema `.env` fajla** (sigurnosni razlog - `.env` se nikada ne commituje na GitHub).

## ✅ Rješenje: Postavi Environment Variables na Railway-u

### Korak 1: Idi na Railway Dashboard
1. Otvori [https://railway.app](https://railway.app)
2. Logiraj se sa GitHub nalogom
3. Klikni na svoju aplikaciju (PriceBot)

### Korak 2: Postavi Environment Variables

1. U bočnom meniju klikni **Variables** (ili pogledaj sliku ispod)
2. Dodaj tri varijable:

```
TELEGRAM_TOKEN = 8222126562:AAHQeCug9X9dmCT5yg_g-oBLtDPXRPBcVTw
GOOGLE_API_KEY = AIzaSyDNSLs0oXe-yHRAVxPuJNI9QdmwvZbtnpc
STRIPE_LINK = https://buy.stripe.com/your_link_here
```

3. Klikni **Save** za svaku varijablu

### Korak 3: Redeploy aplikacije

1. U Railway dashboard-u, klikni **Deployments**
2. Pronađi zadnji deployment
3. Klikni **Redeploy** (ili sačekaj da Railway automatski redeploya)

---

## 🔍 Kako provjeri da je sve OK?

Nakon redeploya:

```bash
# SSH u Railway kontejner
railway shell

# Provjeri da li su varijable dostupne
echo $TELEGRAM_TOKEN
echo $GOOGLE_API_KEY
```

Trebale bi biti vidljive vrijednosti.

---

## 📝 Gdje se varijable koriste u kodu?

```python
# bot.py, linije 23-25
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
STRIPE_LINK = os.getenv("STRIPE_LINK")
```

Bot automatski učitava varijable iz Railway-a umjesto `.env` fajla.

---

## 🆘 Ako još uvijek ne radi?

1. **Provjeri logove na Railway-u:**
   - Klikni na aplikaciju
   - Pogledaj **Logs** tab
   - Traži `"Greška pri Gemini pretrazi"`

2. **Testiraj API ključ:**
   ```bash
   railway shell
   python -c "import os; from dotenv import load_dotenv; load_dotenv(); print(os.getenv('GOOGLE_API_KEY'))"
   ```

3. **Kontaktiraj Railway support** ako se problem ne riješi

---

## ✨ Savjet za budućnost

Kada sljedeći put deployaš kod:
- Ne commituj `.env` (već je u `.gitignore` ✅)
- Postavi varijable direktno na Railway-u
- `.env.example` pokazuje koje varijable trebaju (educational purposes)

---

**Sada bi trebalo da radi! 🚀**
