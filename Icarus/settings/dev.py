from .base import *
from environs import Env

env = Env()
env.read_env() 
# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = env.bool("DEBUG", default=False)


# SECURITY WARNING: keep the secret key used in production secret!
SECRET_KEY = env.str("SECRET_KEY")

# 1. Phone HMAC key — generate once, never change (rotation requires a data migration)
PHONE_HASH_KEY = env.str("PHONE_HASH_KEY")

FIELD_ENCRYPTION_KEYS = [
    env.str("FIELD_ENCRYPTION_KEY"),
]

# 3. Base URL for unsubscribe links in newsletter emails
BASE_URL = env("BASE_URL", default="http://localhost:8000")

# SECURITY WARNING: define the correct hosts in production!
ALLOWED_HOSTS = ["*"]

EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"


try:
    from .local import *  # type: ignore
except ImportError:
    pass
