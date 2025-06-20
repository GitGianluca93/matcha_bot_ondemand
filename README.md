# All Matcha Restock Bot 🍵

A Telegram bot that monitors product availability and price changes on various websites. Get notified when products come back in stock or when their prices change.

## Setup Instructions

1. **Create a Telegram Bot**
   - Open Telegram and search for [@BotFather](https://t.me/botfather)
   - Send `/newbot` command
   - Follow the instructions to create your bot
   - Copy the API token provided by BotFather

2. **Get Your Chat ID**
   - Start a chat with [@RawDataBot](https://t.me/RawDataBot)
   - It will show you your chat ID in the message
   - Copy the "id" number

3. **Set Up GitHub Repository**
   - Create a new repository on GitHub
   - Add these secrets in your repository settings (Settings > Secrets > New repository secret):
     - `TELEGRAM_TOKEN`: Your bot token from step 1
     - `CHAT_ID`: Your chat ID from step 2

4. **Deploy the Bot**
   - Push this code to your GitHub repository
   - The bot will automatically start running via GitHub Actions

## Using the Bot

1. Start the bot by sending `/start` in your Telegram chat
2. Use the inline buttons to:
   - Add products to monitor
   - Remove products from monitoring
   - List all monitored products
   - Manually check product status

### Adding Products
- Click "Add Product" button or use `/add <product_url>`
- The bot will start monitoring the product

### Removing Products
- Click "Remove Product" button
- Select the product you want to remove from the list

### Checking Status
- Click "List Products" to see all monitored products
- Click "Check Now" to force an immediate check of all products

## Features

- 🔄 Automatic monitoring every 5 minutes
- 🔔 Instant notifications when products become available/unavailable
- 💰 Price tracking (when available)
- 🔲 Easy-to-use button interface
- 🏃‍♂️ Runs 24/7 via GitHub Actions

## Notes

- The bot checks products every 5 minutes by default
- Make sure the product URLs are accessible without login
- Some websites might block frequent requests
