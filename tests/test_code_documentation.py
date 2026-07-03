"""验证运行时代码的模块与具名定义都具备可维护的说明。"""

import ast
from pathlib import Path
import unittest


RUNTIME_ROOT = Path(__file__).resolve().parents[1] / "tools"


class RuntimeDocumentationTests(unittest.TestCase):
    """对 `tools` 包执行静态文档覆盖检查。"""

    def test_runtime_modules_and_definitions_have_docstrings(self):
        """报告缺少模块、类、函数或方法 docstring 的源码位置。"""
        missing = []

        for path in sorted(RUNTIME_ROOT.rglob("*.py")):
            relative_path = path.relative_to(RUNTIME_ROOT.parent)
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))

            if not ast.get_docstring(tree):
                missing.append(f"{relative_path}: module")

            for node in ast.walk(tree):
                if not isinstance(
                    node,
                    (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef),
                ):
                    continue
                if not ast.get_docstring(node):
                    kind = "class" if isinstance(node, ast.ClassDef) else "function"
                    missing.append(
                        f"{relative_path}:{node.lineno} {kind} {node.name}"
                    )

        self.assertEqual(
            [],
            missing,
            "运行时代码仍缺少 docstring:\n" + "\n".join(missing),
        )


if __name__ == "__main__":
    unittest.main()
