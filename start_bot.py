from settings import TOKEN
from src.bot_controller import BotController
import os

if __name__ == '__main__':
    os.environ['TZ'] = 'Europe/Moscow'
    bot = BotController(TOKEN)
    bot.run()
