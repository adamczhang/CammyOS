"""Finite camera control lease without starting video, microphone or a recorder."""
import time
import re
from cammy_live import LiveSession
class ControlSession(LiveSession):
    def _run(self,generation):
        client=None;held=False
        try:
            client=self.client_factory();identity=client.identity()
            if identity.get('os')!='CammyOS' or identity.get('simulation'):
                raise RuntimeError('Real authenticated Cammy required')
            state=client.state()
            if not re.fullmatch('[0-9a-f]{32}',state.get('boot_id','')):
                raise RuntimeError('Integrated body runtime required')
            initial_boot=state['boot_id']
            if state.get('owner') is not None or state.get('preview_active'):
                raise RuntimeError('Cammy already has active control or media')
            if not self._valid(generation):return
            client.acquire(ttl=30);held=True;boot=self.read_boot(client)
            with self.lock:
                if not self._valid(generation):return
                self.control_client=client;self.boot=boot;self.phase='control'
            renew_at=time.monotonic()+3;check_at=time.monotonic()+.5
            while self._valid(generation) and time.monotonic()<self.ends:
                now=time.monotonic()
                if now>=check_at:
                    state=client.state()
                    if state.get('boot_id')!=initial_boot:
                        raise RuntimeError('Cammy restarted')
                    if state.get('owner')!=client.agent:raise RuntimeError('Control lease lost')
                    check_at=now+.5
                if now>=renew_at:
                    if not self._valid(generation):return
                    client.renew(ttl=30);renew_at=now+3
                self.cancel.wait(.02)
        except Exception as error:
            self._fail(str(error) if isinstance(error,RuntimeError) else 'Body control unavailable')
        finally:
            with self.lock:self.control_client=None;self.cancel.set();self.phase='stopping'
            if held:
                try:self.release_acknowledged=client.release().get('released') is True
                except Exception:self.error='Control release unacknowledged; lease/hold must expire'
            with self.lock:self.phase='faulted' if self.error else 'idle'

    def status(self):
        value=super().status()
        value.update(control_only=True,sensing_requested=False)
        return value
