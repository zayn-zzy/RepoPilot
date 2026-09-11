"""Tree-sitter based JavaScript / TypeScript / TSX parser — the same
extraction contract as PythonParser: symbols (functions, classes,
methods, arrow-function constants, TS interfaces), module imports
(ES imports + require()), and REFERENCES (calls + member accesses) for
the call/reference graph. Syntax errors are tolerated the same way."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

import tree_sitter_javascript
import tree_sitter_typescript
from tree_sitter import Language, Parser

from .symbols import (
    ImportInfo,
    Location,
    ParsedModule,
    Reference,
    Symbol,
    SymbolKind,
)

_IDENTIFIER_PATH_RE = re.compile(r"[A-Za-z_$][\w$]*(\.[A-Za-z_$][\w$]*)*")

_LANGUAGES = {
    "javascript": tree_sitter_javascript.language(),
    "typescript": tree_sitter_typescript.language_typescript(),
    "tsx": tree_sitter_typescript.language_tsx(),
}

# Def nodes that name a symbol (function/class/interface declarations).
_FUNCTION_DECLS = ("function_declaration", "generator_function_declaration")
_CLASS_DECLS = ("class_declaration", "abstract_class_declaration")


class JsParser:
    def __init__(self, language: str = "typescript"):
        if language not in _LANGUAGES:
            raise ValueError(f"JsParser does not support {language!r}")
        self.language = language
        self._parser = Parser(Language(_LANGUAGES[language]))
        self._raw = b""

    def parse_file(self, path: str | Path, module_name: str | None = None) -> ParsedModule:
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

        if ntype in _FUNCTION_DECLS or ntype in _CLASS_DECLS:
            name_node = node.child_by_field_name("name")
            if name_node is not None:
                kind = (SymbolKind.CLASS if ntype in _CLASS_DECLS
                        else SymbolKind.FUNCTION)
                self._extract_definition(node, name_node, scope, mod, kind)
                body = node.child_by_field_name("body")
                if body is not None:
                    self._walk(body, scope + [self._text(name_node)], mod)
            return

        if ntype == "interface_declaration":          # TypeScript
            name_node = node.child_by_field_name("name")
            if name_node is not None:
                self._extract_definition(node, name_node, scope, mod,
                                         SymbolKind.INTERFACE)
                body = node.child_by_field_name("body")
                if body is not None:
                    self._walk(body, scope + [self._text(name_node)], mod)
            return

        if ntype == "method_definition" or ntype == "field_definition":
            # class A { m(x) {...} } / class A { m = () => {...} }
            name_node = node.child_by_field_name("name")
            if name_node is not None:
                self._extract_definition(node, name_node, scope, mod,
                                         SymbolKind.METHOD)
                body = node.child_by_field_name("body")
                if body is not None:
                    self._walk(body, scope + [self._text(name_node)], mod)
            return

        if ntype == "variable_declarator":
            # const f = () => {...} / const f = function () {...}
            name_node = node.child_by_field_name("name")
            value = node.child_by_field_name("value")
            if (name_node is not None and name_node.type == "identifier"
                    and value is not None
                    and value.type in ("arrow_function", "function")):
                self._extract_definition(value, name_node, scope, mod,
                                         SymbolKind.FUNCTION)
                # A block body has a `body` field; a concise arrow body
                # (`=> expr`) is the expression itself — walk the whole
                # value (params are identifiers and add nothing).
                body = value.child_by_field_name("body") or value
                self._walk(body, scope + [self._text(name_node)], mod)
                return
            # Other values (require(...), literals, plain calls) still
            # carry references — walk them, but do not re-handle the name.
            if value is not None:
                self._walk(value, scope, mod)
            return

        if ntype == "import_statement":
            self._extract_import(node, mod)
            return  # never descend into imports

        if ntype in ("call_expression", "new_expression"):
            self._extract_call(node, scope, mod)
            args = node.child_by_field_name("arguments")
            if args is not None:
                self._walk(args, scope, mod)   # nested calls in arguments
            return

        if ntype == "member_expression":
            # obj.prop — an attribute reference; the object itself may
            # contain calls (getObj().prop), so keep walking it.
            # Receiver/protocol members (this.x, module.exports) are not
            # cross-symbol references.
            text = self._text(node)
            if not text.startswith(("this.", "module.exports", "exports.",
                                    "self.")):
                self._extract_reference(node, scope, mod, kind="attribute")
            obj = node.child_by_field_name("object")
            if obj is not None:
                self._walk(obj, scope, mod)
            return

        if ntype in ("identifier", "property_identifier", "string",
                     "comment", "number", "this", "super", "regex",
                     "jsx_text", "jsx_opening_element", "jsx_closing_element",
                     "jsx_attribute", "jsx_namespace_name",
                     "jsx_identifier"):
            # Leaves and markup without reference edges (jsx elements may
            # contain expression containers with calls — jsx_expression
            # falls through to the generic walk below; template strings
            # fall through too — their ${...} substitutions hold calls).
            if not ntype.startswith("jsx_"):
                return

        for child in node.children:
            self._walk(child, scope, mod)

    # ─── Definitions ─────────────────────────────────────────

    def _extract_definition(self, node, name_node, scope: list[str],
                            mod: ParsedModule, kind: SymbolKind) -> None:
        name = self._text(name_node)
        qualified = ".".join(([mod.module_name] if mod.module_name else [])
                             + scope + [name])
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
        ))

    def _signature(self, node) -> str:
        """The declaration head: everything up to the body block (or the
        '=>' for arrows)."""
        text = self._text(node)
        body = node.child_by_field_name("body")
        if body is not None:
            text = text[:body.start_byte - node.start_byte]
        return text.strip()

    # ─── Imports ─────────────────────────────────────────────

    def _extract_import(self, node, mod: ParsedModule) -> None:
        source_node = node.child_by_field_name("source")
        module = self._string_text(source_node) if source_node is not None else ""
        lineno = node.start_point[0] + 1

        def add(symbol=None, alias=None):
            mod.imports.append(ImportInfo(
                file_path=mod.file_path, module=module, symbol=symbol,
                alias=alias, lineno=lineno))

        # The clause: named_imports / namespace_import / a bare identifier
        # (default import) — nested inside import_clause.
        found = False
        for child in node.named_children:
            if child.type == "import_clause":
                for sub in child.named_children:
                    if sub.type == "named_imports":
                        for spec in sub.named_children:
                            if spec.type != "import_specifier":
                                continue
                            name_n = spec.child_by_field_name("name")
                            alias_n = spec.child_by_field_name("alias")
                            add(self._text(name_n) if name_n else "",
                                self._text(alias_n) if alias_n else None)
                            found = True
                    elif sub.type == "namespace_import":
                        name_n = sub.child_by_field_name("name")
                        add("*", self._text(name_n) if name_n else None)
                        found = True
                    elif sub.type == "identifier":     # default import
                        add("default", self._text(sub))
                        found = True
        if not found:
            add()   # side-effect import: import './styles.css'

    def _extract_call(self, node, scope: list[str], mod: ParsedModule) -> None:
        callee = (node.child_by_field_name("function")
                  or node.child_by_field_name("constructor"))
        if callee is None:
            return
        # require('mod') / import('mod') — a module import, not a call edge.
        if callee.type in ("identifier", "import"):
            if self._text(callee).split(".")[-1] in ("require", "import"):
                args = node.child_by_field_name("arguments")
                module = ""
                if args is not None:
                    for a in args.named_children:
                        if a.type == "string":
                            module = self._string_text(a)
                if module:
                    # Destructuring: const {a, b} = require('./m') binds a
                    # and b to the module's exports.
                    bindings = self._require_bindings(node)
                    if bindings:
                        for name in bindings:
                            mod.imports.append(ImportInfo(
                                file_path=mod.file_path, module=module,
                                symbol=name, alias=name,
                                lineno=node.start_point[0] + 1,
                            ))
                    else:
                        mod.imports.append(ImportInfo(
                            file_path=mod.file_path, module=module,
                            lineno=node.start_point[0] + 1,
                        ))
                return
        self._extract_reference(node, scope, mod, kind="call",
                                target_node=callee)

    def _require_bindings(self, call_node) -> list[str]:
        """Names bound by `const {a, b} = require(...)` — the declarator's
        object pattern (shorthand props and `x: y` pairs)."""
        declarator = call_node.parent
        if declarator is None or declarator.type != "variable_declarator":
            return []
        pattern = declarator.child_by_field_name("name")
        if pattern is None or pattern.type != "object_pattern":
            return []
        names = []
        for item in pattern.named_children:
            # tree-sitter-javascript ≥0.23 names these *_pattern nodes.
            if item.type in ("shorthand_property_identifier",
                             "shorthand_property_identifier_pattern"):
                names.append(self._text(item))
            elif item.type in ("pair", "pair_pattern"):
                value = item.child_by_field_name("value")
                if value is not None and value.type == "identifier":
                    names.append(self._text(value))
        return names

    # ─── References ──────────────────────────────────────────

    def _extract_reference(self, node, scope: list[str], mod: ParsedModule,
                           kind: str, target_node=None) -> None:
        text = self._text(target_node or node)
        if not _IDENTIFIER_PATH_RE.fullmatch(text):
            return
        caller = ".".join(([mod.module_name] if mod.module_name else []) + scope)
        mod.references.append(Reference(
            file_path=mod.file_path,
            caller=caller,
            target=text,
            kind=kind,
            lineno=node.start_point[0] + 1,
        ))

    # ─── Helpers ─────────────────────────────────────────────

    def _string_text(self, node) -> str:
        """The contents of a string node, quotes stripped."""
        text = self._text(node)
        if len(text) >= 2 and text[0] in "\"'`" and text[-1] == text[0]:
            return text[1:-1]
        return text

    def _text(self, node) -> str:
        return self._raw[node.start_byte:node.end_byte].decode("utf-8", errors="replace")
