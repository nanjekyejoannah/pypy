import sys
import py
from rpython.rtyper.lltypesystem import lltype, rffi
from rpython.jit.metainterp.optimizeopt.optimizer import Optimizer, Optimization
from rpython.jit.metainterp.optimizeopt.util import make_dispatcher_method
from rpython.jit.metainterp.optimizeopt.dependency import DependencyGraph
from rpython.jit.metainterp.resoperation import rop
from rpython.jit.metainterp.resume import Snapshot
from rpython.rlib.debug import debug_print, debug_start, debug_stop
from rpython.jit.metainterp.jitexc import JitException

class NotAVectorizeableLoop(JitException):
    def __str__(self):
        return 'NotAVectorizeableLoop()'

def optimize_vector(metainterp_sd, jitdriver_sd, loop, optimizations):
    opt = VectorizingOptimizer(metainterp_sd, jitdriver_sd, loop, optimizations)
    try:
        opt.propagate_all_forward()
        # TODO
        def_opt = Optimizer(metainterp_sd, jitdriver_sd, loop, optimizations)
        def_opt.propagate_all_forward()
    except NotAVectorizeableLoop:
        # vectorization is not possible, propagate only normal optimizations
        def_opt = Optimizer(metainterp_sd, jitdriver_sd, loop, optimizations)
        def_opt.propagate_all_forward()

class VectorizingOptimizer(Optimizer):
    """ Try to unroll the loop and find instructions to group """

    def __init__(self, metainterp_sd, jitdriver_sd, loop, optimizations):
        Optimizer.__init__(self, metainterp_sd, jitdriver_sd, loop, optimizations)
        self.vec_info = LoopVectorizeInfo(self)
        self.memory_refs = []
        self.dependency_graph = None
        self.first_debug_merge_point = False
        self.last_debug_merge_point = None
        self.pack_set = None

    def emit_unrolled_operation(self, op):
        if op.getopnum() == rop.DEBUG_MERGE_POINT:
            self.last_debug_merge_point = op
            if not self.first_debug_merge_point:
                self.first_debug_merge_point = True
            else:
                return False
        self._last_emitted_op = op
        self._newoperations.append(op)
        return True

    def _rename_arguments_ssa(self, rename_map, label_args, jump_args):
        # fill the map with the renaming boxes. keys are boxes from the label
        # values are the target boxes.

        # it is assumed that #label_args == #jump_args
        for i in range(len(label_args)):
            la = label_args[i]
            ja = jump_args[i]
            if la != ja:
                rename_map[la] = ja

    def unroll_loop_iterations(self, loop, unroll_factor):
        """ Unroll the loop X times. Unroll_factor of 0 = no unrolling,
        1 once, ...
        """
        op_count = len(loop.operations)

        label_op = loop.operations[0]
        jump_op = loop.operations[op_count-1]

        self.vec_info.track_memory_refs = True

        self.emit_unrolled_operation(label_op)

        # TODO use the new optimizer structure (branch of fijal currently)
        label_op_args = [self.getvalue(box).get_key_box() for box in label_op.getarglist()]
        values = [self.getvalue(box) for box in label_op.getarglist()]

        operations = []
        for i in range(1,op_count-1):
            op = loop.operations[i].clone()
            operations.append(op)
            self.emit_unrolled_operation(op)
            self.vec_info.inspect_operation(op)
        jump_op_args = jump_op.getarglist()

        rename_map = {}
        for i in range(0, unroll_factor):
            # for each unrolling factor the boxes are renamed.
            self._rename_arguments_ssa(rename_map, label_op_args, jump_op_args)
            for op in operations:
                copied_op = op.clone()

                if copied_op.result is not None:
                    # every result assigns a new box, thus creates an entry
                    # to the rename map.
                    new_assigned_box = copied_op.result.clonebox()
                    rename_map[copied_op.result] = new_assigned_box
                    copied_op.result = new_assigned_box

                args = copied_op.getarglist()
                for i, arg in enumerate(args):
                    try:
                        value = rename_map[arg]
                        copied_op.setarg(i, value)
                    except KeyError:
                        pass

                # not only the arguments, but also the fail args need
                # to be adjusted. rd_snapshot stores the live variables
                # that are needed to resume.
                if copied_op.is_guard():
                    new_snapshot = self.clone_snapshot(copied_op.rd_snapshot,
                                                       rename_map)
                    copied_op.rd_snapshot = new_snapshot

                self.emit_unrolled_operation(copied_op)
                self.vec_info.inspect_operation(copied_op)

            # the jump arguments have been changed
            # if label(iX) ... jump(i(X+1)) is called, at the next unrolled loop
            # must look like this: label(i(X+1)) ... jump(i(X+2))

            args = jump_op.getarglist()
            for i, arg in enumerate(args):
                try:
                    value = rename_map[arg]
                    jump_op.setarg(i, value)
                except KeyError:
                    pass
            # map will be rebuilt, the jump operation has been updated already
            rename_map.clear()

        if self.last_debug_merge_point is not None:
            self._last_emitted_op = self.last_debug_merge_point
            self._newoperations.append(self.last_debug_merge_point)
        self.emit_unrolled_operation(jump_op)

    def clone_snapshot(self, snapshot, rename_map):
        # snapshots are nested like the MIFrames
        if snapshot is None:
            return None
        boxes = snapshot.boxes
        new_boxes = boxes[:]
        for i,box in enumerate(boxes):
            try:
                value = rename_map[box]
                new_boxes[i] = value
            except KeyError:
                pass

        snapshot = Snapshot(self.clone_snapshot(snapshot.prev, rename_map),
                            new_boxes)
        return snapshot

    def _gather_trace_information(self, loop, track_memref = False):
        self.vec_info.track_memory_refs = track_memref
        for i,op in enumerate(loop.operations):
            self.vec_info.inspect_operation(op)

    def get_unroll_count(self):
        """ This is an estimated number of further unrolls """
        # this optimization is not opaque, and needs info about the CPU
        byte_count = self.vec_info.smallest_type_bytes
        if byte_count == 0:
            return 0
        simd_vec_reg_bytes = 16 # TODO get from cpu
        unroll_factor = simd_vec_reg_bytes // byte_count
        return unroll_factor-1 # it is already unrolled once

    def propagate_all_forward(self):

        self.clear_newoperations()

        self._gather_trace_information(self.loop)

        byte_count = self.vec_info.smallest_type_bytes
        if byte_count == 0:
            # stop, there is no chance to vectorize this trace
            raise NotAVectorizeableLoop()

        unroll_factor = self.get_unroll_count()

        self.unroll_loop_iterations(self.loop, unroll_factor)

        self.loop.operations = self.get_newoperations();
        self.clear_newoperations();

        self.build_dependency_graph()
        self.find_adjacent_memory_refs()

    def build_dependency_graph(self):
        self.dependency_graph = DependencyGraph(self.loop.operations)

    def find_adjacent_memory_refs(self):
        """ the pre pass already builds a hash of memory references and the
        operations. Since it is in SSA form there are no array indices.
        If there are two array accesses in the unrolled loop
        i0,i1 and i1 = int_add(i0,c), then i0 = i0 + 0, i1 = i0 + 1.
        They are represented as a linear combination: i*c/d + e, i is a variable,
        all others are integers that are calculated in reverse direction"""
        loop = self.loop
        operations = loop.operations
        integral_mod = IntegralMod(self)
        for opidx,memref in self.vec_info.memory_refs.items():
            integral_mod.reset()
            while True:

                for dep in self.dependency_graph.instr_dependencies(opidx):
                    # this is a use, thus if dep is not a defintion
                    # it points back to the definition
                    # if memref.origin == dep.defined_arg and not dep.is_definition:
                    if memref.origin in dep.args:
                        # if is_definition is false the params is swapped
                        # idx_to attributes points to define
                        def_op = operations[dep.idx_from]
                        opidx = dep.idx_from
                        break
                else:
                    # this is an error in the dependency graph
                    raise RuntimeError("a variable usage does not have a " +
                             " definition. Cannot continue!")

                op = operations[opidx]
                if op.getopnum() == rop.LABEL:
                    break

                integral_mod.inspect_operation(def_op)
                if integral_mod.is_const_mod:
                    integral_mod.update_memory_ref(memref)
                else:
                    break

        self.pack_set = PackSet(self.dependency_graph, operations)
        memory_refs = self.vec_info.memory_refs.items()
        # initialize the pack set
        for a_opidx,a_memref in memory_refs:
            for b_opidx,b_memref in memory_refs:
                # instead of compare every possible combination and
                # exclue a_opidx == b_opidx only consider the ones
                # that point forward:
                if a_opidx < b_opidx:
                    if a_memref.is_adjacent_to(b_memref):
                        if self.pack_set.can_be_packed(a_memref, b_memref):
                            self.pack_set.packs.append(Pair(a_memref, b_memref))

    def vectorize_trace(self, loop):
        """ Implementation of the algorithm introduced by Larsen. Refer to
              '''Exploiting Superword Level Parallelism
                 with Multimedia Instruction Sets'''
            for more details.
        """

        for i,operation in enumerate(loop.operations):

            if operation.getopnum() == rop.RAW_LOAD:
                # TODO while the loop is unrolled, build memory accesses
                pass


        # was not able to vectorize
        return False

def isomorphic(l_op, r_op):
    """ Described in the paper ``Instruction-Isomorphism in Program Execution''.
    I think this definition is to strict. TODO -> find another reference
    For now it must have the same instruction type, the array parameter must be equal,
    and it must be of the same type (both size in bytes and type of array)
    .
    """
    if l_op.getopnum() == r_op.getopnum() and \
       l_op.getarg(0) == r_op.getarg(0):
        l_d = l_op.getdescr()
        r_d = r_op.getdescr()
        if l_d is not None and r_d is not None:
            if l_d.get_item_size_in_bytes() == r_d.get_item_size_in_bytes():
                if l_d.getflag() == r_d.getflag():
                    return True

        elif l_d is None and r_d is None:
            return True

    return False

class PackSet(object):

    def __init__(self, dependency_graph, operations):
        self.packs = []
        self.dependency_graph = dependency_graph
        self.operations = operations

    def can_be_packed(self, lh_ref, rh_ref):
        l_op = self.operations[lh_ref.op_idx]
        r_op = self.operations[lh_ref.op_idx]
        if isomorphic(l_op, r_op):
            if self.dependency_graph.independant(lh_ref.op_idx, rh_ref.op_idx):
                for pack in self.packs:
                    if pack.left == lh_ref or pack.right == rh_ref:
                        return False
                return True
        return False


class Pack(object):
    """ A pack is a set of n statements that are:
        * isomorphic
        * independant
        Statements are named operations in the code.
    """
    def __init__(self, ops):
        self.operations = ops

class Pair(Pack):
    """ A special Pack object with only two statements. """
    def __init__(self, left, right):
        assert isinstance(left, MemoryRef)
        assert isinstance(right, MemoryRef)
        self.left = left
        self.right = right
        Pack.__init__(self, [left, right])

    def __eq__(self, other):
        if isinstance(other, Pair):
            return self.left == other.left and \
                   self.right == other.right

class MemoryRef(object):
    def __init__(self, op_idx, array, origin, descr):
        self.op_idx = op_idx
        self.array = array
        self.origin = origin
        self.descr = descr
        self.coefficient_mul = 1
        self.coefficient_div = 1
        self.constant = 0

    def is_adjacent_to(self, other):
        """ this is a symmetric relation """
        match, off = self.calc_difference(other)
        if match:
            return off == 1 or off == -1
        return False

    def is_adjacent_after(self, other):
        """ the asymetric relation to is_adjacent_to """
        match, off = self.calc_difference(other)
        if match:
            return off == 1
        return False

    def __eq__(self, other):
        match, off = self.calc_difference(other)
        if match:
            return off == 0
        return False

    def __ne__(self, other):
        return not self.__eq__(other)


    def calc_difference(self, other):
        if self.array == other.array \
            and self.origin == other.origin:
            mycoeff = self.coefficient_mul // self.coefficient_div
            othercoeff = other.coefficient_mul // other.coefficient_div
            diff = other.constant - self.constant
            return mycoeff == othercoeff, diff
        return False, 0

    def __repr__(self):
        return 'MemoryRef(%s*(%s/%s)+%s)' % (self.origin, self.coefficient_mul,
                                            self.coefficient_div, self.constant)

class IntegralMod(object):
    """ Calculates integral modifications on an integer object.
    The operations must be provided in backwards direction and of one
    variable only. Call reset() to reuse this object for other variables.
    """

    def __init__(self, optimizer):
        self.optimizer = optimizer
        self.reset()

    def reset(self):
        self.is_const_mod = False
        self.coefficient_mul = 1
        self.coefficient_div = 1
        self.constant = 0
        self.used_box = None

    def _update_additive(self, i):
        return (i * self.coefficient_mul) / self.coefficient_div

    additive_func_source = """
    def operation_{name}(self, op):
        box_a0 = op.getarg(0)
        box_a1 = op.getarg(1)
        a0 = self.optimizer.getvalue(box_a0)
        a1 = self.optimizer.getvalue(box_a1)
        self.is_const_mod = True
        if a0.is_constant() and a1.is_constant():
            self.used_box = None
            self.constant += self._update_additive(box_a0.getint() {op} \
                                                      box_a1.getint())
        elif a0.is_constant():
            self.constant {op}= self._update_additive(box_a0.getint())
            self.used_box = box_a1
        elif a1.is_constant():
            self.constant {op}= self._update_additive(box_a1.getint())
            self.used_box = box_a0
        else:
            self.is_const_mod = False
    """
    exec py.code.Source(additive_func_source.format(name='INT_ADD', 
                                                    op='+')).compile()
    exec py.code.Source(additive_func_source.format(name='INT_SUB', 
                                                    op='-')).compile()
    del additive_func_source

    multiplicative_func_source = """
    def operation_{name}(self, op):
        box_a0 = op.getarg(0)
        box_a1 = op.getarg(1)
        a0 = self.optimizer.getvalue(box_a0)
        a1 = self.optimizer.getvalue(box_a1)
        self.is_const_mod = True
        if a0.is_constant() and a1.is_constant():
            # here these factor becomes a constant, thus it is
            # handled like any other additive operation
            self.used_box = None
            self.constant += self._update_additive(box_a0.getint() {cop} \
                                                      box_a1.getint())
        elif a0.is_constant():
            self.coefficient_{tgt} {op}= box_a0.getint()
            self.used_box = box_a1
        elif a1.is_constant():
            self.coefficient_{tgt} {op}= box_a1.getint()
            self.used_box = box_a0
        else:
            self.is_const_mod = False
    """
    exec py.code.Source(multiplicative_func_source.format(name='INT_MUL', 
                                                 op='*', tgt='mul',
                                                 cop='*')).compile()
    exec py.code.Source(multiplicative_func_source.format(name='INT_FLOORDIV',
                                                 op='*', tgt='div',
                                                 cop='/')).compile()
    exec py.code.Source(multiplicative_func_source.format(name='UINT_FLOORDIV',
                                                 op='*', tgt='div',
                                                 cop='/')).compile()
    del multiplicative_func_source
    

    def update_memory_ref(self, memref):
        #print("updating memory ref pre: ", memref)
        memref.constant = self.constant
        memref.coefficient_mul = self.coefficient_mul
        memref.coefficient_div = self.coefficient_div
        memref.origin = self.used_box
        #print("updating memory ref post: ", memref)

    def default_operation(self, operation):
        pass
integral_dispatch_opt = make_dispatcher_method(IntegralMod, 'operation_',
        default=IntegralMod.default_operation)
IntegralMod.inspect_operation = integral_dispatch_opt


class LoopVectorizeInfo(object):

    def __init__(self, optimizer):
        self.optimizer = optimizer
        self.smallest_type_bytes = 0
        self.memory_refs = {}
        self.track_memory_refs = False

    array_access_source = """
    def operation_{name}(self, op):
        descr = op.getdescr()
        if self.track_memory_refs:
            idx = len(self.optimizer._newoperations)-1
            self.memory_refs[idx] = \
                    MemoryRef(idx, op.getarg(0), op.getarg(1), op.getdescr())
        if not descr.is_array_of_pointers():
            byte_count = descr.get_item_size_in_bytes()
            if self.smallest_type_bytes == 0 \
               or byte_count < self.smallest_type_bytes:
                self.smallest_type_bytes = byte_count
    """
    exec py.code.Source(array_access_source.format(name='RAW_LOAD')).compile()
    exec py.code.Source(array_access_source.format(name='GETARRAYITEM_GC')).compile()
    exec py.code.Source(array_access_source.format(name='GETARRAYITEM_RAW')).compile()
    del array_access_source

    def default_operation(self, operation):
        pass
dispatch_opt = make_dispatcher_method(LoopVectorizeInfo, 'operation_',
        default=LoopVectorizeInfo.default_operation)
LoopVectorizeInfo.inspect_operation = dispatch_opt

