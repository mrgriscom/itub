import os.path



THERMOMETER = 'sdr'
#THERMOMETER = 'usb'

TEMP_LOG = 'templog.sqlite'

RELAY_PIN = 2
RELAY_OPEN_STATE = True  # True for pin high, False for pin low

SETPOINT_MINMAX = [35, 44]
SETPOINT_STEP = .5
SETPOINT_TOLERANCE = .5  # keep temp within +/- this amount of setpoint

ENABLE_SECURITY = True
LOGIN_PASSWORD = None
COOKIE_SECRET = None
assert not LOGIN_PASSWORD and not COOKIE_SECRET, 'set only in localsettings.py'

SSL_CONFIG = {
    'certfile': os.path.join(os.path.dirname(__file__), 'private/ssl/selfsigned.crt'),
    'keyfile': os.path.join(os.path.dirname(__file__), 'private/ssl/selfsigned.key'),
}


try:
    from localsettings import *
except ImportError:
    pass
