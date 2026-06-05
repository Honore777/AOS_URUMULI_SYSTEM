# config.py
import os
from dotenv import load_dotenv
from flask_sqlalchemy import SQLAlchemy

# Load environment from a local .env file in development
load_dotenv()

# Initialize database - imported globally
db = SQLAlchemy()

class Config:
    # Application
    SECRET_KEY = os.environ.get('SECRET_KEY', 'urumulismartsystem')

    # Database
    SQLALCHEMY_DATABASE_URI = os.environ.get('DATABASE_URL')  
    
    

    # Debug: Log which database URL is being used
    print(f"DEBUG: DATABASE_URL_CLONE = {os.environ.get('DATABASE_URL_CLONE')}")
    print(f"DEBUG: DATABASE_URL = {os.environ.get('DATABASE_URL')}")
    print(f"DEBUG: Using SQLALCHEMY_DATABASE_URI = {SQLALCHEMY_DATABASE_URI}")

    # Force connection pool disposal to prevent caching
    SQLALCHEMY_ENGINE_OPTIONS = {
        'pool_pre_ping': True,
        'pool_size': int(os.environ.get('POOL_SIZE', '7')),
        'max_overflow': int(os.environ.get('MAX_OVERFLOW', '10')),
        'pool_timeout': int(os.environ.get('POOL_TIMEOUT', '35')),
        'pool_recycle': int(os.environ.get('POOL_RECYCLE', '400')),
        'pool_reset_on_return': 'commit',
    }
    
    
    SQLALCHEMY_TRACK_MODIFICATIONS = os.environ.get(
        'SQLALCHEMY_TRACK_MODIFICATIONS', 'False'
    ).lower() in ('1', 'true', 'yes')

    # Brevo (Transactional email) configuration
    BREVO_API_KEY = os.environ.get('BREVO_API_KEY')
    BREVO_SENDER_EMAIL = os.environ.get('BREVO_SENDER_EMAIL')
    BREVO_SENDER_NAME = os.environ.get('BREVO_SENDER_NAME', 'Urumuli Smart System')

    # Optional Supabase (useful for storage/auth separate from DB)
    SUPABASE_URL = os.environ.get('SUPABASE_URL_TESTING')
    SUPABASE_KEY = os.environ.get('SERVICE_KEY')

    # Logging configuration (controlled by env vars)
    LOG_LEVEL = os.environ.get('LOG_LEVEL', 'INFO').upper()
    LOG_FILE = os.environ.get('LOG_FILE', 'logs/app.log')
    LOG_MAX_BYTES = int(os.environ.get('LOG_MAX_BYTES', '10485760'))
    LOG_BACKUP_COUNT = int(os.environ.get('LOG_BACKUP_COUNT', '5'))
    LOG_FORMAT = os.environ.get('LOG_FORMAT', '%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    # Optionally enable SQL echoing for short profiling runs
    SQLALCHEMY_ECHO = os.environ.get('SQLALCHEMY_ECHO', 'False').lower() in ('1', 'true', 'yes')
