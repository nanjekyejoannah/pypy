from pypy.conftest import gettestobjspace
from pypy.conftest import option
from pypy.rpython.tool.rffi_platform import CompilationError
import py

class OracleNotConnectedTestBase(object):

    @classmethod
    def setup_class(cls):
        try:
            from pypy.module.oracle import roci
        except ImportError:
            py.test.skip("Oracle client not available")

        space = gettestobjspace(usemodules=('oracle',))
        cls.space = space
        space.setitem(space.builtin.w_dict, space.wrap('oracle'),
                      space.getbuiltinmodule('cx_Oracle'))
        oracle_connect = option.oracle_connect
        if not oracle_connect:
            py.test.skip(
                "Please set --oracle-connect to a valid connect string")
        usrpwd, tnsentry = oracle_connect.rsplit('@', 1)
        username, password = usrpwd.split('/', 1)
        cls.w_username = space.wrap(username)
        cls.w_password = space.wrap(password)
        cls.w_tnsentry = space.wrap(tnsentry)

class OracleTestBase(OracleNotConnectedTestBase):
    @classmethod
    def setup_class(cls):
        super(OracleTestBase, cls).setup_class()
        cls.w_cnx = cls.space.appexec(
            [cls.w_username, cls.w_password, cls.w_tnsentry],
            """(username, password, tnsentry):
                import cx_Oracle
                return cx_Oracle.connect(username, password, tnsentry)
            """)

    def teardown_class(cls):
        cls.space.call_method(cls.w_cnx, "close")

class AppTestConnection(OracleNotConnectedTestBase):

    def teardown_method(self, func):
        if hasattr(self, 'cnx'):
            self.cnx.close()

    def test_connect(self):
        self.cnx = oracle.connect(self.username, self.password,
                                  self.tnsentry, threaded=True)
        assert self.cnx.username == self.username
        assert self.cnx.password == self.password
        assert self.cnx.tnsentry == self.tnsentry
        assert isinstance(self.cnx.version, str)

    def test_singleArg(self):
        self.cnx = oracle.connect("%s/%s@%s" % (self.username, self.password,
                                                self.tnsentry))
        assert self.cnx.username == self.username
        assert self.cnx.password == self.password
        assert self.cnx.tnsentry == self.tnsentry

    def test_connect_badPassword(self):
        raises(oracle.DatabaseError, oracle.connect,
               self.username, self.password + 'X', self.tnsentry)

    def test_connect_badConnectString(self):
        raises(oracle.DatabaseError, oracle.connect,
               self.username)
        raises(oracle.DatabaseError, oracle.connect,
               self.username + "@" + self.tnsentry)
        raises(oracle.DatabaseError, oracle.connect,
               self.username + "@" + self.tnsentry + "/" + self.password)
        
    def test_exceptionOnClose(self):
        connection = oracle.connect(self.username, self.password,
                                    self.tnsentry)
        connection.close()
        raises(oracle.InterfaceError, connection.rollback)

    def test_makedsn(self):
        formatString = ("(DESCRIPTION=(ADDRESS_LIST=(ADDRESS=(PROTOCOL=TCP)"
                        "(HOST=%s)(PORT=%d)))(CONNECT_DATA=(SID=%s)))")
        args = ("hostname", 1521, "TEST")
        result = oracle.makedsn(*args)
        assert result == formatString % args

    def test_rollbackOnClose(self):
        self.cnx = oracle.connect(self.username, self.password,
                self.tnsentry)
        cursor = self.cnx.cursor()
        try:
            cursor.execute("drop table pypy_test_temp")
        except oracle.DatabaseError:
            pass
        cursor.execute("create table pypy_test_temp (n number)")
    
        otherConnection = oracle.connect(self.username, self.password,
                self.tnsentry)
        otherCursor = otherConnection.cursor()
        otherCursor.execute("insert into pypy_test_temp (n) values (1)")
        otherConnection.close()
        cursor.execute("select count(*) from pypy_test_temp")
        count, = cursor.fetchone()
        assert count == 0


