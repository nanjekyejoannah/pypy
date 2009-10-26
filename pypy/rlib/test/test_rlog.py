from pypy.rlib import rlog
from pypy.rlib.rarithmetic import intmask
from pypy.tool.udir import udir


def test_log_direct():
    messages = []
    class MyLog:
        def Aa(self, msg):
            messages.append(msg)
    previous = rlog._log
    try:
        rlog._log = MyLog()
        rlog.debug_log("Aa", "hello %(foo)d %(bar)d", foo=5, bar=7)
        assert messages == ["hello 5 7"]
    finally:
        rlog._log = previous

def test_logcategory():
    message = "abc%(foo)ddef%(bar)sghi"
    cat = rlog.LogCategory("Aa", message, 17)
    assert cat.category == "Aa"
    assert cat.message == message
    assert cat.index == 17
    assert cat.entries == [('foo', 'd'), ('bar', 's')]


class MyLogWriter(rlog.AbstractLogWriter):
    _path = udir.join('test_rlog.logwriter')

    def get_filename(self):
        return str(self._path)
    def create_buffer(self):
        self.content = []
    def write_int(self, n):
        assert isinstance(n, int)
        self.content.append(n)
    def write_str(self, s):
        assert isinstance(s, str)
        self.content.append(s)

def test_logwriter():
    class FakeCategory:
        def __init__(self, index, category, message):
            self.index = index
            self.category = category
            self.message = message
    #
    logwriter = MyLogWriter()
    cat5 = FakeCategory(5, "F5", "foobar")
    cat7 = FakeCategory(7, "F7", "baz")
    logwriter.add_entry(cat5)
    logwriter.add_entry(cat5)
    logwriter.add_entry(cat7)
    logwriter.add_entry(cat5)
    #
    assert logwriter.content == [
        ord('R'), ord('L'), ord('o'), ord('g'), ord('\n'), -1, 0,
        0, 5, "F5", "foobar",
        5,
        5,
        0, 7, "F7", "baz",
        7,
        5]

def test_logcategory_call():
    message = "abc%(foo)ddef%(bar)sghi"
    cat = rlog.LogCategory("Aa", message, 17)
    logwriter = MyLogWriter()
    call = cat.gen_call(logwriter)
    call(515, "hellooo")
    call(2873, "woooooorld")
    #
    assert logwriter.content == [
        ord('R'), ord('L'), ord('o'), ord('g'), ord('\n'), -1, 0,
        0, 17, "Aa", message,
        17, 515, "hellooo",
        17, 2873, "woooooorld"]


class TestLLLogWriter:
    COUNTER = 0

    def open(self):
        path = udir.join('test_rlog.lllogwriter%d' % TestLLLogWriter.COUNTER)
        self.path = path
        TestLLLogWriter.COUNTER += 1
        #
        class MyLLLogWriter(rlog.LLLogWriter):
            def get_filename(self):
                return str(path)
        #
        logwriter = MyLLLogWriter()
        logwriter.open_file()
        return logwriter

    def read_uint(self, f):
        shift = 0
        result = 0
        lastbyte = ord(f.read(1))
        while lastbyte & 0x80:
            result |= ((lastbyte & 0x7F) << shift)
            shift += 7
            lastbyte = ord(f.read(1))
        result |= (lastbyte << shift)
        return result

    def check(self, expected):
        f = self.path.open('rb')
        f.seek(0, 2)
        totalsize = f.tell()
        f.seek(0, 0)
        header = f.read(5)
        assert header == 'RLog\n'
        for expect in [-1, 0] + expected:
            if isinstance(expect, int):
                result = self.read_uint(f)
                assert intmask(result) == expect
            elif isinstance(expect, str):
                length = self.read_uint(f)
                assert length < totalsize
                got = f.read(length)
                assert got == expect
            else:
                assert 0, expect
        moredata = f.read(10)
        assert not moredata

    def test_write_int(self):
        logwriter = self.open()
        for i in range(logwriter.BUFSIZE):
            logwriter.write_int(i)
        logwriter._close()
        self.check(range(logwriter.BUFSIZE))
        assert logwriter.writecount <= 3

    def test_write_str(self):
        logwriter = self.open()
        slist = map(str, range(logwriter.BUFSIZE))
        for s in slist:
            logwriter.write_str(s)
        logwriter._close()
        self.check(slist)
        assert logwriter.writecount <= 14

    def test_write_mixed(self):
        logwriter = self.open()
        xlist = []
        for i in range(logwriter.BUFSIZE):
            if i & 1:
                i = str(i)
            xlist.append(i)
        for x in xlist:
            if isinstance(x, int):
                logwriter.write_int(x)
            else:
                logwriter.write_str(x)
        logwriter._close()
        self.check(xlist)
        assert logwriter.writecount <= 7

    def test_write_long_str(self):
        logwriter = self.open()
        slist = ['abcdefg' * n for n in [10, 100, 1000, 10000]]
        for s in slist:
            logwriter.write_str(s)
        logwriter._close()
        self.check(slist)
        assert logwriter.writecount <= 9
