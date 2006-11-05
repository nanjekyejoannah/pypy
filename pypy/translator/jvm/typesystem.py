"""
Definition and some basic translations between PyPy ootypesystem and
JVM type system.

Here are some tentative non-obvious decisions:

Signed scalar types mostly map as is.  

Unsigned scalar types are a problem; the basic idea is to store them
as signed values, but execute special code when working with them.  Another
option would be to use classes, or to use the "next larger" type and remember to use appropriate modulos.  The jury is out on
this.  Another idea would be to add a variant type system that does
not have unsigned values, and write the required helper and conversion
methods in RPython --- then it could be used for multiple backends.

Python strings are mapped to byte arrays, not Java Strings, since
Python strings are really sets of bytes, not unicode code points.
Jury is out on this as well; this is not the approach taken by cli,
for example.

Python Unicode strings, on the other hand, map directly to Java Strings.

WeakRefs can hopefully map to Java Weak References in a straight
forward fashion.

Collections can hopefully map to Java collections instances.  Note
that JVM does not have an idea of generic typing at its lowest level
(well, they do have signature attributes, but those don't really count
for much).

"""
from pypy.rpython.lltypesystem import lltype, llmemory
from pypy.rpython.ootypesystem import ootype
from pypy.translator.jvm.option import getoption
from pypy.translator.jvm.log import log

# ___________________________________________________________________________
# Type Descriptors
#
# Internal representations of types for the JVM.  Generally speaking,
# only the generator code should deal with these and even it tries to
# avoid them except write before dumping to the output file.

class JvmTypeDescriptor(str):
    """
    An internal class representing JVM type descriptors, which are
    essentially Java's short hand for types.  This is the lowest level
    of our representation for types and are mainly used when defining
    the types of fields or arguments to methods.  The grammar for type
    descriptors can be read about here:
    
    http://java.sun.com/docs/books/vmspec/2nd-edition/html/ClassFile.doc.html

    We use this class also to represent method descriptors, which define
    a set of argument and return types.
    """
    def is_scalar(self):
        return self[0] != 'L' and self[0] != '['
    def is_reference(self):
        return not self.is_scalar()
    def is_array(self):
        return self[0] == '['
    def is_method(self):
        return self[0] == '('
    def class_name(self):
        """ Converts a descriptor like Ljava/lang/Object; to
        full class name java.lang.Object """
        return self.int_class_name().replace('/','.')
    def int_class_name(self):
        """ Converts a descriptor like Ljava/lang/Object; to
        internal class name java/lang/Object """
        assert self[0] == 'L' and self[-1] == ';'
        return self[1:-1]
    def type_width(self):
        """ Returns number of JVM words this type takes up.  JVM words
        are a theoretically abstract quantity that basically
        represents 32 bits; so most types are 1, but longs and doubles
        are 2. """
        if self[0] == 'J' or self[0] == 'D':
            return 2
        return 1

# JVM type functions

def desc_for_array_of(jdescr):
    """ Returns a JvmType representing an array of 'jtype', which must be
    another JvmType """
    assert isinstance(jdescr, JvmTypeDescriptor)
    return JvmTypeDescriptor('['+jdescr)

def desc_for_class(classnm):
    """ Returns a JvmType representing a particular class 'classnm', which
    should be a fully qualified java class name (i.e., 'java.lang.String') """
    return JvmTypeDescriptor('L%s;' % classnm.replace('.','/'))

def desc_for_method(argtypes, rettype):
    """ A Java method has a descriptor, which is a string specified
    its argument and return types.  This function converts a list of
    argument types (JvmTypes) and the return type (also a JvmType),
    into one of these descriptor strings. """
    return JvmTypeDescriptor("(%s)%s" % ("".join(argtypes), rettype))

# ______________________________________________________________________
# Basic JVM Types

class JvmType(object):
    """
    The JvmType interface defines the interface for type objects
    that we return in the database in various places.
    """
    def __init__(self, descriptor):
        """ 'descriptor' should be a jvm.generator.JvmTypeDescriptor object
        for this type """
        self.descriptor = descriptor  # public
        self.name = None              # public, string like "java.lang.Object"
                                      # (None for scalars and arrays)
    def lookup_field(self, fieldnm):
        """ Returns a jvm.generator.Field object representing the field
        with the given name, or raises KeyError if that field does not
        exist on this type. """
        raise NotImplementedException
    def lookup_method(self, methodnm):
        """ Returns a jvm.generator.Method object representing the method
        with the given name, or raises KeyError if that field does not
        exist on this type. """
        raise NotImplementedException

    def __repr__(self):
        return "%s<%s>" % (self.__class__.__name__, self.descriptor)

class JvmScalarType(JvmType):
    """
    Subclass used for all scalar type instances.
    """
    def __init__(self, descrstr):
        JvmType.__init__(self, JvmTypeDescriptor(descrstr))
    def lookup_field(self, fieldnm):
        raise KeyError(fieldnm)        # Scalar objects have no fields
    def lookup_method(self, methodnm): 
        raise KeyError(methodnm)       # Scalar objects have no methods

jVoid = JvmScalarType('V')
jInt = JvmScalarType('I')
jLong = JvmScalarType('J')
jBool = JvmScalarType('Z')
jDouble = JvmScalarType('D')
jByte = JvmScalarType('B')
jChar = JvmScalarType('C')
class JvmClassType(JvmType):
    """
    Base class used for all class instances.  Kind of an abstract class;
    instances of this class do not support field or method lookup and
    only work to obtain the descriptor.  We use it on occasion for classes
    like java.lang.Object etc.
    """
    def __init__(self, classnm):
        JvmType.__init__(self, desc_for_class(classnm))
        self.name = classnm # public String, like 'java.lang.Object'
    def lookup_field(self, fieldnm):
        raise KeyError(fieldnm) # we treat as opaque type
    def lookup_method(self, methodnm):
        raise KeyError(fieldnm) # we treat as opaque type

jThrowable = JvmClassType('java.lang.Throwable')
jObject = JvmClassType('java.lang.Object')
jString = JvmClassType('java.lang.String')
jArrayList = JvmClassType('java.util.ArrayList')
jHashMap = JvmClassType('java.util.HashMap')
jIterator = JvmClassType('java.util.Iterator')
jClass = JvmClassType('java.lang.Class')
jStringBuilder = JvmClassType('java.lang.StringBuilder')
jPrintStream = JvmClassType('java.io.PrintStream')
jMath = JvmClassType('java.lang.Math')
jList = JvmClassType('java.util.List')
jPyPy = JvmClassType('pypy.PyPy')
jPyPyConst = JvmClassType('pypy.Constant')
jPyPyMain = JvmClassType('pypy.Main')

class JvmArrayType(JvmType):
    """
    Subclass used for all array instances.
    """
    def __init__(self, elemtype):
        JvmType.__init__(self, desc_for_array_of(elemtype.descriptor))
        self.element_type = elemtype
    def lookup_field(self, fieldnm):
        raise KeyError(fieldnm)  # TODO adjust interface to permit opcode here
    def lookup_method(self, methodnm): 
        raise KeyError(methodnm) # Arrays have no methods
    
jByteArray = JvmArrayType(jByte)
jObjectArray = JvmArrayType(jObject)
jStringArray = JvmArrayType(jString)


