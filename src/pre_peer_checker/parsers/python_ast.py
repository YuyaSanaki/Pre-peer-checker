"""Python スクリプト静的解析（標準 ast → 読込→変数→作図→保存 DAG）."""

from __future__ import annotations

import ast
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path


PLOT_FUNCS = frozenset(
    {
        "sns.boxplot",
        "sns.stripplot",
        "sns.violinplot",
        "sns.barplot",
        "sns.scatterplot",
        "sns.catplot",
        "plt.scatter",
        "plt.plot",
        "plt.bar",
        "plt.boxplot",
        "px.strip",
        "px.box",
        "px.scatter",
    }
)

READ_FUNCS = frozenset(
    {
        "pd.read_csv",
        "pd.read_excel",
        "pd.read_table",
        "pd.read_parquet",
        "pd.read_fwf",
        "read_csv",
        "read_excel",
        "read_table",
        "read_parquet",
        "openpyxl.load_workbook",
        "load_workbook",
    }
)

SAVE_FUNCS = frozenset(
    {
        "plt.savefig",
        "fig.savefig",
        "savefig",
        "to_csv",
        "to_excel",
        "to_parquet",
    }
)


@dataclass
class PlotCall:
    func: str
    args: list[str]
    kwargs: dict[str, str]
    lineno: int
    data_expr: str | None = None
    assigned_to: str | None = None


@dataclass
class ReadCall:
    func: str
    args: list[str]
    kwargs: dict[str, str]
    lineno: int
    path: str | None = None
    assigned_to: str | None = None


@dataclass
class SaveCall:
    func: str
    path: str | None
    lineno: int
    plot_var: str | None = None
    args: list[str] = field(default_factory=list)
    kwargs: dict[str, str] = field(default_factory=dict)


@dataclass
class ScriptDAG:
    """読込パス → 変数 → 作図 → 保存ファイル名."""

    reads: list[ReadCall] = field(default_factory=list)
    plots: list[PlotCall] = field(default_factory=list)
    saves: list[SaveCall] = field(default_factory=list)
    var_sources: dict[str, str] = field(default_factory=dict)
    edges: list[tuple[str, str, str]] = field(default_factory=list)
    backend: str = "python-ast"

    def to_dict(self) -> dict:
        return {
            "reads": [asdict(x) for x in self.reads],
            "plots": [asdict(x) for x in self.plots],
            "saves": [asdict(x) for x in self.saves],
            "var_sources": dict(self.var_sources),
            "edges": [list(e) for e in self.edges],
            "backend": self.backend,
        }


def _literal_str(node: ast.AST | None) -> str | None:
    if node is None:
        return None
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _call_name(node: ast.Call) -> str:
    if isinstance(node.func, ast.Attribute):
        parts: list[str] = [node.func.attr]
        cur: ast.AST = node.func.value
        while isinstance(cur, ast.Attribute):
            parts.append(cur.attr)
            cur = cur.value
        if isinstance(cur, ast.Name):
            parts.append(cur.id)
            return ".".join(reversed(parts))
        # obj.savefig → savefig
        return parts[0]
    if isinstance(node.func, ast.Name):
        return node.func.id
    return ""


def _kw(node: ast.Call, name: str) -> ast.AST | None:
    for kw in node.keywords:
        if kw.arg == name:
            return kw.value
    return None


def _first_str_arg(node: ast.Call) -> str | None:
    if node.args:
        return _literal_str(node.args[0])
    for key in ("path", "fname", "filename", "filepath_or_buffer"):
        lit = _literal_str(_kw(node, key))
        if lit:
            return lit
    return None


class _ScriptVisitor(ast.NodeVisitor):
    def __init__(self) -> None:
        self.reads: list[ReadCall] = []
        self.plots: list[PlotCall] = []
        self.saves: list[SaveCall] = []
        self.var_sources: dict[str, str] = {}
        self._pending_assign: str | None = None

    def visit_Assign(self, node: ast.Assign) -> None:
        targets = [t.id for t in node.targets if isinstance(t, ast.Name)]
        if len(targets) == 1 and isinstance(node.value, ast.Call):
            self._pending_assign = targets[0]
            self.visit(node.value)
            self._pending_assign = None
            return
        if len(targets) == 1:
            self.var_sources[targets[0]] = ast.unparse(node.value)[:200]
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        if isinstance(node.target, ast.Name) and isinstance(node.value, ast.Call):
            self._pending_assign = node.target.id
            self.visit(node.value)
            self._pending_assign = None
            return
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        func_name = _call_name(node)
        short = func_name.split(".")[-1] if func_name else ""
        assigned = self._pending_assign

        if func_name in READ_FUNCS or short in {"read_csv", "read_excel", "read_table", "read_parquet"}:
            path = _first_str_arg(node)
            rc = ReadCall(
                func=func_name or short,
                args=[ast.unparse(a) for a in node.args],
                kwargs={kw.arg: ast.unparse(kw.value) for kw in node.keywords if kw.arg},
                lineno=node.lineno,
                path=path,
                assigned_to=assigned,
            )
            self.reads.append(rc)
            if assigned and path:
                self.var_sources[assigned] = path
            elif assigned:
                self.var_sources[assigned] = func_name

        elif func_name in PLOT_FUNCS or (
            short in {"boxplot", "stripplot", "violinplot", "barplot", "scatterplot", "catplot"}
            and func_name.startswith("sns.")
        ):
            data_node = _kw(node, "data")
            data_expr = ast.unparse(data_node) if data_node is not None else None
            if data_expr is None and node.args:
                # rare positional data=
                data_expr = ast.unparse(node.args[0])
            self.plots.append(
                PlotCall(
                    func=func_name,
                    args=[ast.unparse(a) for a in node.args],
                    kwargs={kw.arg: ast.unparse(kw.value) for kw in node.keywords if kw.arg},
                    lineno=node.lineno,
                    data_expr=data_expr,
                    assigned_to=assigned,
                )
            )

        elif func_name in SAVE_FUNCS or short in SAVE_FUNCS:
            path = _first_str_arg(node)
            plot_var = None
            if isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name):
                if short == "savefig":
                    plot_var = node.func.value.id
            self.saves.append(
                SaveCall(
                    func=func_name or short,
                    path=path,
                    lineno=node.lineno,
                    plot_var=plot_var,
                    args=[ast.unparse(a) for a in node.args],
                    kwargs={kw.arg: ast.unparse(kw.value) for kw in node.keywords if kw.arg},
                )
            )

        self.generic_visit(node)


def _build_edges(dag: ScriptDAG) -> None:
    edges: list[tuple[str, str, str]] = []
    for r in dag.reads:
        src = r.path or f"read@{r.lineno}"
        if r.assigned_to:
            edges.append((src, r.assigned_to, "read"))
        else:
            edges.append((src, f"anon_read@{r.lineno}", "read"))
    for p in dag.plots:
        data = p.data_expr or "unknown"
        plot_id = p.assigned_to or f"plot@{p.lineno}"
        edges.append((data, plot_id, "plot"))
        if data in dag.var_sources:
            edges.append((dag.var_sources[data], data, "bind"))
    for s in dag.saves:
        out = s.path or f"save@{s.lineno}"
        src = s.plot_var or "current_figure"
        edges.append((src, out, "save"))
    dag.edges = edges


def build_python_dag(code_str: str) -> ScriptDAG:
    tree = ast.parse(code_str)
    visitor = _ScriptVisitor()
    visitor.visit(tree)
    dag = ScriptDAG(
        reads=visitor.reads,
        plots=visitor.plots,
        saves=visitor.saves,
        var_sources=dict(visitor.var_sources),
        backend="python-ast",
    )
    _build_edges(dag)
    return dag


def trace_python_script(code_str: str) -> list[PlotCall]:
    return build_python_dag(code_str).plots


def analyze_python_script(code_str: str) -> dict[str, list]:
    """後方互換: plots / reads リストを返す."""
    dag = build_python_dag(code_str)
    return {"plots": dag.plots, "reads": dag.reads, "saves": dag.saves, "dag": dag}


def notebook_to_source(path: Path | str) -> str:
    """`.ipynb` のコードセルを結合（nbformat 優先、無ければ簡易 JSON）."""
    path = Path(path)
    try:
        import nbformat

        nb = nbformat.read(path, as_version=4)
        parts: list[str] = []
        for cell in nb.cells:
            if cell.get("cell_type") == "code":
                src = cell.get("source") or ""
                if isinstance(src, list):
                    src = "".join(src)
                parts.append(str(src))
        return "\n\n".join(parts)
    except Exception:
        raw = json.loads(path.read_text(encoding="utf-8"))
        parts = []
        for cell in raw.get("cells", []):
            if cell.get("cell_type") == "code":
                src = cell.get("source") or ""
                if isinstance(src, list):
                    src = "".join(src)
                parts.append(str(src))
        return "\n\n".join(parts)


def analyze_python_file(path: Path | str) -> dict[str, list]:
    path = Path(path)
    if path.suffix.lower() == ".ipynb":
        text = notebook_to_source(path)
    else:
        text = path.read_text(encoding="utf-8")
    return analyze_python_script(text)


def analyze_python_dag_file(path: Path | str) -> ScriptDAG:
    path = Path(path)
    if path.suffix.lower() == ".ipynb":
        return build_python_dag(notebook_to_source(path))
    return build_python_dag(path.read_text(encoding="utf-8"))
