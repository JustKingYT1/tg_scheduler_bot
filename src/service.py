import os
import json
import logging
from telethon import TelegramClient, errors
from telethon.sessions import StringSession
from src.database_models import User, Schedule
from datetime import datetime, timedelta
from apscheduler.triggers.cron import CronTrigger
from settings import ABC
from apscheduler.schedulers.asyncio import AsyncIOScheduler
import telethon

class TelethonClientManager:
    bot_id: int
    def __init__(self, api_id, api_hash, scheduler):
        self.api_id = api_id
        self.api_hash = api_hash
        self.clients = {}
        self.auth_states = {}
        self.scheduler: AsyncIOScheduler = scheduler
        self.session_file = 'sessions.json'
        self.load_sessions()

    def set_chat_bot_id(self, bot_id):
        self.bot_id = bot_id

    def load_sessions(self):
        if os.path.exists(self.session_file):
            with open(self.session_file, 'r') as f:
                sessions = json.load(f)
                for user_id, session_str in sessions.items():
                    client = TelegramClient(StringSession(session_str), self.api_id, self.api_hash, system_version='4.16.30-vxCUSTOM')
                    self.clients[int(user_id)] = client

    def save_sessions(self):
        sessions = {str(user_id): client.session.save() for user_id, client in self.clients.items()}
        with open(self.session_file, 'w') as f:
            json.dump(sessions, f)

    @staticmethod        
    def code_converter(code: str):
        res: str = ''
        for symbol in code:
            num = ABC.get(symbol)
            res += str(num)
        try:
            return int(res)
        except Exception as ex:
            return f'error ~ {ex}'
        
    async def start_authorization(self, user_id):
        client = TelegramClient(StringSession(), self.api_id, self.api_hash, system_version='4.16.30-vxCUSTOM')
        self.auth_states[user_id] = {'client': client, 'step': 'phone'}
        await client.connect()
        return 'Введите номер телефона:'

    async def process_authorization(self, user_id, input_data):
        step = self.auth_states[user_id]['step']
        client = self.auth_states[user_id]['client']

        if step == 'phone':
            try:
                await client.send_code_request(input_data)
                self.auth_states[user_id]['phone'] = input_data
                self.auth_states[user_id]['step'] = 'code'
                return 'Введите код из SMS:'
            except errors.FloodWaitError as e:
                logging.error(f'Flood wait error: {e}')
                return f'Подождите {e.seconds} секунд перед повторной попыткой.'
            except Exception as e:
                logging.error(f'Ошибка при отправке кода: {e}')
                return 'Ошибка при отправке кода. Пожалуйста, попробуйте снова.'

        elif step == 'code':
            phone = self.auth_states[user_id]['phone']
            try:
                print(input_data)
                code = self.code_converter(input_data)
                print(code)
                await client.sign_in(phone, code)
                self.clients[user_id] = client
                del self.auth_states[user_id]
                self.save_sessions()
                User.get_or_create(user_id=user_id)
                return 'Вы успешно авторизовались!'
            except errors.SessionPasswordNeededError:
                self.auth_states[user_id]['step'] = 'password'
                return 'Введите пароль:'
            except errors.PhoneCodeInvalidError:
                return 'Неверный код. Попробуйте снова.'
            except Exception as e:
                logging.error(f'Ошибка авторизации с кодом: {e}')
                return 'Ошибка авторизации. Пожалуйста, попробуйте снова.'

        elif step == 'password':
            try:
                await client.sign_in(password=input_data)
                self.clients[user_id] = client
                del self.auth_states[user_id]
                self.save_sessions()
                User.get_or_create(user_id=user_id)
                return 'Вы успешно авторизовались!'
            except Exception as e:
                logging.error(f'Ошибка авторизации с паролем: {e}')
                return 'Неверный пароль. Попробуйте снова.'

    async def logout(self, user_id: int) -> str:
        client = self.clients.get(user_id)
        if not client:
            return 'Пользователь не найден.'

        try:
            if not client.is_connected():
                await client.connect()
            await client.log_out()
            print(user_id)
            User.get(User.user_id == user_id).delete_instance(recursive=True)
            os.remove(self.session_file)
            del self.clients[user_id]
            return 'Вы успешно вышли из аккаунта.'
        except ConnectionError as e:
            logging.error(f'Ошибка подключения при выходе из аккаунта: {e}')
            return 'Произошла ошибка при выходе из аккаунта. Пожалуйста, попробуйте снова.'
    
    async def get_message(self, user_id):
        if user_id not in self.clients:
            self.load_sessions()

        client: TelegramClient = self.clients[user_id]

        async with client:
            source_peer = await client.get_input_entity(int(self.bot_id))
            msgs = await client.get_messages(source_peer, limit=2)  
            return msgs[0].id

    async def send_message(self, user_id, message_id, chats):
        if user_id not in self.clients:
            self.load_sessions()

        client: TelegramClient = self.clients[user_id]

        async with client:
            source_peer = await client.get_input_entity(int(self.bot_id))
            for target_chat_id in chats:
                target_peer = await client.get_input_entity(int(target_chat_id))
                try:
                    await client.forward_messages(target_peer, int(message_id), source_peer, drop_author=True)
                except telethon.errors.rpcerrorlist.ChatAdminRequiredError:
                    continue
            user = await client.get_me()
            await client.send_message(user.id, f'Сообщение "{message_id}" было отправлено в чаты: {", ".join([str(chat_id) for chat_id in chats])}')

    async def schedule_message(self, user_id, message, scheduled_times, chats):
        user, created = User.get_or_create(user_id=user_id)
        for time in scheduled_times:
            scheduled_datetime = datetime.combine(datetime.today(), time)
            if scheduled_datetime < datetime.now():
                scheduled_datetime += timedelta(days=1)
            job = self.scheduler.add_job(self.send_scheduled_message, CronTrigger(hour=time.hour, minute=time.minute, second=0, jitter=60), args=[user_id, message, chats], coalesce=False)
            Schedule.create(id=job.id, user=user, message=message, scheduled_time=scheduled_datetime, chats=json.dumps(chats))
        return 'Сообщение успешно запланировано.'

    async def get_chats(self, user_id):
        if user_id not in self.clients:
            self.load_sessions()

        client = self.clients[user_id]
        async with client:
            dialogs = await client.get_dialogs()
            return dialogs

    async def get_chat_titles(self, user_id, chat_ids):
        if user_id not in self.clients:
            raise ValueError("User not authorized")

        client = self.clients[user_id]
        titles = {}
        async with client:
            dialogs = await client.get_dialogs()
            chat_map = {dialog.id: dialog.title for dialog in dialogs if dialog.id in chat_ids}
            for chat_id in chat_ids:
                titles[chat_id] = chat_map.get(chat_id, f"Чат с ID {chat_id} (не найден)")
        return titles
    
    async def send_scheduled_message(self, user_id, message, chats):
        await self.send_message(user_id, message, chats)
