from pypy.lang.prolog.interpreter.term import NonVar, Term, Var, Atom
from pypy.lang.prolog.interpreter.engine import Continuation
from pypy.lang.prolog.interpreter import helper, error
from pypy.lang.prolog.interpreter.prologopcode import opcodedesc
from pypy.rlib.objectmodel import we_are_translated
from pypy.rlib.jit import hint, we_are_jitted

queryatom = Atom.newatom("<dummy>")

class Code(object):
    _immutable_ = True

    def empty_init(self):
        self.term_info = [] # tuples of (functor, numargs, signature)
        self.opcode_head = ""
        self.opcode = ""
        self.constants = [] # list of ground Prolog objects
        self.functions = [] # list of Function objects
        self.maxlocalvar = 0


Code.dynamic_code = Code()
Code.dynamic_code.empty_init()
Code.dynamic_code.maxlocalvar = 1
Code.dynamic_code.opcode_head = "l\x00\x00"
Code.dynamic_code.opcode = "l\x00\x00D"


def compile(head, body, engine):
    comp = Compiler(engine)
    return comp.compile(head, body)

class Compiler(object):
    def __init__(self, engine):
        self.engine = engine

    def compile(self, head, body):
        self.term_info = [] # tuples of (functor, numargs, signature)
        self.term_info_map = {}
        self.opcode = []
        self.constants = [] # list of ground Prolog objects
        self.constant_map = {}
        self.functions = [] # list of Function objects
        self.functionmap = {}
        self.maxlocalvar = 0
        self.varmap = {}
        self.can_contain_cut = False
        result = Code()
        self.compile_termbuilding(head)
        result.opcode_head = self.getbytecode()
        if body is not None:
            self.compile_body(body)
        result.opcode = self.getbytecode()
        result.constants = self.constants
        result.term_info = self.term_info
        result.functions = self.functions
        result.maxlocalvar = len(self.varmap)
        result.can_contain_cut = self.can_contain_cut
        return result

    def compile_termbuilding(self, term):
        if helper.is_ground(term, self.engine):
            num = self.getconstnum(term)
            self.emit_opcode(opcodedesc.PUTCONSTANT, num)
        elif isinstance(term, Var):
            num = self.getvarnum(term)
            self.emit_opcode(opcodedesc.PUTLOCALVAR, num)
        else:
            assert isinstance(term, Term)
            for arg in term.args:
                self.compile_termbuilding(arg)
            num = self.getsignum(term)
            self.emit_opcode(opcodedesc.MAKETERM, num)

    def compile_body(self, body):
        from pypy.lang.prolog.builtin import builtins_list, builtins_index

        body = body.dereference(self.engine.heap)
        if isinstance(body, Var):
            self.can_contain_cut = True
            self.compile_termbuilding(body)
            self.emit_opcode(opcodedesc.DYNAMIC_CALL)
            return
        body = helper.ensure_callable(body)
        if body.signature == ",/2":
            assert isinstance(body, Term)
            self.compile_body(body.args[0])
            self.compile_body(body.args[1])
            return
        elif body.signature == "=/2":
            assert isinstance(body, Term)
            self.compile_termbuilding(body.args[0])
            self.compile_termbuilding(body.args[1])
            self.emit_opcode(opcodedesc.UNIFY)
        elif body.signature == "!/0":
            self.can_contain_cut = True
            self.emit_opcode(opcodedesc.CUT)
        elif body.signature in builtins_index:
            i = builtins_index[body.signature]
            self.compile_termbuilding(body)
            self.emit_opcode(opcodedesc.CALL_BUILTIN, i)
        else:
            self.compile_termbuilding(body)
            num = self.getfunction(body.signature)
            self.emit_opcode(opcodedesc.STATIC_CALL, num)

    def emit_opcode(self, desc, arg=-1):
        self.opcode.append(desc.index)
        if desc.hasargument:
            if not 0 <= arg < 65536:
                raise error.UncatchableError("too many constants or variables!")
            self.opcode.append(arg >> 8)
            self.opcode.append(arg & 0xff)

    def getbytecode(self):
        bytecodes = [chr(c) for c in self.opcode]
        self.opcode = []
        return "".join(bytecodes)


    def getvarnum(self, var):
        try:
            return self.varmap[var]
        except KeyError:
            result = self.varmap[var] = len(self.varmap)
            return result

    def getsignum(self, term):
        try:
            return self.term_info_map[term.signature]
        except KeyError:
            result = len(self.term_info_map)
            self.term_info_map[term.signature] = result
            self.term_info.append((term.name, len(term.args), term.signature))
            return result

    def getconstnum(self, const):
        try:
            return self.constant_map[const]
        except KeyError:
            result = len(self.constant_map)
            self.constant_map[const] = result
            self.constants.append(const)
            return result

    def getfunction(self, signature):
        try:
            return self.functionmap[signature]
        except KeyError:
            result = len(self.functionmap)
            self.functionmap[signature] = result
            self.functions.append(self.engine.lookup_userfunction(signature))
            return result

