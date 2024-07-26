from settings import TOKEN
from src.bot_controller import BotController

if __name__ == '__main__':
    bot = BotController(TOKEN)
    bot.run()
