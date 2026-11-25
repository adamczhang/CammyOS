"""Local play for Cammy: small games that never leave the PC (family deployment, any mode).

Games are text in, text out: the desktop panel or the API sends an answer, Cammy replies.
With `--local-voice` and the installed local engines (whisper.cpp small.en and Kokoro, the
existing host/conversation_worker.py), Cammy also speaks the replies through its own speaker
and takes spoken answers from its microphone. Nothing is stored, logged or sent anywhere:
heard text only feeds the game and is never kept in the game state or the API.

Every start, stop and family transition begins a new session; a reply, speech job or
transcription that belongs to an older session is dropped (no late results).
"""
import asyncio
import logging
import random
import re

log = logging.getLogger('c3.play')

GAMES = {
    'guess': 'Guess my number',
    'rps': 'Rock, paper, scissors',
    'sums': 'Little sums',
}
WORDS = {w: i for i, w in enumerate(
    'zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen '
    'sixteen seventeen eighteen nineteen twenty'.split())}
WORDS.update({'to': 2, 'too': 2, 'for': 4, 'won': 1, 'ate': 8})
MOVES = ('rock', 'paper', 'scissors')
BEATS = {'rock': 'scissors', 'paper': 'rock', 'scissors': 'paper'}
SPOKEN_MIN, SPOKEN_MAX = 11200, 256000      # local STT bounds: 0.35 to 8 s of 16 kHz mono


def number_in(text):
    for token in re.findall(r'[a-z]+|\d+', (text or '').lower()):
        if token.isdigit():
            return int(token)
        if token in WORDS:
            return WORDS[token]
    return None


def move_in(text):
    text = (text or '').lower()
    found = [m for m in MOVES if m.rstrip('s') in text]
    return found[0] if len(found) == 1 else None


class Game:
    def __init__(self, rng):
        self.rng = rng
        self.done = False

    def start(self):
        raise NotImplementedError

    def answer(self, text):
        raise NotImplementedError


class Guess(Game):
    TRIES = 6

    def start(self):
        self.secret, self.tries = self.rng.randint(1, 10), 0
        return "I'm thinking of a number from one to ten. What is it?"

    def answer(self, text):
        n = number_in(text)
        if n is None or not 1 <= n <= 10:
            return 'Say a number from one to ten.'
        self.tries += 1
        if n == self.secret:
            self.done = True
            return 'Yes, it was %d! You got it in %d %s.' % (n, self.tries, 'try' if self.tries == 1 else 'tries')
        if self.tries >= self.TRIES:
            self.done = True
            return 'Good try! It was %d.' % self.secret
        return 'Higher!' if n < self.secret else 'Lower!'


class RockPaperScissors(Game):
    ROUNDS = 3

    def start(self):
        self.round, self.you, self.me = 0, 0, 0
        return 'Rock, paper or scissors?'

    def answer(self, text):
        yours = move_in(text)
        if yours is None:
            return 'Say rock, paper or scissors.'
        mine = self.rng.choice(MOVES)
        self.round += 1
        if BEATS[yours] == mine:
            self.you += 1
            line = '%s beats %s. You win this one!' % (yours.capitalize(), mine)
        elif BEATS[mine] == yours:
            self.me += 1
            line = '%s beats %s. I win this one!' % (mine.capitalize(), yours)
        else:
            line = 'We both picked %s!' % mine
        if self.round >= self.ROUNDS:
            self.done = True
            end = ('You win the game!' if self.you > self.me else 'I win the game!' if self.me > self.you
                   else "It's a draw!")
            return '%s %s' % (line, end)
        return line + ' Again: rock, paper or scissors?'


class Sums(Game):
    QUESTIONS = 5

    def start(self):
        self.asked, self.right = 0, 0
        return self._next('')

    def _next(self, before):
        self.a = self.rng.randint(0, 5)
        self.b = self.rng.randint(0, 5)
        self.asked += 1
        return ('%s What is %d plus %d?' % (before, self.a, self.b)).strip()

    def answer(self, text):
        n = number_in(text)
        if n is None:
            return 'What is %d plus %d? Say a number.' % (self.a, self.b)
        good = n == self.a + self.b
        self.right += good
        line = 'Yes!' if good else 'Nearly: %d plus %d is %d.' % (self.a, self.b, self.a + self.b)
        if self.asked >= self.QUESTIONS:
            self.done = True
            return '%s You got %d out of %d!' % (line, self.right, self.QUESTIONS)
        return self._next(line)


KINDS = {'guess': Guess, 'rps': RockPaperScissors, 'sums': Sums}


class LocalPlay:
    """One game at a time. Call from the event loop."""

    def __init__(self, *, voice=None, rng=None):
        self.voice = voice
        self.rng = rng or random.Random()
        self.session = 0
        self.game = None
        self.kind = None
        self.reply = ''

    def state(self):
        return {'games': [{'id': k, 'name': v} for k, v in GAMES.items()], 'game': self.kind,
                'reply': self.reply, 'done': bool(self.game and self.game.done), 'session': self.session,
                'voice': bool(self.voice and self.voice.available)}

    def _say(self, text):
        self.reply = text
        if self.voice is not None:
            self.voice.say(text, self.session, lambda: self.session)
        return self.state()

    def start(self, kind):
        if not isinstance(kind, str) or kind not in KINDS:
            raise ValueError('game: ' + ' | '.join(KINDS))
        self.reset('new game')
        self.kind, self.game = kind, KINDS[kind](self.rng)
        return self._say(self.game.start())

    def answer(self, text, session=None):
        """An answer typed or heard. `session` (heard answers) must still be the current one."""
        if session is not None and session != self.session:
            return self.state()           # a late transcription from an older game
        if self.game is None or self.game.done:
            return self.state()
        if not isinstance(text, str) or not text.strip() or len(text) > 200:
            raise ValueError('text: 1..200 characters')
        return self._say(self.game.answer(text))

    def stop(self):
        self.reset('stopped')
        return self.state()

    def reset(self, reason=''):
        """New session: the game ends and speech or listening in flight is dropped."""
        self.session += 1
        self.game = self.kind = None
        self.reply = ''
        if self.voice is not None:
            self.voice.cancel()

    @property
    def listening(self):
        return bool(self.voice and self.voice.available and self.game is not None and not self.game.done)


class LocalVoice:
    """Kokoro replies through Cammy's speaker and whisper.cpp answers from its microphone,
    with the existing local worker (cammy_conversation.LocalSpeech). Only if it is installed."""

    def __init__(self, speaker, speech=None, loop=None):
        self.speaker = speaker
        self.loop = loop
        self.speaking_job = self.listening_job = None
        self.utterance = None
        self.available = False
        self.problem = ''
        try:
            if speech is None:
                import cammy_conversation
                speech = cammy_conversation.LocalSpeech()
                paths = [cammy_conversation.VOICE_PYTHON,
                         cammy_conversation.MODEL_ROOT / 'ggerganov/whisper.cpp/en_whisper_small.ggml',
                         cammy_conversation.MODEL_ROOT / 'hexgrad/Kokoro-82M/kokoro-v1_0.pth',
                         cammy_conversation.MODEL_ROOT / 'hexgrad/Kokoro-82M/voices/af_heart.pt']
                missing = [p for p in paths if not p.is_file()]
                if missing:
                    raise RuntimeError('local speech resources missing')
            self.speech = speech
            self.available = True
        except Exception as exc:  # noqa: BLE001 - play works typed-only without it
            self.speech = None
            self.problem = str(exc)[:120]
            log.warning('local voice unavailable: %s', self.problem)

    def cancel(self):
        self.utterance = None
        for job in (self.speaking_job, self.listening_job):
            if job is not None:
                job.cancel()
        self.speaking_job = self.listening_job = None
        if self.speaker is not None:
            self.speaker.flush()

    async def _finish(self, job):
        await asyncio.to_thread(job.thread.join)
        value, error = job.poll() or (None, 'unfinished')
        return None if error else value

    def say(self, text, session, current):
        if not self.available or self.speaker is None:
            return
        text = text[:160]
        if self.speaking_job is not None:
            self.speaking_job.cancel()
        self.speaking_job = job = self.speech.synthesize(text)

        async def play():
            pcm = await self._finish(job)
            if self.speaking_job is not job:
                return
            self.speaking_job = None
            if pcm and current() == session:
                self.speaker.say(pcm, end=True)
        (self.loop or asyncio.get_running_loop()).create_task(play())

    def on_speech(self, event, play, calendar=None):
        """Recognize an active game's answers or an adult's local Calendar request."""
        def context():
            if play.listening:
                return ('play', play.session)
            grant = calendar.voice_context() if calendar is not None and play.game is None else None
            return ('calendar', grant) if grant is not None else None

        current = context()
        if not self.available or current is None or self.listening_job is not None:
            self.utterance = None
            return
        if event.kind == 'start':
            self.utterance = (current, bytearray(event.pcm or b''))
        elif event.kind == 'audio' and self.utterance is not None:
            if len(self.utterance[1]) < SPOKEN_MAX:
                self.utterance[1].extend(event.pcm or b'')
        elif event.kind == 'end' and self.utterance is not None:
            session, pcm = self.utterance
            self.utterance = None
            pcm = bytes(pcm[:SPOKEN_MAX - SPOKEN_MAX % 2])
            if len(pcm) < SPOKEN_MIN or session != current:
                return
            self.listening_job = job = self.speech.transcribe(pcm)

            async def heard():
                raw = await self._finish(job)
                if self.listening_job is not job:
                    return
                self.listening_job = None
                if not raw or session != context():
                    return
                try:
                    import json
                    text = json.loads(raw).get('text', '')
                except ValueError:
                    return
                if isinstance(text, str) and text.strip():
                    try:
                        text = text.strip()[:200]
                        if session[0] == 'play':
                            play.answer(text, session=session[1])
                        else:
                            reply = await calendar.voice_request(text, session[1])
                            if reply:
                                self.say(reply, session, context)
                    except ValueError:
                        pass
            (self.loop or asyncio.get_running_loop()).create_task(heard())


def add_routes(app, play, body):
    """GET /v1/play, POST /v1/play/{start,answer,stop}."""
    from aiohttp import web

    async def state(_):
        return web.json_response(play.state())

    async def start(request):
        try:
            return web.json_response(play.start((await body(request)).get('game')))
        except ValueError as exc:
            raise web.HTTPBadRequest(text=str(exc))

    async def answer(request):
        try:
            return web.json_response(play.answer((await body(request)).get('text')))
        except ValueError as exc:
            raise web.HTTPBadRequest(text=str(exc))

    async def stop(_):
        return web.json_response(play.stop())

    app.add_routes([web.get('/v1/play', state), web.post('/v1/play/start', start),
                    web.post('/v1/play/answer', answer), web.post('/v1/play/stop', stop)])
