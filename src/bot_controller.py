import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, Message, CallbackQuery
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, ContextTypes, filters
from telegram.request import HTTPXRequest
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from settings import TOKEN, API_HASH, API_ID, ABC
from src.service import TelethonClientManager
from src.database_models import db, User, Schedule
import json
from datetime import datetime, timedelta
import peewee
import asyncio

# Включение логирования
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

class BotController:
    def __init__(self, token):
        self.application = Application.builder().token(token).request(HTTPXRequest(connect_timeout=10, read_timeout=20)).build()
        self.scheduler = AsyncIOScheduler()
        self.scheduler.start()
        self.telethon_manager = TelethonClientManager(API_ID, API_HASH, self.scheduler)
        self.chats_per_page = 5
        db.connect()
        db.create_tables([User, Schedule])

        message_handler = MessageHandler(
            filters=(
                (filters.TEXT & ~filters.COMMAND) |
                filters.PHOTO |
                filters.VIDEO |
                filters.Document.ALL
            ),
            callback=self.handle_message
        )

        self.application.add_handler(CommandHandler('start', self.start))
        self.application.add_handler(message_handler)
        self.application.add_handler(CallbackQueryHandler(self.button))

    def run(self):
        self.application.run_polling()

    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await self.display_main_menu(update.message)

    async def delete_schedule(self, query: CallbackQuery, context: ContextTypes.DEFAULT_TYPE, schedule_id: int) -> None:
        try:
            schedule = Schedule.get_by_id(schedule_id)
            schedule.delete_instance()
            self.scheduler.remove_job(f'schedule_{schedule_id}')
            await query.edit_message_text(f'Расписание {schedule_id} успешно удалено.')
        except peewee.DoesNotExist:
            await query.edit_message_text('Ошибка: Расписание не найдено.')
        except Exception as e:
            logging.error(f'Ошибка при удалении расписания: {e}')
            await query.edit_message_text('Ошибка при удалении расписания. Попробуйте снова.')
        # После удаления возвращаемся к главному меню
        await self.show_schedules(query, context)

    async def show_schedule_details(self, query: CallbackQuery, context: ContextTypes.DEFAULT_TYPE, schedule_id: int) -> None:
        try:
            schedule = Schedule.get_by_id(schedule_id)
            chat_titles = await self.telethon_manager.get_chat_titles(query.from_user.id, json.loads(schedule.chats))
            chat_titles_str = ', '.join([title for chat_id, title in chat_titles.items()])
            context.user_data['edit_schedule_id'] = schedule_id
            message = (f'Расписание ID: {schedule.id}\n'
                    f'Время: {schedule.scheduled_time}\n'
                    f'Сообщение: {schedule.message}\n'
                    f'Чаты: {chat_titles_str}')

            keyboard = [
                [InlineKeyboardButton("Редактировать время", callback_data=f'edit_time_{schedule.id}')],
                [InlineKeyboardButton("Редактировать сообщение", callback_data=f'edit_message_{schedule.id}')],
                [InlineKeyboardButton("Редактировать чаты", callback_data=f'edit_chats_{schedule.id}')],
                [InlineKeyboardButton("Удалить расписание", callback_data=f'delete_schedule_{schedule.id}')],
                [InlineKeyboardButton("Назад", callback_data='show_schedules')]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await query.edit_message_text(message, reply_markup=reply_markup)
        except peewee.DoesNotExist:
            await query.edit_message_text('Ошибка: Расписание не найдено.')
        except Exception as e:
            logging.error(f'Ошибка при показе деталей расписания: {e}')
            await query.edit_message_text('Ошибка при показе деталей расписания. Попробуйте снова.')

    async def button(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        user_id = query.from_user.id
        data = query.data

        await query.answer()

        if data.startswith('edit_time'):
            context.user_data['edit_step'] = 'time'
            await query.edit_message_text('Введите новое время в формате HH:MM через запятую. Например: 14:30, 18:45')

        elif data.startswith('edit_message'):
            context.user_data['edit_step'] = 'message'
            await query.edit_message_text('Введите новый текст сообщения:')

        elif data.startswith('edit_chats'):
            context.user_data['preferences'] = True
            context.user_data['edit_step'] = 'chats'
            context.user_data['current_page'] = 0
            context.user_data['selected_chats'] = []
            await query.edit_message_text('Выберите новые чаты')
            await self.handle_edit(update, context)

        elif data.startswith('instructions'):
            await update.callback_query.edit_message_text('Добро пожаловать. Телеграм дофига умный, поэтому есть данная инструкция. При вводе кода который пришлет вам телеграм, вам придется воспользоваться табличкой которую я создал, каждая буква отвечает за свою цифру, то бишь вводите код из букв, который соответветсвовал бы вашему коду в числовом виде.\na = 1\tb = 2\tc = 3 \nd = 4\te = 5\tf = 6\ng = 7\th = 8\ti = 9 \n\tj = 10')

        elif data.startswith('save_schedule'):
            await self.save_schedule(query, context)

        elif data.startswith('delete_schedule_'):
            schedule_id = int(data.split('_')[2])
            await self.delete_schedule(query, context, schedule_id)
        elif data.startswith('schedule_'):
            schedule_id = int(data.split('_')[1])
            await self.show_schedule_details(query, context, schedule_id)

        elif data == 'show_schedules':
            context.user_data['schedule_page'] = 0
            await self.show_schedules(update, context)
        
        elif data.startswith('schedule_'):
            schedule_id = int(data.split('_')[1])
            schedule = Schedule.get_by_id(schedule_id)
            chat_titles = await self.telethon_manager.get_chat_titles(user_id, json.loads(schedule.chats))
            chat_titles_str = ', '.join([title for chat_id, title in chat_titles.items()])

            message = (f'Расписание ID: {schedule.id}\n'
                    f'Время: {schedule.scheduled_time}\n'
                    f'Сообщение: {schedule.message}\n'
                    f'Чаты: {chat_titles_str}')

            keyboard = [[InlineKeyboardButton("Назад", callback_data='show_schedules')]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await query.edit_message_text(message, reply_markup=reply_markup)

        elif data in ['prev_schedules_page', 'next_schedules_page']:
            await self.navigate_schedules_page(update, context)

        elif query.data == 'prev_page':
            context.user_data['current_page'] = max(0, context.user_data.get('current_page', 0) - 1)
            await self.display_chat_selection_menu(query, context)
        elif query.data == 'next_page':
            context.user_data['current_page'] = context.user_data.get('current_page', 0) + 1
            await self.display_chat_selection_menu(query, context)
        elif query.data == 'back':
            if context.user_data.get('preferences'):
                schedule = context.user_data['current_schedule']
                schedule.chats = context.user_data['selected_chats']
                schedule.save()
                await query.message.reply_text('Чаты изменены')
            context.user_data['preferences'] = False
            await self.display_main_menu(query)
        elif query.data == 'authorize':
            if user_id in self.telethon_manager.clients:
                await query.edit_message_text('Вы уже авторизованы.')
            else:
                response = await self.telethon_manager.start_authorization(user_id)
                await query.edit_message_text(response)
                context.user_data['auth_step'] = 'phone'

        elif query.data == 'create_schedule':
            if user_id not in self.telethon_manager.clients:
                await query.edit_message_text('Пожалуйста, сначала авторизуйтесь с помощью кнопки "Авторизация".')
            else:
                context.user_data['selected_chats'] = []
                await self.display_chat_selection_menu(query, context)
        elif query.data.startswith('select_chat_'):
            chat_id = int(query.data.split('_')[-1])
            if chat_id not in context.user_data['selected_chats']:
                context.user_data['selected_chats'].append(chat_id)
            await self.display_chat_selection_menu(query, context, preferences=context.user_data.get('preferences'))

        elif query.data == 'done_selecting_chats':
            if not context.user_data.get('selected_chats'):
                await query.edit_message_text('Пожалуйста, выберите хотя бы один чат.')
                await self.display_chat_selection_menu(query, context)
            else:
                context.user_data['schedule_step'] = 'time'
                await query.edit_message_text('Введите время в формате HH:MM через запятую.')

        elif data == 'confirm_edit':
            await self.save_edited_schedule(query, context)

        elif query.data == 'show_chats':
            if user_id not in self.telethon_manager.clients:
                await query.edit_message_text('Пожалуйста, сначала авторизуйтесь с помощью кнопки "Авторизация".')
            else:
                chats = await self.telethon_manager.get_chats(user_id)
                chat_list = '\n'.join([chat.title for chat in chats])
                keyboard = [[InlineKeyboardButton("Назад", callback_data='back')]]
                reply_markup = InlineKeyboardMarkup(keyboard)
                await query.edit_message_text(f'Ваши чаты:\n{chat_list}', reply_markup=reply_markup)

        elif query.data == 'logout':
            print(user_id)
            keyboard = [[InlineKeyboardButton("Подтвердить", callback_data='confirm_logout')],
                        [InlineKeyboardButton("Отмена", callback_data='back')]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await query.edit_message_text('Вы уверены, что хотите выйти из аккаунта?', reply_markup=reply_markup)

        elif query.data == 'confirm_logout':
            print(user_id)
            response = await self.telethon_manager.logout(user_id)
            await query.edit_message_text(response)

            if 'успешно' in response:
                await self.display_main_menu(query)

    async def navigate_schedules_page(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        data = query.data

        if data == 'prev_schedules_page':
            context.user_data['schedule_page'] = max(0, context.user_data.get('schedule_page', 0) - 1)
        elif data == 'next_schedules_page':
            context.user_data['schedule_page'] = context.user_data.get('schedule_page', 0) + 1

        # Перезапускаем отображение расписаний с новой страницы
        await self.show_schedules(update, context)

    async def show_schedules(self, query: CallbackQuery, context: ContextTypes.DEFAULT_TYPE) -> None:
        user_id = query.from_user.id if isinstance(query, CallbackQuery) else query.callback_query.from_user.id
        query = query if isinstance(query, CallbackQuery) else query.callback_query
        current_page = context.user_data.get('schedule_page', 0)
        # Параметры для постраничного вывода
        schedules_per_page = 5

        schedules = Schedule.select().where(Schedule.user_id == user_id)
        total_schedules = len(schedules)

        # Определяем начало и конец текущей страницы
        start = current_page * schedules_per_page
        end = start + schedules_per_page

        # Получаем расписания для текущей страницы
        page_schedules = schedules[start:end]

        keyboard = [[InlineKeyboardButton("Назад", callback_data='back'),]]

        # Проверяем, что расписания есть на текущей странице
        if not page_schedules:
            await query.edit_message_text('Нет расписаний для отображения.', reply_markup=InlineKeyboardMarkup(keyboard))
            return

        # Формируем клавиатуру с кнопками для расписаний
        keyboard = [
            [InlineKeyboardButton(f"Расписание {s.id}", callback_data=f'schedule_{s.id}')] for s in page_schedules
        ]

        # Добавляем кнопки навигации
        if current_page > 0:
            keyboard.append([InlineKeyboardButton("Предыдущая страница", callback_data='prev_schedules_page')])
        if end < total_schedules:
            keyboard.append([InlineKeyboardButton("Следующая страница", callback_data='next_schedules_page')])

        keyboard.append([InlineKeyboardButton("Назад", callback_data='back')])

        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text('Ваше расписание:', reply_markup=reply_markup)

    async def handle_edit(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if update:
            if update.message:
                text = update.message.text if isinstance(update, Update) or isinstance(update, CallbackQuery) else update.text
                user_id = update.message.from_user.id if isinstance(update, Update) or isinstance(update, CallbackQuery) else update.from_user.id
                if update.message.audio:
                    audio = update.message.audio 
                    text = '1'
                    print(audio)
                elif update.message.video:
                    video = update.message.video
                    text = '1'
                    print(video)
                elif update.message.photo:
                    photo = update.message.photo
                    text = '1'
                    print(photo)

            else:
                text = '1'
        if 'edit_step' in context.user_data:
            schedule_id = context.user_data.get('edit_schedule_id')
            edit_step = context.user_data['edit_step']

            if edit_step == 'time':
                try:
                    times = text.split(',')
                    scheduled_times = [datetime.strptime(time.strip(), '%H:%M').time() for time in times]
                    context.user_data['new_time'] = scheduled_times
                    schedule = Schedule.get(Schedule.id == schedule_id)
                    new_scheduled_time = datetime.combine(datetime.today(), scheduled_times[0])
                    if new_scheduled_time < datetime.now():
                        new_scheduled_time += timedelta(days=1)
                    schedule.scheduled_time = new_scheduled_time
                    schedule.save()
                    await update.message.reply_text('Время успешно изменено.')
                    await self.display_main_menu(update.message)
                except Exception as e:
                    logging.error(f'Ошибка при обработке времени: {e}')
                    await update.message.reply_text('Ошибка при вводе времени. Убедитесь, что формат времени правильный.')

            elif edit_step == 'message':
                message = text.strip()
                if not message:
                    await update.message.reply_text('Сообщение не может быть пустым. Пожалуйста, введите новый текст сообщения.')
                    return
                id = await self.telethon_manager.get_message(user_id)
                context.user_data['new_message'] = id
                schedule = Schedule.get(Schedule.id == schedule_id)
                schedule.message = id
                schedule.save()
                await update.message.reply_text('Сообщение успешно изменено.')
                await self.display_main_menu(update.message)

            elif edit_step == 'chats':
                context.user_data['selected_chats'] = context.user_data.get('selected_chats', [])
                context.user_data['preferences'] = True
                print(context.user_data['preferences'])
                schedule = Schedule.get(Schedule.id==schedule_id)
                context.user_data['current_schedule'] = schedule
                await self.display_chat_selection_menu(update.callback_query, context, preferences=True)    

        else:
            await update.message.reply_text('Неизвестный шаг редактирования.')

        context.user_data['edit_schedule_id'] = None



    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if update.message:
            user_id = update.message.from_user.id
            text = update.message.text

            if 'auth_step' in context.user_data:
                response = await self.telethon_manager.process_authorization(user_id, text)
                await update.message.reply_text(response)
                if 'успешно' in response:
                    del context.user_data['auth_step']
                    await self.display_main_menu(update.message)
            elif 'schedule_step' in context.user_data:
                await self.handle_schedule(update, context)
            elif 'edit_step' in context.user_data:
                await self.handle_edit(update, context)
            else:
                await update.message.reply_text('Пожалуйста, выберите действие с помощью инлайн-кнопок.')

    async def save_edited_schedule(self, callback_query: CallbackQuery, context: ContextTypes.DEFAULT_TYPE) -> None:
        user_id = callback_query.from_user.id
        schedule_id = context.user_data.get('edit_schedule_id')
        
        if not schedule_id:
            await callback_query.message.reply_text('Ошибка: Расписание не выбрано.')
            return

        schedule = Schedule.get_by_id(schedule_id)

        if 'new_times' in context.user_data:
            times = context.user_data['new_times']
            for time in times:
                # Обновление времени в базе данных
                schedule.scheduled_time = datetime.combine(schedule.scheduled_time.date(), time)
        
        if 'new_message' in context.user_data:
            schedule.message = context.user_data['new_message']

        if 'selected_chats' in context.user_data:
            schedule.chats = json.dumps(context.user_data['selected_chats'])

        schedule.save()
        
        await callback_query.message.reply_text('Расписание успешно обновлено.')
        # Очистка данных пользователя и возвращение к главному меню
        context.user_data.clear()
        await self.display_main_menu(callback_query.message)

    async def handle_schedule(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        user_id = update.message.from_user.id
        text = update.message.message_id if context.user_data.get('schedule_step') == 'message' else update.message.text
        if update.message:
            if update.message.audio:
                audio = update.message.audio 
                text = '1'
            elif update.message.video:
                video = update.message.video
                text = '1'
            elif update.message.photo:
                photo = update.message.photo
                text = '1'
        else:
            if update.audio:
                audio = update.audio 
                text = '1'
            elif update.video:
                video = update.video
                text = '1'
            elif update.photo:
                photo = update.photo
                text = '1'
        if user_id not in self.telethon_manager.clients:
            await update.message.reply_text('Пожалуйста, сначала авторизуйтесь с помощью кнопки "Авторизация".')
            return

        if 'schedule_step' in context.user_data:
            if context.user_data['schedule_step'] == 'time':
                try:
                    times = [datetime.strptime(time.strip(), '%H:%M').time() for time in text.split(',')]
                    context.user_data['times'] = times
                    context.user_data['schedule_step'] = 'message'
                    await update.message.reply_text('Введите текст сообщения.')
                except ValueError:
                    await update.message.reply_text('Неправильный формат времени. Пожалуйста, введите время в формате HH:MM через запятую.')

            elif context.user_data['schedule_step'] == 'message':
                message = await self.telethon_manager.get_message(user_id)
                chat_ids = context.user_data['selected_chats']
                times = context.user_data['times']
                await self.telethon_manager.schedule_message(user_id, message, times, chat_ids)
                await update.message.reply_text('Расписание создано успешно.')
                await self.display_main_menu(message=update.message)
                del context.user_data['schedule_step']
                del context.user_data['selected_chats']
                del context.user_data['times']

    async def save_schedule(self, callback_query, context: ContextTypes.DEFAULT_TYPE) -> None:
        user_id = callback_query.from_user.id
        times = context.user_data.get('schedule_times', [])
        message = context.user_data.get('schedule_message', '')
        selected_chats = context.user_data.get('selected_chats', [])

        # Сохраняем расписание в базу данных и добавляем задачу в планировщик
        for time in times:
            scheduled_datetime = datetime.combine(datetime.today(), time)
            if scheduled_datetime < datetime.now():
                scheduled_datetime += timedelta(days=1)
            Schedule.create(user_id=user_id, message=message, scheduled_time=scheduled_datetime, chats=json.dumps(selected_chats))
            self.scheduler.add_job(self.telethon_manager.send_scheduled_message, CronTrigger(hour=time.hour, minute=time.minute, second=0), args=[user_id, message, selected_chats])

        # Очищаем состояние пользователя
        context.user_data.clear()

        # Отправляем сообщение об успешном создании расписания
        await callback_query.message.reply_text('Сообщение успешно запланировано!')

        # Отображаем главное меню
        await self.display_main_menu(callback_query.message)

    def reload_scheduler(self):
        self.scheduler.remove_all_jobs()
        
        schedules = Schedule.select()
        for schedule in schedules:
            scheduled_time = schedule.scheduled_time
            if scheduled_time < datetime.now():
                scheduled_time += timedelta(days=1)
                schedule.scheduled_time = scheduled_time
                schedule.save()
            self.scheduler.add_job(
                self.telethon_manager.send_scheduled_message,
                CronTrigger(hour=scheduled_time.hour, minute=scheduled_time.minute, second=0),
                args=[schedule.user.user_id, schedule.message, json.loads(schedule.chats)],
                id=f'schedule_{schedule.id}'
            )

        self.scheduler.add_job(
            self.reload_scheduler,
            CronTrigger(hour=0, minute=0, second=0),
            id='daily_display_main_menu'
        )
    
    async def display_main_menu(self, message):
        user_id = message.from_user.id if hasattr(message, 'from_user') else message.message.from_user.id
        keyboard = [
            [InlineKeyboardButton("Создать расписание", callback_data='create_schedule')],
            [InlineKeyboardButton("Показать чаты", callback_data='show_chats')],
            [InlineKeyboardButton("Показать расписание", callback_data='show_schedules')],
            [InlineKeyboardButton("Выйти из аккаунта", callback_data='logout')]
        ] if user_id in self.telethon_manager.clients else [
            [InlineKeyboardButton("Авторизация", callback_data='authorize')],
            [InlineKeyboardButton("Инструкция к авторизации", callback_data='instructions')]
        ]

        reply_markup = InlineKeyboardMarkup(keyboard)

        if user_id in self.telethon_manager.clients:
            self.telethon_manager.set_chat_bot_id(self.application.bot.id)

        self.reload_scheduler()

        if hasattr(message, 'edit_message_text'):
            await message.edit_message_text('Привет! Я твой бот-контроллер. Пожалуйста, авторизуйтесь и настройте расписание.', reply_markup=reply_markup)
        else:
            await message.reply_text('Привет! Я твой бот-контроллер. Пожалуйста, авторизуйтесь и настройте расписание.', reply_markup=reply_markup)

    async def display_chat_selection_menu(self, message, context, preferences: bool=False) -> None:
        user_id = message.from_user.id if isinstance(message, Message) or isinstance(message, CallbackQuery) else message.message.from_user.id
        chats = await self.telethon_manager.get_chats(user_id)
        selected_chats = context.user_data.get('selected_chats', [])
        current_page = context.user_data.get('current_page', 0)
        start = current_page * self.chats_per_page
        end = start + self.chats_per_page

        unselected_chats = [chat for chat in chats if chat.id not in selected_chats]
        page_chats = unselected_chats[start:end]

        keyboard = [
            [InlineKeyboardButton(chat.title, callback_data=f'select_chat_{chat.id}')] for chat in page_chats
        ]
        if current_page > 0:
            keyboard.append([InlineKeyboardButton("Предыдущая страница", callback_data='prev_page')])
        if end < len(unselected_chats):
            keyboard.append([InlineKeyboardButton("Следующая страница", callback_data='next_page')])

        keyboard.append([InlineKeyboardButton("Назад", callback_data='back')])
        keyboard.append([InlineKeyboardButton("Готово", callback_data='done_selecting_chats' if not preferences else f'back')])
        reply_markup = InlineKeyboardMarkup(keyboard)
        if isinstance(message, CallbackQuery):
            await message.edit_message_text('Выберите чаты для отправки сообщений:', reply_markup=reply_markup)
        elif isinstance(message, Message):
            await message.reply_text('Выберите чаты для отправки сообщений:', reply_markup=reply_markup)

