"""Implements the distutils command 'build_antlr'."""

from distutils.core import Command
from distutils.version import LooseVersion
from os import environ, listdir, walk
from os.path import basename, isfile, join, relpath, splitext
from re import compile
from shutil import which
from subprocess import run, PIPE, STDOUT
from typing import List

from antlr_distutils import __path__


class AntlrGrammar(object):
    """ Basic information about an ANTLR grammar file.

    For generation of ANTLR based parsers basic information about the grammar like imports is necessary. This
    information and the functionality to retrieve this information out of a grammar file is placed in this class.
    """

    def __init__(self, path):
        """Initializes a new AntlrGrammar object.

        :param path: path to grammar file
        """
        # By convention grammar name is always equal to file name
        self.name = splitext(basename(path))[0]
        self.path = path
        self.imports = self._read_imports()

    def _read_imports(self):
        """ Reads all imported grammars out of grammar file.

        :return: a list of imported grammars
        """
        import_stmt_regex = compile('import(.*);')

        try:
            with open(self.path) as f:
                match = import_stmt_regex.search(f.read())
                if match:
                    imported_grammars = match.group(1)
                    return [s.strip() for s in imported_grammars.split(',')]
                else:
                    return None
        except IOError:
            print("Can't read grammar", self.path)
            return None


# noinspection PyPep8Naming,PyAttributeOutsideInit
class build_antlr(Command):
    """ A distutils command for generating ANTLR based parsers.

    An extra command for distutils to generate ANTLR based parsers, lexers, listeners and visitors. The build_antlr
    command wraps the Java based generator provided by ANTLR developers. It searches for all grammar files and generates
    a python package containing a modules specified in the user options. Please keep in mind that only grammars are
    generated which aren't included by other grammars. This prevents generation of shared content like common terminals.

    :cvar _MIN_JAVA_VERSION: Minimal version of java required by ANTLR
    :cvar _EXT_LIB_DIR: Relative path to external libs directory
    :cvar _GRAMMAR_FILE_EXT: File extension of ANTLR grammars
    :cvar description: Description of build_antlr command
    :cvar user_options: Options which can be passed by the user
    :cvar boolean_options: Subset of user options which are binary
    :cvar negative_opt: Dictionary of user options which exclude each other
    """

    _MIN_JAVA_VERSION = '1.6.0'

    _EXT_LIB_DIR = 'lib'

    _GRAMMAR_FILE_EXT = 'g4'

    description = 'generate a parser based on ANTLR'

    user_options = [
        ('build-lib=', 'd', "directory to \"build\" (copy) to"),
        ('listener', None, "generate parse tree listener [default]"),
        ('no-listener', None, "don't generate parse tree listener"),
        ('visitor', None, "generate parse tree visitor"),
        ('no-visitor', None, "don't generate parse tree visitor [default]")
    ]

    boolean_options = ['listener', 'no-listener', 'visitor', 'no-visitor']

    # TODO: check if negative options are working
    negative_opt = {'no-listener': 'listener', 'no-visitor': 'visitor'}

    def initialize_options(self):
        """Sets default values for all the options that this command supports. Note that these defaults may be
        overridden by other commands, by the setup script, by config files, or by the command-line.
        """
        self.build_lib = None
        self.listener = None
        self.visitor = None

    def finalize_options(self):
        """Sets final values for all the options that this command supports. This is always called as late as possible,
        ie. after any option assignments from the command-line or from other commands have been done.
        """
        # Find out the build directories, ie. where to install from.
        self.set_undefined_options('build', ('build_lib', 'build_lib'))

        if self.listener is None:
            self.listener = True

        if self.visitor is None:
            self.visitor = True

    def _find_java(self) -> str:
        """Searches for a working Java Runtime Environment (JRE) set in JAVA_HOME or PATH environment variables. A JRE
        located in JAVA_HOME will be preferred.

        :return: a path to a working JRE or None if no JRE was found
        """
        # First check if a working Java is set in JAVA_HOME
        if 'JAVA_HOME' in environ:
            java_bin_dir = join(environ['JAVA_HOME'], 'bin')
            java_exe = which('java', path=java_bin_dir)
            if java_exe and self._validate_java(java_exe):
                return java_exe

        # If Java wasn't found in JAVA_HOME fallback to PATH
        java_exe = which('java', path=None)
        if java_exe and self._validate_java(java_exe):
            return java_exe

        # Java wasn't found on the system
        return None

    def _validate_java(self, executable: str) -> bool:
        """Validates a Java Runtime Environment (JRE) if it fulfills minimal version required by ANTLR

        :return: flag whether JRE is at minimum required version
        """
        result = run([executable, '-version'], stdout=PIPE, stderr=STDOUT, universal_newlines=True)

        if result.returncode == 0:
            version_regex = compile('\d+(.\d+){2}(_\d+)?')
            version_match = version_regex.search(result.stdout)

            if version_match:
                # Create normalized versions containing only valid chars
                validated_version = LooseVersion(version_match.group(0).replace('_', '.'))
                min_version = LooseVersion(self._MIN_JAVA_VERSION.replace('_', '.'))

                return validated_version >= min_version

        return False

    def _find_antlr(self) -> str:
        """Searches for ANTLR library at antlr-distutils install location.

        :return: a path to ANTLR library or None if library wasn't found
        """
        antlr_jar_path = join(__path__[0], self._EXT_LIB_DIR)
        antlr_jar_regex = compile('^antlr-\d+(.\d+){1,2}-complete.jar$')
        # Search for all _files_ matching regex in antlr_jar_path
        antlr_jar_matches = [element for element in listdir(antlr_jar_path) if isfile(join(antlr_jar_path, element)) and
                             antlr_jar_regex.match(element) is not None]
        if antlr_jar_matches:
            # If more than one antlr jar was found return path of the first one
            antlr_jar = join(antlr_jar_path, antlr_jar_matches[0])
            return antlr_jar
        else:
            return None

    def _find_grammars(self, base_path: str) -> List[AntlrGrammar]:
        """Searches for all ANTLR grammars in package source directory and returns a list of it. Only grammars which
        aren't included by other grammars are part of this list.

        :return: a list of all found ANTLR grammars
        """
        grammars = []
        for root, _, files in walk(base_path, followlinks=True):
            grammar_files = [f for f in files if f.endswith("." + self._GRAMMAR_FILE_EXT)]
            for fb in grammar_files:
                grammars.append(AntlrGrammar(relpath(join(root, fb), base_path)))

        # TODO: Filter all grammars which are included by other grammars

        return grammars

    def run(self):
        """Performs all tasks necessary to generate ANTLR based parsers for all found grammars. This process is
        controlled by the user options passed on the command line or set internally to default values.
        """
        java_exe = self._find_java()
        assert java_exe is not None, "No compatible JRE was found on the system."

        antlr_jar = self._find_antlr()
        assert antlr_jar is not None, "No antlr jar was found in directory for external libraries."

        self._grammars = self._find_grammars(".")

        # TODO: determine python package name and create __init__ file

        # TODO: create java call list based on user options

        # TODO: should stdout and stderror handled in a different way?
        for grammar in self._grammars:
            run([java_exe, '-jar', antlr_jar, '-o', self.build_lib, '-listener', '-visitor', '-Dlanguage=Python3',
                 '-lib', 'hello/dsl/common', grammar.path])
