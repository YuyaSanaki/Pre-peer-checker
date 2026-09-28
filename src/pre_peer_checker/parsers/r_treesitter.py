"""R スクリプト静的解析.

優先: tree-sitter（`tree_sitter_r` または `tree-sitter-language-pack`）
フォールバック: 正規表現（tree-sitter 未導入時のみ）
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

_PLOT_FUNCS = frozenset({"ggplot", "qplot", "ggplotly"})
_READ_FUNCS = frozenset(
    {
        "read.csv",
        "read.csv2",
        "read.table",
        "read_csv",
        "read_excel",
        "read.xlsx",
        "fread",
        "load",
        "readRDS",
    }
)
_SAVE_FUNCS = frozenset({"ggsave", "pdf", "png", "jpeg", "tiff", "svg"})
_PIPE_OPS = frozenset({"%>%", "|>", "%T>%", "%<>%"})
_ALL_FUNCS = _PLOT_FUNCS | _READ_FUNCS | _SAVE_FUNCS

_CALL_RE = re.compile(
    r"(?:(?P<ns>[A-Za-z.][\w.]*)::)?(?P<func>"
    + "|".join(re.escape(f) for f in sorted(_ALL_FUNCS, key=len, reverse=True))
    + r")\s*\(",
    re.MULTILINE,
)


@dataclass
class RDataBinding:
    """後方互換: 単一呼び出し."""

    function: str
    arguments: str
    lineno: int


@dataclass
class RRead:
    function: str
    path: str | None
    assigned_to: str | None
    lineno: int
    arguments: str = ""


@dataclass
class RPlot:
    function: str
    data_expr: str | None
    layers: list[str]
    assigned_to: str | None
    lineno: int
    arguments: str = ""
    piped_from: str | None = None


@dataclass
class RSave:
    function: str
    path: str | None
    plot_expr: str | None
    lineno: int
    arguments: str = ""


@dataclass
class RScriptDAG:
    reads: list[RRead] = field(default_factory=list)
    plots: list[RPlot] = field(default_factory=list)
    saves: list[RSave] = field(default_factory=list)
    bindings: list[RDataBinding] = field(default_factory=list)
    var_sources: dict[str, str] = field(default_factory=dict)
    edges: list[tuple[str, str, str]] = field(default_factory=list)
    backend: str = "regex"

    def to_dict(self) -> dict:
        return {
            "reads": [asdict(x) for x in self.reads],
            "plots": [asdict(x) for x in self.plots],
            "saves": [asdict(x) for x in self.saves],
            "bindings": [asdict(x) for x in self.bindings],
            "var_sources": dict(self.var_sources),
            "edges": [list(e) for e in self.edges],
            "backend": self.backend,
        }


def treesitter_available() -> bool:
    try:
        _get_r_parser()
        return True
    except Exception:
        return False


def _get_r_parser() -> tuple[Any, str]:
    """Return (parser, backend_name). Prefer dedicated grammar, then language-pack."""
    try:
        import tree_sitter_r as tsr
        from tree_sitter import Language, Parser

        language = Language(tsr.language())
        parser = Parser(language)
        return parser, "tree-sitter-r"
    except Exception:
        pass
    from tree_sitter_language_pack import get_parser

    return get_parser("r"), "tree-sitter-language-pack"


def _extract_args(code: str, open_paren_idx: int) -> str:
    depth = 0
    i = open_paren_idx
    while i < len(code):
        ch = code[i]
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return code[open_paren_idx + 1 : i]
        elif ch in {'"', "'"}:
            quote = ch
            i += 1
            while i < len(code) and code[i] != quote:
                if code[i] == "\\":
                    i += 1
                i += 1
        i += 1
    return code[open_paren_idx + 1 :]


def _analyze_with_regex(script_code: str) -> RScriptDAG:
    bindings: list[RDataBinding] = []
    for m in _CALL_RE.finditer(script_code):
        func = m.group("func")
        open_idx = m.end() - 1
        args = _extract_args(script_code, open_idx)
        lineno = script_code.count("\n", 0, m.start()) + 1
        bindings.append(RDataBinding(function=func, arguments=args, lineno=lineno))
    dag = RScriptDAG(bindings=bindings, backend="regex")
    # best-effort path / data extraction for regex mode
    for b in bindings:
        if b.function in _READ_FUNCS:
            path = _first_string_literal(b.arguments)
            dag.reads.append(
                RRead(function=b.function, path=path, assigned_to=None, lineno=b.lineno, arguments=b.arguments)
            )
        elif b.function in _PLOT_FUNCS:
            data = _first_positional_ident(b.arguments)
            dag.plots.append(
                RPlot(
                    function=b.function,
                    data_expr=data,
                    layers=[],
                    assigned_to=None,
                    lineno=b.lineno,
                    arguments=b.arguments,
                )
            )
        elif b.function in _SAVE_FUNCS:
            dag.saves.append(
                RSave(
                    function=b.function,
                    path=_first_string_literal(b.arguments),
                    plot_expr=_second_positional_ident(b.arguments),
                    lineno=b.lineno,
                    arguments=b.arguments,
                )
            )
    _build_edges(dag)
    return dag


def _first_string_literal(args: str) -> str | None:
    m = re.search(r'["\']([^"\']+)["\']', args)
    return m.group(1) if m else None


def _first_positional_ident(args: str) -> str | None:
    # data=foo or first bare identifier before comma / aes(
    m = re.search(r"(?:^|,)\s*data\s*=\s*([A-Za-z.][\w.]*)", args)
    if m:
        return m.group(1)
    m = re.match(r"\s*([A-Za-z.][\w.]*)\s*(?:,|$)", args)
    if m and m.group(1) != "aes":
        return m.group(1)
    return None


def _second_positional_ident(args: str) -> str | None:
    parts = [p.strip() for p in args.split(",")]
    if len(parts) >= 2:
        m = re.match(r"^([A-Za-z.][\w.]*)$", parts[1])
        if m:
            return m.group(1)
    return None


def _node_text(node: Any) -> str:
    return node.text.decode("utf8")


def _call_func_name(call_node: Any) -> str:
    fn = call_node.child_by_field_name("function")
    if fn is None:
        return ""
    if fn.type == "identifier":
        return _node_text(fn)
    if fn.type == "namespace_operator":
        rhs = fn.child_by_field_name("rhs")
        if rhs is not None:
            return _node_text(rhs)
        # fallback: last identifier
        ids = [c for c in fn.children if c.type == "identifier"]
        if ids:
            return _node_text(ids[-1])
    return _node_text(fn)


def _string_content(node: Any) -> str | None:
    if node is None:
        return None
    if node.type == "string":
        for c in node.children:
            if c.type == "string_content":
                return _node_text(c)
        t = _node_text(node)
        return t.strip("\"'")
    if node.type == "argument":
        for c in node.named_children:
            lit = _string_content(c)
            if lit is not None:
                return lit
    return None


def _ident(node: Any) -> str | None:
    if node is None:
        return None
    if node.type == "identifier":
        return _node_text(node)
    if node.type == "argument":
        # name = value  or bare value
        named = [c for c in node.children if c.is_named]
        if len(named) == 1 and named[0].type == "identifier":
            return _node_text(named[0])
        # skip name= when looking for value
        ids = [c for c in node.children if c.type == "identifier"]
        if len(ids) >= 2:
            return _node_text(ids[-1])
        if len(ids) == 1 and "=" not in {_node_text(c) for c in node.children}:
            return _node_text(ids[0])
    return None


def _arg_nodes(call_node: Any) -> list[Any]:
    args = call_node.child_by_field_name("arguments")
    if args is None:
        return []
    return [c for c in args.named_children if c.type == "argument"]


def _arg_name(arg_node: Any) -> str | None:
    # argument with name = value → first identifier before '='
    kids = list(arg_node.children)
    for i, c in enumerate(kids):
        if c.type == "=" and i > 0 and kids[i - 1].type == "identifier":
            return _node_text(kids[i - 1])
    return None


def _binary_op(node: Any) -> str | None:
    for c in node.children:
        if not c.is_named:
            t = _node_text(c)
            if t in {"<-", "<<-", "=", "+", "%>%", "|>", "%T>%", "%<>%"} or c.type == "special":
                if c.type == "special":
                    return _node_text(c)
                return t
        if c.type == "special":
            return _node_text(c)
    return None


def _left_right(node: Any) -> tuple[Any | None, Any | None]:
    named = [c for c in node.children if c.is_named]
    if len(named) >= 2:
        return named[0], named[1]
    return None, None


def _find_pipe_lhs_data(node: Any) -> str | None:
    """Walk left through pipes / + layers to find data expression."""
    cur = node
    while cur is not None:
        if cur.type == "identifier":
            return _node_text(cur)
        if cur.type == "call":
            return None
        if cur.type == "binary_operator":
            op = _binary_op(cur)
            left, right = _left_right(cur)
            if op in _PIPE_OPS:
                cur = left
                continue
            if op == "+":
                cur = left
                continue
            if op in {"<-", "<<-", "="}:
                cur = right
                continue
            break
        break
    return None


def _ggplot_data_and_layers(expr: Any) -> tuple[str | None, list[str], Any | None]:
    """From ggplot(...) + geom_* chain, return (data, layers, ggplot_call)."""
    layers: list[str] = []
    ggplot_call = None
    data_expr = None
    piped = None

    def walk(n: Any) -> None:
        nonlocal ggplot_call, data_expr, piped
        if n is None:
            return
        if n.type == "call":
            fname = _call_func_name(n)
            if fname in _PLOT_FUNCS and ggplot_call is None:
                ggplot_call = n
                args = _arg_nodes(n)
                # data= named or first positional non-aes
                for a in args:
                    aname = _arg_name(a)
                    if aname == "data":
                        data_expr = _ident(a) or _node_text(a).split("=", 1)[-1].strip()
                        break
                if data_expr is None and args:
                    first = args[0]
                    if _arg_name(first) is None:
                        # bare identifier or call — skip aes()
                        inner = first.named_children[0] if first.named_children else first
                        if inner.type == "call" and _call_func_name(inner) == "aes":
                            pass
                        else:
                            data_expr = _ident(first) or (
                                _node_text(inner) if inner.type == "identifier" else None
                            )
            elif fname.startswith("geom_") or fname.startswith("stat_") or fname in {
                "coord_flip",
                "facet_wrap",
                "facet_grid",
                "theme",
                "labs",
                "xlab",
                "ylab",
                "ggtitle",
                "scale_x_discrete",
                "scale_y_continuous",
            }:
                layers.append(fname)
            return
        if n.type == "binary_operator":
            op = _binary_op(n)
            left, right = _left_right(n)
            if op in _PIPE_OPS:
                # LHS is data, RHS may be ggplot(...)
                if left is not None and left.type == "identifier":
                    piped = _node_text(left)
                    if data_expr is None:
                        data_expr = piped
                elif left is not None:
                    walk(left)
                walk(right)
                return
            if op == "+":
                walk(left)
                walk(right)
                return
            walk(left)
            walk(right)

    walk(expr)
    if data_expr is None and piped:
        data_expr = piped
    return data_expr, layers, ggplot_call


def _walk_treesitter(root: Any, source: bytes) -> RScriptDAG:
    dag = RScriptDAG()

    def handle_call(call: Any, assigned_to: str | None, pipe_data: str | None) -> None:
        fname = _call_func_name(call)
        args_node = call.child_by_field_name("arguments")
        args_text = _node_text(args_node) if args_node else ""
        # strip surrounding parens for binding.arguments consistency with regex
        if args_text.startswith("(") and args_text.endswith(")"):
            args_inner = args_text[1:-1]
        else:
            args_inner = args_text
        lineno = call.start_point[0] + 1

        if fname in _ALL_FUNCS:
            dag.bindings.append(RDataBinding(function=fname, arguments=args_inner, lineno=lineno))

        if fname in _READ_FUNCS:
            path = None
            for a in _arg_nodes(call):
                path = _string_content(a)
                if path:
                    break
            dag.reads.append(
                RRead(
                    function=fname,
                    path=path,
                    assigned_to=assigned_to,
                    lineno=lineno,
                    arguments=args_inner,
                )
            )
            if assigned_to and path:
                dag.var_sources[assigned_to] = path
            elif assigned_to:
                dag.var_sources[assigned_to] = fname

        elif fname in _PLOT_FUNCS:
            data_expr, layers, _ = _ggplot_data_and_layers(call)
            if pipe_data and not data_expr:
                data_expr = pipe_data
            dag.plots.append(
                RPlot(
                    function=fname,
                    data_expr=data_expr,
                    layers=layers,
                    assigned_to=assigned_to,
                    lineno=lineno,
                    arguments=args_inner,
                    piped_from=pipe_data,
                )
            )

        elif fname in _SAVE_FUNCS:
            path = None
            plot_expr = None
            args = _arg_nodes(call)
            if args:
                path = _string_content(args[0])
            if len(args) >= 2:
                plot_expr = _ident(args[1])
            for a in args:
                if _arg_name(a) == "filename":
                    path = _string_content(a) or path
                if _arg_name(a) in {"plot", "x"}:
                    plot_expr = _ident(a) or plot_expr
            dag.saves.append(
                RSave(
                    function=fname,
                    path=path,
                    plot_expr=plot_expr,
                    lineno=lineno,
                    arguments=args_inner,
                )
            )

    def visit(node: Any, assigned_to: str | None = None, pipe_data: str | None = None) -> None:
        if node.type == "binary_operator":
            op = _binary_op(node)
            left, right = _left_right(node)
            if op in {"<-", "<<-", "="} and left is not None and left.type == "identifier":
                name = _node_text(left)
                # ggplot chain or pipe assigned
                if right is not None and right.type == "binary_operator":
                    data, layers, gcall = _ggplot_data_and_layers(right)
                    if gcall is not None:
                        lineno = gcall.start_point[0] + 1
                        args_node = gcall.child_by_field_name("arguments")
                        args_text = _node_text(args_node) if args_node else ""
                        args_inner = args_text[1:-1] if args_text.startswith("(") else args_text
                        fname = _call_func_name(gcall)
                        dag.bindings.append(
                            RDataBinding(function=fname, arguments=args_inner, lineno=lineno)
                        )
                        dag.plots.append(
                            RPlot(
                                function=fname,
                                data_expr=data,
                                layers=layers,
                                assigned_to=name,
                                lineno=lineno,
                                arguments=args_inner,
                            )
                        )
                        if name and data:
                            dag.var_sources[name] = f"plot:{data}"
                        # Do not re-walk ggplot(...) or it duplicates the plot node
                        return
                visit(right, assigned_to=name)
                return
            if op in _PIPE_OPS:
                lhs_data = None
                if left is not None and left.type == "identifier":
                    lhs_data = _node_text(left)
                elif left is not None:
                    visit(left)
                    lhs_data = _find_pipe_lhs_data(left)
                # RHS ggplot(+layers)
                if right is not None:
                    data, layers, gcall = _ggplot_data_and_layers(right)
                    if gcall is not None:
                        data = data or lhs_data
                        lineno = gcall.start_point[0] + 1
                        args_node = gcall.child_by_field_name("arguments")
                        args_text = _node_text(args_node) if args_node else ""
                        args_inner = args_text[1:-1] if args_text.startswith("(") else args_text
                        fname = _call_func_name(gcall)
                        dag.bindings.append(
                            RDataBinding(function=fname, arguments=args_inner, lineno=lineno)
                        )
                        dag.plots.append(
                            RPlot(
                                function=fname,
                                data_expr=data,
                                layers=layers,
                                assigned_to=assigned_to,
                                lineno=lineno,
                                arguments=args_inner,
                                piped_from=lhs_data,
                            )
                        )
                        return
                    visit(right, assigned_to=assigned_to, pipe_data=lhs_data)
                return
            if op == "+":
                data, layers, gcall = _ggplot_data_and_layers(node)
                if gcall is not None and not any(p.lineno == gcall.start_point[0] + 1 for p in dag.plots):
                    lineno = gcall.start_point[0] + 1
                    args_node = gcall.child_by_field_name("arguments")
                    args_text = _node_text(args_node) if args_node else ""
                    args_inner = args_text[1:-1] if args_text.startswith("(") else args_text
                    fname = _call_func_name(gcall)
                    dag.bindings.append(
                        RDataBinding(function=fname, arguments=args_inner, lineno=lineno)
                    )
                    dag.plots.append(
                        RPlot(
                            function=fname,
                            data_expr=data or pipe_data,
                            layers=layers,
                            assigned_to=assigned_to,
                            lineno=lineno,
                            arguments=args_inner,
                            piped_from=pipe_data,
                        )
                    )
                    return
            visit(left, assigned_to=assigned_to, pipe_data=pipe_data)
            visit(right, assigned_to=assigned_to, pipe_data=pipe_data)
            return

        if node.type == "call":
            handle_call(node, assigned_to=assigned_to, pipe_data=pipe_data)
            # visit nested calls inside arguments (e.g. aes) lightly
            for a in _arg_nodes(node):
                for c in a.named_children:
                    if c.type in {"call", "binary_operator"}:
                        visit(c)
            return

        for c in node.named_children:
            visit(c, assigned_to=assigned_to, pipe_data=pipe_data)

    visit(root)
    _build_edges(dag)
    return dag


def _build_edges(dag: RScriptDAG) -> None:
    edges: list[tuple[str, str, str]] = []
    for r in dag.reads:
        src = r.path or f"read@{r.lineno}"
        dst = r.assigned_to or f"anon_read@{r.lineno}"
        edges.append((src, dst, "read"))
    for p in dag.plots:
        data = p.data_expr or p.piped_from or "unknown"
        plot_id = p.assigned_to or f"plot@{p.lineno}"
        edges.append((data, plot_id, "plot"))
        if data in dag.var_sources:
            edges.append((dag.var_sources[data], data, "bind"))
    for s in dag.saves:
        out = s.path or f"save@{s.lineno}"
        src = s.plot_expr or "last_plot"
        edges.append((src, out, "save"))
    dag.edges = edges


def _analyze_with_treesitter(script_code: str) -> RScriptDAG:
    parser, backend = _get_r_parser()
    tree = parser.parse(bytes(script_code, "utf8"))
    dag = _walk_treesitter(tree.root_node, bytes(script_code, "utf8"))
    dag.backend = backend
    return dag


def build_r_dag(script_code: str, *, require_treesitter: bool = False) -> RScriptDAG:
    """Build read→var→ggplot→ggsave DAG. Prefer tree-sitter when available."""
    try:
        return _analyze_with_treesitter(script_code)
    except Exception:
        if require_treesitter:
            raise
        return _analyze_with_regex(script_code)


def analyze_r_script(script_code: str) -> list[RDataBinding]:
    """ggplot / read.* / ggsave 呼び出しを抽出（後方互換）."""
    return build_r_dag(script_code).bindings


def analyze_r_file(path: Path | str) -> list[RDataBinding]:
    text = Path(path).read_text(encoding="utf-8")
    return analyze_r_script(text)


def analyze_r_dag_file(path: Path | str) -> RScriptDAG:
    path = Path(path)
    text = path.read_text(encoding="utf-8", errors="ignore")
    return build_r_dag(text)
