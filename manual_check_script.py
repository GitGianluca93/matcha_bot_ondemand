import os
import json
import logging
import requests
import re
import asyncio
from urllib.parse import urlparse
from bs4 import BeautifulSoup
from datetime import datetime
from telegram import Bot
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Constants
PRODUCTS_FILE = 'products.json'
SITE_CONFIGS_FILE = 'site_configs.json'
TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN')
CHAT_ID = os.getenv('CHAT_ID')
CHECK_TYPE = os.getenv('CHECK_TYPE', 'check_all')

def load_products():
    """Load products from JSON file"""
    if os.path.exists(PRODUCTS_FILE):
        with open(PRODUCTS_FILE, 'r') as f:
            return json.load(f)
    return []

def save_products(products):
    """Save products to JSON file"""
    with open(PRODUCTS_FILE, 'w') as f:
        json.dump(products, f, indent=2)

def load_site_configs():
    """Load site configurations"""
    if os.path.exists(SITE_CONFIGS_FILE):
        with open(SITE_CONFIGS_FILE, 'r') as f:
            return json.load(f)
    return {}

def get_site_config(url):
    """Get site-specific configuration"""
    configs = load_site_configs()
    domain = urlparse(url).netloc.lower()
    
    for site_domain, config in configs.items():
        if site_domain in domain:
            return config
    
    return configs.get('default', {})

def extract_price(text):
    """Extract price from text using regex"""
    if not text:
        return None
        
    text = text.replace(' ', '').replace(',', '.')
    
    price_patterns = [
        r'€\s*(\d+[.,]?\d*)',
        r'(\d+[.,]?\d*)\s*€',
        r'EUR\s*(\d+[.,]?\d*)',
        r'(\d+[.,]?\d*)\s*EUR',
        r'(\d+[.,]\d{2})'
    ]
    
    for pattern in price_patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            try:
                price = float(match.group(1).replace(',', '.'))
                return f"€{price:.2f}"
            except ValueError:
                continue
    return None

def clean_text(text):
    """Clean and normalize text for comparison"""
    if not text:
        return ""
    return ' '.join(text.lower().split())

async def check_product(url):
    """Check product availability and price"""
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.5',
            'Connection': 'keep-alive',
            'Cache-Control': 'no-cache',
            'Pragma': 'no-cache'
        }
        
        config = get_site_config(url)
        
        session = requests.Session()
        response = session.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        
        soup = BeautifulSoup(response.text, 'html.parser')
        
        is_available = None
        price = None
        
        # Check availability
        for selector in config.get('availability_selectors', []):
            elements = soup.select(selector)
            if elements:
                availability_text = clean_text(elements[0].get_text())
                logger.info(f"Found availability text: {availability_text}")
                
                for indicator in config.get('in_stock_texts', []):
                    if clean_text(indicator) in availability_text:
                        is_available = True
                        break
                
                for indicator in config.get('out_of_stock_texts', []):
                    if clean_text(indicator) in availability_text:
                        is_available = False
                        break
                
                if is_available is not None:
                    break
        
        # Check entire page if no specific availability found
        if is_available is None:
            page_text = clean_text(soup.get_text())
            
            for indicator in config.get('in_stock_texts', []):
                if clean_text(indicator) in page_text:
                    is_available = True
                    break
            
            for indicator in config.get('out_of_stock_texts', []):
                if clean_text(indicator) in page_text:
                    is_available = False
                    break
        
        if is_available is None:
            is_available = True
            logger.info(f"No clear availability indicators found for {url}, defaulting to available")
        
        # Try to find price
        for selector in config.get('price_selectors', []):
            elements = soup.select(selector)
            if elements:
                price_text = elements[0].get_text().strip()
                price = extract_price(price_text)
                if price:
                    logger.info(f"Found price {price} using selector {selector}")
                    break
        
        if not price:
            price_elements = soup.find_all(text=re.compile(r'[€]\s*\d+[.,]?\d*|\d+[.,]?\d*\s*[€]'))
            if price_elements:
                price = extract_price(price_elements[0])
                if price:
                    logger.info(f"Found price {price} in general content")
        
        result = {
            'available': is_available,
            'price': price,
            'last_checked': datetime.now().isoformat()
        }
        
        logger.info(f"Check result for {url}: {result}")
        return result
        
    except Exception as e:
        logger.error(f"Error checking product {url}: {str(e)}")
        return None

async def send_telegram_message(bot, message):
    """Send message to Telegram"""
    try:
        await bot.send_message(chat_id=CHAT_ID, text=message, parse_mode='Markdown')
        logger.info("Message sent to Telegram")
    except Exception as e:
        logger.error(f"Error sending Telegram message: {str(e)}")

async def check_all_products():
    """Check all products and send notifications"""
    products = load_products()
    if not products:
        logger.info("No products to check")
        return
    
    bot = Bot(token=TELEGRAM_TOKEN)
    updated_products = []
    changes_found = False
    
    logger.info(f"Checking {len(products)} products...")
    
    # Check each product
    for i, product in enumerate(products):
        logger.info(f"Checking product {i+1}/{len(products)}: {product['url']}")
        
        result = await check_product(product['url'])
        if result is None:
            logger.warning(f"Failed to check product: {product['url']}")
            updated_products.append(product)
            continue
        
        # Check for changes
        status_changed = False
        price_changed = False
        
        if 'available' in product and product['available'] != result['available']:
            status_changed = True
            status = "ora disponibile! 🎉" if result['available'] else "non più disponibile 😔"
            price_info = f"\nPrezzo: {result['price']}" if result.get('price') else ""
            message = f"🔄 *Cambio di Stato Rilevato!*\n\nURL: {product['url']}\nStato: {status}{price_info}"
            await send_telegram_message(bot, message)
            changes_found = True
        
        elif (product.get('price') != result.get('price') and
              product.get('price') is not None and
              result.get('price') is not None):
            price_changed = True
            message = (f"💰 *Prezzo Cambiato!*\n\n"
                      f"URL: {product['url']}\n"
                      f"Vecchio prezzo: {product['price']}\n"
                      f"Nuovo prezzo: {result['price']}")
            await send_telegram_message(bot, message)
            changes_found = True
        
        # Update product info
        product.update({
            'available': result['available'],
            'price': result.get('price'),
            'last_checked': result['last_checked']
        })
        updated_products.append(product)
        
        # Small delay between checks
        await asyncio.sleep(1)
    
    # Save updated products
    save_products(updated_products)
    
    # Send summary
    if not changes_found and updated_products:
        message = f"✅ Controllo completato su {len(updated_products)} prodotti. Nessun cambiamento rilevato."
        await send_telegram_message(bot, message)
    
    logger.info(f"Check completed. Changes found: {changes_found}")

async def list_products_summary():
    """Send a summary of all monitored products"""
    products = load_products()
    if not products:
        bot = Bot(token=TELEGRAM_TOKEN)
        await send_telegram_message(bot, "Nessun prodotto monitorato al momento.")
        return
    
    bot = Bot(token=TELEGRAM_TOKEN)
    message = "📋 *Prodotti Monitorati:*\n\n"
    
    for i, product in enumerate(products, 1):
        status = "✅ Disponibile" if product.get('available', False) else "❌ Non Disponibile"
        price = f" - Prezzo: {product.get('price', 'N/A')}" if product.get('price') else ""
        last_checked = datetime.fromisoformat(product['last_checked']).strftime("%d/%m/%Y %H:%M") if product.get('last_checked') else "Mai"
        message += f"*{i}.* [{status}]\n{product['url']}{price}\n_Ultimo controllo: {last_checked}_\n\n"
    
    await send_telegram_message(bot, message)

async def health_check():
    """Send a health check message"""
    bot = Bot(token=TELEGRAM_TOKEN)
    products = load_products()
    message = f"🤖 *Bot Status Check*\n\nBot funzionante ✅\nProdotti monitorati: {len(products)}\nTimestamp: {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}"
    await send_telegram_message(bot, message)

async def main():
    """Main function"""
    logger.info(f"Starting manual check. Type: {CHECK_TYPE}")
    
    if CHECK_TYPE == 'check_all':
        await check_all_products()
    elif CHECK_TYPE == 'list_products':
        await list_products_summary()
    elif CHECK_TYPE == 'health_check':
        await health_check()
    else:
        logger.error(f"Unknown check type: {CHECK_TYPE}")
    
    logger.info("Manual check completed")

if __name__ == '__main__':
    asyncio.run(main())
