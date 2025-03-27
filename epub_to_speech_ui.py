import os
import time
import sys
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
                               QListWidget, QListWidgetItem, QPushButton, QLabel, QComboBox,
                               QProgressBar, QFileDialog, QMessageBox, QCheckBox, QDoubleSpinBox,
                               QSpinBox, QTextEdit)
from PySide6.QtCore import Qt, QThread, Signal, QObject
import epub_to_speech


class ConversionWorker(QObject):
    progress = Signal(int, int)  # current, total
    log_message = Signal(str)
    finished = Signal()
    error = Signal(str)
    overwrite_required = Signal(str, str)

    def __init__(self, epub_path, voice, output_dir, temperature, top_p, repetition_penalty, selected_chapters):
        super().__init__()
        self.epub_path = epub_path
        self.voice = voice
        self.output_dir = output_dir
        self.temperature = temperature
        self.top_p = top_p
        self.repetition_penalty = repetition_penalty
        self.selected_chapters = selected_chapters
        self._is_running = True
        self.overwrite_confirmed = False

    def run(self):
        try:
            book_title, chapters = epub_to_speech.extract_chapters_from_epub(self.epub_path)
            if not chapters:
                self.error.emit("No chapters found in EPUB file")
                return

            selected_chapters = [chapters[i] for i in self.selected_chapters]
            total_chapters = len(selected_chapters)
            self.log_message.emit(f"Starting conversion of {total_chapters} chapters...")

            output_dir = self.output_dir or f"outputs/epub_{book_title.replace(' ', '_')}"
            epub_to_speech.ensure_directory_exists(output_dir)

            chapter_files = []
            for idx, chapter in enumerate(selected_chapters):
                if not self._is_running:
                    break

                self.log_message.emit(f"\n▶ Processing chapter {idx + 1}/{total_chapters}: {chapter['title']}")
                self.progress.emit(idx + 1, total_chapters)

                safe_title = epub_to_speech.re.sub(r'[^\w\s-]', '', chapter['title']).strip().replace(' ', '_')
                output_file = f"{output_dir}/{idx + 1:03d}_{safe_title}.wav"

                # Create custom logger for chunk-level logging
                def chunk_logger(msg):
                    self.log_message.emit(f"  {msg}")

                epub_to_speech.process_text_in_chunks(
                    text=chapter['content'],
                    voice=self.voice,
                    temperature=self.temperature,
                    top_p=self.top_p,
                    repetition_penalty=self.repetition_penalty,
                    output_file=output_file,
                    chapter_info={
                        'index': idx,
                        'title': chapter['title'],
                        'total': total_chapters
                    },
                    log_callback=chunk_logger
                )
                chapter_files.append(output_file)
                self.log_message.emit(f"✓ Chapter {idx + 1} completed")

            if self._is_running and chapter_files:
                self.log_message.emit("\nMerging chapters into final audiobook...")
                output_wav = f"{output_dir}/{book_title}_complete.wav"
                output_m4b = os.path.splitext(output_wav)[0] + ".m4b"

                if os.path.exists(output_wav) or os.path.exists(output_m4b):
                    self.overwrite_required.emit(output_wav, output_m4b)
                    # Wait for response (using a simple flag)
                    while not hasattr(self, 'overwrite_response'):
                        time.sleep(0.1)
                    if not self.overwrite_confirmed:
                        self.log_message.emit("Conversion aborted by user.")
                        return
                epub_to_speech.merge_chapter_wav_files(chapter_files, output_wav, create_m4b=True, silent=True)
                self.log_message.emit(f"\n✅ All chapters merged into {output_wav}")

            self.finished.emit()

        except Exception as e:
            self.error.emit(str(e))

    def stop(self):
        self._is_running = False


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("EPUB to Audiobook Converter")
        self.setGeometry(100, 100, 800, 600)

        self.worker = None
        self.thread = None

        self.init_ui()

    def handle_overwrite(self, output_wav, output_m4b):
        reply = QMessageBox.question(
            self, 'Overwrite?',
            f"Files {output_wav} or {output_m4b} exist. Overwrite?",
            QMessageBox.Yes | QMessageBox.No
        )
        self.worker.overwrite_confirmed = reply == QMessageBox.Yes
        self.worker.overwrite_response = True  # Signal to continue

    def init_ui(self):
        main_widget = QWidget()
        layout = QVBoxLayout()

        # File selection
        file_layout = QHBoxLayout()
        self.file_label = QLabel("No EPUB file selected")
        file_btn = QPushButton("Choose EPUB...")
        file_btn.clicked.connect(self.select_epub)
        file_layout.addWidget(self.file_label)
        file_layout.addWidget(file_btn)

        # Chapter list
        self.chapter_list = QListWidget()
        self.chapter_list.setSelectionMode(QListWidget.ExtendedSelection)

        # Selection controls
        btn_layout = QHBoxLayout()
        select_all_btn = QPushButton("Select All")
        select_all_btn.clicked.connect(lambda: self.toggle_selection(True))
        deselect_all_btn = QPushButton("Deselect All")
        deselect_all_btn.clicked.connect(lambda: self.toggle_selection(False))
        btn_layout.addWidget(select_all_btn)
        btn_layout.addWidget(deselect_all_btn)
        select_highlighted_btn = QPushButton("Select Highlighted")
        select_highlighted_btn.clicked.connect(self.select_highlighted)
        deselect_highlighted_btn = QPushButton("Deselect Highlighted")
        deselect_highlighted_btn.clicked.connect(self.deselect_highlighted)
        btn_layout.addWidget(select_highlighted_btn)
        btn_layout.addWidget(deselect_highlighted_btn)

        # Voice selection
        voice_layout = QHBoxLayout()
        voice_layout.addWidget(QLabel("Voice:"))
        self.voice_combo = QComboBox()
        self.voice_combo.addItems(epub_to_speech.AVAILABLE_VOICES)
        self.voice_combo.setCurrentText(epub_to_speech.DEFAULT_VOICE)
        voice_layout.addWidget(self.voice_combo)

        # Parameters
        param_layout = QHBoxLayout()
        param_layout.addWidget(QLabel("Temperature:"))
        self.temp_spin = QDoubleSpinBox()
        self.temp_spin.setRange(0, 2.0)
        self.temp_spin.setValue(epub_to_speech.TEMPERATURE)
        self.temp_spin.setSingleStep(0.01)
        param_layout.addWidget(self.temp_spin)

        param_layout.addWidget(QLabel("Top P:"))
        self.top_p_spin = QDoubleSpinBox()
        self.top_p_spin.setRange(0, 1.0)
        self.top_p_spin.setValue(epub_to_speech.TOP_P)
        self.top_p_spin.setSingleStep(0.01)
        param_layout.addWidget(self.top_p_spin)

        param_layout.addWidget(QLabel("Rep. Penalty:"))
        self.rep_penalty_spin = QDoubleSpinBox()
        self.rep_penalty_spin.setRange(1.0, 2.0)
        self.rep_penalty_spin.setValue(epub_to_speech.REPETITION_PENALTY)
        self.rep_penalty_spin.setSingleStep(0.01)
        param_layout.addWidget(self.rep_penalty_spin)

        # Output directory
        output_layout = QHBoxLayout()
        self.output_label = QLabel("Default output directory")
        output_btn = QPushButton("Choose Output...")
        output_btn.clicked.connect(self.select_output)
        output_layout.addWidget(self.output_label)
        output_layout.addWidget(output_btn)

        # Progress
        self.progress_bar = QProgressBar()
        self.progress_label = QLabel("Ready")

        # Log
        self.log_area = QTextEdit()
        self.log_area.setReadOnly(True)

        # Start button
        self.start_btn = QPushButton("Start Conversion")
        self.start_btn.clicked.connect(self.start_conversion)

        # Assemble layout
        layout.addLayout(file_layout)
        layout.addWidget(QLabel("Chapters:"))
        layout.addWidget(self.chapter_list)
        layout.addLayout(btn_layout)
        layout.addLayout(voice_layout)
        layout.addLayout(param_layout)
        layout.addLayout(output_layout)
        layout.addWidget(self.progress_label)
        layout.addWidget(self.progress_bar)
        layout.addWidget(self.log_area)
        layout.addWidget(self.start_btn)

        main_widget.setLayout(layout)
        self.setCentralWidget(main_widget)

    def toggle_selection(self, select):
        for i in range(self.chapter_list.count()):
            item = self.chapter_list.item(i)
            item.setCheckState(Qt.Checked if select else Qt.Unchecked)

    def select_epub(self):
        path, _ = QFileDialog.getOpenFileName(self, "Select EPUB file", "", "EPUB files (*.epub)")
        if path:
            self.file_label.setText(path)
            self.load_chapters(path)

    def select_output(self):
        path = QFileDialog.getExistingDirectory(self, "Select Output Directory")
        if path:
            self.output_label.setText(path)

    def load_chapters(self, epub_path):
        self.chapter_list.clear()
        book_title, chapters = epub_to_speech.extract_chapters_from_epub(epub_path)
        if chapters:
            for chapter in chapters:
                item = QListWidgetItem(chapter['title'])
                item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
                item.setCheckState(Qt.Checked)
                self.chapter_list.addItem(item)

    def start_conversion(self):
        if not self.file_label.text() or self.file_label.text() == "No EPUB file selected":
            QMessageBox.warning(self, "Error", "Please select an EPUB file first")
            return

        selected_chapters = [i for i in range(self.chapter_list.count())
                             if self.chapter_list.item(i).checkState() == Qt.Checked]
        if not selected_chapters:
            QMessageBox.warning(self, "Error", "Please select at least one chapter")
            return

        params = {
            'epub_path': self.file_label.text(),
            'voice': self.voice_combo.currentText(),
            'output_dir': self.output_label.text() if self.output_label.text() != "Default output directory" else None,
            'temperature': self.temp_spin.value(),
            'top_p': self.top_p_spin.value(),
            'repetition_penalty': self.rep_penalty_spin.value(),
            'selected_chapters': selected_chapters
        }

        # Clear previous logs
        self.log_area.clear()

        # Setup worker thread
        self.thread = QThread()
        self.worker = ConversionWorker(**params)
        self.worker.moveToThread(self.thread)

        self.worker.overwrite_required.connect(self.handle_overwrite)

        # Connect signals
        self.thread.started.connect(self.worker.run)
        self.worker.progress.connect(self.update_progress)
        self.worker.log_message.connect(self.log_area.append)
        self.worker.finished.connect(self.thread.quit)
        self.worker.finished.connect(self.worker.deleteLater)
        self.thread.finished.connect(self.thread.deleteLater)
        self.worker.error.connect(self.show_error)

        # Start thread
        self.thread.start()

        # UI updates
        self.start_btn.setEnabled(False)
        self.start_btn.setText("Converting...")

    def update_progress(self, current, total):
        self.progress_bar.setMaximum(total)
        self.progress_bar.setValue(current)
        self.progress_label.setText(f"Progress: {current}/{total} chapters")

    def show_error(self, message):
        QMessageBox.critical(self, "Error", message)
        self.log_area.append(f"\n❌ Error: {message}")
        self.reset_ui()

    def reset_ui(self):
        self.start_btn.setEnabled(True)
        self.start_btn.setText("Start Conversion")
        self.progress_bar.reset()
        self.progress_label.setText("Ready")

    def select_highlighted(self):
        selected_items = self.chapter_list.selectedItems()
        for item in selected_items:
            item.setCheckState(Qt.Checked)

    def deselect_highlighted(self):
        selected_items = self.chapter_list.selectedItems()
        for item in selected_items:
            item.setCheckState(Qt.Unchecked)

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())