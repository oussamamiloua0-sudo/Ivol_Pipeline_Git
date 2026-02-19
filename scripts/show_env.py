import os, re
from pathlib import Path
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / '.env', override=True)

u = os.getenv('DB_URL','')
masked = re.sub(r'//([^:]+):([^@]*)@', r'//\1:REDACTED@', u)
print('DB_URL_MASKED =', masked)
print('DB_URL_REPR   =', repr(masked))
print('DB_SSL_MODE   =', os.getenv('DB_SSL_MODE'))
print('DB_SSL_CA     =', os.getenv('DB_SSL_CA'))
