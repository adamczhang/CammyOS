"""Client for the existing StackChan ParamFace HTTP contract.

The current Cammy endpoint targets a PC preview; it does not imply a camera LCD.
Only the separate app-session endpoints request real camera/microphone sensing.
"""
import json
import urllib.request


class ParamFace:
    def __init__(self, endpoint='http://127.0.0.1:8801'):
        if endpoint != 'http://127.0.0.1:8801':
            raise ValueError('This local adapter uses the loopback Reachy app')
        self.endpoint = endpoint
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def _request(self, path, raw=None):
        request = urllib.request.Request(self.endpoint+path, data=raw,
                                         headers={'Content-Type':'text/plain'})
        with self.opener.open(request, timeout=4) as response:
            return json.load(response)

    def status(self):
        return self._request('/status')

    def face(self):
        return self._request('/face')

    def expression(self, name, hold=True):
        if name not in ('neutral','happy','sad','angry','sleepy','doubt'):
            raise ValueError('Unsupported ParamFace expression')
        return self._request('/face?expr='+name+'&hold='+str(int(bool(hold))))

    def load(self, face, save=False):
        raw = json.dumps(face, allow_nan=False, separators=(',',':')).encode()
        if len(raw)>65536:
            raise ValueError('Face JSON exceeds64KiB')
        return self._request('/face?save='+str(int(bool(save))), raw)
