from tornado.ioloop import IOLoop
import tornado.web as web
import tornado.gen as gen
from tornado.template import Template
import tornado.auth
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

SETPOINT_ALWAYS_OFF = 0
SETPOINT_ALWAYS_ON = 99

RELAY_PIN = 2
RELAY_OPEN_STATE = True  # True for pin high, False for pin low

SETPOINT_RANGE = .5  # keep temp within +/- this amount of setpoint


class MockRelay(object):
    def on(self):
        print 'relay pin high'
    def off(self):
        print 'relay pin low'

class Controller(object):
    def __init__(self):
        self.setpoint = SETPOINT_ALWAYS_OFF
        #self.last_set = None
        self.cur_temp = None
        self.temp_as_of = None
        self.heater_on = False

        try:
            import gpiozero as io
            self.relay = io.LED(RELAY_PIN, initial_value=RELAY_OPEN_STATE)
        except ImportError:
            print 'NO GPIO - not running on pi?'
            self.relay = MockRelay()

    def new_setpoint(self, setpoint):
        logging.info('new setpoint %s' % setpoint)
        self.setpoint = setpoint
        #self.last_set = datetime.now()
        self.update_state()

    def new_temp(self, temp):
        logging.info('temperature update %s' % temp)
        self.cur_temp = temp
        self.temp_as_of = datetime.now()
        self.update_state()

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
            threshold = self.setpoint + (1 if self.heater_on else -1) * SETPOINT_RANGE
            heat = self.cur_temp < threshold
        self.set_heater(heat)

    def set_heater(self, on):
        if on == self.heater_on:
            return

        logging.info('heater ' + ('on' if on else 'off'))
        self.heater_on = on
        relay_state = on ^ RELAY_OPEN_STATE
        getattr(self.relay, 'on' if relay_state else 'off')()

RESTART_INTERVAL = timedelta(minutes=5)
class TemperatureMonitor(threading.Thread):
    def __init__(self, controller, poll_interval=60.):
        self.controller = controller
        self.poll_interval = timedelta(seconds=poll_interval*.9)
        self.up = True
        self.last_result = datetime.now()

    def on_new_temp(self, temp):
        logging.debug('new temp from device %s' % temp)
        self.last_result = datetime.now()
        
        if self.controller.temp_as_of is not None and datetime.now() - self.controller.temp_as_of < self.poll_interval:
            return

        # store in sqlite

        self.controller.new_temp(temp)

    def terminate(self):
        self.up = False
        self.cleanup()

    def init(self):
        pass

    def cleanup(self):
        pass

    def run(self):
        self.init()
        while self.up:
            if datetime.now() - self.controller.temp_as_of > RESTART_INTERVAL:
                logging.info('restarting temperature service due to lack of output')
                self.cleanup()
                time.sleep(5.)
                self.init()
                self.last_result = datetime.now()
            
            self.tick()
            time.sleep(.01)
            

DEVICE_TEMPLATE = {'id': 91}
SDR_CMD = os.path.expanduser('~/rtl_433/build/src/rtl_433 -f 434040000 -F json -R 19')
class SDRTempMonitor(TemperatureMonitor):
    def init(self):
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

        if any(payload[k] != JSON_TEMPLATE[k] for k in JSON_TEMPLATE.keys()):
            logging.debug('transmission from different device %s' % payload)
            return

        self.on_new_temp(payload['temperature_C'])

        
USB_CMD = os.path.expanduser('~/usb-thermometer/pcsensor')
class USBTempMonitor(TemperatureMonitor):
    def tick(self):
        stdout, _ = Popen(USB_CMD, shell=True).communicate()
        m = re.search('[-+.0-9]+C', stdout)
        if m:
            self.on_new_temp(float(m.group(0)[:-1]))


        
        
class MainHandler(web.RequestHandler):
    #def get_current_user(self):
    #    return self.get_secure_cookie(ID_COOKIE)

    #@property
    #def is_auth_user(self):
    #    return self.current_user in settings.AUTH_USERS

    #def get_login_url(self):
    #    return self.reverse_url('login')

    def get(self):
        self.render('main.html', temp=tempmon.temp, temp_at=tempmon.temp_at)
        
    def post(self):
        action = self.get_argument('action')
        if action == 'on':
            relay.off()
        elif action == 'off':
            relay.on()
        
        self.render('main.html', temp=tempmon.temp, temp_at=tempmon.temp_at)



if __name__ == "__main__":

    try:
        import gpiozero as io
        relay = io.LED(2, initial_value=True)
    except ImportError:
        print 'NO GPIO'
        relay = None
        
    parser = OptionParser()
    (options, args) = parser.parse_args()

    try:
        port = int(args[0])
    except IndexError:
        port = 8000

    tempmon = SDRTempThread()
    tempmon.start()
        
    application = web.Application([
        (r'/', MainHandler),

        #web.URLSpec('/login', LoginHandler, name='login'),
        #web.URLSpec('/oauth2callback', LoginHandler, name='oauth'),
        #web.URLSpec('/logout', LogoutHandler, name='logout'),

        (r'/(.*)', web.StaticFileHandler, {'path': 'static'}),
    ],
        template_path='templates',
        debug=True,
        #cookie_secret=settings.COOKIE_SECRET,
        #google_oauth=settings.OAUTH['google']
    )
    application.listen(port) #, ssl_options=settings.SSL_CONFIG)

    try:
        IOLoop.instance().start()
    except KeyboardInterrupt:
        pass
    except Exception, e:
        print e
        raise

    logging.info('shutting down...')
    tempmon.terminate()
