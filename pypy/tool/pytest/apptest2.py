import sys
import os

import pytest
from pypy import pypydir
import pypy.interpreter.function
from pypy.tool.pytest import app_rewrite
from pypy.interpreter.error import OperationError
from pypy.interpreter.module import Module
from pypy.tool.pytest import objspace
from pypy.tool.pytest import appsupport


class AppTestModule(pytest.Module):
    def __init__(self, path, parent, rewrite_asserts=False):
        super(AppTestModule, self).__init__(path, parent)
        self.rewrite_asserts = rewrite_asserts

    def collect(self):
        _, source = app_rewrite._prepare_source(self.fspath)
        space = objspace.gettestobjspace()
        w_rootdir = space.newtext(
            os.path.join(pypydir, 'tool', 'pytest', 'ast-rewriter'))
        w_source = space.newtext(source)
        fname = str(self.fspath)
        w_fname = space.newtext(fname)
        if self.rewrite_asserts:
            w_mod = space.appexec([w_rootdir, w_source, w_fname],
                                """(rootdir, source, fname):
                import sys
                sys.path.insert(0, rootdir)
                from ast_rewrite import rewrite_asserts, create_module

                co = rewrite_asserts(source, fname)
                mod = create_module(fname, co)
                return mod
            """)
        else:
            w_mod = create_module(space, w_fname, fname, source)
        mod_dict = w_mod.getdict(space).unwrap(space)
        items = []
        for name, w_obj in mod_dict.items():
            if not name.startswith('test_'):
                continue
            if not isinstance(w_obj, pypy.interpreter.function.Function):
                continue
            items.append(AppTestFunction(name, self, w_obj))
        return items

    def setup(self):
        pass

def create_module(space, w_name, filename, source):
    w_mod = Module(space, w_name)
    w_dict = w_mod.getdict(space)
    space.setitem(w_dict, space.newtext('__file__'), space.newtext(filename))
    space.exec_(source, w_dict, w_dict, filename=filename)
    return w_mod


class AppError(Exception):

    def __init__(self, excinfo):
        self.excinfo = excinfo


class AppTestFunction(pytest.Item):

    def __init__(self, name, parent, w_obj):
        super(AppTestFunction, self).__init__(name, parent)
        self.w_obj = w_obj

    def runtest(self):
        target = self.w_obj
        space = target.space
        self.execute_appex(space, target)

    def repr_failure(self, excinfo):
        if excinfo.errisinstance(AppError):
            excinfo = excinfo.value.excinfo
        return super(AppTestFunction, self).repr_failure(excinfo)

    def execute_appex(self, space, w_func):
        space.getexecutioncontext().set_sys_exc_info(None)
        try:
            space.call_function(w_func)
        except OperationError as e:
            if self.config.option.raise_operr:
                raise
            tb = sys.exc_info()[2]
            if e.match(space, space.w_KeyboardInterrupt):
                raise KeyboardInterrupt, KeyboardInterrupt(), tb
            appexcinfo = appsupport.AppExceptionInfo(space, e)
            if appexcinfo.traceback:
                raise AppError, AppError(appexcinfo), tb
            raise
