
# Linux-only

from __future__ import with_statement
import os
from errno import EINTR
from pypy.rpython.lltypesystem import lltype, rffi
from pypy.interpreter.gateway import unwrap_spec
from pypy.interpreter.error import OperationError
from pypy.module.select import interp_epoll
from pypy.module.select.interp_epoll import W_Epoll, FD_SETSIZE
from pypy.module.select.interp_epoll import epoll_event
from pypy.module.transaction import interp_transaction
from pypy.rlib import rstm, rposix


# a _nowrapper version, to be sure that it does not allocate anything
_epoll_wait = rffi.llexternal(
    "epoll_wait",
    [rffi.INT, lltype.Ptr(rffi.CArray(epoll_event)), rffi.INT, rffi.INT],
    rffi.INT,
    compilation_info = interp_epoll.eci,
    _nowrapper = True
)


class EPollPending(interp_transaction.AbstractPending):
    maxevents = FD_SETSIZE - 1    # for now
    evs = lltype.nullptr(rffi.CArray(epoll_event))

    def __init__(self, space, epoller, w_callback):
        self.space = space
        self.epoller = epoller
        self.w_callback = w_callback
        self.evs = lltype.malloc(rffi.CArray(epoll_event), self.maxevents,
                                 flavor='raw', add_memory_pressure=True,
                                 track_allocation=False)
        self.force_quit = False

    def __del__(self):
        evs = self.evs
        if evs:
            self.evs = lltype.nullptr(rffi.CArray(epoll_event))
            lltype.free(evs, flavor='raw', track_allocation=False)

    def run(self):
        # This code is run non-transactionally.  Careful, no GC available.
        state = interp_transaction.state
        if state.has_exception() or self.force_quit:
            return
        fd = rffi.cast(rffi.INT, self.epoller.epfd)
        maxevents = rffi.cast(rffi.INT, self.maxevents)
        timeout = rffi.cast(rffi.INT, 500)     # for now: half a second
        nfds = _epoll_wait(fd, self.evs, maxevents, timeout)
        nfds = rffi.cast(lltype.Signed, nfds)
        #
        if nfds < 0:
            errno = rposix.get_errno()
            if errno == EINTR:
                nfds = 0    # ignore, just wait for more later
            else:
                # unsure how to trigger this case
                state = interp_transaction.state
                state.got_exception_errno = errno
                state.must_reraise_exception(_reraise_from_errno)
                return
        # We have to allocate new PendingCallback objects, but we can't
        # allocate anything here because we are not running transactionally.
        # Workaround for now: run a new tiny transaction just to create
        # and register these PendingCallback's.
        self.nfds = nfds
        rstm.perform_transaction(EPollPending._add_real_transactions,
                                 EPollPending, self)
        # XXX could be avoided in the common case with some pool of
        # PendingCallback instances

    @staticmethod
    def _add_real_transactions(self, retry_counter):
        evs = self.evs
        for i in range(self.nfds):
            event = evs[i]
            fd = rffi.cast(lltype.Signed, event.c_data.c_fd)
            PendingCallback(self.w_callback, fd, event.c_events).register()
        # re-register myself to epoll_wait() for more
        self.register()


class PendingCallback(interp_transaction.AbstractPending):
    def __init__(self, w_callback, fd, events):
        self.w_callback = w_callback
        self.fd = fd
        self.events = events

    def run_in_transaction(self, space):
        space.call_function(self.w_callback, space.wrap(self.fd),
                                             space.wrap(self.events))


def _reraise_from_errno():
    state = interp_transaction.state
    space = state.space
    errno = state.got_exception_errno
    msg = os.strerror(errno)
    w_type = space.w_IOError
    w_error = space.call_function(w_type, space.wrap(errno), space.wrap(msg))
    raise OperationError(w_type, w_error)


@unwrap_spec(epoller=W_Epoll)
def add_epoll(space, epoller, w_callback):
    state = interp_transaction.state
    if state.epolls is None:
        state.epolls = {}
    elif epoller in state.epolls:
        raise OperationError(state.w_error,
            space.wrap("add_epoll(ep): ep is already registered"))
    pending = EPollPending(space, epoller, w_callback)
    state.epolls[epoller] = pending
    pending.register()

@unwrap_spec(epoller=W_Epoll)
def remove_epoll(space, epoller):
    state = interp_transaction.state
    if state.epolls is None:
        pending = None
    else:
        pending = state.epolls.get(epoller, None)
    if pending is None:
        raise OperationError(state.w_error,
            space.wrap("remove_epoll(ep): ep is not registered"))
    pending.force_quit = True
    del state.epolls[epoller]
