from pypy.annotation import model as annmodel
from pypy.translator.translator import TranslationContext, graphof
from pypy.rpython.llinterp import LLInterpreter
from pypy.rpython.test.test_llinterp import interpret
from pypy.rpython.lltypesystem import lltype



# ____________________________________________________________

class TestNormalize(object):

    def rtype(self, fn, argtypes, resulttype):
        t = TranslationContext()
        a = t.buildannotator()
        s = a.build_types(fn, argtypes)
        assert s == a.typeannotation(resulttype)
        typer = t.buildrtyper()
        typer.specialize()
        #t.view()
        t.checkgraphs()
        return t


    def test_normalize_f2_as_taking_string_argument(self):
        def f1(l1):
            pass
        def f2(l2):
            pass
        def g(n):
            if n > 0:
                f1("123")
                f = f1
            else:
                f2("b")
                f = f2
            f("a")

        # The call table looks like:
        #
        #                 FuncDesc(f1)  FuncDesc(f2)
        #   --------------------------------------------
        #   line g+2:       graph1
        #   line g+5:                      graph2
        #   line g+7:       graph1         graph2
        #
        # But all lines get compressed to a single line.

        translator = self.rtype(g, [int], annmodel.s_None)
        f1graph = graphof(translator, f1)
        f2graph = graphof(translator, f2)
        s_l1 = translator.annotator.binding(f1graph.getargs()[0])
        s_l2 = translator.annotator.binding(f2graph.getargs()[0])
        assert s_l1.__class__ == annmodel.SomeString   # and not SomeChar
        assert s_l2.__class__ == annmodel.SomeString   # and not SomeChar
        #translator.view()

    def test_normalize_keyword_call(self):
        def f1(a, b):
            return (a, b, 0, 0)
        def f2(b, c=123, a=456, d=789):
            return (a, b, c, d)
        def g(n):
            if n > 0:
                f = f1
            else:
                f = f2
            f(a=5, b=6)

        translator = self.rtype(g, [int], annmodel.s_None)
        f1graph = graphof(translator, f1)
        f2graph = graphof(translator, f2)
        assert len(f1graph.getargs()) == 2
        assert len(f2graph.getargs()) == 2   # normalized to the common call pattern
        #translator.view()

    def test_normalize_returnvar(self):
        def add_one(n):
            return n+1
        def add_half(n):
            return n+0.5
        def dummyfn(n, i):
            if i == 1:
                adder = add_one
            else:
                adder = add_half
            return adder(n)

        res = interpret(dummyfn, [52, 1])
        assert type(res) is float and res == 53.0
        res = interpret(dummyfn, [7, 2])
        assert type(res) is float and res == 7.5

    def test_normalize_missing_return(self):
        def add_one(n):
            return n+1
        def oups(n):
            raise ValueError
        def dummyfn(n, i):
            if i == 1:
                adder = add_one
            else:
                adder = oups
            try:
                return adder(n)
            except ValueError:
                return -1

        translator = self.rtype(dummyfn, [int, int], int)
        add_one_graph = graphof(translator, add_one)
        oups_graph    = graphof(translator, oups)
        assert add_one_graph.getreturnvar().concretetype == lltype.Signed
        assert oups_graph   .getreturnvar().concretetype == lltype.Signed
        #translator.view()

    def test_normalize_abstract_method(self):
        class Base:
            def fn(self):
                raise NotImplementedError
        class Sub1(Base):
            def fn(self):
                return 1
        class Sub2(Base):
            def fn(self):
                return -2
        def dummyfn(n):
            if n == 1:
                x = Sub1()
            else:
                x = Sub2()
            return x.fn()

        translator = self.rtype(dummyfn, [int], int)
        base_graph = graphof(translator, Base.fn.im_func)
        sub1_graph = graphof(translator, Sub1.fn.im_func)
        sub2_graph = graphof(translator, Sub2.fn.im_func)
        assert base_graph.getreturnvar().concretetype == lltype.Signed
        assert sub1_graph.getreturnvar().concretetype == lltype.Signed
        assert sub2_graph.getreturnvar().concretetype == lltype.Signed

        llinterp = LLInterpreter(translator.rtyper)
        res = llinterp.eval_graph(graphof(translator, dummyfn), [1])
        assert res == 1
        res = llinterp.eval_graph(graphof(translator, dummyfn), [2])
        assert res == -2


class PBase:
    def fn(self):
        raise NotImplementedError
class PSub1(PBase):
    def fn(self):
        return 1
class PSub2(PBase):
    def fn(self):
        return 2
def prefn(n):
    if n == 1:
        x = PSub1()
    else:
        x = PSub2()
    return x.fn() * 100 + isinstance(x, PSub2)


class TestNormalizeAfterTheFact(TestNormalize):

    def rtype(self, fn, argtypes, resulttype, checkfunction=None):
        t = TranslationContext()
        a = t.buildannotator()
        a.build_types(prefn, [int])
        typer = t.buildrtyper()
        typer.specialize()
        #t.view()

        s_result = a.typeannotation(resulttype)

        from pypy.rpython import annlowlevel
        # annotate, normalize and rtype fn after the fact
        annhelper = annlowlevel.MixLevelHelperAnnotator(typer)               
        graph = annhelper.getgraph(fn, [a.typeannotation(argtype) for argtype in argtypes],
                                   s_result)
        annhelper.finish()
        t.checkgraphs()

        if checkfunction is not None:
            checkfunction(t)

        # sanity check prefn
        llinterp = LLInterpreter(typer)
        res = llinterp.eval_graph(graphof(t, prefn), [1])
        assert res == 100
        res = llinterp.eval_graph(graphof(t, prefn), [2])
        assert res == 201
        
        return t

    def test_mix_after_recursion(self):
        def prefn(n):
            if n:
                return 2*prefn(n-1)
            else:
                return 1
        
        t = TranslationContext()
        a = t.buildannotator()
        a.build_types(prefn, [int])
        typer = t.buildrtyper()
        typer.specialize()
        #t.view()

        def f():
            return 1

        from pypy.rpython import annlowlevel
        annhelper = annlowlevel.MixLevelHelperAnnotator(typer)               
        graph = annhelper.getgraph(f, [], annmodel.SomeInteger())
        annhelper.finish()
        
    def test_add_more_subclasses(self):
        from pypy.rpython import rclass
        from pypy.rpython.lltypesystem.rclass import ll_issubclass
        class Sub3(PBase):
            def newmethod(self):
                return 3
        def dummyfn(n):
            x = Sub3()
            return x.newmethod()

        def checkfunction(translator):
            # make sure that there is a sensible comparison defined on the
            # symbolics
            bk = translator.annotator.bookkeeper
            rtyper = translator.rtyper
            base_classdef = bk.getuniqueclassdef(PBase)
            base_vtable = rclass.getclassrepr(rtyper, base_classdef).getruntime()
            sub3_classdef = bk.getuniqueclassdef(Sub3)
            sub3_vtable = rclass.getclassrepr(rtyper, sub3_classdef).getruntime()
            assert ll_issubclass(sub3_vtable, base_vtable)
            assert not ll_issubclass(base_vtable, sub3_vtable)

        translator = self.rtype(dummyfn, [int], int, checkfunction)
        base_graph    = graphof(translator, PBase.fn.im_func)
        sub1_graph    = graphof(translator, PSub1.fn.im_func)
        sub2_graph    = graphof(translator, PSub2.fn.im_func)
        sub3_graph    = graphof(translator, Sub3.fn.im_func)
        dummyfn_graph = graphof(translator, dummyfn)
        assert base_graph.getreturnvar().concretetype == lltype.Signed
        assert sub1_graph.getreturnvar().concretetype == lltype.Signed
        assert sub2_graph.getreturnvar().concretetype == lltype.Signed
        assert sub3_graph.getreturnvar().concretetype == lltype.Signed
        assert dummyfn_graph.getreturnvar().concretetype == lltype.Signed

    def test_call_memoized_function_with_defaults(self):
        class Freezing:
            def _freeze_(self):
                return True
        fr1 = Freezing(); fr1.x = 1
        fr2 = Freezing(); fr2.x = 2
        def getorbuild(key1, key2=fr2, flag3=True):
            return key1.x * 100 + key2.x * 10 + flag3
        getorbuild._annspecialcase_ = "specialize:memo"

        def f1(i):
            if i > 0:
                fr = fr1
            else:
                fr = fr2
            if i % 2:
                return getorbuild(fr)
            else:
                return getorbuild(fr, fr2, False)

        for i in [-7, -2, 100, 5]:
            res = interpret(f1, [i])
            assert res == f1(i)
