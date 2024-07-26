from peewee import Model, CharField, IntegerField, TextField, DateTimeField, ForeignKeyField, SqliteDatabase
from datetime import datetime

db = SqliteDatabase('schedule.db')

class BaseModel(Model):
    class Meta:
        database = db

class User(BaseModel):
    user_id = IntegerField(unique=True)

class Schedule(BaseModel):
    user = ForeignKeyField(User, backref='schedules')
    message = TextField()
    scheduled_time = DateTimeField()
    chats = TextField()
