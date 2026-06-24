import os
import django
from django.conf import settings
from django.contrib.sessions.backends.db import SessionStore

settings.configure(
    INSTALLED_APPS=[
        'django.contrib.sessions',
    ],
    DATABASES={
        'default': {
            'ENGINE': 'django.db.backends.sqlite3',
            'NAME': ':memory:',
        }
    }
)
django.setup()
from django.core.management import call_command
call_command('migrate', 'sessions')

s = SessionStore()
s['my_list'] = []
s.save()
print("Initial:", s.session_key, s['my_list'])

# Load again
s2 = SessionStore(session_key=s.session_key)
my_list = s2.get('my_list', [])
my_list.append(1)
s2['my_list'] = my_list
s2.save()

# Load again
s3 = SessionStore(session_key=s.session_key)
print("After append and assign:", s3['my_list'])

# Now let's try WITHOUT assigning, just modifying
s4 = SessionStore(session_key=s.session_key)
my_list2 = s4.get('my_list', [])
my_list2.append(2)
s4.save()

# Load again
s5 = SessionStore(session_key=s.session_key)
print("After append without assign:", s5['my_list'])

