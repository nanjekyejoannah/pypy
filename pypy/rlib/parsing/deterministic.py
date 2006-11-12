import py

try:
    set
except NameError:
    from sets import Set as set, FrozenSet as frozenset

def compress_char_set(chars):
    chars = list(chars)
    chars.sort()
    result = [chars[0], 1]
    for a, b in zip(chars[:-1], chars[1:]):
        if ord(a) == ord(b) - 1:
            result.append(result.pop() + 1)
        else:
            result.append(b)
            result.append(1)
    real_result = []
    for i in range(len(result) // 2):
        real_result.append((result[i * 2], result[i * 2 + 1]))
    return real_result

class LexerError(Exception):
    def __init__(self, input, state, index, lineno=-1, columnno=-1):
        self.input = input
        self.state = state
        self.index = index
        self.lineno = lineno
        self.columnno = columnno
        self.args = (input, state, index, lineno, columnno)

    def nice_error_message(self, filename="<unknown>"):
        result = ["  File %s, line %s" % (filename, self.lineno)]
        result.append(self.input.splitlines()[self.lineno])
        result.append(" " * self.columnno + "^")
        result.append("LexerError")
        return "\n".join(result)

def make_nice_charset_repr(chars):
    charranges = compress_char_set(chars)
    result = []
    for a, num in charranges:
        if num == 1:
            result.append(a)
            if a == "-":
                result.append("\\-")
        else:
            result.append("%s-%s" % (repr(a)[1:-1], repr(chr(ord(a) + num - 1))[1:-1]))
    return "".join(result)

class DFA(object):
    def __init__(self, num_states=0, transitions=None, final_states=None,
                 unmergeable_states=None, names=None):
        self.num_states = 0
        if transitions is None:
            transitions = {}
        if final_states is None:
            final_states = set()
        if unmergeable_states is None:
            unmergeable_states = set()
        if names is None:
            names = []
        self.transitions = transitions
        self.final_states = final_states
        self.unmergeable_states = unmergeable_states
        self.names = names

    def __repr__(self):
        from pprint import pformat
        return "DFA%s" % (pformat((
            self.num_states, self.transitions, self.final_states,
            self.unmergeable_states, self.names)), )

    def add_state(self, name=None, final=False, unmergeable=False):
        state = self.num_states
        self.num_states += 1
        if final:
            self.final_states.add(state)
        if unmergeable:
            self.unmergeable_states.add(state)
        if name is None:
            name = str(state)
        self.names.append(name)
        return self.num_states - 1

    def __setitem__(self, (state, input), next_state):
        self.transitions[state, input] = next_state

    def __getitem__(self, (state, input)):
        return self.transitions[state, input]

    def __contains__(self, (state, input)):
        return (state, input) in self.transitions

    def get_all_chars(self):
        all_chars = set()
        for state, input in self.transitions:
            all_chars.add(input)
        return all_chars

    def optimize(self):
        all_chars = self.get_all_chars()
        # find mergeable
        non_final = frozenset(set(range(self.num_states)) - self.final_states -
                              self.unmergeable_states)
        final = frozenset(self.final_states - self.unmergeable_states)
        state_to_set = {}
        equivalence_sets = set()
        if non_final:
            equivalence_sets.add(non_final)
        if final:
            equivalence_sets.add(final)
        for state in range(self.num_states):
            if state in final:
                state_to_set[state] = final
            elif state in self.unmergeable_states:
                singleset = frozenset([state])
                state_to_set[state] = singleset
                equivalence_sets.add(singleset)
            else:
                state_to_set[state] = non_final
        assert len(equivalence_sets) <= self.num_states
        while len(equivalence_sets) < self.num_states:
            new_equivalence_sets = set()
            changed = False
            for equivalent in equivalence_sets:
                #print "checking", equivalent
                for char in all_chars:
                    targets = {}
                    for state in equivalent:
                        if (state, char) in self:
                            nextstate = self[state, char]
                            target = frozenset(state_to_set[nextstate])
                        else:
                            nextstate = None
                            target = None
                        targets.setdefault(target, set()).add(state)
                    if len(targets) != 1:
                        #print "\nsplitting %s with %r\ninto %s" % (equivalent, char, targets.values())
                        for target, newequivalent in targets.iteritems():
                            #print "   ", newequivalent
                            newequivalent = frozenset(newequivalent)
                            new_equivalence_sets.add(newequivalent)
                            for state in newequivalent:
                                state_to_set[state] = newequivalent
                            #print "   ", new_equivalence_sets
                        changed = True
                        break
                else:
                    new_equivalence_sets.add(equivalent)
            if not changed:
                break
            #print "end", equivalence_sets
            #print new_equivalence_sets
            equivalence_sets = new_equivalence_sets
        if len(equivalence_sets) == self.num_states:
            return False
        #print equivalence_sets
        # merging the states
        newnames = []
        newtransitions = {}
        newnum_states = len(equivalence_sets)
        newstates = list(equivalence_sets)
        newstate_to_index = {}
        newfinal_states = set()
        newunmergeable_states = set()
        for i, newstate in enumerate(newstates):
            newstate_to_index[newstate] = i
        # bring startstate into first slot
        startstateindex = newstate_to_index[state_to_set[0]]
        newstates[0], newstates[startstateindex] = newstates[startstateindex], newstates[0]
        newstate_to_index[newstates[0]] = 0
        newstate_to_index[newstates[startstateindex]] = startstateindex
        for i, newstate in enumerate(newstates):
            name = ", ".join([self.names[s] for s in newstate])
            for state in newstate:
                if state in self.unmergeable_states:
                    newunmergeable_states.add(i)
                    name = self.names[state]
                if state in self.final_states:
                    newfinal_states.add(i)
            newnames.append(name)
        for (state, char), nextstate in self.transitions.iteritems():
            newstate = newstate_to_index[state_to_set[state]]
            newnextstate = newstate_to_index[state_to_set[nextstate]]
            newtransitions[newstate, char] = newnextstate
        self.names = newnames
        self.transitions = newtransitions
        self.num_states = newnum_states
        self.final_states = newfinal_states
        self.unmergeable_states = newunmergeable_states
        return True

    def make_code(self):
        result = ["""
def recognize(input):
    i = 0
    state = 0
    while 1:
"""]
        state_to_chars = {}
        for (state, char), nextstate in self.transitions.iteritems():
            state_to_chars.setdefault(state, {}).setdefault(nextstate, set()).add(char)
        above = set()
        for state, nextstates in state_to_chars.iteritems():
            above.add(state)
            result.append("""\
        if state == %s:
            if i < len(input):
                char = input[i]
                i += 1
            else:""" % (state, ))
            if state in self.final_states:
                result.append("                return True")
            else:
                result.append("                break")
            elif_prefix = ""
            for nextstate, chars in nextstates.iteritems():
                final = nextstate in self.final_states
                compressed = compress_char_set(chars)
                if nextstate in above:
                    continue_prefix = "\n" + " " * 16 + "continue"
                else:
                    continue_prefix = ""
                for i, (a, num) in enumerate(compressed):
                    if num < 5:
                        for charord in range(ord(a), ord(a) + num):
                            result.append("""
            %sif char == %r:
                state = %s%s""" % (elif_prefix, chr(charord), nextstate, continue_prefix))
                            if not elif_prefix:
                                elif_prefix = "el"
                    else:
                        result.append("""
            %sif %r <= char <= %r:
                state = %s%s""" % (elif_prefix, a, chr(ord(a) + num - 1), nextstate, continue_prefix))
                        if not elif_prefix:
                            elif_prefix = "el"
            
            result.append(" " * 12 + "else:")
            result.append(" " * 16 + "break")
        #print state_to_chars.keys()
        for state in range(self.num_states):
            if state in state_to_chars:
                continue
            result.append("""\
        if state == %s:
            if i == len(input):
                return True
            else:
                break""" % (state, ))
        result.append("        break")
        result.append("    raise LexerError(input, state, i)")
        result = "\n".join(result)
        while "\n\n" in result:
            result = result.replace("\n\n", "\n")
        #print result
        exec py.code.Source(result).compile()
        return recognize
        
    def make_lexing_code(self):
        result = ["""
def recognize(runner, i):
    assert i >= 0
    input = runner.text
    state = 0
    while 1:
"""]
        state_to_chars = {}
        for (state, char), nextstate in self.transitions.iteritems():
            state_to_chars.setdefault(state, {}).setdefault(nextstate, set()).add(char)
        above = set()
        for state, nextstates in state_to_chars.iteritems():
            above.add(state)
            result.append("        if state == %s:" % (state, ))
            if state in self.final_states:
                result.append("            runner.last_matched_index = i - 1")
                result.append("            runner.last_matched_state = state")
            result.append("""\
            if i < len(input):
                char = input[i]
                i += 1
            else:
                runner.state = %s""" % (state, ))
            if state in self.final_states:
                result.append("                return i")
            else:
                result.append("                return ~i")
            elif_prefix = ""
            for nextstate, chars in nextstates.iteritems():
                final = nextstate in self.final_states
                compressed = compress_char_set(chars)
                if nextstate in above:
                    continue_prefix = "\n" + " " * 16 + "continue"
                else:
                    continue_prefix = ""
                for i, (a, num) in enumerate(compressed):
                    if num < 3:
                        for charord in range(ord(a), ord(a) + num):
                            result.append("""
            %sif char == %r:
                state = %s%s""" % (elif_prefix, chr(charord), nextstate, continue_prefix))
                            if not elif_prefix:
                                elif_prefix = "el"
                    else:
                        result.append("""
            %sif %r <= char <= %r:
                state = %s%s""" % (elif_prefix, a, chr(ord(a) + num - 1), nextstate, continue_prefix))
                        if not elif_prefix:
                            elif_prefix = "el"
            
            result.append(" " * 12 + "else:")
            result.append(" " * 16 + "break")
        #print state_to_chars.keys()
        for state in range(self.num_states):
            if state in state_to_chars:
                continue
            assert state in self.final_states
        result.append("""\
        runner.last_matched_state = state
        runner.last_matched_index = i - 1
        runner.state = state
        if i == len(input):
            return i
        else:
            return ~i
        break
    runner.state = state
    return ~i""")
        result = "\n".join(result)
        while "\n\n" in result:
            result = result.replace("\n\n", "\n")
        #print result
        exec py.code.Source(result).compile()
        return recognize

    def get_runner(self):
        return DFARunner(self)

    def make_nondeterministic(self):
        result = NFA()
        result.num_states = self.num_states
        result.names = self.names
        result.start_states = set([0])
        result.final_states = self.final_states.copy()
        for (state, input), nextstate in self.transitions.iteritems():
            result.add_transition(state, nextstate, input)
        return result

    def dot(self):
        result = ["graph G {"]
        for i in range(self.num_states):
            if i == 0:
                extra = ", color=red"
            else:
                extra = ""
            if i in self.final_states:
                shape = "octagon"
            else:
                shape = "box"
            result.append(
                'state%s [label="%s", shape=%s%s];' %
                    (i, repr(self.names[i]).replace("\\", "\\\\"), shape, extra))
        edges = {}
        for (state, input), next_state in self.transitions.iteritems():
            edges.setdefault((state, next_state), set()).add(input)
        for (state, next_state), inputs in edges.iteritems():
            inputs = make_nice_charset_repr(inputs)
            result.append('state%s -- state%s [label="%s", arrowhead=normal];' %
                          (state, next_state, repr(inputs).replace("\\", "\\\\")))
        result.append("}")
        return "\n".join(result)

    def view(self):
        from pypy.translator.tool.pygame import graphclient
        p = py.test.ensuretemp("automaton").join("temp.dot")
        dot = self.dot()
        p.write(dot)
        plainpath = p.new(ext="plain")
        try:
            py.process.cmdexec("neato -Tplain %s > %s" % (p, plainpath))
        except py.error.Error:
            py.process.cmdexec("fdp -Tplain %s > %s" % (p, plainpath))
        graphclient.display_dot_file(str(plainpath))

class DFARunner(object):
    def __init__(self, automaton):
        self.automaton = automaton
        self.state = 0

    def nextstate(self, char):
        self.state = self.automaton[self.state, char]
        return self.state
        
    def recognize(self, s):
        self.state = 0
        try:
            for char in s:
                self.nextstate(char)
        except KeyError:
            return False
        return self.state in self.automaton.final_states

class NFA(object):
    def __init__(self):
        self.num_states = 0
        self.names = []
        self.transitions = {}
        self.start_states = set()
        self.final_states = set()
        self.unmergeable_states = set()

    def add_state(self, name=None, start=False, final=False,
                  unmergeable=False):
        new_state = self.num_states
        self.num_states += 1
        if name is None:
            name = str(new_state)
        self.names.append(name)
        if start:
            self.start_states.add(new_state)
        if final:
            self.final_states.add(new_state)
        if unmergeable:
            self.unmergeable_states.add(new_state)
        return new_state

    def add_transition(self, state, next_state, input=None):
        subtransitions = self.transitions.setdefault(state, {})
        subtransitions.setdefault(input, set()).add(next_state)

    def get_next_states(self, state, char):
        result = set()
        sub_transitions = self.transitions.get(state, {})
        for e_state in self.epsilon_closure([state]):
            result.update(self.transitions.get(e_state, {}).get(char, set()))
        return result

    def epsilon_closure(self, states):
        result = set(states)
        stack = list(states)
        while stack:
            state = stack.pop()
            for next_state in self.transitions.get(state, {}).get(None, set()):
                if next_state not in result:
                    result.add(next_state)
                    stack.append(next_state)
        return result

    def make_deterministic(self, name_precedence=None):
        fda = DFA()
        set_to_state = {}
        stack = []
        def get_state(states):
            states = self.epsilon_closure(states)
            frozenstates = frozenset(states)
            if frozenstates in set_to_state:
                return set_to_state[frozenstates]
            if states == self.start_states:
                assert not set_to_state
            final = bool(
                filter(None, [state in self.final_states for state in states]))
            name = ", ".join([self.names[state] for state in states])
            if name_precedence is not None:
                name_index = len(name_precedence)
            unmergeable = False
            for state in states:
                #print state
                if state in self.unmergeable_states:
                    new_name = self.names[state]
                    if name_precedence is not None:
                        try:
                            index = name_precedence.index(new_name)
                        except ValueError:
                            index = name_index
                        #print new_name, index, name_precedence
                        if index < name_index:
                            name_index = index
                            name = new_name
                    else:
                        name = new_name
                    unmergeable = True
            result = set_to_state[frozenstates] = fda.add_state(
                name, final, unmergeable)
            stack.append((result, states))
            return result
        startstate = get_state(self.start_states)
        while stack:
            fdastate, ndastates = stack.pop()
            chars_to_states = {}
            for state in ndastates:
                sub_transitions = self.transitions.get(state, {})
                for char, next_states in sub_transitions.iteritems():
                    chars_to_states.setdefault(char, set()).update(next_states)
            for char, states in chars_to_states.iteritems():
                if char is None:
                    continue
                fda[fdastate, char] = get_state(states)
        return fda

    def update(self, other):
        mapping = {}
        for i, name in enumerate(other.names):
            new_state = self.add_state(name)
            mapping[i] = new_state
        for state, subtransitions in other.transitions.iteritems():
            new_state = mapping[state]
            new_subtransitions = self.transitions.setdefault(new_state, {})
            for input, next_states in subtransitions.iteritems():
                next_states = [mapping[i] for i in next_states]
                new_subtransitions.setdefault(input, set()).update(next_states)
        return mapping

    def view(self):
        from pypy.translator.tool.pygame import graphclient
        p = py.test.ensuretemp("automaton").join("temp.dot")
        dot = self.dot()
        p.write(dot)
        plainpath = p.new(ext="plain")
        try:
            try:
                py.process.cmdexec("neato -Tplain %s > %s" % (p, plainpath))
            except py.error.Error:
                py.process.cmdexec("fdp -Tplain %s > %s" % (p, plainpath))
        except py.error.Error:
            p.write(
                dot.replace("graph G {", "digraph G {").replace(" -- ", " -> "))
            py.process.cmdexec("dot -Tplain %s > %s" % (p, plainpath))
        graphclient.display_dot_file(str(plainpath))

    def dot(self):
        result = ["graph G {"]
        for i in range(self.num_states):
            if i in self.start_states:
                extra = ", color=red"
            else:
                extra = ""
            if i in self.final_states:
                peripheries = 2
                extra += ", shape=octagon"
            else:
                peripheries = 1
            result.append(
                'state%s [label="%s", peripheries=%s%s];' %
                    (i, self.names[i], peripheries, extra))
        for state, sub_transitions in self.transitions.iteritems():
            for input, next_states in sub_transitions.iteritems():
                for next_state in next_states:
                    result.append(
                        'state%s -- state%s [label="%s", arrowhead=normal];' %
                            (state, next_state, repr(input).replace("\\", "\\\\")))
        result.append("}")
        return "\n".join(result)

class SetNFARunner(object):
    def __init__(self, automaton):
        self.automaton = automaton
    
    def next_state(self, char):
        nextstates = set()
        for state in self.states:
            nextstates.update(self.automaton.get_next_states(state, char))
        return nextstates

    def recognize(self, s):
        self.states = self.automaton.start_states.copy()
        for char in s:
            nextstates = self.next_state(char)
            if not nextstates:
                return False
            self.states = nextstates
        for state in self.states:
            if state in self.automaton.final_states:
                return True
        return False

class BacktrackingNFARunner(object):
    def __init__(self, automaton):
        self.automaton = automaton
   
    def recognize(self, s):
        def recurse(i, state):
            if i == len(s):
                return state in self.automaton.final_states
            for next_state in self.automaton.get_next_states(state, s[i]):
                if recurse(i + 1, next_state):
                    return True
            return False
        for state in self.automaton.start_states:
            if recurse(0, state):
                return True
        return False
