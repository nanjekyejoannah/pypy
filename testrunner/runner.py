import sys
import os
import thread
import Queue
import py
import util
import collections

READ_MODE = 'rU'
WRITE_MODE = 'wb'


def fix_interppath_in_win32(path, cwd, _win32):
    if (_win32 and not os.path.isabs(path) and
       ('\\' in path or '/' in path)):
        path = os.path.join(str(cwd), path)
    return path


def execute_args(cwd, test, logfname, interp, test_driver,
                 _win32=(sys.platform=='win32')):
    args = interp + test_driver
    args += ['-p', 'resultlog',
             '--resultlog=%s' % logfname,
             '--junitxml=%s.junit' % logfname,
             test]

    args = map(str, args)

    args[0] = fix_interppath_in_win32(args[0], cwd, _win32)
    return args


def execute_test(cwd, test, out, logfname, interp, test_driver,
                 runfunc, timeout=None):
    args = execute_args(cwd, test, logfname, interp, test_driver)
    with out.open('w') as fp:
        exitcode = runfunc(args, cwd, fp, timeout=timeout)
    return exitcode


def worker(num, n, run_param, testdirs, result_queue):
    sessdir = run_param.sessdir
    root = run_param.root
    test_driver = run_param.test_driver
    interp = run_param.interp
    runfunc = run_param.runfunc
    timeout = run_param.timeout
    cleanup = run_param.cleanup
    # xxx cfg thread start
    while 1:
        try:
            test = testdirs.pop(0)
        except IndexError:
            result_queue.put(None) # done
            return
        result_queue.put(('start', test))
        basename = py.path.local(test).purebasename
        logfname = sessdir.join("%d-%s-pytest-log" % (num, basename))
        one_output = sessdir.join("%d-%s-output" % (num, basename))
        num += n

        try:
            exitcode = execute_test(root, test, one_output, logfname,
                                    interp, test_driver, runfunc=runfunc,
                                    timeout=timeout)
            cleanup(test)
        except:
            print "execute-test for %r failed with:" % test
            import traceback
            traceback.print_exc()
            exitcode = util.EXECUTEFAILED

        if one_output.check(file=1):
            output = one_output.read(READ_MODE)
        else:
            output = ""

        if logfname.check(file=1):
            logdata = logfname.read(READ_MODE)
        else:
            logdata = ""




        failure, extralog = util.interpret_exitcode(exitcode, test, logdata)

        junit = logfname + '.junit'
        if junit.check(file=1):
            pass
        if extralog:
            logdata += extralog

        result_queue.put(('done', test, failure, logdata, output))

invoke_in_thread = thread.start_new_thread

def start_workers(n, run_param, testdirs):
    result_queue = Queue.Queue()
    for i in range(n):
        invoke_in_thread(worker, (i, n, run_param, testdirs,
                                  result_queue))
    return result_queue


def execute_tests(run_param, testdirs, logfile):
    sessdir = py.path.local.make_numbered_dir(prefix='usession-testrunner-',
                                              keep=4)
    run_param.sessdir = sessdir

    N = run_param.parallel_runs
    if N > 1:
        run_param.log("running %d parallel test workers", N)
    failure = False

    for testname in testdirs:
        run_param.log("-- %s", testname)
    run_param.log("-- total: %d to run", len(testdirs))

    result_queue = start_workers(N, run_param, testdirs)

    done = 0
    started = 0

    worker_done = 0
    while True:
        res = result_queue.get()
        if res is None:
            worker_done += 1
            if worker_done == N:
                break
            continue

        if res[0] == 'start':
            started += 1
            run_param.log("++ starting %s [%d started in total]",
                          res[1], started)
            continue

        testname, somefailed, logdata, output = res[1:]
        done += 1
        failure = failure or somefailed

        heading = "__ %s [%d done in total, somefailed=%s] " % (
            testname, done, somefailed)

        run_param.log(heading.ljust(79, '_'))

        run_param.log(output.rstrip())
        if logdata:
            logfile.write(logdata)

    return failure


class RunParam(object):
    run = staticmethod(util.run)
    dry_run = staticmethod(util.dry_run)

    pytestpath = py.path.local(__file__).dirpath().dirpath().join('pytest.py')
    assert pytestpath.check()
    test_driver = [pytestpath]

    cherrypick = None

    def __init__(self, root, out):
        self.root = root
        self.out = out
        self.interp = [os.path.abspath(sys.executable)]
        self.runfunc = self.run
        self.parallel_runs = 1
        self.timeout = None
        self.cherrypick = None

    @classmethod
    def from_options(cls, opts, out):
        root = py.path.local(opts.root)

        self = cls(root, out)

        self.parallel_runs = opts.parallel_runs
        self.timeout = opts.timeout

        if opts.dry_run:
            self.runfunc = self.dry_run
        else:
            self.runfunc = self.run
        return self

    def log(self, fmt, *args):
        self.out.write((fmt % args) + '\n')

    def is_test_py_file(self, p):
        name = p.basename
        return name.startswith('test_') and name.endswith('.py')

    def reltoroot(self, p):
        rel = p.relto(self.root)
        return rel.replace(os.sep, '/')

    def collect_one_testdir(self, testdirs, reldir, tests):
        testdirs.append(reldir)
        return

    def cleanup(self, test):
        # used for test_collect_testdirs
        pass

    def collect_testdirs(self, testdirs, p=None):
        if p is None:
            p = self.root

        reldir = self.reltoroot(p)
        if p.check():
            entries = [p1 for p1 in p.listdir() if p1.check(dotfile=0)]
        else:
            entries = []
        entries.sort()

        if p != self.root:
            for p1 in entries:
                if self.is_test_py_file(p1):
                    self.collect_one_testdir(testdirs, reldir,
                                   [self.reltoroot(t) for t in entries
                                    if self.is_test_py_file(t)])
                    break

        for p1 in entries:
            if p1.check(dir=1, link=0):
                self.collect_testdirs(testdirs, p1)


renamed_configs = {
    'pypy/pytest-A.cfg': 'testrunner/apptests.cfg',
    'pypy/testrunner_cfg.py': 'testrunner/owntests.cfg',
}


def main(opts, args, RunParamClass):

    if opts.logfile is None:
        print "no logfile specified"
        sys.exit(2)

    logfile = open(opts.logfile, WRITE_MODE)
    if opts.output == '-':
        out = sys.stdout
    else:
        out = open(opts.output, WRITE_MODE)

    testdirs = deque()

    run_param = RunParamClass.from_options(opts, out)
    # the config files are python files whose run overrides the content
    # of the run_param instance namespace
    # in that code function overriding method should not take self
    # though a self and self.__class__ are available if needed
    for config_py_file in opts.config:
        config_py_file = renamed_configs.get(config_py_file, config_py_file)
        config_py_file = os.path.expanduser(config_py_file)
        if os.path.isfile(config_py_file):
            run_param.log("using config %s", config_py_file)
            execfile(config_py_file, run_param.__dict__)
        else:
            print >>out, "ignoring non-existant config", config_py_file

    if run_param.cherrypick:
        for p in run_param.cherrypick:
            run_param.collect_testdirs(testdirs, root.join(p))
    else:
        run_param.collect_testdirs(testdirs)

    if opts.parallel_runs:
        run_param.parallel_runs = opts.parallel_runs
    if opts.timeout:
        run_param.timeout = opts.timeout
    run_param.dry_run = opts.dry_run

    if run_param.dry_run:
        print >>out, '\n'.join([str((k, getattr(run_param, k))) \
                        for k in dir(run_param) if k[:2] != '__'])

    res = execute_tests(run_param, testdirs, logfile)

    if res:
        sys.exit(1)


if __name__ == '__main__':
    opts, args = util.parser.parse_args()
    main(opts, args, RunParam)
