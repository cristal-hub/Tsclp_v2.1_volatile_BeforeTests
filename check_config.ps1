# check_config.ps1
# Проверка конфигурации без раскрытия секретов.

.\venv\Scripts\Activate.ps1

python -c @"
import config
try:
    config.validate()
    print('OK: конфигурация валидна')
except Exception as e:
    print('ОШИБКА:', e)
print('USE_SANDBOX =', config.USE_SANDBOX)
print('COMMISSION_PER_SIDE =', config.COMMISSION_PER_SIDE)
print('TELEGRAM_ALLOWED_USER_ID задан:', bool(config.TELEGRAM_ALLOWED_USER_ID))
print('STARTING_BALANCE задан:', bool(config.STARTING_BALANCE))
"@