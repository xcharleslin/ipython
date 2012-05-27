# encoding: utf-8
"""Magic functions for InteractiveShell.
"""

#-----------------------------------------------------------------------------
#  Copyright (C) 2001 Janko Hauser <jhauser@zscout.de> and
#  Copyright (C) 2001 Fernando Perez <fperez@colorado.edu>
#  Copyright (C) 2008 The IPython Development Team

#  Distributed under the terms of the BSD License.  The full license is in
#  the file COPYING, distributed as part of this software.
#-----------------------------------------------------------------------------

#-----------------------------------------------------------------------------
# Imports
#-----------------------------------------------------------------------------
# Stdlib
import os
import re
import sys
import types
from getopt import getopt, GetoptError

# Our own
from IPython.config.configurable import Configurable
from IPython.core import oinspect
from IPython.core.error import UsageError
from IPython.core.prefilter import ESC_MAGIC
from IPython.external.decorator import decorator
from IPython.utils.ipstruct import Struct
from IPython.utils.process import arg_split
from IPython.utils.text import dedent
from IPython.utils.traitlets import Bool, Dict, Instance
from IPython.utils.warn import error, warn

#-----------------------------------------------------------------------------
# Globals
#-----------------------------------------------------------------------------

# A dict we'll use for each class that has magics, used as temporary storage to
# pass information between the @line/cell_magic method decorators and the
# @magics_class class decorator, because the method decorators have no
# access to the class when they run.  See for more details:
# http://stackoverflow.com/questions/2366713/can-a-python-decorator-of-an-instance-method-access-the-class

magics = dict(line={}, cell={})

magic_kinds = ('line', 'cell')
magic_spec = ('line', 'cell', 'line_cell')

#-----------------------------------------------------------------------------
# Utility classes and functions
#-----------------------------------------------------------------------------

class Bunch: pass


def on_off(tag):
    """Return an ON/OFF string for a 1/0 input. Simple utility function."""
    return ['OFF','ON'][tag]


def compress_dhist(dh):
    head, tail = dh[:-10], dh[-10:]

    newhead = []
    done = set()
    for h in head:
        if h in done:
            continue
        newhead.append(h)
        done.add(h)

    return newhead + tail


def needs_local_scope(func):
    """Decorator to mark magic functions which need to local scope to run."""
    func.needs_local_scope = True
    return func

#-----------------------------------------------------------------------------
# Class and method decorators for registering magics
#-----------------------------------------------------------------------------

def magics_class(cls):
    cls.registered = True
    cls.magics = dict(line = magics['line'],
                      cell = magics['cell'])
    magics['line'] = {}
    magics['cell'] = {}
    return cls


def record_magic(dct, mtype, mname, func):
    if mtype == 'line_cell':
        dct['line'][mname] = dct['cell'][mname] = func
    else:
        dct[mtype][mname] = func


def validate_type(magic_kind):
    if magic_kind not in magic_spec:
        raise ValueError('magic_kind must be one of %s, %s given' %
                         magic_kinds, magic_kind)


# The docstrings for the decorator below will be fairly similar for the two
# types (method and function), so we generate them here once and reuse the
# templates below.
_docstring_template = \
"""Decorate the given {0} as {1} magic.

The decorator can be used with or without arguments, as follows.

i) without arguments: it will create a {1} magic named as the {0} being
decorated::

    @deco
    def foo(...)

will create a {1} magic named `foo`.

ii) with one string argument: which will be used as the actual name of the
resulting magic::

    @deco('bar')
    def foo(...)

will create a {1} magic named `bar`.
"""

# These two are decorator factories.  While they are conceptually very similar,
# there are enough differences in the details that it's simpler to have them
# written as completely standalone functions rather than trying to share code
# and make a single one with convoluted logic.

def _method_magic_marker(magic_kind):
    """Decorator factory for methods in Magics subclasses.
    """

    validate_type(magic_kind)

    # This is a closure to capture the magic_kind.  We could also use a class,
    # but it's overkill for just that one bit of state.
    def magic_deco(arg):
        call = lambda f, *a, **k: f(*a, **k)

        if callable(arg):
            # "Naked" decorator call (just @foo, no args)
            func = arg
            name = func.func_name
            retval = decorator(call, func)
            record_magic(magics, magic_kind, name, name)
        elif isinstance(arg, basestring):
            # Decorator called with arguments (@foo('bar'))
            name = arg
            def mark(func, *a, **kw):
                record_magic(magics, magic_kind, name, func.func_name)
                return decorator(call, func)
            retval = mark
        else:
            raise ValueError("Decorator can only be called with "
                             "string or function")
        return retval

    # Ensure the resulting decorator has a usable docstring
    magic_deco.__doc__ = _docstring_template.format('method', magic_kind)
    return magic_deco


def _function_magic_marker(magic_kind):
    """Decorator factory for standalone functions.
    """
    validate_type(magic_kind)
    
    # This is a closure to capture the magic_kind.  We could also use a class,
    # but it's overkill for just that one bit of state.
    def magic_deco(arg):
        call = lambda f, *a, **k: f(*a, **k)

        # Find get_ipython() in the caller's namespace
        caller = sys._getframe(1)
        for ns in ['f_locals', 'f_globals', 'f_builtins']:
            get_ipython = getattr(caller, ns).get('get_ipython')
            if get_ipython is not None:
                break
        else:
            raise('Decorator can only run in context where `get_ipython` exists')

        ip = get_ipython()

        if callable(arg):
            # "Naked" decorator call (just @foo, no args)
            func = arg
            name = func.func_name
            ip.register_magic_function(func, magic_kind, name)
            retval = decorator(call, func)
        elif isinstance(arg, basestring):
            # Decorator called with arguments (@foo('bar'))
            name = arg
            def mark(func, *a, **kw):
                ip.register_magic_function(func, magic_kind, name)
                return decorator(call, func)
            retval = mark
        else:
            raise ValueError("Decorator can only be called with "
                             "string or function")
        return retval

    # Ensure the resulting decorator has a usable docstring
    ds = _docstring_template.format('function', magic_kind)

    ds += dedent("""
    Note: this decorator can only be used in a context where IPython is already
    active, so that the `get_ipython()` call succeeds.  You can therefore use
    it in your startup files loaded after IPython initializes, but *not* in the
    IPython configuration file itself, which is executed before IPython is
    fully up and running.  Any file located in the `startup` subdirectory of
    your configuration profile will be OK in this sense.
    """)
    
    magic_deco.__doc__ = ds
    return magic_deco


# Create the actual decorators for public use

# These three are used to decorate methods in class definitions
line_magic = _method_magic_marker('line')
cell_magic = _method_magic_marker('cell')
line_cell_magic = _method_magic_marker('line_cell')

# These three decorate standalone functions and perform the decoration
# immediately.  They can only run where get_ipython() works
register_line_magic = _function_magic_marker('line')
register_cell_magic = _function_magic_marker('cell')
register_line_cell_magic = _function_magic_marker('line_cell')

#-----------------------------------------------------------------------------
# Core Magic classes
#-----------------------------------------------------------------------------

class MagicsManager(Configurable):
    """Object that handles all magic-related functionality for IPython.
    """
    # Non-configurable class attributes

    # A two-level dict, first keyed by magic type, then by magic function, and
    # holding the actual callable object as value.  This is the dict used for
    # magic function dispatch
    magics = Dict

    # A registry of the original objects that we've been given holding magics.
    registry = Dict

    shell = Instance('IPython.core.interactiveshell.InteractiveShellABC')

    auto_magic = Bool

    _auto_status = [
        'Automagic is OFF, % prefix IS needed for line magics.',
        'Automagic is ON, % prefix IS NOT needed for line magics.']

    user_magics = Instance('IPython.core.magics.UserMagics')

    def __init__(self, shell=None, config=None, user_magics=None, **traits):

        super(MagicsManager, self).__init__(shell=shell, config=config,
                                           user_magics=user_magics, **traits)
        self.magics = dict(line={}, cell={})
        # Let's add the user_magics to the registry for uniformity, so *all*
        # registered magic containers can be found there.
        self.registry[user_magics.__class__.__name__] = user_magics

    def auto_status(self):
        """Return descriptive string with automagic status."""
        return self._auto_status[self.auto_magic]

    def lsmagic(self):
        """Return a dict of currently available magic functions.

        The return dict has the keys 'line' and 'cell', corresponding to the
        two types of magics we support.  Each value is a list of names.
        """
        return self.magics

    def register(self, *magic_objects):
        """Register one or more instances of Magics.
        """
        # Start by validating them to ensure they have all had their magic
        # methods registered at the instance level
        for m in magic_objects:
            if not m.registered:
                raise ValueError("Class of magics %r was constructed without "
                                 "the @register_macics class decorator")
            if type(m) is type:
                # If we're given an uninstantiated class
                m = m(self.shell)

            # Now that we have an instance, we can register it and update the
            # table of callables
            self.registry[m.__class__.__name__] = m
            for mtype in magic_kinds:
                self.magics[mtype].update(m.magics[mtype])

    def register_function(self, func, magic_kind='line', magic_name=None):
        """Expose a standalone function as magic function for ipython.
        """

        # Create the new method in the user_magics and register it in the
        # global table
        validate_type(magic_kind)
        magic_name = func.func_name if magic_name is None else magic_name
        setattr(self.user_magics, magic_name, func)
        record_magic(self.magics, magic_kind, magic_name, func)

    def define_magic(self, name, func):
        """Support for deprecated API.

        This method exists only to support the old-style definition of magics.
        It will eventually be removed.  Deliberately not documented further.
        """
        meth = types.MethodType(func, self.user_magics)
        setattr(self.user_magics, name, meth)
        record_magic(self.magics, 'line', name, meth)

# Key base class that provides the central functionality for magics.

class Magics(object):
    """Base class for implementing magic functions.

    Shell functions which can be reached as %function_name. All magic
    functions should accept a string, which they can parse for their own
    needs. This can make some functions easier to type, eg `%cd ../`
    vs. `%cd("../")`

    Classes providing magic functions need to subclass this class, and they
    MUST:

    - Use the method decorators `@line_magic` and `@cell_magic` to decorate
    individual methods as magic functions, AND

    - Use the class decorator `@magics_class` to ensure that the magic
    methods are properly registered at the instance level upon instance
    initialization.

    See :mod:`magic_functions` for examples of actual implementation classes.
    """
    # Dict holding all command-line options for each magic.
    options_table = None
    # Dict for the mapping of magic names to methods, set by class decorator
    magics = None
    # Flag to check that the class decorator was properly applied
    registered = False
    # Instance of IPython shell
    shell = None

    def __init__(self, shell):
        if not(self.__class__.registered):
            raise ValueError('Magics subclass without registration - '
                             'did you forget to apply @magics_class?')
        self.shell = shell
        self.options_table = {}
        # The method decorators are run when the instance doesn't exist yet, so
        # they can only record the names of the methods they are supposed to
        # grab.  Only now, that the instance exists, can we create the proper
        # mapping to bound methods.  So we read the info off the original names
        # table and replace each method name by the actual bound method.
        for mtype in magic_kinds:
            tab = self.magics[mtype]
            # must explicitly use keys, as we're mutating this puppy
            for magic_name in tab.keys():
                meth_name = tab[magic_name]
                if isinstance(meth_name, basestring):
                    tab[magic_name] = getattr(self, meth_name)

    def arg_err(self,func):
        """Print docstring if incorrect arguments were passed"""
        print 'Error in arguments:'
        print oinspect.getdoc(func)

    def format_latex(self, strng):
        """Format a string for latex inclusion."""

        # Characters that need to be escaped for latex:
        escape_re = re.compile(r'(%|_|\$|#|&)',re.MULTILINE)
        # Magic command names as headers:
        cmd_name_re = re.compile(r'^(%s.*?):' % ESC_MAGIC,
                                 re.MULTILINE)
        # Magic commands
        cmd_re = re.compile(r'(?P<cmd>%s.+?\b)(?!\}\}:)' % ESC_MAGIC,
                            re.MULTILINE)
        # Paragraph continue
        par_re = re.compile(r'\\$',re.MULTILINE)

        # The "\n" symbol
        newline_re = re.compile(r'\\n')

        # Now build the string for output:
        #strng = cmd_name_re.sub(r'\n\\texttt{\\textsl{\\large \1}}:',strng)
        strng = cmd_name_re.sub(r'\n\\bigskip\n\\texttt{\\textbf{ \1}}:',
                                strng)
        strng = cmd_re.sub(r'\\texttt{\g<cmd>}',strng)
        strng = par_re.sub(r'\\\\',strng)
        strng = escape_re.sub(r'\\\1',strng)
        strng = newline_re.sub(r'\\textbackslash{}n',strng)
        return strng

    def parse_options(self, arg_str, opt_str, *long_opts, **kw):
        """Parse options passed to an argument string.

        The interface is similar to that of getopt(), but it returns back a
        Struct with the options as keys and the stripped argument string still
        as a string.

        arg_str is quoted as a true sys.argv vector by using shlex.split.
        This allows us to easily expand variables, glob files, quote
        arguments, etc.

        Options:
          -mode: default 'string'. If given as 'list', the argument string is
          returned as a list (split on whitespace) instead of a string.

          -list_all: put all option values in lists. Normally only options
          appearing more than once are put in a list.

          -posix (True): whether to split the input line in POSIX mode or not,
          as per the conventions outlined in the shlex module from the
          standard library."""

        # inject default options at the beginning of the input line
        caller = sys._getframe(1).f_code.co_name
        arg_str = '%s %s' % (self.options_table.get(caller,''),arg_str)

        mode = kw.get('mode','string')
        if mode not in ['string','list']:
            raise ValueError,'incorrect mode given: %s' % mode
        # Get options
        list_all = kw.get('list_all',0)
        posix = kw.get('posix', os.name == 'posix')
        strict = kw.get('strict', True)

        # Check if we have more than one argument to warrant extra processing:
        odict = {}  # Dictionary with options
        args = arg_str.split()
        if len(args) >= 1:
            # If the list of inputs only has 0 or 1 thing in it, there's no
            # need to look for options
            argv = arg_split(arg_str, posix, strict)
            # Do regular option processing
            try:
                opts,args = getopt(argv,opt_str,*long_opts)
            except GetoptError,e:
                raise UsageError('%s ( allowed: "%s" %s)' % (e.msg,opt_str,
                                        " ".join(long_opts)))
            for o,a in opts:
                if o.startswith('--'):
                    o = o[2:]
                else:
                    o = o[1:]
                try:
                    odict[o].append(a)
                except AttributeError:
                    odict[o] = [odict[o],a]
                except KeyError:
                    if list_all:
                        odict[o] = [a]
                    else:
                        odict[o] = a

        # Prepare opts,args for return
        opts = Struct(odict)
        if mode == 'string':
            args = ' '.join(args)

        return opts,args

    def default_option(self, fn, optstr):
        """Make an entry in the options_table for fn, with value optstr"""

        if fn not in self.lsmagic():
            error("%s is not a magic function" % fn)
        self.options_table[fn] = optstr
