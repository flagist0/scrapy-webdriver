import inspect
from collections import deque
from threading import Lock

from scrapy import log
from scrapy.signals import engine_stopped
from scrapy_webdriver.http import WebdriverRequest, WebdriverActionRequest
from selenium import webdriver


class WebdriverManager(object):
    """Manages the life cycle of a webdriver instance."""
    USER_AGENT_KEY = 'phantomjs.page.settings.userAgent'

    def __init__(self, crawler):
        self.crawler = crawler
        self._lock = Lock()
        self._wait_queue = deque()
        self._wait_inpage_queue = deque()
        self._browser = crawler.settings.get('WEBDRIVER_BROWSER', None)
        self._user_agent = crawler.settings.get('USER_AGENT', None)
        self._options = crawler.settings.get('WEBDRIVER_OPTIONS', dict())
        self._extensions = crawler.settings.get('WEBDRIVER_EXTENSIONS', [])
        self._webdriver = None
        if isinstance(self._browser, basestring):
            if '.' in self._browser:
                module, browser = self._browser.rsplit('.', 2)
            else:
                module, browser = 'selenium.webdriver', self._browser
            module = __import__(module, fromlist=[browser])
            self._browser = getattr(module, browser)
        elif inspect.isclass(self._browser):
            self._browser = self._browser
        else:
            self._webdriver = self._browser

    @property
    def _desired_capabilities(self):
        capabilities = dict()
        if self._user_agent is not None:
            capabilities[self.USER_AGENT_KEY] = self._user_agent
        return capabilities or None

    def webdriver(self, request=None):
        """Return the webdriver instance, instantiate it if necessary."""
        if self._webdriver is None:
            self._init_webdriver(request)
        return self._webdriver

    def acquire(self, request):
        """Acquire lock for the request, or enqueue request upon failure."""
        assert isinstance(request, WebdriverRequest), \
            'Only a WebdriverRequest can use the webdriver instance.'
        if self._lock.acquire(False):
            request.manager = self
            return request
        else:
            if isinstance(request, WebdriverActionRequest):
                queue = self._wait_inpage_queue
            else:
                queue = self._wait_queue
            queue.append(request)

    def acquire_next(self):
        """Return the next waiting request, if any.

        In-page requests are returned first.

        """
        try:
            request = self._wait_inpage_queue.popleft()
        except IndexError:
            try:
                request = self._wait_queue.popleft()
            except IndexError:
                return
        return self.acquire(request)

    def release(self):
        """Release the the webdriver instance's lock."""
        self._lock.release()

    def _init_webdriver(self, request):
        short_arg_classes = (webdriver.Firefox, webdriver.Ie)
        if issubclass(self._browser, short_arg_classes):
            cap_attr = 'capabilities'
        else:
            cap_attr = 'desired_capabilities'
        options = self._options
        options[cap_attr] = self._desired_capabilities

        options = self._extract_options_from_request(request, options) #Modify options if request contained supported headers
        self._webdriver = self._browser(**options)

        self.crawler.signals.connect(self._cleanup, signal=engine_stopped)

    def _reinit_webdriver(self, request):
        log.msg('Restarting webdriver', level=log.DEBUG)
        print 'Restarting webdriver'
        self._webdriver.quit()
        self._init_webdriver(request)


    def _cleanup(self):
        """Clean up when the scrapy engine stops."""
        if self._webdriver is not None:
            self._webdriver.quit()
            assert len(self._wait_queue) + len(self._wait_inpage_queue) == 0, \
                'Webdriver queue not empty at engine stop.'

    def _extract_options_from_request(self, request, options):
        'Extract browser options from request. Currently only Firefox and Accept-Language/User-Agent are supported'
        if request:
            if issubclass(self._browser, webdriver.Firefox):
                profile = webdriver.FirefoxProfile()

                for ext_path in self._extensions:
                    profile.add_extension(ext_path)

                if 'Accept-Language' in request.headers: #Set languages to accept from server
                    languages = request.headers['Accept-Language']
                    profile.set_preference('intl.accept_languages', languages)
                    log.msg('Set accepted languages to "%s"' % languages, level=log.DEBUG)

                if 'User-Agent' in request.headers: #Set user agent
                    ua = request.headers['User-Agent']
                    profile.set_preference("general.useragent.override", ua)
                    log.msg('Set user agent to "%s"' % ua, level=log.DEBUG)

                options['firefox_profile'] = profile

        return options
