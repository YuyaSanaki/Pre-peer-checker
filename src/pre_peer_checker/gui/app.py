"""PyQt6 GUI — フォルダ D&D + 実行 1 クリック（Phase 3）。

DGX (Linux) でも開発・動作確認可能。MLX 実機確認のみ Mac 必須。
"""

from __future__ import annotations

import sys
from pathlib import Path

from pre_peer_checker.gui.worker import GuiRunConfig, run_verification_job


def _qt_available() -> bool:
    try:
        from PyQt6.QtWidgets import QApplication  # noqa: F401

        return True
    except Exception:
        return False


def main(argv: list[str] | None = None) -> int:
    if not _qt_available():
        print(
            "PyQt6 が必要です: pip install 'pre-peer-checker[gui]'\n"
            "（表示のないサーバでは DISPLAY / Wayland を確認してください）",
            file=sys.stderr,
        )
        return 2

    from PyQt6.QtCore import QThread, pyqtSignal
    from PyQt6.QtGui import QDragEnterEvent, QDropEvent
    from PyQt6.QtWidgets import (
        QApplication,
        QComboBox,
        QFileDialog,
        QHBoxLayout,
        QLabel,
        QLineEdit,
        QListWidget,
        QMainWindow,
        QMessageBox,
        QProgressBar,
        QPushButton,
        QTableWidget,
        QTableWidgetItem,
        QVBoxLayout,
        QWidget,
    )

    class DropList(QListWidget):
        def __init__(self, placeholder: str):
            super().__init__()
            self.setAcceptDrops(True)
            self.setMinimumHeight(90)
            self._placeholder = placeholder
            self._refresh_placeholder()

        def _refresh_placeholder(self) -> None:
            if self.count() == 0:
                self.addItem(self._placeholder)

        def paths(self) -> list[Path]:
            out: list[Path] = []
            for i in range(self.count()):
                text = self.item(i).text()
                if text == self._placeholder:
                    continue
                out.append(Path(text))
            return out

        def add_path(self, path: Path) -> None:
            if self.count() == 1 and self.item(0).text() == self._placeholder:
                self.clear()
            if str(path) not in [self.item(i).text() for i in range(self.count())]:
                self.addItem(str(path.resolve()))

        def dragEnterEvent(self, event: QDragEnterEvent) -> None:  # noqa: N802
            if event.mimeData().hasUrls():
                event.acceptProposedAction()
            else:
                event.ignore()

        def dropEvent(self, event: QDropEvent) -> None:  # noqa: N802
            for url in event.mimeData().urls():
                p = Path(url.toLocalFile())
                if p.exists():
                    self.add_path(p)
            event.acceptProposedAction()

    class VerifyThread(QThread):
        finished_ok = pyqtSignal(object)
        failed = pyqtSignal(str)
        status = pyqtSignal(str)

        def __init__(self, config: GuiRunConfig):
            super().__init__()
            self.config = config

        def run(self) -> None:
            self.status.emit("検証を実行中…（決定論的コア）")
            result = run_verification_job(self.config)
            if result.ok:
                self.finished_ok.emit(result)
            else:
                self.failed.emit(result.error or "不明なエラー")

    class MainWindow(QMainWindow):
        def __init__(self) -> None:
            super().__init__()
            self.setWindowTitle("Pre-peer-checker — 投稿前データ照合")
            self.resize(820, 640)
            self._thread: VerifyThread | None = None

            root = QWidget()
            self.setCentralWidget(root)
            layout = QVBoxLayout(root)

            title = QLabel("論文データ照合（完全ローカル）")
            title.setStyleSheet("font-size: 20px; font-weight: 600;")
            layout.addWidget(title)
            layout.addWidget(
                QLabel("原稿・生データ・Figure フォルダをドロップ（複数可）。照合エンジンは汎用パターンで動作します。")
            )

            layout.addWidget(QLabel("入力フォルダ / ファイル"))
            self.input_list = DropList("ここにフォルダをドロップ、または「追加」")
            layout.addWidget(self.input_list)
            row_in = QHBoxLayout()
            btn_add = QPushButton("入力を追加…")
            btn_add.clicked.connect(self._add_input)
            btn_clear_in = QPushButton("クリア")
            btn_clear_in.clicked.connect(lambda: (self.input_list.clear(), self.input_list._refresh_placeholder()))
            row_in.addWidget(btn_add)
            row_in.addWidget(btn_clear_in)
            row_in.addStretch()
            layout.addLayout(row_in)

            layout.addWidget(QLabel("過去論文画像コーパス（任意・H3）"))
            self.corpus_list = DropList("任意: 過去 Figure 画像フォルダをドロップ")
            layout.addWidget(self.corpus_list)
            row_c = QHBoxLayout()
            btn_corpus = QPushButton("コーパスを追加…")
            btn_corpus.clicked.connect(self._add_corpus)
            btn_clear_c = QPushButton("クリア")
            btn_clear_c.clicked.connect(
                lambda: (self.corpus_list.clear(), self.corpus_list._refresh_placeholder())
            )
            row_c.addWidget(btn_corpus)
            row_c.addWidget(btn_clear_c)
            row_c.addStretch()
            layout.addLayout(row_c)

            out_row = QHBoxLayout()
            out_row.addWidget(QLabel("レポート出力"))
            self.out_edit = QLineEdit(str(Path("outputs/report.html").resolve()))
            out_row.addWidget(self.out_edit)
            browse = QPushButton("…")
            browse.clicked.connect(self._browse_out)
            out_row.addWidget(browse)
            layout.addLayout(out_row)

            llm_mode_row = QHBoxLayout()
            llm_mode_row.addWidget(QLabel("Legend LLM 補助"))
            self.legend_llm = QComboBox()
            self.legend_llm.addItem("自動（規則で n を読み切れなかった Figure だけ）", "auto")
            self.legend_llm.addItem("常に（全 Figure を LLM で読む）", "on")
            self.legend_llm.addItem("オフ（規則のみ）", "off")
            llm_mode_row.addWidget(self.legend_llm)
            layout.addLayout(llm_mode_row)

            try:
                from pre_peer_checker.llm.registry import list_profiles, load_registry

                _, default_llm, default_vlm = load_registry()
                llm_row = QHBoxLayout()
                llm_row.addWidget(QLabel("LLM"))
                self.llm_profile = QComboBox()
                for p in list_profiles("text"):
                    self.llm_profile.addItem(p.label, p.id)
                idx = self.llm_profile.findData(default_llm)
                if idx >= 0:
                    self.llm_profile.setCurrentIndex(idx)
                llm_row.addWidget(self.llm_profile, 1)
                layout.addLayout(llm_row)

                vlm_row = QHBoxLayout()
                vlm_row.addWidget(QLabel("VLM"))
                self.vlm_profile = QComboBox()
                for p in list_profiles("vision"):
                    self.vlm_profile.addItem(p.label, p.id)
                idx = self.vlm_profile.findData(default_vlm)
                if idx >= 0:
                    self.vlm_profile.setCurrentIndex(idx)
                vlm_row.addWidget(self.vlm_profile, 1)
                layout.addLayout(vlm_row)
            except Exception:
                self.llm_profile = None
                self.vlm_profile = None


            self.run_btn = QPushButton("実行")
            self.run_btn.setMinimumHeight(40)
            self.run_btn.setStyleSheet(
                "QPushButton { background: #111827; color: white; font-size: 15px; "
                "border-radius: 8px; padding: 8px 16px; }"
                "QPushButton:disabled { background: #9ca3af; }"
            )
            self.run_btn.clicked.connect(self._start)
            layout.addWidget(self.run_btn)

            self.progress = QProgressBar()
            self.progress.setRange(0, 0)
            self.progress.hide()
            layout.addWidget(self.progress)
            self.status = QLabel("待機中")
            self.status.setStyleSheet("color: #6b7280;")
            layout.addWidget(self.status)

            self.table = QTableWidget(0, 4)
            self.table.setHorizontalHeaderLabels(["タグ", "タイトル", "場所", "pattern_id"])
            self.table.horizontalHeader().setStretchLastSection(True)
            layout.addWidget(self.table)

            open_row = QHBoxLayout()
            self.open_btn = QPushButton("HTML レポートを開く")
            self.open_btn.setEnabled(False)
            self.open_btn.clicked.connect(self._open_report)
            open_row.addWidget(self.open_btn)
            open_row.addStretch()
            layout.addLayout(open_row)
            self._last_report: Path | None = None

        def _add_input(self) -> None:
            d = QFileDialog.getExistingDirectory(self, "入力フォルダ")
            if d:
                self.input_list.add_path(Path(d))

        def _add_corpus(self) -> None:
            d = QFileDialog.getExistingDirectory(self, "コーパスフォルダ")
            if d:
                self.corpus_list.add_path(Path(d))

        def _browse_out(self) -> None:
            path, _ = QFileDialog.getSaveFileName(
                self, "レポート保存先", self.out_edit.text(), "HTML (*.html)"
            )
            if path:
                self.out_edit.setText(path)

        def _start(self) -> None:
            inputs = self.input_list.paths()
            if not inputs:
                QMessageBox.warning(self, "入力なし", "入力フォルダを追加してください。")
                return
            out = Path(self.out_edit.text())
            config = GuiRunConfig(
                inputs=inputs,
                corpus=self.corpus_list.paths(),
                output_html=out,
                output_json=out.with_suffix(".json"),
                legend_llm=self.legend_llm.currentData(),
                legend_llm_profile=(
                    self.llm_profile.currentData() if self.llm_profile is not None else None
                ),
                vlm_profile=(
                    self.vlm_profile.currentData() if self.vlm_profile is not None else None
                ),
            )
            self.run_btn.setEnabled(False)
            self.progress.show()
            self.open_btn.setEnabled(False)
            self.table.setRowCount(0)
            self._thread = VerifyThread(config)
            self._thread.status.connect(self.status.setText)
            self._thread.finished_ok.connect(self._on_ok)
            self._thread.failed.connect(self._on_fail)
            self._thread.start()

        def _on_ok(self, result) -> None:
            self.progress.hide()
            self.run_btn.setEnabled(True)
            self.status.setText(f"完了 — Warning {result.n_warnings} 件")
            self._last_report = result.report_path
            self.open_btn.setEnabled(bool(result.report_path))
            self.table.setRowCount(len(result.warning_rows))
            for r, row in enumerate(result.warning_rows):
                self.table.setItem(r, 0, QTableWidgetItem(str(row.get("tag", ""))))
                self.table.setItem(r, 1, QTableWidgetItem(str(row.get("title", ""))))
                self.table.setItem(r, 2, QTableWidgetItem(str(row.get("location", ""))))
                self.table.setItem(r, 3, QTableWidgetItem(str(row.get("pattern_id", ""))))
            self.table.resizeColumnsToContents()

        def _on_fail(self, message: str) -> None:
            self.progress.hide()
            self.run_btn.setEnabled(True)
            self.status.setText("失敗")
            QMessageBox.critical(self, "検証エラー", message)

        def _open_report(self) -> None:
            if not self._last_report or not self._last_report.exists():
                return
            from PyQt6.QtCore import QUrl
            from PyQt6.QtGui import QDesktopServices

            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._last_report.resolve())))

    app = QApplication(argv or sys.argv)
    app.setApplicationName("pre-peer-checker")
    win = MainWindow()
    win.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
