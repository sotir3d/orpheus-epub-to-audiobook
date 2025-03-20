import sys
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
                               QListWidget, QListWidgetItem, QPushButton, QLabel, QComboBox,
                               QProgressBar, QFileDialog, QMessageBox, QCheckBox, QDoubleSpinBox,
                               QSpinBox, QTextEdit)
from PySide6.QtCore import Qt, QThread, Signal, QObject
import epub_to_speech

class ConversionWorker(QObject):
    progress = Signal(str, int, int)
    finished = Signal()
    error = Signal(str)

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

    def run(self):
        try:
            book_title, chapters = epub_to_speech.extract_chapters_from_epub(self.epub_path)
            if not chapters:
                self.error.emit("No chapters found in EPUB file")
                return

            # Process only selected chapters
            selected_chapters = [chapters[i] for i in self.selected_chapters]
            total_chapters = len(selected_chapters)

            # Create output directory
            output_dir = self.output_dir or f"outputs/epub_{book_title.replace(' ', '_')}"
            epub_to_speech.ensure_directory_exists(output_dir)

            chapter_files = []
            for idx, chapter in enumerate(selected_chapters):
                if not self._is_running:
                    break

                self.progress.emit(chapter['title'], idx+1, total_chapters)

                safe_title = epub_to_speech.re.sub(r'[^\w\s-]', '', chapter['title']).strip().replace(' ', '_')
                output_file = f"{output_dir}/{idx+1:03d}_{safe_title}.wav"

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
                    }
                )
                chapter_files.append(output_file)

            if self._is_running and chapter_files:
                output_wav = f"{output_dir}/{book_title}_complete.wav"
                epub_to_speech.merge_chapter_wav_files(chapter_files, output_wav, create_m4b=True)
                self.progress.emit("Conversion complete!", total_chapters, total_chapters)

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

        # Worker thread
        self.worker = None
        self.thread = None

        self.init_ui()

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
        self.chapter_list.setSelectionMode(QListWidget.MultiSelection)

        # Selection controls
        btn_layout = QHBoxLayout()
        select_all_btn = QPushButton("Select All")
        select_all_btn.clicked.connect(lambda: self.toggle_selection(True))
        deselect_all_btn = QPushButton("Deselect All")
        deselect_all_btn.clicked.connect(lambda: self.toggle_selection(False))
        btn_layout.addWidget(select_all_btn)
        btn_layout.addWidget(deselect_all_btn)

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
        self.temp_spin.setRange(0.1, 2.0)
        self.temp_spin.setValue(epub_to_speech.TEMPERATURE)
        param_layout.addWidget(self.temp_spin)

        param_layout.addWidget(QLabel("Top P:"))
        self.top_p_spin = QDoubleSpinBox()
        self.top_p_spin.setRange(0.1, 1.0)
        self.top_p_spin.setValue(epub_to_speech.TOP_P)
        param_layout.addWidget(self.top_p_spin)

        param_layout.addWidget(QLabel("Rep. Penalty:"))
        self.rep_penalty_spin = QDoubleSpinBox()
        self.rep_penalty_spin.setRange(1.0, 2.0)
        self.rep_penalty_spin.setValue(epub_to_speech.REPETITION_PENALTY)
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
            item.setSelected(select)

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
            self.toggle_selection(True)

    def start_conversion(self):
        if not self.file_label.text() or self.file_label.text() == "No EPUB file selected":
            QMessageBox.warning(self, "Error", "Please select an EPUB file first")
            return

        selected_chapters = [i for i in range(self.chapter_list.count())
                            if self.chapter_list.item(i).checkState() == Qt.Checked]
        if not selected_chapters:
            QMessageBox.warning(self, "Error", "Please select at least one chapter")
            return

        # Get parameters
        params = {
            'epub_path': self.file_label.text(),
            'voice': self.voice_combo.currentText(),
            'output_dir': self.output_label.text() if self.output_label.text() != "Default output directory" else None,
            'temperature': self.temp_spin.value(),
            'top_p': self.top_p_spin.value(),
            'repetition_penalty': self.rep_penalty_spin.value(),
            'selected_chapters': selected_chapters
        }

        # Setup worker thread
        self.thread = QThread()
        self.worker = ConversionWorker(**params)
        self.worker.moveToThread(self.thread)

        # Connect signals
        self.thread.started.connect(self.worker.run)
        self.worker.progress.connect(self.update_progress)
        self.worker.finished.connect(self.thread.quit)
        self.worker.finished.connect(self.worker.deleteLater)
        self.thread.finished.connect(self.thread.deleteLater)
        self.worker.error.connect(self.show_error)

        # Start thread
        self.thread.start()

        # UI updates
        self.start_btn.setEnabled(False)
        self.start_btn.setText("Converting...")

    def update_progress(self, message, current, total):
        self.progress_bar.setMaximum(total)
        self.progress_bar.setValue(current)
        self.progress_label.setText(f"Processing: {message}")
        self.log_area.append(f"Chapter {current}/{total}: {message}")

    def show_error(self, message):
        QMessageBox.critical(self, "Error", message)
        self.reset_ui()

    def reset_ui(self):
        self.start_btn.setEnabled(True)
        self.start_btn.setText("Start Conversion")
        self.progress_bar.reset()
        self.progress_label.setText("Ready")

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())