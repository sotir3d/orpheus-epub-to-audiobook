# --- START OF FILE epub_to_speech_ui.py ---

import os
import sys
import time
from datetime import datetime
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
                               QListWidget, QListWidgetItem, QPushButton, QLabel, QComboBox,
                               QProgressBar, QFileDialog, QMessageBox, QCheckBox, QDoubleSpinBox,
                               QSpinBox, QTextEdit, QGroupBox, QFormLayout, QSizePolicy,
                               QStatusBar) # Added QGroupBox, QFormLayout, QSizePolicy, QStatusBar
from PySide6.QtCore import Qt, QThread, Signal, QObject, QTimer # Added QTimer for delayed stop state change
from PySide6.QtGui import QPalette, QColor # Added for highlighting

# Assuming epub_to_speech.py is in the same directory or accessible via PYTHONPATH
import epub_to_speech

class ConversionWorker(QObject):
    progress = Signal(int, int, str)  # current_chap_num, total_chapters, chapter_title
    processing_chapter_index = Signal(int) # Index in the QListWidget
    log_message = Signal(str)
    finished = Signal(bool) # True if completed, False if stopped
    error = Signal(str)
    overwrite_required = Signal(str, str) # wav_path, m4b_path

    def __init__(self, epub_path, voice, output_dir, temperature, top_p, repetition_penalty, selected_chapter_indices):
        super().__init__()
        self.epub_path = epub_path
        self.voice = voice
        self.output_dir = output_dir
        self.temperature = temperature
        self.top_p = top_p
        self.repetition_penalty = repetition_penalty
        self.selected_chapter_indices = selected_chapter_indices # Store indices directly
        self._is_running = True
        self.overwrite_response = None # None = pending, True = Yes, False = No
        self.book_title = "Unknown Book" # Store book title

    def run(self):
        try:
            self.book_title, all_chapters = epub_to_speech.extract_chapters_from_epub(self.epub_path)
            if not all_chapters:
                self.error.emit("No chapters found in EPUB file")
                return

            # Filter chapters based on selected indices
            selected_chapters_data = [(idx, all_chapters[idx]) for idx in self.selected_chapter_indices]
            total_chapters_to_process = len(selected_chapters_data)

            self.log_message.emit(f"Starting conversion of {total_chapters_to_process} chapters for '{self.book_title}'...")

            # Determine effective output directory
            effective_output_dir = self.output_dir
            if not effective_output_dir:
                 safe_book_title = epub_to_speech.re.sub(r'[^\w\s-]', '', self.book_title).strip().replace(' ', '_')
                 effective_output_dir = f"outputs/epub_{safe_book_title}"

            epub_to_speech.ensure_directory_exists(effective_output_dir)
            self.log_message.emit(f"Output directory: {os.path.abspath(effective_output_dir)}")

            chapter_files = []
            for i, (original_index, chapter) in enumerate(selected_chapters_data):
                if not self._is_running:
                    self.log_message.emit("Conversion stopped by user.")
                    self.finished.emit(False) # Indicate stopped
                    return

                self.log_message.emit(f"\n▶ Processing chapter {i + 1}/{total_chapters_to_process}: {chapter['title']}")
                self.processing_chapter_index.emit(original_index) # Emit the original index for UI highlighting
                self.progress.emit(i + 1, total_chapters_to_process, chapter['title'])

                safe_title = epub_to_speech.re.sub(r'[^\w\s-]', '', chapter['title']).strip().replace(' ', '_')
                # Use original index for filename consistency if chapters are skipped
                output_file = f"{effective_output_dir}/{original_index + 1:03d}_{safe_title}.wav"

                # Create custom logger for chunk-level logging
                def chunk_logger(msg):
                    self.log_message.emit(f"  {msg}")

                # Check if individual chapter file exists
                if os.path.exists(output_file):
                    # Simple overwrite for now, could add more granular control
                    self.log_message.emit(f"  WARNING: Overwriting existing chapter file: {output_file}")

                try:
                    epub_to_speech.process_text_in_chunks(
                        text=chapter['content'],
                        voice=self.voice,
                        temperature=self.temperature,
                        top_p=self.top_p,
                        repetition_penalty=self.repetition_penalty,
                        output_file=output_file,
                        chapter_info={
                            'index': i, # Use sequential index for logging within this run
                            'title': chapter['title'],
                            'total': total_chapters_to_process
                        },
                        log_callback=chunk_logger
                    )
                    chapter_files.append(output_file)
                    self.log_message.emit(f"✓ Chapter {i + 1} completed.")
                except Exception as chapter_exc:
                    self.log_message.emit(f"❌ ERROR processing chapter {i + 1}: {chapter['title']} - {chapter_exc}")
                    # Option: Continue with next chapter or stop? Currently continues.
                    # self.error.emit(f"Error in chapter '{chapter['title']}': {chapter_exc}")
                    # return # Uncomment to stop on chapter error

            if not self._is_running: # Check again before merging
                 self.log_message.emit("Conversion stopped before merging.")
                 self.finished.emit(False)
                 return

            if chapter_files:
                self.log_message.emit("\nMerging chapters into final audiobook...")
                safe_book_title = epub_to_speech.re.sub(r'[^\w\s-]', '', self.book_title).strip().replace(' ', '_')
                output_wav = f"{effective_output_dir}/{safe_book_title}_complete.wav"
                output_m4b = os.path.splitext(output_wav)[0] + ".m4b"

                if os.path.exists(output_wav) or os.path.exists(output_m4b):
                    self.overwrite_response = None # Reset response flag
                    self.overwrite_required.emit(output_wav, output_m4b)
                    # Wait for response from the main thread
                    while self.overwrite_response is None:
                        if not self._is_running: # Allow stopping while waiting for dialog
                            self.log_message.emit("Conversion stopped while waiting for overwrite confirmation.")
                            self.finished.emit(False)
                            return
                        time.sleep(0.1)

                    if self.overwrite_response is False:
                        self.log_message.emit("Merging aborted by user (overwrite denied).")
                        self.finished.emit(False) # Treat as stopped/cancelled
                        return
                    else:
                         self.log_message.emit("Overwrite confirmed by user.")


                # Ensure the list of files to merge actually exists before calling merge
                existing_chapter_files = [f for f in chapter_files if os.path.exists(f)]
                if not existing_chapter_files:
                    self.log_message.emit("No valid chapter audio files found to merge.")
                    self.error.emit("No chapter audio files were successfully created.")
                    return
                if len(existing_chapter_files) != len(chapter_files):
                     self.log_message.emit(f"Warning: Merging only {len(existing_chapter_files)} out of {len(chapter_files)} expected chapter files.")


                merge_success = epub_to_speech.merge_chapter_wav_files(
                    existing_chapter_files,
                    output_wav,
                    create_m4b=True,
                    silent=False # Show ffmpeg output in console
                )
                if merge_success:
                    self.log_message.emit(f"\n✅ All chapters merged into {output_wav} (and .m4b)")
                    # Optional: Clean up individual chapter WAVs?
                    # for f in existing_chapter_files:
                    #     try: os.remove(f)
                    #     except OSError: pass
                    # self.log_message.emit("Cleaned up individual chapter WAV files.")
                else:
                    self.log_message.emit(f"\n❌ Failed to merge chapters or create M4B.")
                    # Keep individual files if merge fails

            else:
                 self.log_message.emit("\nNo chapters were processed successfully, skipping merge.")


            self.finished.emit(True) # Indicate successful completion

        except Exception as e:
            import traceback
            self.log_message.emit(f"\n❌ An unexpected error occurred: {e}")
            self.log_message.emit(traceback.format_exc())
            self.error.emit(str(e))
        finally:
             # Ensure the worker signals it's done stopping in case of error/stop
             self._is_running = False


    def stop(self):
        self.log_message.emit("Stop signal received...")
        self._is_running = False
        # If waiting for overwrite confirmation, set response to False to break loop
        if self.overwrite_response is None:
            self.overwrite_response = False


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("EPUB to Audiobook Converter")
        self.setGeometry(100, 100, 900, 700) # Slightly larger window

        self.worker = None
        self.thread = None
        self.current_epub_path = None
        self.current_output_dir = None
        self.book_title = None
        self.all_chapters_data = [] # Store chapter data {'title': '...', 'content': '...'}
        self.highlighted_chapter_item = None
        self.normal_palette = self.palette() # Store default palette
        self.highlight_palette = QPalette()
        # self.highlight_palette.setColor(QPalette.Base, QColor("lightyellow")) # OLD
        self.highlight_palette.setColor(QPalette.Base, QColor(75, 75, 75))  # NEW - A subtle gray highlight
        self.highlight_palette.setColor(QPalette.Text, QColor("white"))  # Ensure text is visible on dark highlight


        self.init_ui()
        self.update_status("Ready")

    def init_ui(self):
        main_widget = QWidget()
        main_layout = QVBoxLayout(main_widget)

        # --- File Selection Group ---
        file_group = QGroupBox("EPUB File")
        file_layout = QHBoxLayout()
        self.file_label = QLabel("No EPUB file selected")
        self.file_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self.file_label.setWordWrap(True)
        self.select_epub_btn = QPushButton("Choose EPUB...")
        self.select_epub_btn.clicked.connect(self.select_epub)
        file_layout.addWidget(self.file_label)
        file_layout.addWidget(self.select_epub_btn)
        file_group.setLayout(file_layout)

        # --- Chapters Group ---
        chapter_group = QGroupBox("Chapters")
        chapter_layout = QVBoxLayout()
        self.chapter_list = QListWidget()
        self.chapter_list.setSelectionMode(QListWidget.ExtendedSelection)
        chapter_buttons_layout = QHBoxLayout()
        select_all_btn = QPushButton("Check All")
        select_all_btn.clicked.connect(lambda: self.toggle_check_all(True))
        deselect_all_btn = QPushButton("Uncheck All")
        deselect_all_btn.clicked.connect(lambda: self.toggle_check_all(False))
        check_selected_btn = QPushButton("Check Highlighted")
        check_selected_btn.setToolTip("Check the chapters currently highlighted in the list.")
        check_selected_btn.clicked.connect(self.check_highlighted)
        uncheck_selected_btn = QPushButton("Uncheck Highlighted")
        uncheck_selected_btn.setToolTip("Uncheck the chapters currently highlighted in the list.")
        uncheck_selected_btn.clicked.connect(self.uncheck_highlighted)
        chapter_buttons_layout.addWidget(select_all_btn)
        chapter_buttons_layout.addWidget(deselect_all_btn)
        chapter_buttons_layout.addStretch()
        chapter_buttons_layout.addWidget(check_selected_btn)
        chapter_buttons_layout.addWidget(uncheck_selected_btn)
        chapter_layout.addWidget(self.chapter_list)
        chapter_layout.addLayout(chapter_buttons_layout)
        chapter_group.setLayout(chapter_layout)

        # --- Parameters Group ---
        params_group = QGroupBox("Conversion Parameters")
        params_layout = QFormLayout() # Use QFormLayout for better label alignment
        self.voice_combo = QComboBox()
        self.voice_combo.addItems(epub_to_speech.AVAILABLE_VOICES)
        try:
            self.voice_combo.setCurrentText(epub_to_speech.DEFAULT_VOICE)
        except: pass # Ignore if default voice isn't in the list somehow
        self.voice_combo.setToolTip("Select the TTS voice.")
        params_layout.addRow(QLabel("Voice:"), self.voice_combo)

        self.temp_spin = QDoubleSpinBox()
        self.temp_spin.setRange(0.0, 2.0)
        self.temp_spin.setValue(epub_to_speech.TEMPERATURE)
        self.temp_spin.setSingleStep(0.05)
        self.temp_spin.setToolTip("Controls randomness. Lower values make the output more deterministic (0.0-2.0).")
        params_layout.addRow(QLabel("Temperature:"), self.temp_spin)

        self.top_p_spin = QDoubleSpinBox()
        self.top_p_spin.setRange(0.0, 1.0)
        self.top_p_spin.setValue(epub_to_speech.TOP_P)
        self.top_p_spin.setSingleStep(0.05)
        self.top_p_spin.setToolTip("Nucleus sampling. Considers tokens with cumulative probability >= top_p (0.0-1.0).")
        params_layout.addRow(QLabel("Top P:"), self.top_p_spin)

        self.rep_penalty_spin = QDoubleSpinBox()
        self.rep_penalty_spin.setRange(1.0, 3.0) # Allow higher range
        self.rep_penalty_spin.setValue(epub_to_speech.REPETITION_PENALTY)
        self.rep_penalty_spin.setSingleStep(0.05)
        self.rep_penalty_spin.setToolTip("Penalty for repeating tokens. Higher values reduce repetition (>=1.0).")
        params_layout.addRow(QLabel("Repetition Penalty:"), self.rep_penalty_spin)
        params_group.setLayout(params_layout)

        # --- Output Group ---
        output_group = QGroupBox("Output")
        output_layout = QHBoxLayout()
        self.output_label = QLabel("Default: ./outputs/epub_[Book Title]/")
        self.output_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self.output_label.setWordWrap(True)
        self.select_output_btn = QPushButton("Choose Directory...")
        self.select_output_btn.clicked.connect(self.select_output)
        output_layout.addWidget(self.output_label)
        output_layout.addWidget(self.select_output_btn)
        output_group.setLayout(output_layout)

        # --- Progress and Log Group ---
        progress_log_group = QGroupBox("Progress & Log")
        progress_log_layout = QVBoxLayout()
        self.progress_bar = QProgressBar()
        self.progress_bar.setTextVisible(True) # Show percentage
        self.progress_bar.setValue(0)
        self.log_area = QTextEdit()
        self.log_area.setReadOnly(True)
        self.log_area.setLineWrapMode(QTextEdit.WidgetWidth) # Wrap lines
        progress_log_layout.addWidget(self.progress_bar)
        progress_log_layout.addWidget(QLabel("Log:"))
        progress_log_layout.addWidget(self.log_area)
        progress_log_group.setLayout(progress_log_layout)

        # --- Control Buttons ---
        control_layout = QHBoxLayout()
        self.start_btn = QPushButton("Start Conversion")
        self.start_btn.setStyleSheet("background-color: darkseagreen; color: black;")
        self.start_btn.clicked.connect(self.start_conversion)
        self.stop_btn = QPushButton("Stop Conversion")
        self.stop_btn.setStyleSheet("background-color: indianred; color: black;")
        self.stop_btn.clicked.connect(self.stop_conversion)
        self.stop_btn.setEnabled(False)  # Initially disabled
        control_layout.addStretch()
        control_layout.addWidget(self.start_btn)
        control_layout.addWidget(self.stop_btn)
        control_layout.addStretch()


        # --- Status Bar ---
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        self.status_label = QLabel("Ready")
        self.status_bar.addWidget(self.status_label)


        # --- Assemble Main Layout ---
        main_layout.addWidget(file_group)
        main_layout.addWidget(chapter_group)

        # Params and Output side-by-side
        hbox_params_output = QHBoxLayout()
        # Add stretch factor (e.g., 1) to both group boxes
        hbox_params_output.addWidget(params_group, stretch=1)
        hbox_params_output.addWidget(output_group, stretch=1)
        main_layout.addLayout(hbox_params_output)

        main_layout.addWidget(progress_log_group)
        main_layout.addLayout(control_layout)

        self.setCentralWidget(main_widget)
        self.set_controls_enabled(True) # Enable controls initially


    def update_status(self, message):
        self.status_label.setText(message)
        print(f"Status: {message}") # Also print to console

    def append_log(self, message):
        """Appends a message to the log area with a timestamp."""
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.log_area.append(f"[{timestamp}] {message}")
        self.log_area.verticalScrollBar().setValue(self.log_area.verticalScrollBar().maximum()) # Auto-scroll

    def set_controls_enabled(self, enabled):
        """Enable or disable input controls."""
        self.select_epub_btn.setEnabled(enabled)
        self.chapter_list.setEnabled(enabled)

        # --- CORRECTED CODE for chapter buttons ---
        buttons_layout_item = self.chapter_list.parent().layout().itemAt(1)  # Get item at index 1
        if buttons_layout_item:
            buttons_layout = buttons_layout_item.layout()  # Get the QHBoxLayout
            if buttons_layout:
                for i in range(buttons_layout.count()):
                    widget_item = buttons_layout.itemAt(i)
                    if widget_item:
                        widget = widget_item.widget()
                        if widget:  # Check if it's a widget (not a spacer/stretch)
                            widget.setEnabled(enabled)
        # --- END CORRECTION ---

        self.voice_combo.setEnabled(enabled)
        self.temp_spin.setEnabled(enabled)
        self.top_p_spin.setEnabled(enabled)
        self.rep_penalty_spin.setEnabled(enabled)
        self.select_output_btn.setEnabled(enabled)

        # Handle start/stop buttons specifically
        if enabled:
            self.start_btn.setEnabled(True)
            self.stop_btn.setEnabled(False)  # Stop is only enabled during conversion
            self.start_btn.setText("Start Conversion")
        else:
            # When disabling controls (i.e., conversion starts)
            self.start_btn.setEnabled(False)
            self.stop_btn.setEnabled(True)
            self.start_btn.setText("Converting...")


    def select_epub(self):
        path, _ = QFileDialog.getOpenFileName(self, "Select EPUB file", "", "EPUB files (*.epub)")
        if path:
            self.current_epub_path = path
            self.file_label.setText(os.path.basename(path))
            self.file_label.setToolTip(path)
            self.update_status(f"Loading chapters from {os.path.basename(path)}...")
            self.log_area.clear() # Clear log for new book
            self.append_log(f"Selected EPUB: {path}")
            QApplication.processEvents() # Update UI
            self.load_chapters(path)


    def select_output(self):
        # Use the directory of the current epub as a starting point, if available
        start_dir = os.path.dirname(self.current_epub_path) if self.current_epub_path else ""
        path = QFileDialog.getExistingDirectory(self, "Select Output Directory", start_dir)
        if path:
            self.current_output_dir = path
            self.output_label.setText(f"Output to: {path}")
            self.output_label.setToolTip(path)
            self.append_log(f"Set output directory: {path}")


    def load_chapters(self, epub_path):
        self.chapter_list.clear()
        self.all_chapters_data = []
        self.book_title = None
        try:
            self.book_title, chapters_data = epub_to_speech.extract_chapters_from_epub(epub_path)
            self.all_chapters_data = chapters_data
            if self.book_title and not self.current_output_dir:
                 safe_book_title = epub_to_speech.re.sub(r'[^\w\s-]', '', self.book_title).strip().replace(' ', '_')
                 default_output = os.path.abspath(f"outputs/epub_{safe_book_title}")
                 self.output_label.setText(f"Default: {default_output}")
                 self.output_label.setToolTip(f"Default output directory: {default_output}")

            if chapters_data:
                self.append_log(f"Found {len(chapters_data)} chapters in '{self.book_title}'.")
                for i, chapter in enumerate(chapters_data):
                    item = QListWidgetItem(f"{i+1:03d}: {chapter['title']}")
                    item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
                    item.setCheckState(Qt.Checked) # Default to checked
                    self.chapter_list.addItem(item)
                self.update_status(f"Ready to convert '{self.book_title}'")
            else:
                self.append_log("No chapters found or EPUB could not be parsed correctly.")
                QMessageBox.warning(self, "No Chapters", "Could not find any valid chapters in the selected EPUB file.")
                self.update_status("Error loading chapters")

        except Exception as e:
            self.append_log(f"Error loading EPUB: {e}")
            QMessageBox.critical(self, "EPUB Load Error", f"Failed to load chapters from EPUB:\n{e}")
            self.update_status("Error loading EPUB")


    def toggle_check_all(self, check):
        for i in range(self.chapter_list.count()):
            item = self.chapter_list.item(i)
            item.setCheckState(Qt.Checked if check else Qt.Unchecked)


    def check_highlighted(self):
        selected_items = self.chapter_list.selectedItems()
        if not selected_items:
            self.update_status("Select chapters in the list first to check them.")
            return
        for item in selected_items:
            item.setCheckState(Qt.Checked)
        self.update_status(f"Checked {len(selected_items)} highlighted chapters.")

    def uncheck_highlighted(self):
        selected_items = self.chapter_list.selectedItems()
        if not selected_items:
            self.update_status("Select chapters in the list first to uncheck them.")
            return
        for item in selected_items:
            item.setCheckState(Qt.Unchecked)
        self.update_status(f"Unchecked {len(selected_items)} highlighted chapters.")


    def start_conversion(self):
        if not self.current_epub_path:
            QMessageBox.warning(self, "Error", "Please select an EPUB file first.")
            return

        selected_chapter_indices = [i for i in range(self.chapter_list.count())
                                     if self.chapter_list.item(i).checkState() == Qt.Checked]
        if not selected_chapter_indices:
            QMessageBox.warning(self, "Error", "Please check at least one chapter to convert.")
            return

        # Clear previous highlighting
        self.reset_chapter_highlight()

        params = {
            'epub_path': self.current_epub_path,
            'voice': self.voice_combo.currentText(),
            'output_dir': self.current_output_dir, # Can be None
            'temperature': self.temp_spin.value(),
            'top_p': self.top_p_spin.value(),
            'repetition_penalty': self.rep_penalty_spin.value(),
            'selected_chapter_indices': selected_chapter_indices # Pass the list of indices
        }

        self.append_log("="*30 + " Starting Conversion " + "="*30)
        self.update_status("Starting conversion...")
        self.progress_bar.setValue(0)
        self.progress_bar.setFormat("Starting...")

        self.set_controls_enabled(False) # Disable inputs, enable Stop button

        # Setup worker thread
        self.thread = QThread(self)
        self.worker = ConversionWorker(**params)
        self.worker.moveToThread(self.thread)

        # Connect signals
        self.worker.progress.connect(self.update_progress)
        self.worker.processing_chapter_index.connect(self.highlight_current_chapter)
        self.worker.log_message.connect(self.append_log)
        self.worker.finished.connect(self.conversion_finished)
        self.worker.error.connect(self.conversion_error)
        self.worker.overwrite_required.connect(self.handle_overwrite_request)

        self.thread.started.connect(self.worker.run)
        self.thread.finished.connect(self.thread_cleanup) # Ensure cleanup

        # Start thread
        self.thread.start()


    def stop_conversion(self):
        if self.worker and self.thread and self.thread.isRunning():
            self.update_status("Stopping conversion...")
            self.append_log("Attempting to stop the conversion...")
            self.stop_btn.setEnabled(False) # Prevent multiple clicks
            self.worker.stop()
            # Don't re-enable controls immediately, wait for finished signal


    def conversion_finished(self, completed):
        """Slot called when worker signals finished."""
        if completed:
            self.update_status("Conversion completed successfully.")
            self.append_log("="*30 + " Conversion Finished " + "="*30)
            self.progress_bar.setValue(self.progress_bar.maximum()) # Ensure bar is full
            self.progress_bar.setFormat("Completed")
        else:
            # Could be stopped by user or aborted (e.g., overwrite denied)
             if not self.worker or self.worker._is_running is False: # Check if worker initiated the stop
                self.update_status("Conversion stopped.")
                self.append_log("="*30 + " Conversion Stopped " + "="*30)
                self.progress_bar.setFormat("Stopped")
             else: # Should not happen often, but handle unexpected finish(False)
                self.update_status("Conversion aborted.")
                self.append_log("="*30 + " Conversion Aborted " + "="*30)
                self.progress_bar.setFormat("Aborted")


        self.reset_ui_after_conversion()


    def conversion_error(self, message):
        """Slot called when worker signals an error."""
        self.update_status("Conversion failed with an error.")
        self.append_log(f"❌❌❌ ERROR: {message}")
        QMessageBox.critical(self, "Conversion Error", f"An error occurred during conversion:\n{message}")
        self.progress_bar.setFormat("Error")
        # Style progress bar red on error
        self.progress_bar.setStyleSheet("QProgressBar::chunk { background-color: red; }")

        self.reset_ui_after_conversion()

    def reset_ui_after_conversion(self):
         """Resets UI elements after conversion finishes, stops, or errors."""
         self.set_controls_enabled(True) # Re-enable controls, disable Stop button
         self.reset_chapter_highlight()
         # Reset progress bar style in case of error
         self.progress_bar.setStyleSheet("")


    def thread_cleanup(self):
         """Clean up thread and worker objects."""
         # Use a QTimer for delayed deletion to avoid issues if called directly from signal
         if self.worker:
             QTimer.singleShot(100, self.worker.deleteLater)
         if self.thread:
             QTimer.singleShot(100, self.thread.deleteLater)
         self.worker = None
         self.thread = None
         self.append_log("Worker thread cleaned up.")


    def update_progress(self, current_chap_num, total_chapters, chapter_title):
        self.progress_bar.setMaximum(total_chapters)
        self.progress_bar.setValue(current_chap_num)
        progress_percent = (current_chap_num / total_chapters) * 100 if total_chapters > 0 else 0
        self.progress_bar.setFormat(f"Chapter {current_chap_num}/{total_chapters} ({progress_percent:.0f}%)")
        self.update_status(f"Processing chapter {current_chap_num}/{total_chapters}: {chapter_title}")


    def highlight_current_chapter(self, index):
        """Highlights the item at the given index in the chapter list."""
        self.reset_chapter_highlight() # Clear previous highlight
        if 0 <= index < self.chapter_list.count():
            item = self.chapter_list.item(index)
            if item:
                # item.setBackground(QColor("lightyellow")) # Simple background color change
                item.listWidget().setPalette(self.highlight_palette) # Change palette for better visibility
                item.setSelected(True) # Also select it
                self.chapter_list.scrollToItem(item, QListWidget.ScrollHint.PositionAtCenter)
                self.highlighted_chapter_item = item


    def reset_chapter_highlight(self):
        """Resets the background color of the previously highlighted item."""
        if self.highlighted_chapter_item:
            # self.highlighted_chapter_item.setBackground(self.chapter_list.palette().base()) # Reset background
             self.highlighted_chapter_item.listWidget().setPalette(self.normal_palette) # Reset palette
             self.highlighted_chapter_item = None

    def handle_overwrite_request(self, output_wav, output_m4b):
        """Shows a confirmation dialog for overwriting files."""
        if not self.worker: return

        files_exist = []
        if os.path.exists(output_wav): files_exist.append(os.path.basename(output_wav))
        if os.path.exists(output_m4b): files_exist.append(os.path.basename(output_m4b))

        reply = QMessageBox.question(
            self,
            'Confirm Overwrite',
            f"The following output file(s) already exist:\n\n"
            f"{', '.join(files_exist)}\n\n"
            f"Do you want to overwrite them?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No # Default to No
        )

        if self.worker: # Check if worker still exists
             self.worker.overwrite_response = (reply == QMessageBox.StandardButton.Yes)

    def closeEvent(self, event):
        """Ensure worker thread is stopped cleanly on window close."""
        if self.thread and self.thread.isRunning():
            reply = QMessageBox.question(
                self, 'Confirm Exit',
                "A conversion is currently in progress. Are you sure you want to exit?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No
            )
            if reply == QMessageBox.StandardButton.Yes:
                self.append_log("Exiting application - stopping active conversion.")
                self.stop_conversion()
                # Give thread a moment to stop - adjust timeout as needed
                if not self.thread.wait(2000): # Wait up to 2 seconds
                    self.append_log("Warning: Worker thread did not stop gracefully.")
                event.accept()
            else:
                event.ignore()
        else:
            event.accept()


if __name__ == "__main__":
    # Add high DPI scaling support if needed
    # QApplication.setAttribute(Qt.AA_EnableHighDpiScaling)
    # QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps)

    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())

# --- END OF FILE epub_to_speech_ui.py ---