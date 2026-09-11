"""Tree-sitter based Python parser — extracts functions, classes, methods
(including nested ones), module-level import statements, and symbol-level
REFERENCES (calls and attribute accesses) for the call/reference graph,
tolerating syntax errors.

Tree-sitter recovers from malformed input, so a file with a syntax error still
yields the symbols in its valid regions (flag set on the ParsedModule).

All node text is sliced from the raw BYTE buffer — tree-sitter offsets are
byte offsets, and slicing a decoded str by them would misalign on any
multi-byte UTF-8 content (box-drawing chars, CJK comments, emoji)."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

import tree_sitter_python
from tree_sitter import Language, Parser

from .symbols import (
    ImportInfo,
    Location,
    ParsedModule,
    Reference,
    Symbol,
    SymbolKind,
)

_IDENTIFIER_PATH_RE = re.compile(r"[A-Za-z_]\w*(\.[A-Za-z_]\w*)*")

_LANGUAGE = Language(tree_sitter_python.language())


class PythonParser:
    def __init__(self) -> None:
        self._parser = Parser(_LANGUAGE)
        self._raw = b""  # source buffer of the current parse

    def parse_file(self, path: str | Path, module_name: str | None = None) -> ParsedModule:
        """Parse one file. `module_name` is the repo-relative dotted module
        name (callers derive it via scanner.path_to_module) — the parser
        itself is path-agnostic and never guesses one from an absolute path."""
        path = Path(path)
        raw = path.read_bytes()
        tree = self._parser.parse(raw)
        self._raw = raw

        mod = ParsedModule(
            file_path=str(path),
            module_name=module_name,
            has_syntax_error=tree.root_node.has_error,
            content_hash=hashlib.sha256(raw).hexdigest(),
        )
        self._walk(tree.root_node, [], mod)
        return mod

    # ─── Traversal ───────────────────────────────────────────

    def _walk(self, node, scope: list[str], mod: ParsedModule) -> None:
        ntype = node.type

        if ntype == "decorated_definition":
            # `@decorator\ndef f(): ...` — descend into the actual definition;
            # decorators don't affect identity.
            child = node.child_by_field_name("definition")
            if child is not None:
                self._walk(child, scope, mod)
            return

        if ntype in ("function_definition", "class_definition"):
            self._extract_definition(node, scope, mod)
            body = node.child_by_field_name("body")
            name_node = node.child_by_field_name("name")
            if body is not None and name_node is not None:
                self._walk(body, scope + [self._text(name_node)], mod)
            return

        if ntype in ("import_statement", "import_from_statement"):
            # Module-level imports only (a conditional import inside an `if`
            # at module level still counts — walk-up stops at def/class/lambda).
            if self._is_module_level(node):
                self._extract_import(node, mod)
            return  # never descend into imports

        if ntype == "call":
            self._extract_reference(node, scope, mod, kind="call")
            # keep walking — arguments may contain nested calls
        elif ntype == "attribute":
            self._extract_reference(node, scope, mod, kind="attribute")
            return  # the attribute's parts are identifiers; nothing below
        elif ntype == "identifier":
            return  # a bare name is not a reference edge (too noisy)

        for child in node.children:
            self._walk(child, scope, mod)

    @staticmethod
    def _is_module_level(node) -> bool:
        cur = node.parent
        while cur is not None:
            if cur.type == "module":
                return True
            if cur.type in ("function_definition", "class_definition", "lambda"):
                return False
            cur = cur.parent
        return False

    # ─── Definitions ─────────────────────────────────────────

    def _extract_definition(self, node, scope: list[str], mod: ParsedModule) -> None:
        name_node = node.child_by_field_name("name")
        if name_node is None:
            return
        name = self._text(name_node)
        if mod.module_name:
            qualified = ".".join([mod.module_name, *scope, name])
        else:
            qualified = ".".join([*scope, name])

        if node.type == "class_definition":
            kind = SymbolKind.CLASS
        elif scope and self._inside_class_body(node.parent):
            kind = SymbolKind.METHOD
        else:
            kind = SymbolKind.FUNCTION

        mod.symbols.append(Symbol(
            name=name,
            kind=kind,
            location=Location(
                file_path=mod.file_path,
                start_line=node.start_point[0] + 1,
                end_line=node.end_point[0] + 1,
            ),
            qualified_name=qualified,
            signature=self._signature(node),
            docstring=self._docstring(node),
        ))

    @staticmethod
    def _inside_class_body(parent) -> bool:
        # The def's parent is the class body block; walk up until a def/class
        # boundary: a class before any function means this def is a method.
        cur = parent
        while cur is not None:
            if cur.type == "class_definition":
                return True
            if cur.type == "function_definition":
                return False
            cur = cur.parent
        return False

    def _signature(self, node) -> str:
        body = node.child_by_field_name("body")
        end = body.start_byte if body is not None else node.end_byte
        text = self._text(node)[:end - node.start_byte].rstrip()
        # "def f(x):" / "class Foo(Bar):" — drop the trailing colon.
        return text[:-1].rstrip() if text.endswith(":") else text

    def _docstring(self, node) -> str:
        body = node.child_by_field_name("body")
        if body is None or body.type != "block" or not body.named_children:
            return ""
        first = body.named_children[0]
        if first.type != "expression_statement":
            return ""
        strings = [c for c in first.children if c.type == "string"]
        if not strings:
            return ""
        text = self._text(strings[0]).strip()
        return text[3:-3] if text.startswith('"""') else text

    # ─── Imports ─────────────────────────────────────────────

    def _extract_import(self, node, mod: ParsedModule) -> None:
        if node.type == "import_statement":
            for named in node.named_children:
                if named.type == "dotted_name":
                    mod.imports.append(ImportInfo(
                        file_path=mod.file_path,
                        module=self._text(named),
                        lineno=node.start_point[0] + 1,
                    ))
                elif named.type == "aliased_import":
                    name_n = named.child_by_field_name("name")
                    alias_n = named.child_by_field_name("alias")
                    mod.imports.append(ImportInfo(
                        file_path=mod.file_path,
                        module=self._text(name_n) if name_n else "",
                        alias=self._text(alias_n) if alias_n else None,
                        lineno=node.start_point[0] + 1,
                    ))
            return

        # import_from_statement
        level = 0
        module = ""
        mn = node.child_by_field_name("module_name")
        if mn is not None:
            if mn.type == "relative_import":
                # "."*level + optional dotted module: count only the leading
                # dots (the dotted_name is a plain child, not a field).
                text = self._text(mn)
                level = len(text) - len(text.lstrip("."))
                dn = None
                for c in mn.named_children:
                    if c.type == "dotted_name":
                        dn = c
                module = self._text(dn) if dn is not None else ""
            else:
                module = self._text(mn)

        for named in node.named_children:
            # tree-sitter-py returns distinct wrapper objects for the same
            # node, so compare by byte range, not identity.
            if mn is not None and named.start_byte == mn.start_byte and named.end_byte == mn.end_byte:
                continue  # the module_name node itself is not an import target
            symbol = None
            alias = None
            if named.type == "dotted_name":
                symbol = self._text(named)
            elif named.type == "aliased_import":
                name_n = named.child_by_field_name("name")
                alias_n = named.child_by_field_name("alias")
                symbol = self._text(name_n) if name_n else ""
                alias = self._text(alias_n) if alias_n else None
            elif named.type == "wildcard_import":
                symbol = "*"
            else:
                continue
            mod.imports.append(ImportInfo(
                file_path=mod.file_path,
                module=module,
                symbol=symbol,
                alias=alias,
                level=level,
                lineno=node.start_point[0] + 1,
            ))

    # ─── References (call / reference graph) ─────────────────

    def _extract_reference(self, node, scope: list[str], mod: ParsedModule,
                           kind: str) -> None:
        """One call/attribute reference. ``caller`` = the qualified name
        of the enclosing symbol (module name for module-level code)."""
        if kind == "call":
            callee = node.child_by_field_name("function")
            if callee is None:
                return
            text = self._text(callee)
            # super().x and cls().y are dispatch, not graph edges.
            if text.startswith("super().") or text.startswith("cls()."):
                return
        else:  # attribute
            text = self._text(node)
            # Receiver member reads (self.x) are not cross-symbol
            # references — method CALLS on self/this stay (they resolve
            # against the enclosing class).
            if text.startswith(("self.", "cls.")):
                return
        if not _IDENTIFIER_PATH_RE.fullmatch(text):
            return  # e.g. f-string/string method calls on literals, chained subscripts
        caller = self._caller_qualified(mod, scope)
        mod.references.append(Reference(
            file_path=mod.file_path,
            caller=caller,
            target=text,
            kind=kind,
            lineno=node.start_point[0] + 1,
        ))

    def _caller_qualified(self, mod: ParsedModule, scope: list[str]) -> str:
        parts = ([mod.module_name] if mod.module_name else []) + scope
        return ".".join(parts)

    # ─── Helpers ─────────────────────────────────────────────

    def _text(self, node) -> str:
        """Node text decoded from the raw byte buffer (offsets are bytes)."""
        return self._raw[node.start_byte:node.end_byte].decode("utf-8", errors="replace")
