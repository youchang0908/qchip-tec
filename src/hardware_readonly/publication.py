"""Stage 2 publication scheduler, isolated copy for the hardware read-only mirror."""


class TransportKeepalive:
    """RFC6455 control traffic only; never renews an application/owner lease."""
    PAYLOAD=b'qchip-readonly'

    def __init__(self,clock):
        self.clock=clock
        self.deadline=clock()+30

    def send_if_due(self,peer):
        if self.clock()<self.deadline:return False
        peer.settimeout(2)
        peer.ping(self.PAYLOAD)
        self.deadline=self.clock()+30
        return True


class PublicationSchedule:
    ACTIVE_SECONDS = 1.
    IDLE_SECONDS = 2.
    IDLE60_SECONDS = 60.

    def __init__(self, clock):
        self.clock = clock
        self.mode = None
        self.interval = self.IDLE_SECONDS
        self.deadline = clock()
        self.signature = None
        self.reason = 'welcome'
        self.transitions = 0
        self.idle60 = False
        self.wake_revision = None
        self.next_frame_at = 0

    def observe(self, state):
        now = self.clock()
        self.idle60 = state.get('idle60', False)
        idle_interval = self.IDLE60_SECONDS if self.idle60 else self.IDLE_SECONDS
        mode = 'active' if (state['remote_viewers'] or state['remote_owner'] or
            state['inflight_commands'] or state['pending_replies']) else 'idle'
        signature = state['event_signature']
        if self.mode is None:
            self.deadline = now
        elif mode != self.mode:
            self.transitions += 1
            if mode == 'active':
                self.deadline, self.reason = now, 'viewer_or_control'
            elif self.idle60:
                self.deadline, self.reason = now, 'idle_enter'
            elif self.reason != 'catchup':
                self.deadline = now + self.IDLE_SECONDS
        if self.signature is not None and signature != self.signature:
            self.deadline, self.reason = now, 'state_event'
        if mode == 'active' and self.wake_revision != state.get('wake_revision'):
            self.deadline, self.reason = now, 'wake'
        self.wake_revision = state.get('wake_revision')
        self.mode, self.signature = mode, signature
        self.interval = self.ACTIVE_SECONDS if mode == 'active' else idle_interval

    def due(self):
        return self.clock() >= max(self.deadline,self.next_frame_at if self.mode=='active' else 0)

    def sent(self, backlog=False):
        now = self.clock()
        self.deadline = now + self.interval
        if self.mode=='active':self.next_frame_at=now+1
        self.reason = 'catchup' if backlog else 'timer'

    def receive_timeout(self, next_tick):
        # Recalculate after send/receive; no fixed recv delay added to each frame.
        deadline=max(self.deadline,self.next_frame_at if self.mode=='active' else 0)
        return max(.001, min(.1, deadline-self.clock(), next_tick-self.clock()))
