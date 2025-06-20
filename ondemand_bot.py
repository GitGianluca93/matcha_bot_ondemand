import os
import json
import logging
import requests
import re
from urllib.parse import urlparse
from bs4 import BeautifulSoup
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, BotCommand
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
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

# Bot commands (removed automatic interval settings)
COMMANDS = [
    BotCommand("start", "🚀 Avvia il bot e mostra il menu principale"),
    BotCommand("help", "❓ Mostra guida e istruzioni"),
    BotCommand("add", "➕ Aggiungi un prodotto da monitorare"),
    BotCommand("list", "📋 Lista dei prodotti monitorati"),
    BotCommand("remove", "❌ Rimuovi un prodotto"),
    BotCommand("check", "🔄 Controlla ora tutti i prodotti"),
    BotCommand("settings", "⚙️ Impostazioni del bot")
]

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
    
    # Try to find a matching config
    for site_domain, config in configs.items():
        if site_domain in domain:
            return config
    
    # Return default config if no match found
    return configs.get('default', {})

def extract_price(text):
    """Extract price from text using regex"""
    if not text:
        return None
        
    # Remove all spaces and replace comma with dot
    text = text.replace(' ', '').replace(',', '.')
    
    # Try to find price patterns
    price_patterns = [
        r'€\s*(\d+[.,]?\d*)',  # €XX.XX or €XX
        r'(\d+[.,]?\d*)\s*€',  # XX.XX€ or XX€
        r'EUR\s*(\d+[.,]?\d*)',  # EURXX.XX or EURXX
        r'(\d+[.,]?\d*)\s*EUR',  # XX.XXEUR or XXEUR
        r'(\d+[.,]\d{2})'  # XX.XX
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
    # Remove extra spaces, convert to lowercase
    return ' '.join(text.lower().split())

# Cache for product checks
CACHE = {}
CACHE_DURATION = 60  # Cache duration in seconds

async def check_product(url):
    """Check product availability and price"""
    try:
        # Check cache first
        now = datetime.now()
        if url in CACHE:
            cache_time, cache_data = CACHE[url]
            if (now - cache_time).total_seconds() < CACHE_DURATION:
                logger.info(f"Using cached data for {url}")
                return cache_data

        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.5',
            'Connection': 'keep-alive',
            'Cache-Control': 'no-cache',
            'Pragma': 'no-cache'
        }
        
        # Get site-specific configuration
        config = get_site_config(url)
        
        # Use shorter timeout and session for better performance
        session = requests.Session()
        response = session.get(url, headers=headers, timeout=10)
        response.raise_for_status()  # Raise exception for bad status codes
        
        soup = BeautifulSoup(response.text, 'html.parser')
        
        # Initialize variables
        is_available = None
        price = None
        
        # Check availability using site-specific selectors
        for selector in config.get('availability_selectors', []):
            elements = soup.select(selector)
            if elements:
                availability_text = clean_text(elements[0].get_text())
                logger.info(f"Found availability text: {availability_text}")
                
                # Check against in-stock indicators
                for indicator in config.get('in_stock_texts', []):
                    if clean_text(indicator) in availability_text:
                        is_available = True
                        break
                
                # Check against out-of-stock indicators
                for indicator in config.get('out_of_stock_texts', []):
                    if clean_text(indicator) in availability_text:
                        is_available = False
                        break
                
                if is_available is not None:
                    break
        
        # If no availability found through selectors, check the entire page
        if is_available is None:
            page_text = clean_text(soup.get_text())
            
            # Check in-stock indicators
            for indicator in config.get('in_stock_texts', []):
                if clean_text(indicator) in page_text:
                    is_available = True
                    break
            
            # Check out-of-stock indicators
            for indicator in config.get('out_of_stock_texts', []):
                if clean_text(indicator) in page_text:
                    is_available = False
                    break
        
        # If still no clear indication, default to True
        if is_available is None:
            is_available = True
            logger.info(f"No clear availability indicators found for {url}, defaulting to available")
        
        # Try to find price using site-specific selectors
        for selector in config.get('price_selectors', []):
            elements = soup.select(selector)
            if elements:
                price_text = elements[0].get_text().strip()
                price = extract_price(price_text)
                if price:
                    logger.info(f"Found price {price} using selector {selector}")
                    break
        
        # If no price found, try to find it in the general content
        if not price:
            # Look for price in any element with currency symbol
            price_elements = soup.find_all(text=re.compile(r'[€]\s*\d+[.,]?\d*|\d+[.,]?\d*\s*[€]'))
            if price_elements:
                price = extract_price(price_elements[0])
                if price:
                    logger.info(f"Found price {price} in general content")
        
        result = {
            'available': is_available,
            'price': price,
            'last_checked': now.isoformat()
        }
        
        # Cache the result
        CACHE[url] = (now, result)
        
        logger.info(f"Check result for {url}: {result}")
        return result
        
    except Exception as e:
        logger.error(f"Error checking product {url}: {str(e)}")
        return None

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send welcome message with available commands"""
    keyboard = [
        [InlineKeyboardButton("➕ Aggiungi Prodotto", callback_data='add_product')],
        [InlineKeyboardButton("📥 Aggiungi Multi Prodotti", callback_data='add_multiple')],
        [InlineKeyboardButton("❌ Rimuovi Prodotto", callback_data='remove_product')],
        [InlineKeyboardButton("🗑️ Rimuovi Tutti", callback_data='remove_all')],
        [InlineKeyboardButton("📋 Lista Prodotti", callback_data='list_products')],
        [InlineKeyboardButton("🔄 Controlla Ora", callback_data='check_now')],
        [InlineKeyboardButton("⚙️ Impostazioni", callback_data='back_to_settings')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        "Benvenuto in All Matcha Restock Bot! 🍵\n\n"
        "*MODALITÀ ON-DEMAND ATTIVA* ⚡\n"
        "Il bot controlla i prodotti solo quando richiesto manualmente.\n\n"
        "Usa i pulsanti qui sotto per gestire il monitoraggio dei prodotti:",
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle button presses"""
    query = update.callback_query
    await query.answer()
    
    if query.data == 'add_product':
        await query.message.reply_text(
            "Inviami l'URL del prodotto da monitorare.\n"
            "Formato: /add <URL_prodotto>"
        )
    
    elif query.data == 'add_multiple':
        await query.message.reply_text(
            "Inviami gli URL dei prodotti da monitorare, uno per riga.\n"
            "Esempio:\n"
            "/addmulti\n"
            "https://esempio.com/prodotto1\n"
            "https://esempio.com/prodotto2\n"
            "https://esempio.com/prodotto3"
        )
    
    elif query.data == 'remove_all':
        products = load_products()
        if not products:
            await query.message.reply_text("Nessun prodotto da rimuovere.")
            return
            
        keyboard = [
            [
                InlineKeyboardButton("✅ Sì, rimuovi tutto", callback_data='confirm_remove_all'),
                InlineKeyboardButton("❌ No, annulla", callback_data='cancel_remove_all')
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.message.reply_text(
            f"⚠️ Sei sicuro di voler rimuovere tutti i {len(products)} prodotti monitorati?",
            reply_markup=reply_markup
        )
    
    elif query.data == 'confirm_remove_all':
        save_products([])  # Clear all products
        await query.message.edit_text("✅ Tutti i prodotti sono stati rimossi dal monitoraggio.")
    
    elif query.data == 'cancel_remove_all':
        await query.message.edit_text("❌ Operazione annullata.")
    
    elif query.data == 'remove_product':
        products = load_products()
        if not products:
            await query.message.reply_text("Nessun prodotto monitorato al momento.")
            return
            
        keyboard = [[InlineKeyboardButton(f"❌ {i+1}. {p['url'][:30]}...", callback_data=f"remove_{i}")]
                   for i, p in enumerate(products)]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.message.reply_text("Seleziona il prodotto da rimuovere:", reply_markup=reply_markup)
    
    elif query.data.startswith('remove_'):
        products = load_products()
        index = int(query.data.split('_')[1])
        if 0 <= index < len(products):
            removed_url = products.pop(index)['url']
            save_products(products)
            await query.message.reply_text(f"Rimosso: {removed_url}")
    
    elif query.data == 'list_products':
        products = load_products()
        if not products:
            await query.message.reply_text("Nessun prodotto monitorato al momento.")
            return
            
        message = "📋 *Prodotti Monitorati:*\n\n"
        for i, product in enumerate(products, 1):
            status = "✅ Disponibile" if product.get('available', False) else "❌ Non Disponibile"
            price = f" - Prezzo: {product.get('price', 'N/A')}" if product.get('price') else ""
            last_checked = datetime.fromisoformat(product['last_checked']).strftime("%d/%m/%Y %H:%M") if product.get('last_checked') else "Mai"
            message += f"*{i}.* [{status}]\n{product['url']}{price}\n_Ultimo controllo: {last_checked}_\n\n"
        
        await query.message.reply_text(message, parse_mode='Markdown', disable_web_page_preview=True)
    
    elif query.data == 'check_now':
        await query.message.edit_text("🔄 Controllo prodotti in corso...")
        await check_all_products_manual(context, query.message.chat_id)
        await query.message.edit_text("✅ Controllo completato!")
        
    elif query.data == 'settings_notifications':
        keyboard = [
            [InlineKeyboardButton("🔔 Modalità On-Demand Attiva", callback_data='info_ondemand')],
            [InlineKeyboardButton("⬅️ Torna alle impostazioni", callback_data='back_to_settings')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.message.edit_text(
            "🔔 *Gestione Modalità*\n\n"
            "Il bot è ora configurato per funzionare in modalità on-demand.\n"
            "I controlli vengono eseguiti solo manualmente tramite il comando /check o il pulsante 'Controlla Ora'.",
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )
    
    elif query.data == 'info_ondemand':
        await query.message.edit_text(
            "ℹ️ *Modalità On-Demand*\n\n"
            "Il bot non esegue controlli automatici periodici.\n"
            "Per controllare i prodotti utilizza:\n"
            "• Il comando /check\n"
            "• Il pulsante 'Controlla Ora' nel menu\n\n"
            "Questo permette un maggiore controllo sui controlli e riduce il consumo di risorse.",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("⬅️ Torna alle impostazioni", callback_data='back_to_settings')
            ]]),
            parse_mode='Markdown'
        )
        
    elif query.data == 'settings_stats':
        products = load_products()
        total = len(products)
        available = sum(1 for p in products if p.get('available', False))
        
        message = (
            "📊 *Statistiche*\n\n"
            f"Prodotti monitorati: {total}\n"
            f"Disponibili: {available}\n"
            f"Non disponibili: {total - available}\n"
            f"Modalità: On-Demand ⚡"
        )
        
        keyboard = [[InlineKeyboardButton("⬅️ Torna alle impostazioni", callback_data='back_to_settings')]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.message.edit_text(
            message,
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )
        
    elif query.data == 'back_to_settings':
        keyboard = [
            [InlineKeyboardButton("🔔 Modalità Controlli", callback_data='settings_notifications')],
            [InlineKeyboardButton("📊 Statistiche", callback_data='settings_stats')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.message.edit_text(
            "⚙️ *Impostazioni*\n\n"
            "Seleziona un'opzione da configurare:",
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )

async def add_product(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Add a new product to monitor"""
    if not context.args:
        await update.message.reply_text("Inserisci l'URL del prodotto.\nFormato: /add <URL_prodotto>")
        return
    
    url = context.args[0]
    products = load_products()
    
    # Check if product already exists
    if any(p['url'] == url for p in products):
        await update.message.reply_text("Questo prodotto è già monitorato!")
        return
    
    # Check product availability
    result = await check_product(url)
    if result is None:
        await update.message.reply_text("Non riesco ad accedere a questo URL. Verifica che sia corretto.")
        return
    
    # Add product to monitoring list
    products.append({
        'url': url,
        'available': result['available'],
        'price': result.get('price'),
        'last_checked': result['last_checked']
    })
    save_products(products)
    
    status = "✅ Disponibile" if result['available'] else "❌ Non Disponibile"
    price_info = f"\nPrezzo: {result['price']}" if result.get('price') else ""
    await update.message.reply_text(
        f"Prodotto aggiunto con successo! 🎉\n\nStato attuale: {status}{price_info}\n\n"
        f"💡 *Nota:* Il bot funziona in modalità on-demand. Usa /check per controllare manualmente i prodotti."
    )

import asyncio

async def check_all_products_manual(context, chat_id):
    """Check all products manually and send notifications with comparison"""
    try:
        products = load_products()
        if not products:
            await context.bot.send_message(chat_id=chat_id, text="Nessun prodotto da controllare.")
            return
            
        updated_products = []
        changes_found = False
        
        # Create tasks for concurrent execution
        tasks = [check_product(product['url']) for product in products]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        for product, result in zip(products, results):
            if isinstance(result, Exception):
                logger.error(f"Error checking {product['url']}: {str(result)}")
                continue
            if result is None:
                continue
            
            # Check if status changed
            if 'available' in product and product['available'] != result['available']:
                status = "ora disponibile! 🎉" if result['available'] else "non più disponibile 😔"
                price_info = f"\nPrezzo: {result['price']}" if result.get('price') else ""
                message = f"🔄 *Cambio di Stato Rilevato!*\n\nURL: {product['url']}\nStato: {status}{price_info}"
                await context.bot.send_message(chat_id=chat_id, text=message, parse_mode='Markdown')
                changes_found = True
            
            # Check if price changed
            elif (product.get('price') != result.get('price') and
                  product.get('price') is not None and
                  result.get('price') is not None):
                message = (f"💰 *Prezzo Cambiato!*\n\n"
                          f"URL: {product['url']}\n"
                          f"Vecchio prezzo: {product['price']}\n"
                          f"Nuovo prezzo: {result['price']}")
                await context.bot.send_message(chat_id=chat_id, text=message, parse_mode='Markdown')
                changes_found = True
            
            # Update product info
            product.update({
                'available': result['available'],
                'price': result.get('price'),
                'last_checked': result['last_checked']
            })
            updated_products.append(product)
        
        save_products(updated_products)
        
        # Send summary if no changes were found
        if not changes_found and updated_products:
            await context.bot.send_message(
                chat_id=chat_id, 
                text=f"✅ Controllo completato su {len(updated_products)} prodotti. Nessun cambiamento rilevato."
            )
            
    except Exception as e:
        logger.error(f"Errore in check_all_products_manual: {str(e)}")
        await context.bot.send_message(chat_id=chat_id, text=f"❌ Errore durante il controllo: {str(e)}")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show help message"""
    help_text = """
*All Matcha Restock Bot - Guida* 🍵
*MODALITÀ ON-DEMAND* ⚡

*Comandi Disponibili:*
/start - Avvia il bot e mostra il menu principale
/help - Mostra questa guida
/add URL - Aggiungi un prodotto da monitorare
/list - Mostra la lista dei prodotti monitorati
/remove - Rimuovi un prodotto dal monitoraggio
/check - Controlla manualmente tutti i prodotti
/settings - Gestisci le impostazioni del bot

*Come Usare il Bot:*
1. Usa /add seguito dall'URL del prodotto per iniziare il monitoraggio
2. Usa /check per controllare manualmente tutti i prodotti
3. Il bot ti notificherà quando:
   • Un prodotto torna disponibile
   • Un prodotto diventa non disponibile
   • Il prezzo cambia

*Esempio:*
/add https://esempio.com/prodotto

*Modalità On-Demand:*
• Il bot NON controlla automaticamente i prodotti
• Tutti i controlli sono manuali tramite /check
• Maggiore controllo e risparmio di risorse
• Controlli più rapidi quando necessario

*Note:*
• Assicurati che gli URL siano corretti e accessibili
• Alcuni siti potrebbero bloccare richieste frequenti
• Usa /check quando vuoi verificare lo stato dei prodotti
"""
    await update.message.reply_text(help_text, parse_mode='Markdown')

async def list_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """List all monitored products"""
    products = load_products()
    if not products:
        await update.message.reply_text("Nessun prodotto monitorato al momento.")
        return
        
    message = "📋 *Prodotti Monitorati:*\n\n"
    for i, product in enumerate(products, 1):
        status = "✅ Disponibile" if product.get('available', False) else "❌ Non Disponibile"
        price = f" - Prezzo: {product.get('price', 'N/A')}" if product.get('price') else ""
        last_checked = datetime.fromisoformat(product['last_checked']).strftime("%d/%m/%Y %H:%M") if product.get('last_checked') else "Mai"
        message += f"*{i}.* [{status}]\n{product['url']}{price}\n_Ultimo controllo: {last_checked}_\n\n"
    
    message += "\n💡 *Usa /check per aggiornare lo stato di tutti i prodotti*"
    await update.message.reply_text(message, parse_mode='Markdown', disable_web_page_preview=True)

async def remove_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show remove product interface"""
    products = load_products()
    if not products:
        await update.message.reply_text("Nessun prodotto da rimuovere.")
        return
        
    keyboard = [[InlineKeyboardButton(f"❌ {i+1}. {p['url'][:30]}...", callback_data=f"remove_{i}")]
               for i, p in enumerate(products)]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        "Seleziona il prodotto da rimuovere:",
        reply_markup=reply_markup
    )

async def check_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Manually check all products"""
    await update.message.reply_text("🔄 Controllo prodotti in corso...")
    await check_all_products_manual(context, update.message.chat_id)

async def settings_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show settings interface"""
    keyboard = [
        [InlineKeyboardButton("🔔 Modalità Controlli", callback_data='settings_notifications')],
        [InlineKeyboardButton("📊 Statistiche", callback_data='settings_stats')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        "⚙️ *Impostazioni*\n\n"
        "Seleziona un'opzione da configurare:",
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )

async def setup_commands(application: Application):
    """Set up bot commands in menu"""
    await application.bot.set_my_commands(COMMANDS)

async def add_multiple_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Add multiple products to monitor"""
    if not update.message.text.startswith("/addmulti"):
        return
        
    # Split message into lines and remove command
    lines = update.message.text.split('\n')[1:]
    if not lines:
        await update.message.reply_text(
            "Nessun URL fornito.\n"
            "Formato:\n"
            "/addmulti\n"
            "URL1\n"
            "URL2\n"
            "URL3"
        )
        return
    
    products = load_products()
    results = []
    
    # Process each URL
    for url in lines:
        url = url.strip()
        if not url:
            continue
            
        # Check if product already exists
        if any(p['url'] == url for p in products):
            results.append(f"❌ {url}: già monitorato")
            continue
        
        # Check product availability
        result = await check_product(url)
        if result is None:
            results.append(f"❌ {url}: URL non accessibile")
            continue
        
        # Add product to monitoring list
        products.append({
            'url': url,
            'available': result['available'],
            'price': result.get('price'),
            'last_checked': result['last_checked']
        })
        
        status = "✅ Disponibile" if result['available'] else "❌ Non Disponibile"
        price_info = f" - Prezzo: {result['price']}" if result.get('price') else ""
        results.append(f"✅ {url}: {status}{price_info}")
    
    save_products(products)
    
    # Send summary message
    summary = "📥 Riepilogo aggiunte:\n\n" + "\n".join(results)
    summary += "\n\n💡 *Usa /check per controllare manualmente i prodotti*"
    await update.message.reply_text(summary, parse_mode='Markdown')

async def remove_all_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Remove all monitored products"""
    products = load_products()
    if not products:
        await update.message.reply_text("Nessun prodotto da rimuovere.")
        return
        
    keyboard = [
        [
            InlineKeyboardButton("✅ Sì, rimuovi tutto", callback_data='confirm_remove_all'),
            InlineKeyboardButton("❌ No, annulla", callback_data='cancel_remove_all')
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        f"⚠️ Sei sicuro di voler rimuovere tutti i {len(products)} prodotti monitorati?",
        reply_markup=reply_markup
    )

def main():
    """Start the bot"""
    try:
        # Create the Application
        application = Application.builder().token(TELEGRAM_TOKEN).build()
        
        # Add handlers
        application.add_handler(CommandHandler("start", start))
        application.add_handler(CommandHandler("help", help_command))
        application.add_handler(CommandHandler("add", add_product))
        application.add_handler(CommandHandler("addmulti", add_multiple_command))
        application.add_handler(CommandHandler("list", list_command))
        application.add_handler(CommandHandler("remove", remove_command))
        application.add_handler(CommandHandler("removeall", remove_all_command))
        application.add_handler(CommandHandler("check", check_command))
        application.add_handler(CommandHandler("settings", settings_command))
        application.add_handler(CallbackQueryHandler(button_handler))
        
        # Set up bot commands (removed automatic job scheduling)
        application.job_queue.run_once(setup_commands, when=1)
        
        # Start the bot (no automatic periodic jobs)
        application.run_polling(allowed_updates=Update.ALL_TYPES)
    except Exception as e:
        logger.error(f"Error in main: {str(e)}")
        raise

if __name__ == '__main__':
    main()
