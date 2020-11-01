from tornado.ioloop import IOLoop
import tornado.web as web
import tornado.gen as gen
from tornado.template import Template
from tornado_http_auth import BasicAuthMixin, auth_required
import tornado.auth
import tornado.websocket as websocket
from tornado.httpclient import AsyncHTTPClient
import logging
import os
from optparse import OptionParser
import json
from datetime import datetime, timedelta
import sys
import re
import threading
from subprocess import Popen, PIPE
import time
import logging
import settings
import sqlite3

class MockRelay(object):
    def on(self):
        print 'relay pin high'
    def off(self):
        print 'relay pin low'

SETPOINT_ALWAYS_OFF = 0
SETPOINT_ALWAYS_ON = 99
class Controller(object):
    def __init__(self):
        self.setpoint = SETPOINT_ALWAYS_OFF
        #self.last_set = None
        self.cur_temp = None
        self.temp_as_of = None
        self.heater_on = False

        self.subscribers = []

        try:
            import gpiozero as io
            self.relay = io.LED(settings.RELAY_PIN, initial_value=settings.RELAY_OPEN_STATE)
        except ImportError:
            print 'NO GPIO - not running on pi?'
            self.relay = MockRelay()

    def subscribe(self, s):
        self.subscribers.append(s)

    def unsubscribe(self, s):
        self.subscribers.remove(s)

    def notify(self):
        for s in self.subscribers:
            s.notify(self.get_state())

    def get_state(self):
        return {
            'heater_on': self.heater_on,
            'setpoint': self.setpoint,
            'cur_temp': self.cur_temp,
            'temp_as_of': str(self.temp_as_of) if self.temp_as_of else None,
        }
        
    def new_setpoint(self, setpoint):
        logging.info('new setpoint %s' % setpoint)
        self.setpoint = setpoint
        #self.last_set = datetime.now()
        self.update_state()

    def new_temp(self, temp):
        # this will be called by the temp monitor thread, so wrap for
        # thread safety
        def _():
            logging.info('temperature update %s' % temp)
            self.cur_temp = temp
            self.temp_as_of = datetime.now()
            self.update_state()
        if settings.USE_TORNADO_CALLBACKS:
            IOLoop.instance().add_callback(_)
        else:
            # sometimes the proper callback method doesn't seem to work?
            _()

    def update_state(self):
        if self.setpoint == SETPOINT_ALWAYS_OFF:
            heat = False
        elif self.setpoint == SETPOINT_ALWAYS_ON:
            heat = True
        elif self.cur_temp is None:
            # no temp received from thermometer yet
            # when in doubt, activate heat
            heat = True
        else:
            threshold = self.setpoint + (1 if self.heater_on else -1) * settings.SETPOINT_TOLERANCE
            heat = self.cur_temp < threshold
        self.set_heater(heat)
        self.notify()
        
    def set_heater(self, on):
        if on == self.heater_on:
            return

        logging.info('heater ' + ('on' if on else 'off'))
        self.heater_on = on
        relay_state = on ^ settings.RELAY_OPEN_STATE
        getattr(self.relay, 'on' if relay_state else 'off')()


def init_db():
    conn = sqlite3.connect(settings.TEMP_LOG, isolation_level=None)
    cur = conn.cursor()
    cur.execute('create table if not exists templog (timestamp text primary key, temp float)')        
    return (conn, cur)

        
RESTART_INTERVAL = timedelta(minutes=5)
POLL_INTERVAL = 60.  # s
class TemperatureMonitor(threading.Thread):
    def __init__(self, controller, poll_interval=POLL_INTERVAL):
        super(TemperatureMonitor, self).__init__()
        
        self.controller = controller

        self.target_poll_interval = timedelta(seconds=poll_interval)
        self.min_poll_interval = timedelta(seconds=poll_interval*.9)
        self.last_result = None

        self.db = None
        
        self.up = True
        
    def interval_ok(self, interval):
        # cache this to a local variable for thread safety
        start = self.controller.temp_as_of
        return start is None or datetime.now() - start > interval
        
    def on_new_temp(self, temp):
        logging.debug('new temp from device %s' % temp)
        self.last_result = datetime.now()

        if not self.interval_ok(self.min_poll_interval):
            return

        self.db.execute('insert into templog values (?,?)', (datetime.now(), temp))
        self.controller.new_temp(temp)

    def terminate(self):
        self.up = False
        self.cleanup()

    def init(self):
        self.last_result = datetime.now()

    def cleanup(self):
        pass

    def run(self):
        self.db = init_db()[1]
        
        self.init()
        while self.up:
            if datetime.now() - self.last_result > RESTART_INTERVAL:
                logging.info('restarting temperature service due to lack of output')
                self.cleanup()
                time.sleep(5.)
                self.init()
            
            self.tick()
            time.sleep(.01)
            

DEVICE_TEMPLATE = {'id': 91}
SDR_CMD = os.path.expanduser('~/rtl_433/build/src/rtl_433 -f 434040000 -F json -R 19')
class SDRTempMonitor(TemperatureMonitor):
    def init(self):
        super(SDRTempMonitor, self).init()
        # don't use shell as it can't be terminated easily
        self.process = Popen(SDR_CMD.split(), stdout=PIPE)

    def cleanup(self):
        if self.process:
            self.process.kill()

    def tick(self):
        data = self.process.stdout.readline()
        if not data:
            logging.debug('process has terminated?')
            return
            
        try:
            payload = json.loads(data)
        except ValueError:
            logging.debug('bad json [%s]' % payload)
            return

        if any(payload[k] != DEVICE_TEMPLATE[k] for k in DEVICE_TEMPLATE.keys()):
            logging.debug('transmission from different device %s' % payload)
            return

        self.on_new_temp(payload['temperature_C'])


USB_ID = '0c45:7401'
USB_CMD = os.path.expanduser('~/usb-thermometer/pcsensor')
class USBTempMonitor(TemperatureMonitor):
    def cleanup(self):
        import fcntl
        USBDEVFS_RESET = 21780
        try:
            lsusb = Popen("lsusb | grep -i %s" % USB_ID, shell=True, stdout=PIPE).communicate()[0].strip().split()
            bus = lsusb[1]
            device = lsusb[3][:-1]
            with open("/dev/bus/usb/%s/%s" % (bus, device), 'w', os.O_WRONLY) as f:
                fcntl.ioctl(f, USBDEVFS_RESET, 0)
        except Exception, e:
            logging.debug('usb reset failed %s' % e)
    
    def tick(self):
        if not self.interval_ok(self.target_poll_interval):
            return
        
        stdout, _ = Popen(USB_CMD, shell=True, stdout=PIPE).communicate()
        temp = None
        m = re.search('[-+.0-9]+C', stdout)
        if m:
            try:
                temp = float(m.group(0)[:-1])
                if temp == 0:
                    # wire not connected
                    temp = None
            except ValueError:
                pass
        if temp is None:
            logging.debug('bad output [%s]' % stdout)
        else:
            # TODO temperature might need calibration adjustment
            self.on_new_temp(temp)

class MockTempMonitor(TemperatureMonitor):
    def tick(self):
        try:
            self.on_new_temp(float(raw_input('temp: ')))
        except ValueError:
            pass
        

# TODO reboot handler


ID_COOKIE = 'user'
VALID_USER = 'valid'
class AuthenticationMixin(object):
    def get_current_user(self):
        return self.get_secure_cookie(ID_COOKIE) if settings.ENABLE_SECURITY else VALID_USER

    def get_login_url(self):
        return self.reverse_url('login')

    @staticmethod
    def authenticate_hard_stop(handler):
        def _wrap(self, *args):
            if not self.current_user:
                self.set_status(403)
                self.finish()
                return
            else:
                handler(self, *args)
        return _wrap
    
# DigestAuthMixin doesn't seem to work on chrome
class LoginHandler(BasicAuthMixin, web.RequestHandler):
    def prepare(self):
        # torpedo request before there's a chance of sending passwords in the clear
        assert settings.ENABLE_SECURITY
    
    @auth_required(realm='Protected', auth_func=lambda username: settings.LOGIN_PASSWORD)
    def get(self):
        self.set_secure_cookie(ID_COOKIE, VALID_USER, path='/')
        self.redirect(self.get_argument('next'))

        
class MainHandler(AuthenticationMixin, web.RequestHandler):
    @web.authenticated
    def get(self):
        self.render('main.html')

def setpoints((_min, _max), step):
    assert _max >= _min and step > 0
    sp = _min
    while sp <= _max + 1e-6:
        yield sp
        sp += step
        
class WebsocketHandler(AuthenticationMixin, websocket.WebSocketHandler):
    def initialize(self, controller):
        self.controller = controller

    @AuthenticationMixin.authenticate_hard_stop
    def get(self, *args):
        # intercept and authenticate before websocket setup / protocol switch
        super(WebsocketHandler, self).get(*args)
        
    def open(self, *args):
        state = self.controller.get_state()

        conn, cur = init_db()
        cur.execute('select * from templog where timestamp > ? order by timestamp', (datetime.now() - settings.HISTORY_WINDOW,))
        hist = cur.fetchall()
        conn.close()

        state.update({
            'constants': {
                'setpoint_off': SETPOINT_ALWAYS_OFF,
                'setpoint_max': SETPOINT_ALWAYS_ON,
                'setpoints': list(setpoints(settings.SETPOINT_MINMAX, settings.SETPOINT_STEP)),
                'tolerance': settings.SETPOINT_TOLERANCE,
                'staleness': settings.TEMP_STALENESS.total_seconds(),
                'hist_window': settings.HISTORY_WINDOW.total_seconds(),
                'polling': POLL_INTERVAL,
                'server_now': str(datetime.now()),
            },
            'history': hist,
        })
        self.notify(state)
        self.controller.subscribe(self)

    def on_message(self, message):
        data = json.loads(message)
        logging.debug('incoming message %s %s' % (self.request.remote_ip, data))

        action = data.get('action')
        if action == 'setpoint':
            self.controller.new_setpoint(data['value'])

    def on_close(self):
        self.controller.unsubscribe(self)

    def notify(self, msg):
        self.write_message(json.dumps(msg))



    

if __name__ == "__main__":

    # TODO buffering handler for file writers in case we get flooded with debug errors every tick
    logging.basicConfig(
        format='%(asctime)s %(levelname)-8s %(message)s',
        level=logging.DEBUG,
        datefmt='%Y-%m-%d %H:%M:%S',
        stream=sys.stderr,
    )
    
    parser = OptionParser()
    (options, args) = parser.parse_args()

    try:
        port = int(args[0])
    except IndexError:
        port = 8000

    ctrl = Controller()        
    tempmon = {
        'usb': USBTempMonitor,
        'sdr': SDRTempMonitor,
        'mock': MockTempMonitor,
    }[settings.THERMOMETER](ctrl)
    tempmon.start()

    application = web.Application([
        web.URLSpec('/login', LoginHandler, name='login'),
        (r'/', MainHandler),
        (r'/socket/', WebsocketHandler, {'controller': ctrl}),
        (r'/(.*)', web.StaticFileHandler, {'path': 'static'}),
    ],
        template_path='templates',
        debug=True,
        cookie_secret=settings.COOKIE_SECRET,
    )
    application.listen(port, ssl_options=settings.SSL_CONFIG if settings.ENABLE_SECURITY else None)

    try:
        IOLoop.instance().start()
    except KeyboardInterrupt:
        pass
    except Exception, e:
        print e
        raise

    logging.info('shutting down...')
    tempmon.terminate()
