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


class SDRTempThread(threading.Thread):
    def __init__(self):
        threading.Thread.__init__(self)
        self.up = True
        self.process = None
        self.temp = None
        self.temp_at = None

    def terminate(self):
        self.up = False
        self.process.terminate()

    def run(self):
        self.process = Popen('../rtl_433/build/src/rtl_433 -f 434040000 -F json', shell=True, stdout=PIPE)
        
        while self.up:
            data = self.process.stdout.readline()
            try:
                payload = json.loads(data)
            except ValueError:
                print 'bad json', data
                continue
            self.temp = payload['temperature_C']
            self.temp_at = datetime.now()
            print 'got temp %s at %s' % (self.temp, self.temp_at)

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
