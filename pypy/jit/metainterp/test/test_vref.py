import py
from pypy.rlib.jit import JitDriver, dont_look_inside, virtual_ref
from pypy.jit.metainterp.test.test_basic import LLJitMixin, OOJitMixin


class VRefTests:

    def test_make_vref_simple(self):
        class X:
            pass
        class ExCtx:
            pass
        exctx = ExCtx()
        #
        def f():
            exctx.topframeref = virtual_ref(X())
            exctx.topframeref = None
            return 1
        #
        self.interp_operations(f, [])
        self.check_operations_history(virtual_ref=1)

    def test_make_vref_and_force(self):
        jitdriver = JitDriver(greens = [], reds = ['total', 'n'])
        #
        class X:
            pass
        class ExCtx:
            pass
        exctx = ExCtx()
        #
        @dont_look_inside
        def force_me():
            return exctx.topframeref().n
        #
        def f(n):
            total = 0
            while total < 300:
                jitdriver.can_enter_jit(total=total, n=n)
                jitdriver.jit_merge_point(total=total, n=n)
                x = X()
                x.n = n + 123
                exctx.topframeref = virtual_ref(x)
                total += force_me() - 100
            return total
        #
        res = self.meta_interp(f, [-4])
        assert res == 16 * 19
        self.check_loops({})      # because we aborted tracing

    def test_simple_no_access(self):
        py.test.skip("in-progress")
        myjitdriver = JitDriver(greens = [], reds = ['n'])
        #
        class XY:
            pass
        class ExCtx:
            pass
        exctx = ExCtx()
        #
        @dont_look_inside
        def externalfn():
            return 1
        #
        def f(n):
            while n > 0:
                myjitdriver.can_enter_jit(n=n)
                myjitdriver.jit_merge_point(n=n)
                xy = XY()
                exctx.topframeref = virtual_ref(xy)
                n -= externalfn()
                exctx.topframeref = None
        #
        self.meta_interp(f, [15])
        self.check_loops(new_with_vtable=0)

    def test_simple_force_always(self):
        py.test.skip("in-progress")
        myjitdriver = JitDriver(greens = [], reds = ['n'])
        #
        class XY:
            pass
        class ExCtx:
            pass
        exctx = ExCtx()
        #
        @dont_look_inside
        def externalfn(n):
            m = exctx.topframeref().n
            assert m == n
            return 1
        #
        def f(n):
            while n > 0:
                myjitdriver.can_enter_jit(n=n)
                myjitdriver.jit_merge_point(n=n)
                xy = XY()
                xy.n = n
                exctx.topframeref = virtual_ref(xy)
                n -= externalfn(n)
                exctx.topframeref = None
        #
        self.meta_interp(f, [15])
        self.check_loops(new_with_vtable=0)


class TestLLtype(VRefTests, LLJitMixin):
    pass
