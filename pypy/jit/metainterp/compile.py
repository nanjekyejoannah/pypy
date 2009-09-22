
from pypy.rpython.ootypesystem import ootype
from pypy.objspace.flow.model import Constant, Variable
from pypy.rlib.objectmodel import we_are_translated
from pypy.conftest import option

from pypy.jit.metainterp.resoperation import ResOperation, rop
from pypy.jit.metainterp.history import TreeLoop, log, Box, History
from pypy.jit.metainterp.history import AbstractDescr, BoxInt, BoxPtr, BoxObj,\
     BoxFloat, Const
from pypy.jit.metainterp import history
from pypy.jit.metainterp.specnode import NotSpecNode
from pypy.jit.metainterp.typesystem import llhelper, oohelper
from pypy.jit.metainterp.optimizeutil import InvalidLoop
from pypy.rlib.debug import debug_print

class LoopToken(object):

    def __init__(self, specnodes, executable_token):
        self.specnodes = specnodes
        self.executable_token = executable_token

def compile_new_loop(metainterp, old_loop_tokens, greenkey, start=0):
    """Try to compile a new loop by closing the current history back
    to the first operation.
    """
    if we_are_translated():
        return compile_fresh_loop(metainterp, old_loop_tokens, greenkey, start)
    else:
        return _compile_new_loop_1(metainterp, old_loop_tokens, greenkey, start)

def compile_new_bridge(metainterp, old_loop_tokens, resumekey):
    """Try to compile a new bridge leading from the beginning of the history
    to some existing place.
    """
    if we_are_translated():
        return compile_fresh_bridge(metainterp, old_loop_tokens, resumekey)
    else:
        return _compile_new_bridge_1(metainterp, old_loop_tokens, resumekey)

class BridgeInProgress(Exception):
    pass


# the following is not translatable
def _compile_new_loop_1(metainterp, old_loop_tokens, greenkey, start):
    old_loop_tokens_1 = old_loop_tokens[:]
    try:
        loop = compile_fresh_loop(metainterp, old_loop_tokens, greenkey, start)
    except Exception, exc:
        show_loop(metainterp, error=exc)
        raise
    else:
        if loop in old_loop_tokens_1:
            log.info("reusing loop at %r" % (loop,))
        else:
            show_loop(metainterp, loop)
    if loop is not None:
        loop.check_consistency()
    return loop

def _compile_new_bridge_1(metainterp, old_loop_tokens, resumekey):
    try:
        target_loop = compile_fresh_bridge(metainterp, old_loop_tokens,
                                           resumekey)
    except Exception, exc:
        show_loop(metainterp, error=exc)
        raise
    else:
        if target_loop is not None:
            show_loop(metainterp, target_loop)
    if target_loop is not None and type(target_loop) is not TerminatingLoop:
        target_loop.check_consistency()
    return target_loop

def show_loop(metainterp, loop=None, error=None):
    # debugging
    if option.view or option.viewloops:
        if error:
            errmsg = error.__class__.__name__
            if str(error):
                errmsg += ': ' + str(error)
        else:
            errmsg = None
        if loop is None or type(loop) is TerminatingLoop:
            extraloops = []
        else:
            extraloops = [loop]
        metainterp.staticdata.stats.view(errmsg=errmsg, extraloops=extraloops)

def create_empty_loop(metainterp):
    name = metainterp.staticdata.stats.name_for_new_loop()
    return TreeLoop(name)

# ____________________________________________________________

def compile_fresh_loop(metainterp, old_loop_tokens, greenkey, start):
    from pypy.jit.metainterp.pyjitpl import DEBUG

    history = metainterp.history
    loop = create_empty_loop(metainterp)
    loop.greenkey = greenkey
    loop.inputargs = history.inputargs
    for box in loop.inputargs:
        assert isinstance(box, Box)
    if start > 0:
        loop.operations = history.operations[start:]
    else:
        loop.operations = history.operations
    loop.operations[-1].jump_target = loop
    metainterp_sd = metainterp.staticdata
    try:
        old_loop = metainterp_sd.state.optimize_loop(metainterp_sd.options,
                                                     old_loop_tokens, loop, 
                                                     metainterp.cpu)
    except InvalidLoop:
        return None
    if old_loop is not None:
        if we_are_translated() and DEBUG > 0:
            debug_print("reusing old loop")
        return old_loop
    send_loop_to_backend(metainterp, loop, None, "loop")
    old_loop_tokens.append(loop)
    return loop

def send_loop_to_backend(metainterp, loop, guard_op, type):
    metainterp.staticdata.profiler.start_backend()
    if guard_op is None:
        executable_token = metainterp.cpu.compile_loop(loop)
        loop.executable_token = executable_token # xxx unhappy
    else:
        metainterp.cpu.compile_bridge(guard_op)        
    metainterp.staticdata.profiler.end_backend()
    if loop is not None:
        metainterp.staticdata.stats.add_new_loop(loop)
    if not we_are_translated():
        if type != "entry bridge":
            metainterp.staticdata.stats.compiled()
        else:
            loop._ignore_during_counting = True
        log.info("compiled new " + type)
    else:
        from pypy.jit.metainterp.pyjitpl import DEBUG
        if DEBUG > 0:
            debug_print("compiled new " + type)

# ____________________________________________________________

class DoneWithThisFrameDescrVoid(AbstractDescr):
    def handle_fail_op(self, metainterp_sd, fail_op):
        assert metainterp_sd.result_type == 'void'
        raise metainterp_sd.DoneWithThisFrameVoid()

class DoneWithThisFrameDescrInt(AbstractDescr):
    def handle_fail_op(self, metainterp_sd, fail_op):
        assert metainterp_sd.result_type == 'int'
        resultbox = fail_op.args[0]
        if isinstance(resultbox, BoxInt):
            result = metainterp_sd.cpu.get_latest_value_int(0)
        else:
            assert isinstance(resultbox, history.Const)
            result = resultbox.getint()
        raise metainterp_sd.DoneWithThisFrameInt(result)

class DoneWithThisFrameDescrRef(AbstractDescr):
    def handle_fail_op(self, metainterp_sd, fail_op):
        assert metainterp_sd.result_type == 'ref'
        resultbox = fail_op.args[0]
        cpu = metainterp_sd.cpu
        if isinstance(resultbox, cpu.ts.BoxRef):
            result = cpu.get_latest_value_ref(0)
        else:
            assert isinstance(resultbox, history.Const)
            result = resultbox.getref_base()
        raise metainterp_sd.DoneWithThisFrameRef(cpu, result)

class DoneWithThisFrameDescrFloat(AbstractDescr):
    def handle_fail_op(self, metainterp_sd, fail_op):
        assert metainterp_sd.result_type == 'float'
        resultbox = fail_op.args[0]
        if isinstance(resultbox, BoxFloat):
            result = metainterp_sd.cpu.get_latest_value_float(0)
        else:
            assert isinstance(resultbox, history.Const)
            result = resultbox.getfloat()
        raise metainterp_sd.DoneWithThisFrameFloat(result)

class ExitFrameWithExceptionDescrRef(AbstractDescr):
    def handle_fail_op(self, metainterp_sd, fail_op):
        assert len(fail_op.args) == 1
        valuebox = fail_op.args[0]
        cpu = metainterp_sd.cpu
        if isinstance(valuebox, cpu.ts.BoxRef):
            value = cpu.get_latest_value_ref(0)
        else:
            assert isinstance(valuebox, history.Const)
            value = valuebox.getref_base()
        raise metainterp_sd.ExitFrameWithExceptionRef(cpu, value)

done_with_this_frame_descr_void = DoneWithThisFrameDescrVoid()
done_with_this_frame_descr_int = DoneWithThisFrameDescrInt()
done_with_this_frame_descr_ref = DoneWithThisFrameDescrRef()
done_with_this_frame_descr_float = DoneWithThisFrameDescrFloat()
exit_frame_with_exception_descr_ref = ExitFrameWithExceptionDescrRef()


prebuiltNotSpecNode = NotSpecNode()

class TerminatingLoopToken(LoopToken):
    def __init__(self, nargs, finishdescr):
        LoopToken.__init__(self, [prebuiltNotSpecNode]*nargs, None)
        self.finishdescr = finishdescr

# pseudo loop tokens to make the life of optimize.py easier
loop_tokens_done_with_this_frame_int = [
    TerminatingLoopToken(1, done_with_this_frame_descr_int)
    ]
# xxx they are the same now
llhelper.loop_tokens_done_with_this_frame_ref = [
    TerminatingLoopToken(1, done_with_this_frame_descr_ref)
    ]
oohelper.loop_tokens_done_with_this_frame_ref = [
    TerminatingLoopToken(1, done_with_this_frame_descr_ref)
    ]
loop_tokens_done_with_this_frame_float = [
    TerminatingLoopToken(1, done_with_this_frame_descr_float)
    ]
loop_tokens_done_with_this_frame_void = [
    TerminatingLoopToken(0, done_with_this_frame_descr_void)
    ]
# xxx they are the same now
llhelper.loop_tokens_exit_frame_with_exception_ref = [
    TerminatingLoopToken(1, exit_frame_with_exception_descr_ref)
    ]
oohelper.loop_tokens_exit_frame_with_exception_ref = [
    TerminatingLoopToken(1, exit_frame_with_exception_descr_ref)
    ]

class ResumeDescr(AbstractDescr):
    def __init__(self, original_greenkey):
        self.original_greenkey = original_greenkey

class ResumeGuardDescr(ResumeDescr):
    counter = 0

    def __init__(self, original_greenkey, guard_op):
        ResumeDescr.__init__(self, original_greenkey)
        self.guard_op = guard_op
        # this class also gets attributes stored by ResumeDataBuilder.finish()

    def handle_fail_op(self, metainterp_sd, fail_op):
        from pypy.jit.metainterp.pyjitpl import MetaInterp
        metainterp = MetaInterp(metainterp_sd)
        patch = self.patch_boxes_temporarily(metainterp_sd, fail_op)
        try:
            return metainterp.handle_guard_failure(fail_op, self)
        finally:
            self.restore_patched_boxes(metainterp_sd, fail_op, patch)

    def patch_boxes_temporarily(self, metainterp_sd, fail_op):
        # A bit indirect: when we hit a rop.FAIL, the current values are
        # stored somewhere in the CPU backend.  Below we fetch them and
        # copy them into the real boxes, i.e. the 'fail_op.args'.  We
        # are in a try:finally path at the end of which, in
        # restore_patched_boxes(), we can safely undo exactly the
        # changes done here.
        cpu = metainterp_sd.cpu
        patch = []
        for i in range(len(fail_op.args)):
            box = fail_op.args[i]
            patch.append(box.clonebox())
            if isinstance(box, BoxInt):
                srcvalue = cpu.get_latest_value_int(i)
                box.changevalue_int(srcvalue)
            elif isinstance(box, cpu.ts.BoxRef):
                srcvalue = cpu.get_latest_value_ref(i)
                box.changevalue_ref(srcvalue)
            elif isinstance(box, Const):
                pass # we don't need to do anything
            else:
                assert False
        return patch

    def restore_patched_boxes(self, metainterp_sd, fail_op, patch):
        for i in range(len(patch)-1, -1, -1):
            srcbox = patch[i]
            dstbox = fail_op.args[i]
            if isinstance(dstbox, BoxInt):
                dstbox.changevalue_int(srcbox.getint())
            elif isinstance(dstbox, metainterp_sd.cpu.ts.BoxRef):
                dstbox.changevalue_ref(srcbox.getref_base())
            elif isinstance(dstbox, Const):
                pass
            else:
                assert False

    def get_guard_op(self):
        guard_op = self.guard_op
        assert guard_op.is_guard()
        if guard_op.optimized is not None:   # should always be the case,
            return guard_op.optimized        # except if not optimizing at all
        else:
            return guard_op

    def compile_and_attach(self, metainterp, new_loop):
        # We managed to create a bridge.  Attach the new operations
        # to the corrsponding guard_op and compile from there
        # xxx unhappy
        guard_op = self.get_guard_op()
        guard_op.suboperations = new_loop.operations
        send_loop_to_backend(metainterp, None, guard_op, "bridge")

class ResumeFromInterpDescr(ResumeDescr):
    def __init__(self, original_greenkey, redkey):
        ResumeDescr.__init__(self, original_greenkey)
        self.redkey = redkey

    def compile_and_attach(self, metainterp, new_loop):
        # We managed to create a bridge going from the interpreter
        # to previously-compiled code.  We keep 'new_loop', which is not
        # a loop at all but ends in a jump to the target loop.  It starts
        # with completely unoptimized arguments, as in the interpreter.
        metainterp_sd = metainterp.staticdata
        metainterp.history.inputargs = self.redkey
        new_loop.greenkey = self.original_greenkey
        new_loop.inputargs = self.redkey
        new_loop.specnodes = [prebuiltNotSpecNode] * len(self.redkey)
        send_loop_to_backend(metainterp, new_loop, None, "entry bridge")
        # send the new_loop to warmspot.py, to be called directly the next time
        metainterp_sd.state.attach_unoptimized_bridge_from_interp(
            self.original_greenkey,
            new_loop)


def compile_fresh_bridge(metainterp, old_loop_tokens, resumekey):
    # The history contains new operations to attach as the code for the
    # failure of 'resumekey.guard_op'.
    #
    # Attempt to use optimize_bridge().  This may return None in case
    # it does not work -- i.e. none of the existing old_loop_tokens match.
    new_loop = create_empty_loop(metainterp)
    new_loop.operations = metainterp.history.operations
    metainterp_sd = metainterp.staticdata
    options = metainterp_sd.options
    try:
        target_loop = metainterp_sd.state.optimize_bridge(options,
                                                          old_loop_tokens,
                                                          new_loop,
                                                          metainterp.cpu)
    except InvalidLoop:
        assert 0, "InvalidLoop in optimize_bridge?"
        return None
    # Did it work?
    if target_loop is not None:
        # Yes, we managed to create a bridge.  Dispatch to resumekey to
        # know exactly what we must do (ResumeGuardDescr/ResumeFromInterpDescr)
        prepare_last_operation(new_loop, target_loop)
        resumekey.compile_and_attach(metainterp, new_loop)
    return target_loop

def prepare_last_operation(new_loop, target_loop):
    op = new_loop.operations[-1]
    if not isinstance(target_loop, TerminatingLoop):
        # normal case
        op.jump_target = target_loop
    else:
        # The target_loop is a pseudo-loop, e.g. done_with_this_frame.
        # Replace the operation with the real operation we want, i.e. a FAIL.
        descr = target_loop.finishdescr
        new_op = ResOperation(rop.FAIL, op.args, None, descr=descr)
        new_loop.operations[-1] = new_op
