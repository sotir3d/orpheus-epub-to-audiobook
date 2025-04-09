# --- START OF FILE epub_to_speech_oute_ui.py ---

import os
import sys
import time
from datetime import datetime
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
                               QListWidget, QListWidgetItem, QPushButton, QLabel, QComboBox,
                               QProgressBar, QFileDialog, QMessageBox, QCheckBox, QDoubleSpinBox,
                               QTextEdit, QGroupBox, QFormLayout, QSizePolicy,
                               QStatusBar)
from PySide6.QtCore import Qt, QThread, Signal, QObject, QTimer
from PySide6.QtGui import QPalette, QColor, QIcon # Added QIcon

# Assuming epub_to_speech_oute.py is in the same directory or accessible via PYTHONPATH
try:
    import epub_to_speech_oute
    import outetts # Import outetts here as well for Speaker type checking and creation
except ImportError as e:
    # ... (keep existing import error handling) ...
    print(f"Error importing backend or outetts: {e}")
    app = QApplication([])
    QMessageBox.critical(None, "Import Error", "Failed to import backend script or outetts library.\n"
                         f"Make sure they are installed and accessible.\n\nError: {e}")
    sys.exit(1)


class ConversionWorker(QObject):
    progress = Signal(int, int, str)
    processing_chapter_index = Signal(int)
    log_message = Signal(str)
    finished = Signal(bool, str)
    overwrite_required = Signal(str, str)

    # Accepts speaker_profile which can be str (name/path) or outetts.Speaker object
    def __init__(self, epub_path, output_dir, temperature, selected_chapter_indices, speaker_profile):
        super().__init__()
        self.epub_path = epub_path
        self.output_dir = output_dir
        self.temperature = temperature
        self.selected_chapter_indices = selected_chapter_indices
        self.speaker_profile = speaker_profile # Can be str or Speaker object
        self._is_running = True
        self.overwrite_response = None

    def check_stop_requested(self):
        return not self._is_running

    def handle_overwrite_request(self, wav_path, m4b_path):
        self.overwrite_response = None
        self.overwrite_required.emit(wav_path, m4b_path)
        self.log_message.emit("Waiting for user confirmation on overwrite...")
        while self.overwrite_response is None:
            if not self._is_running:
                self.log_message.emit("Stop requested while waiting for overwrite confirmation.")
                return False
            time.sleep(0.1)
        self.log_message.emit(f"Overwrite confirmation received: {'Yes' if self.overwrite_response else 'No'}")
        return self.overwrite_response

    def run(self):
        try:
            # Pass the speaker_profile directly (could be str or Speaker object)
            success, message = epub_to_speech_oute.process_epub_chapters(
                epub_path=self.epub_path,
                output_dir=self.output_dir,
                temperature=self.temperature,
                selected_chapter_indices=self.selected_chapter_indices,
                speaker_profile=self.speaker_profile, # Pass it here
                log_callback=self.log_message.emit,
                progress_callback=self.progress.emit,
                processing_chapter_callback=self.processing_chapter_index.emit,
                check_stop_callback=self.check_stop_requested,
                overwrite_callback=self.handle_overwrite_request
            )
            self.finished.emit(success, message)

        except Exception as e:
            import traceback
            error_msg = f"Unexpected worker error: {e}"
            self.log_message.emit(f"\n❌ {error_msg}")
            self.log_message.emit(traceback.format_exc())
            self.finished.emit(False, error_msg)
        finally:
             self._is_running = False

    def stop(self):
        self.log_message.emit("Stop signal received by worker...")
        self._is_running = False
        if self.overwrite_response is None:
            self.overwrite_response = False


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("EPUB to Audiobook Converter (outeTTS)")
        self.setGeometry(100, 100, 900, 750) # Slightly taller

        self.worker = None
        self.thread = None
        self.current_epub_path = None
        self.current_output_dir = None
        self.book_title = None
        self.all_chapters_data = []
        self.highlighted_chapter_item = None
        self.normal_palette = self.palette()
        self.highlight_palette = QPalette()
        self.highlight_palette.setColor(QPalette.Base, QColor(75, 75, 75))
        self.highlight_palette.setColor(QPalette.Text, QColor("white"))

        # Store current speaker selection: can be str (name/path) or Speaker object
        self._active_speaker_profile = epub_to_speech_oute.DEFAULT_SPEAKER
        # Store a user-friendly name for the label
        self._active_speaker_display_name = epub_to_speech_oute.DEFAULT_SPEAKER

        self.init_ui()
        self.update_status("Ready")
        self.check_backend_initialization()

    def check_backend_initialization(self):
        """Checks if the outeTTS interface initialized correctly on first use."""
        self.update_status("Initializing outeTTS backend...")
        QApplication.processEvents() # Update UI
        try:
            epub_to_speech_oute.get_outeTTS_interface() # Trigger initialization
            self.append_log("outeTTS backend initialized successfully.")
            self.update_status("Ready (outeTTS backend loaded)")
            self.set_controls_enabled(True) # Ensure controls are enabled after init
        except Exception as e:
             self.append_log(f"❌ ERROR: Failed to initialize outeTTS backend: {e}")
             self.update_status("ERROR: outeTTS backend failed to load!")
             QMessageBox.critical(self, "Backend Error",
                                  f"Failed to initialize the outeTTS backend.\n"
                                  f"Please check console logs and ensure models are accessible.\n\nError: {e}")
             self.start_btn.setEnabled(False)
             self.start_btn.setText("Backend Error")
             # Keep other controls disabled if backend fails? Maybe better user experience.
             self.set_controls_enabled(False) # Disable most controls if backend fails


    def init_ui(self):
        main_widget = QWidget()
        main_layout = QVBoxLayout(main_widget)

        # --- File Selection Group ---
        # ... (no changes) ...
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
        # ... (no changes) ...
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
        params_layout = QFormLayout()

        # Speaker Selection (Modified for WAV creation)
        speaker_row_widget = QWidget() # Use a container widget for the row
        speaker_layout = QHBoxLayout(speaker_row_widget)
        speaker_layout.setContentsMargins(0,0,0,0) # Remove margins for tighter layout

        self.speaker_label = QLabel(f"Speaker: {self._active_speaker_display_name}")
        self.speaker_label.setToolTip("Current text-to-speech speaker profile.")
        self.speaker_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred) # Allow label to expand

        self.create_speaker_btn = QPushButton("Create from WAV...") # Renamed button
        self.create_speaker_btn.setToolTip("Create a speaker profile from a WAV/MP3/FLAC file.")
        self.create_speaker_btn.clicked.connect(self.create_speaker_from_audio) # Connect to new function

        self.reset_speaker_btn = QPushButton("Reset") # New Reset Button
        self.reset_speaker_btn.setToolTip(f"Reset speaker to default ({epub_to_speech_oute.DEFAULT_SPEAKER})")
        # self.reset_speaker_btn.setIcon(QIcon.fromTheme("edit-clear")) # Optional icon
        self.reset_speaker_btn.clicked.connect(self.reset_speaker_to_default)

        speaker_layout.addWidget(self.speaker_label)
        speaker_layout.addWidget(self.create_speaker_btn)
        speaker_layout.addWidget(self.reset_speaker_btn)

        # Add the container widget holding the speaker layout to the form layout
        params_layout.addRow(speaker_row_widget) # Add the HBox widget as a row

        # Temperature
        self.temp_spin = QDoubleSpinBox()
        self.temp_spin.setRange(0.0, 1.0)
        self.temp_spin.setValue(epub_to_speech_oute.TEMPERATURE)
        self.temp_spin.setSingleStep(0.05)
        self.temp_spin.setToolTip("Controls randomness. Lower values are more deterministic (0.0-1.0).")
        params_layout.addRow(QLabel("Temperature:"), self.temp_spin)

        params_group.setLayout(params_layout)

        # --- Output Group ---
        # ... (no changes) ...
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
        # ... (no changes) ...
        progress_log_group = QGroupBox("Progress & Log")
        progress_log_layout = QVBoxLayout()
        self.progress_bar = QProgressBar()
        self.progress_bar.setTextVisible(True)
        self.progress_bar.setValue(0)
        self.log_area = QTextEdit()
        self.log_area.setReadOnly(True)
        self.log_area.setLineWrapMode(QTextEdit.WidgetWidth)
        progress_log_layout.addWidget(self.progress_bar)
        progress_log_layout.addWidget(QLabel("Log:"))
        progress_log_layout.addWidget(self.log_area)
        progress_log_group.setLayout(progress_log_layout)


        # --- Control Buttons ---
        # ... (no changes) ...
        control_layout = QHBoxLayout()
        self.start_btn = QPushButton("Start Conversion")
        self.start_btn.setStyleSheet("background-color: darkseagreen; color: black;")
        self.start_btn.clicked.connect(self.start_conversion)
        self.stop_btn = QPushButton("Stop Conversion")
        self.stop_btn.setStyleSheet("background-color: indianred; color: black;")
        self.stop_btn.clicked.connect(self.stop_conversion)
        self.stop_btn.setEnabled(False)
        control_layout.addStretch()
        control_layout.addWidget(self.start_btn)
        control_layout.addWidget(self.stop_btn)
        control_layout.addStretch()


        # --- Status Bar ---
        # ... (no changes) ...
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        self.status_label = QLabel("Ready")
        self.status_bar.addWidget(self.status_label)


        # --- Assemble Main Layout ---
        main_layout.addWidget(file_group)
        main_layout.addWidget(chapter_group)
        hbox_params_output = QHBoxLayout()
        hbox_params_output.addWidget(params_group, stretch=1)
        hbox_params_output.addWidget(output_group, stretch=1)
        main_layout.addLayout(hbox_params_output)
        main_layout.addWidget(progress_log_group)
        main_layout.addLayout(control_layout)

        self.setCentralWidget(main_widget)
        self.set_controls_enabled(True) # Start enabled, check_backend might disable later

    # --- UI Control and Logging ---

    def update_status(self, message):
        self.status_label.setText(message)

    def append_log(self, message):
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.log_area.append(f"[{timestamp}] {message}")
        self.log_area.verticalScrollBar().setValue(self.log_area.verticalScrollBar().maximum())

    def set_controls_enabled(self, enabled):
        """Enable or disable input controls."""
        self.select_epub_btn.setEnabled(enabled)
        self.chapter_list.setEnabled(enabled)
        self.create_speaker_btn.setEnabled(enabled) # Enable speaker create button
        self.reset_speaker_btn.setEnabled(enabled) # Enable speaker reset button

        buttons_layout = self.chapter_list.parent().layout().itemAt(1).layout()
        if buttons_layout:
            for i in range(buttons_layout.count()):
                widget_item = buttons_layout.itemAt(i)
                if widget_item and widget_item.widget():
                    widget_item.widget().setEnabled(enabled)

        self.temp_spin.setEnabled(enabled)
        self.select_output_btn.setEnabled(enabled)

        # Special handling for start/stop based on backend status and conversion state
        backend_ok = self.status_label.text() != "ERROR: outeTTS backend failed to load!"
        is_converting = self.thread is not None and self.thread.isRunning()

        self.start_btn.setEnabled(enabled and backend_ok and not is_converting)
        self.stop_btn.setEnabled(enabled and backend_ok and is_converting)

        if is_converting:
            self.start_btn.setText("Converting...")
        elif not backend_ok:
            self.start_btn.setText("Backend Error")
        else:
            self.start_btn.setText("Start Conversion")


    # --- File/Directory/Speaker Selection ---

    def select_epub(self):
        # ... (no changes) ...
        path, _ = QFileDialog.getOpenFileName(self, "Select EPUB file", "", "EPUB files (*.epub)")
        if path:
            self.current_epub_path = path
            base_name = os.path.basename(path)
            self.file_label.setText(base_name)
            self.file_label.setToolTip(path)
            self.update_status(f"Loading chapters from {base_name}...")
            self.log_area.clear()
            self.append_log(f"Selected EPUB: {path}")
            QApplication.processEvents()
            self.load_chapters(path)

    def select_output(self):
        # ... (no changes) ...
        start_dir = os.path.dirname(self.current_epub_path) if self.current_epub_path else ""
        path = QFileDialog.getExistingDirectory(self, "Select Output Directory", start_dir)
        if path:
            self.current_output_dir = path
            self.output_label.setText(f"Output to: {path}")
            self.output_label.setToolTip(path)
            self.append_log(f"Set output directory: {path}")


    def create_speaker_from_audio(self):
        """Opens dialog for WAV/MP3, creates speaker, optionally saves as JSON."""
        # Filter for common audio files outetts might support
        audio_filter = "Audio Files (*.wav *.mp3 *.flac *.ogg);;All Files (*)"
        path, _ = QFileDialog.getOpenFileName(self, "Select Audio File for Speaker Profile", "", audio_filter)

        if not path:
            return # User cancelled

        self.update_status(f"Creating speaker from {os.path.basename(path)}...")
        self.append_log(f"Attempting to create speaker profile from: {path}")
        QApplication.processEvents() # Update UI

        try:
            interface = epub_to_speech_oute.get_outeTTS_interface() # Ensure interface is ready
            if not interface:
                 raise RuntimeError("outeTTS Interface not available.")

            # Create the speaker object - this might take a second or two
            self.set_controls_enabled(False) # Briefly disable controls
            QApplication.processEvents()
            new_speaker = interface.create_speaker(path)
            self.set_controls_enabled(True) # Re-enable controls


            # Store the created speaker object for use
            self._active_speaker_profile = new_speaker
            # Update display name
            self._active_speaker_display_name = f"Custom ({os.path.basename(path)})"
            self.speaker_label.setText(f"Speaker: {self._active_speaker_display_name}")
            self.speaker_label.setToolTip(f"Using custom speaker created from {path}")

            self.append_log(f"Successfully created speaker profile from {os.path.basename(path)}.")
            self.update_status("Custom speaker created.")

            # Ask user if they want to save this profile
            reply = QMessageBox.question(self, "Save Speaker Profile?",
                                         "Speaker profile created successfully.\n\n"
                                         "Do you want to save this profile as a .json file for future use?",
                                         QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                         QMessageBox.StandardButton.No)

            if reply == QMessageBox.StandardButton.Yes:
                self.save_speaker_profile(new_speaker, os.path.splitext(os.path.basename(path))[0]) # Pass object and suggested name

        except Exception as e:
            self.set_controls_enabled(True) # Ensure controls are re-enabled on error
            self.append_log(f"❌ Error creating speaker profile: {e}")
            self.update_status("Error creating speaker.")
            QMessageBox.critical(self, "Speaker Creation Error",
                                 f"Failed to create speaker profile from audio file:\n{e}\n\n"
                                 "Ensure the audio file is valid and the outetts backend is working.")
            # Reset to default if creation fails? Optional.
            # self.reset_speaker_to_default()

    def save_speaker_profile(self, speaker_object, suggested_name="custom_speaker"):
        """Saves the given speaker object to a JSON file selected by the user."""
        json_filter = "JSON Files (*.json)"
        save_path, _ = QFileDialog.getSaveFileName(self, "Save Speaker Profile As", f"{suggested_name}.json", json_filter)

        if not save_path:
            self.append_log("Speaker profile saving cancelled.")
            return # User cancelled

        # Ensure filename ends with .json
        if not save_path.lower().endswith(".json"):
            save_path += ".json"

        try:
            interface = epub_to_speech_oute.get_outeTTS_interface()
            if not interface:
                 raise RuntimeError("outeTTS Interface not available.")

            interface.save_speaker(speaker_object, save_path)
            self.append_log(f"Speaker profile saved successfully to: {save_path}")
            self.update_status("Speaker profile saved.")
             # Optional: Update the active profile to be the saved path?
             # self._active_speaker_profile = save_path
             # self._active_speaker_display_name = os.path.basename(save_path)
             # self.speaker_label.setText(f"Speaker: {self._active_speaker_display_name}")
             # self.speaker_label.setToolTip(f"Using saved speaker profile: {save_path}")

        except Exception as e:
             self.append_log(f"❌ Error saving speaker profile: {e}")
             self.update_status("Error saving speaker profile.")
             QMessageBox.critical(self, "Save Error", f"Failed to save speaker profile:\n{e}")


    def reset_speaker_to_default(self):
        """Resets the active speaker to the default."""
        default_speaker_name = epub_to_speech_oute.DEFAULT_SPEAKER
        self._active_speaker_profile = default_speaker_name
        self._active_speaker_display_name = default_speaker_name
        self.speaker_label.setText(f"Speaker: {self._active_speaker_display_name}")
        self.speaker_label.setToolTip(f"Using default speaker: {default_speaker_name}")
        self.append_log(f"Reset speaker to default: {default_speaker_name}")
        self.update_status("Speaker reset to default.")


    # --- Chapter Handling ---

    def load_chapters(self, epub_path):
        # ... (no changes) ...
        self.chapter_list.clear()
        self.all_chapters_data = []
        self.book_title = None
        try:
            self.book_title, chapters_data = epub_to_speech_oute.extract_chapters_from_epub(epub_path)
            self.all_chapters_data = chapters_data

            if self.book_title and not self.current_output_dir:
                 safe_book_title = epub_to_speech_oute.re.sub(r'[^\w\s-]', '', self.book_title).strip().replace(' ', '_')
                 default_output = os.path.abspath(f"outputs/epub_{safe_book_title}")
                 self.output_label.setText(f"Default: {default_output}")
                 self.output_label.setToolTip(f"Default output directory: {default_output}")

            if chapters_data:
                self.append_log(f"Found {len(chapters_data)} chapters in '{self.book_title}'.")
                for i, chapter in enumerate(chapters_data):
                    item = QListWidgetItem(f"{i+1:03d}: {chapter['title']}")
                    item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
                    item.setCheckState(Qt.Checked)
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
        # ... (no changes) ...
        state = Qt.Checked if check else Qt.Unchecked
        for i in range(self.chapter_list.count()):
            self.chapter_list.item(i).setCheckState(state)


    def check_highlighted(self):
        # ... (no changes) ...
        selected_items = self.chapter_list.selectedItems()
        if not selected_items:
            self.update_status("Select chapters in the list first to check them.")
            return
        for item in selected_items: item.setCheckState(Qt.Checked)
        self.update_status(f"Checked {len(selected_items)} highlighted chapters.")


    def uncheck_highlighted(self):
        # ... (no changes) ...
        selected_items = self.chapter_list.selectedItems()
        if not selected_items:
            self.update_status("Select chapters in the list first to uncheck them.")
            return
        for item in selected_items: item.setCheckState(Qt.Unchecked)
        self.update_status(f"Unchecked {len(selected_items)} highlighted chapters.")


    # --- Conversion Process ---

    def start_conversion(self):
        if not self.current_epub_path:
            QMessageBox.warning(self, "Error", "Please select an EPUB file first.")
            return

        selected_chapter_indices = [i for i in range(self.chapter_list.count())
                                     if self.chapter_list.item(i).checkState() == Qt.Checked]
        if not selected_chapter_indices:
            QMessageBox.warning(self, "Error", "Please check at least one chapter to convert.")
            return

        if self.status_label.text() == "ERROR: outeTTS backend failed to load!":
             QMessageBox.critical(self, "Backend Error", "Cannot start conversion, the outeTTS backend failed to initialize.")
             return

        self.reset_chapter_highlight()

        # Get parameters - self._active_speaker_profile now holds name, path, or object
        params = {
            'epub_path': self.current_epub_path,
            'output_dir': self.current_output_dir,
            'temperature': self.temp_spin.value(),
            'selected_chapter_indices': selected_chapter_indices,
            'speaker_profile': self._active_speaker_profile # Pass the active profile
        }

        self.append_log("="*30 + " Starting Conversion " + "="*30)
        self.update_status("Starting conversion...")
        self.progress_bar.setValue(0)
        self.progress_bar.setFormat("Starting...")
        self.progress_bar.setStyleSheet("")

        self.set_controls_enabled(False) # Disable controls

        self.thread = QThread(self)
        self.worker = ConversionWorker(**params)
        self.worker.moveToThread(self.thread)

        # Connect signals
        self.worker.progress.connect(self.update_progress)
        self.worker.processing_chapter_index.connect(self.highlight_current_chapter)
        self.worker.log_message.connect(self.append_log)
        self.worker.finished.connect(self.conversion_finished)
        self.worker.overwrite_required.connect(self.handle_overwrite_request_dialog)

        self.thread.started.connect(self.worker.run)
        self.thread.finished.connect(self.thread_cleanup)

        self.thread.start()

    def stop_conversion(self):
        # ... (no changes) ...
        if self.worker and self.thread and self.thread.isRunning():
            self.update_status("Stopping conversion...")
            self.append_log("Attempting to stop the conversion...")
            self.stop_btn.setEnabled(False)
            self.worker.stop()


    def conversion_finished(self, success, message):
        # ... (logic is fine, maybe adjust logging symbols slightly) ...
        if success:
            self.update_status("Conversion completed successfully.")
            self.append_log(f"✅ {'='*30} Conversion Finished: {message} {'='*30}")
            self.progress_bar.setValue(self.progress_bar.maximum())
            self.progress_bar.setFormat("Completed")
        else:
            if message == "Stopped" or message == "Overwrite denied":
                self.update_status(f"Conversion {message.lower()}.")
                self.append_log(f"⏹️ {'='*30} Conversion {message} {'='*30}")
                self.progress_bar.setFormat(message)
            else: # Actual error
                self.update_status(f"Conversion failed: {message}")
                self.append_log(f"❌❌❌ ERROR: {message}")
                self.progress_bar.setFormat("Error")
                self.progress_bar.setStyleSheet("QProgressBar::chunk { background-color: indianred; }")
                QMessageBox.critical(self, "Conversion Error", f"An error occurred during conversion:\n{message}")

        self.reset_ui_after_conversion()


    def reset_ui_after_conversion(self):
         # ... (no changes) ...
         self.set_controls_enabled(True)
         self.reset_chapter_highlight()


    def thread_cleanup(self):
         # ... (no changes) ...
         if self.worker: self.worker.deleteLater()
         if self.thread: self.thread.deleteLater()
         self.worker = None
         self.thread = None


    def update_progress(self, current_chap_num, total_chapters, chapter_title):
        # ... (no changes) ...
        self.progress_bar.setMaximum(total_chapters)
        self.progress_bar.setValue(current_chap_num)
        if total_chapters > 0:
            progress_percent = (current_chap_num / total_chapters) * 100
            self.progress_bar.setFormat(f"Chapter {current_chap_num}/{total_chapters} ({progress_percent:.0f}%)")
        else:
             self.progress_bar.setFormat(f"Chapter {current_chap_num}/{total_chapters}")
        self.update_status(f"Processing chapter {current_chap_num}/{total_chapters}: {chapter_title}")


    def highlight_current_chapter(self, index):
        # ... (no changes) ...
        self.reset_chapter_highlight()
        if 0 <= index < self.chapter_list.count():
            item = self.chapter_list.item(index)
            if item:
                item.setSelected(True)
                self.chapter_list.scrollToItem(item, QListWidget.ScrollHint.PositionAtCenter)
                self.highlighted_chapter_item = item


    def reset_chapter_highlight(self):
        # ... (no changes) ...
         if self.highlighted_chapter_item:
            self.highlighted_chapter_item.setSelected(False)
            self.highlighted_chapter_item = None


    def handle_overwrite_request_dialog(self, output_wav, output_m4b):
        # ... (no changes) ...
        if not self.worker: return

        files_exist = []
        if os.path.exists(output_wav): files_exist.append(os.path.basename(output_wav))
        if os.path.exists(output_m4b): files_exist.append(os.path.basename(output_m4b))

        reply = QMessageBox.question(
            self, 'Confirm Overwrite',
            f"The following final output file(s) already exist:\n\n"
            f"{', '.join(files_exist)}\n\n"
            f"Do you want to overwrite them?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No
        )

        if self.worker:
             self.worker.overwrite_response = (reply == QMessageBox.StandardButton.Yes)


    def closeEvent(self, event):
        # ... (no changes) ...
        if self.thread and self.thread.isRunning():
            reply = QMessageBox.question(
                self, 'Confirm Exit',
                "A conversion is in progress. Stop and exit?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No
            )
            if reply == QMessageBox.StandardButton.Yes:
                self.append_log("Exiting application - stopping active conversion.")
                self.stop_conversion()
                if self.thread:
                    if not self.thread.wait(3000):
                        self.append_log("Warning: Worker thread did not finish stopping gracefully.")
                event.accept()
            else:
                event.ignore()
        else:
            event.accept()


if __name__ == "__main__":
    # ... (keep optional High DPI and Theme) ...
    app = QApplication(sys.argv)

    app.setStyle("Fusion")
    dark_palette = QPalette()
    dark_palette.setColor(QPalette.Window, QColor(53, 53, 53))
    dark_palette.setColor(QPalette.WindowText, Qt.white)
    dark_palette.setColor(QPalette.Base, QColor(35, 35, 35))
    dark_palette.setColor(QPalette.AlternateBase, QColor(53, 53, 53))
    dark_palette.setColor(QPalette.ToolTipBase, Qt.white)
    dark_palette.setColor(QPalette.ToolTipText, Qt.white)
    dark_palette.setColor(QPalette.Text, Qt.white)
    dark_palette.setColor(QPalette.Button, QColor(53, 53, 53))
    dark_palette.setColor(QPalette.ButtonText, Qt.white)
    dark_palette.setColor(QPalette.BrightText, Qt.red)
    dark_palette.setColor(QPalette.Link, QColor(42, 130, 218))
    dark_palette.setColor(QPalette.Highlight, QColor(42, 130, 218))
    dark_palette.setColor(QPalette.HighlightedText, Qt.black)
    dark_palette.setColor(QPalette.Disabled, QPalette.Text, QColor(127, 127, 127))
    dark_palette.setColor(QPalette.Disabled, QPalette.ButtonText, QColor(127, 127, 127))
    app.setPalette(dark_palette)
    app.setStyleSheet("QToolTip { color: #ffffff; background-color: #2a82da; border: 1px solid white; }")


    if 'epub_to_speech_oute' in sys.modules:
         window = MainWindow()
         window.show()
         sys.exit(app.exec())
    else:
         sys.exit(1)

# --- END OF FILE epub_to_speech_oute_ui.py ---
