from rpython.translator.c.support import c_string_constant
from rpython.translator.stm.stmgcintf import StmOperations


def stm_start_transaction(funcgen, op):
    # only for testing.  With stmgc, this operation should have been handled
    # already by gctransform.
    assert funcgen.db.translator.config.translation.gc == 'none'
    return 'stm_nogc_start_transaction();'

def stm_stop_transaction(funcgen, op):
    # only for testing.  With stmgc, this operation should have been handled
    # already by gctransform.
    assert funcgen.db.translator.config.translation.gc == 'none'
    return 'stm_nogc_stop_transaction();'

def stm_barrier(funcgen, op):
    category_change = op.args[0].value
    assert type(category_change) is str and len(category_change) == 3   # "x2y"
    arg = funcgen.expr(op.args[1])
    result = funcgen.expr(op.result)
    return '%s = STM_BARRIER_%s(%s);' % (result, category_change, arg)

def stm_ptr_eq(funcgen, op):
    arg0 = funcgen.expr(op.args[0])
    arg1 = funcgen.expr(op.args[1])
    result = funcgen.expr(op.result)
    return '%s = STM_PTR_EQ(%s, %s);' % (result, arg0, arg1)

def stm_become_inevitable(funcgen, op):
    try:
        info = op.args[0].value
    except IndexError:
        info = "rstm.become_inevitable"    # cannot insert it in 'llop'
    string_literal = c_string_constant(info)
    return 'BecomeInevitable(%s);' % (string_literal,)

##def stm_jit_invoke_code(funcgen, op):
##    return funcgen.OP_DIRECT_CALL(op)

def _stm_nogc_init_function():
    """Called at process start-up when running with no GC."""
    StmOperations.descriptor_init()
    StmOperations.begin_inevitable_transaction()


def op_stm(funcgen, op):
    func = globals()[op.opname]
    return func(funcgen, op)
