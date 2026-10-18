"""Syntax checking with Python's own parser."""

import ast

from devassist.analyzers.base import Analyzer
from devassist.models import Finding

SYNTAX_ERROR_CODE = "syntax-error"
TOO_DEEP_MESSAGE = "too deeply nested for Python's parser"


class SyntaxAnalyzer(Analyzer):
    """Checks that the snippet is valid Python, using ``ast.parse``.

    Parsing builds a syntax tree without running anything, so it is safe on
    untrusted code.
    """

    name = "syntax"

    def analyze(self, code: str) -> list[Finding]:
        """Parse the code and report a syntax error if there is one.

        Args:
            code: The Python source to check.

        Returns:
            One finding for the first syntax error, or an empty list. Code
            nested so deeply that the parser runs out of recursion counts as a
            syntax error, since Python itself couldn't compile it either. How
            deep that is depends on the OS: about 3,000 chained ``+`` on Windows.
        """
        try:
            ast.parse(code)
        except SyntaxError as error:
            return [
                Finding(
                    tool=self.name,
                    code=SYNTAX_ERROR_CODE,
                    message=error.msg,
                    line=error.lineno or 1,
                )
            ]
        except RecursionError:
            return [
                Finding(tool=self.name, code=SYNTAX_ERROR_CODE, message=TOO_DEEP_MESSAGE, line=1)
            ]
        return []
