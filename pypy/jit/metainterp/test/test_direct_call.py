
import py
from pypy.rlib.jit import JitDriver, hint
from pypy.jit.metainterp.test.test_basic import LLJitMixin
from pypy.rlib.libffi import CDLL, ffi_type_sint, ffi_type_double, ArgChain, Func
from pypy.tool.udir import udir
from pypy.translator.tool.cbuild import ExternalCompilationInfo
from pypy.translator.platform import platform
from pypy.rpython.lltypesystem import lltype, rffi

class TestDirectCall(LLJitMixin):
    def setup_class(cls):
        # prepare C code as an example, so we can load it and call
        # it via rlib.libffi
        c_file = udir.ensure("test_jit_direct_call", dir=1).join("xlib.c")
        c_file.write(py.code.Source('''
        int sum_xy(int x, double y)
        {
           return (x + (int)y);
        }
        '''))
        eci = ExternalCompilationInfo(export_symbols=['sum_xy'])
        cls.lib_name = str(platform.compile([c_file], eci, 'x',
                                            standalone=False))

    def test_one(self):
        driver = JitDriver(reds = ['n', 'func'], greens = [])        

        def f(n):
            cdll = CDLL(self.lib_name)
            func = cdll.getpointer('sum_xy', [ffi_type_sint, ffi_type_double],
                                   ffi_type_sint)
            while n < 10:
                driver.jit_merge_point(n=n, func=func)
                driver.can_enter_jit(n=n, func=func)
                func = hint(func, promote=True)
                argchain = ArgChain()
                argchain.int(n).float(1.2)
                n = func.call(argchain, lltype.Signed)
            return n
            
        res = self.meta_interp(f, [0])
        assert res == 10
        self.check_loops({
                'call': 1,
                'guard_no_exception': 1,
                'int_lt': 1,
                'guard_true': 1,
                'jump': 1})

