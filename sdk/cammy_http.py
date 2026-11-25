"""Standard HTTP transport with the M105 station-mode framing workaround.

The camera's Wi-Fi receive path can corrupt a short body when headers and body
are sent in quick separate writes. Serialize our small JSON requests in one send.
This remains ordinary HTTP/1.1; no protocol or server parser changes are needed.
SPDX-License-Identifier: MIT
"""
import http.client
import urllib.request


class CameraConnection(http.client.HTTPConnection):
    def _send_output(self, message_body=None, encode_chunked=False):
        # Keep the standard implementation for streaming/chunked requests.
        if encode_chunked or (message_body is not None and not isinstance(message_body, bytes)):
            return super()._send_output(message_body, encode_chunked=encode_chunked)
        message = b'\r\n'.join(self._buffer) + b'\r\n\r\n' + (message_body or b'')
        self._buffer.clear()
        self.send(message)


class CameraHTTPHandler(urllib.request.HTTPHandler):
    def http_open(self, request):
        return self.do_open(CameraConnection, request)


def camera_opener():
    return urllib.request.build_opener(urllib.request.ProxyHandler({}), CameraHTTPHandler())
