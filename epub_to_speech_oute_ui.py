# --- START OF FILE epub_to_speech_oute_ui.py ---

import os
import sys
import time
from datetime import datetime
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
                               QListWidget, QListWidgetItem, QPushButton, QLabel, QComboBox, # Use QComboBox
                               QProgressBar, QFileDialog, QMessageBox, QCheckBox, QDoubleSpinBox,
                               QTextEdit, QGroupBox, QFormLayout, QSizePolicy,
                               QStatusBar)
from PySide6.QtCore import Qt, QThread, Signal, QObject, QTimer
from PySide6.QtGui import QPalette, QColor, QIcon

# Import backend and outetts
try:
    import epub_to_speech_oute
    import outetts
except ImportError as e:
    # ... (keep existing import error handling) ...
    print(f"Error importing backend or outetts: {e}")
    app = QApplication([])
    QMessageBox.critical(None, "Import Error", "Failed to import backend script or outetts library.\n"
                         f"Make sure they are installed and accessible.\n\nError: {e}")
    sys.exit(1)

# --- ConversionWorker (no changes needed here) ---
class ConversionWorker(QObject):
    # ... (keep existing worker code) ...
    progress = Signal(int, int, str)
    processing_chapter_index = Signal(int)
    log_message = Signal(str)
    finished = Signal(bool, str)
    overwrite_required = Signal(str, str)

    def __init__(self, epub_path, output_dir, temperature, selected_chapter_indices, speaker_profile):
        super().__init__()
        self.epub_path = epub_path
        self.output_dir = output_dir
        self.temperature = temperature
        self.selected_chapter_indices = selected_chapter_indices
        self.speaker_profile = speaker_profile # Can be str (name/path) or Speaker object
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
            success, message = epub_to_speech_oute.process_epub_chapters(
                # ... (pass args, speaker_profile is already correct type) ...
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
            # ... (error handling) ...
            import traceback
            error_msg = f"Unexpected worker error: {e}"
            self.log_message.emit(f"\n❌ {error_msg}")
            self.log_message.emit(traceback.format_exc())
            self.finished.emit(False, error_msg)
        finally:
             self._is_running = False

    def stop(self):
        # ... (keep existing stop code) ...
        self.log_message.emit("Stop signal received by worker...")
        self._is_running = False
        if self.overwrite_response is None:
            self.overwrite_response = False

# --- MainWindow ---
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("EPUB to Audiobook Converter (outeTTS)")
        self.setGeometry(100, 100, 900, 750)

        # ... (keep worker, thread, path variables etc.) ...
        self.worker = None
        self.thread = None
        self.current_epub_path = None
        self.current_output_dir = None
        self.book_title = None
        self.all_chapters_data = []
        self.highlighted_chapter_item = None
        self.normal_palette = self.palette()
        # ... (highlight palette) ...

        # Active speaker profile now primarily stores the *identifier* (str name or str path)
        # The Speaker object is only held temporarily after creation, before saving.
        self._active_speaker_identifier = epub_to_speech_oute.DEFAULT_SPEAKER

        self.init_ui()
        self.update_status("Ready")
        self.check_backend_initialization() # This will also populate the dropdown

    def check_backend_initialization(self):
        self.update_status("Initializing outeTTS backend...")
        QApplication.processEvents()
        try:
            epub_to_speech_oute.get_outeTTS_interface()
            self.append_log("outeTTS backend initialized successfully.")
            self.update_status("Ready (outeTTS backend loaded)")
            # Populate dropdown *after* ensuring backend is ready
            self.populate_speaker_dropdown()
            self.set_controls_enabled(True)
        except Exception as e:
             # ... (keep existing error handling) ...
             self.append_log(f"❌ ERROR: Failed to initialize outeTTS backend: {e}")
             self.update_status("ERROR: outeTTS backend failed to load!")
             QMessageBox.critical(self, "Backend Error", #... error message ...
                                  f"Failed to initialize the outeTTS backend.\n"
                                  f"Please check console logs and ensure models are accessible.\n\nError: {e}")
             self.set_controls_enabled(False) # Disable controls if backend fails

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
        # ... chapter buttons ...
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
        params_layout = QFormLayout() # Use FormLayout for label alignment

        # Speaker Selection (Dropdown + Create Button)
        speaker_row_widget = QWidget()
        speaker_layout = QHBoxLayout(speaker_row_widget)
        speaker_layout.setContentsMargins(0,0,0,0)

        self.speaker_combo = QComboBox()
        self.speaker_combo.setToolTip("Select a speaker profile (default or saved .json).")
        self.speaker_combo.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        # Connect signal AFTER populating dropdown
        # self.speaker_combo.currentIndexChanged.connect(self.speaker_selection_changed)
        # Use activated signal to avoid firing twice during programmatic changes
        self.speaker_combo.activated.connect(self.speaker_selection_changed)

        self.create_speaker_btn = QPushButton("Create...") # Shorter name maybe?
        self.create_speaker_btn.setToolTip("Create a new speaker profile from a WAV/MP3/FLAC file.")
        self.create_speaker_btn.clicked.connect(self.create_speaker_from_audio)

        speaker_layout.addWidget(self.speaker_combo, 1) # Give combo box more space
        speaker_layout.addWidget(self.create_speaker_btn)

        # Add the speaker selection row to the form layout
        params_layout.addRow(QLabel("Speaker Profile:"), speaker_row_widget) # Add label + widget HBox

        # Temperature (remains the same)
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
        # ... output label and button ...
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
        # ... progress bar and log area ...
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
        # ... start/stop buttons ...
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
        self.set_controls_enabled(False) # Start disabled until backend check completes

    # --- Speaker Dropdown Logic ---

    def populate_speaker_dropdown(self):
        """Clears and fills the speaker dropdown with default and saved profiles."""
        self.speaker_combo.blockSignals(True) # Prevent signals during population
        self.speaker_combo.clear()
        current_selection_identifier = self._active_speaker_identifier # Store current to reselect

        # 1. Add Default Speaker
        default_name = epub_to_speech_oute.DEFAULT_SPEAKER
        self.speaker_combo.addItem(f"Default ({default_name})", userData=default_name)

        # 2. Scan for Saved Profiles
        profile_dir = epub_to_speech_oute.SPEAKER_PROFILE_DIR
        found_profiles = []
        if os.path.isdir(profile_dir):
            try:
                for filename in os.listdir(profile_dir):
                    if filename.lower().endswith(".json"):
                        full_path = os.path.join(profile_dir, filename)
                        display_name = os.path.splitext(filename)[0] # Name without extension
                        found_profiles.append({"display": display_name, "path": full_path})
            except OSError as e:
                self.append_log(f"Warning: Could not read speaker profiles directory '{profile_dir}': {e}")

        # Sort profiles alphabetically by display name for consistency
        found_profiles.sort(key=lambda x: x['display'])

        # Add sorted profiles to dropdown
        for profile in found_profiles:
            self.speaker_combo.addItem(profile['display'], userData=profile['path'])

        # 3. Reselect previous or default
        found_index = self.speaker_combo.findData(current_selection_identifier)
        if found_index != -1:
            self.speaker_combo.setCurrentIndex(found_index)
        else:
            # If previous selection not found (e.g., deleted file), default to index 0
            self.speaker_combo.setCurrentIndex(0)
            # Update the stored identifier to match the new selection
            self._active_speaker_identifier = self.speaker_combo.currentData()

        self.speaker_combo.blockSignals(False) # Re-enable signals
        # Manually trigger update for the initial state
        self.speaker_selection_changed()


    def speaker_selection_changed(self):
        """Updates the active speaker identifier when dropdown selection changes."""
        selected_data = self.speaker_combo.currentData()
        selected_text = self.speaker_combo.currentText()

        if selected_data: # Should always have data
            self._active_speaker_identifier = selected_data
            if selected_data == epub_to_speech_oute.DEFAULT_SPEAKER:
                self.append_log(f"Selected default speaker: {selected_data}")
                self.speaker_combo.setToolTip(f"Using default speaker: {selected_data}")
            else:
                # It's a path
                display_name = os.path.basename(selected_data)
                self.append_log(f"Selected speaker profile: {display_name}")
                self.speaker_combo.setToolTip(f"Using saved profile: {selected_data}")
        else:
             # Fallback, though should not happen with current logic
             self.append_log("Warning: No data associated with selected speaker. Reverting to default.")
             self.reset_speaker_to_default() # Use the reset function


    # --- UI Control and Logging ---

    def update_status(self, message):
        self.status_label.setText(message)

    def append_log(self, message):
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.log_area.append(f"[{timestamp}] {message}")
        self.log_area.verticalScrollBar().setValue(self.log_area.verticalScrollBar().maximum())

    def set_controls_enabled(self, enabled):
        """Enable or disable input controls."""
        is_converting = self.thread is not None and self.thread.isRunning()
        backend_ok = self.status_label.text() != "ERROR: outeTTS backend failed to load!"

        # Enable based on 'enabled' AND backend status
        effective_enabled = enabled and backend_ok

        self.select_epub_btn.setEnabled(effective_enabled and not is_converting)
        self.chapter_list.setEnabled(effective_enabled and not is_converting)
        self.speaker_combo.setEnabled(effective_enabled and not is_converting) # Enable dropdown
        self.create_speaker_btn.setEnabled(effective_enabled and not is_converting) # Enable create button

        buttons_layout = self.chapter_list.parent().layout().itemAt(1).layout()
        if buttons_layout:
            for i in range(buttons_layout.count()):
                widget_item = buttons_layout.itemAt(i)
                if widget_item and widget_item.widget():
                    widget_item.widget().setEnabled(effective_enabled and not is_converting)

        self.temp_spin.setEnabled(effective_enabled and not is_converting)
        self.select_output_btn.setEnabled(effective_enabled and not is_converting)

        # Start/Stop buttons depend on conversion state AND backend status
        self.start_btn.setEnabled(effective_enabled and not is_converting)
        self.stop_btn.setEnabled(effective_enabled and is_converting) # Stop only available if backend OK and running

        # Update start button text
        if not backend_ok:
            self.start_btn.setText("Backend Error")
        elif is_converting:
            self.start_btn.setText("Converting...")
        else:
            self.start_btn.setText("Start Conversion")


    # --- File/Directory Selection ---
    def select_epub(self):
        # ... (no changes) ...
        path, _ = QFileDialog.getOpenFileName(self, "Select EPUB file", "", "EPUB files (*.epub)")
        if path:
             #... (update label, load chapters) ...
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
             #... (update label) ...
            self.current_output_dir = path
            self.output_label.setText(f"Output to: {path}")
            self.output_label.setToolTip(path)
            self.append_log(f"Set output directory: {path}")


    # --- Speaker Creation/Saving ---
    def create_speaker_from_audio(self):
        audio_filter = "Audio Files (*.wav *.mp3 *.flac *.ogg);;All Files (*)"
        path, _ = QFileDialog.getOpenFileName(self, "Select Audio File for Speaker Profile", "", audio_filter)
        if not path: return

        self.update_status(f"Creating speaker from {os.path.basename(path)}...")
        self.append_log(f"Attempting to create speaker profile from: {path}")
        QApplication.processEvents()

        temp_speaker_object = None # Store the object temporarily

        try:
            interface = epub_to_speech_oute.get_outeTTS_interface()
            if not interface: raise RuntimeError("outeTTS Interface not available.")

            self.set_controls_enabled(False)
            QApplication.processEvents()
            temp_speaker_object = interface.create_speaker(path) # Store the object
            self.set_controls_enabled(True)

            self.append_log(f"Successfully created speaker profile object from {os.path.basename(path)}.")
            self.update_status("Custom speaker created (unsaved).")

            # Ask user if they want to save this profile
            reply = QMessageBox.question(self, "Save Speaker Profile?",
                                         "Speaker profile created successfully.\n\n"
                                         "Do you want to save this profile as a .json file to the list?",
                                         QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                         QMessageBox.StandardButton.Yes) # Default to Yes

            if reply == QMessageBox.StandardButton.Yes:
                self.save_speaker_profile(temp_speaker_object, os.path.splitext(os.path.basename(path))[0])
            else:
                # If not saved, keep the object temporarily active but don't add to list
                self._active_speaker_identifier = temp_speaker_object # Use the object directly
                # Maybe add a temporary item to the dropdown? Or just leave it selected internally.
                # For simplicity, we won't add a temp item. User can convert with it once.
                self.append_log("Using newly created speaker (unsaved) for next conversion.")
                self.speaker_combo.setToolTip(f"Using unsaved speaker from {os.path.basename(path)}")
                # Find default and reselect it visually to avoid confusion? Or add a temp item?
                # Let's find default and select it visually, but keep the object internally
                default_index = self.speaker_combo.findData(epub_to_speech_oute.DEFAULT_SPEAKER)
                if default_index != -1:
                    self.speaker_combo.setCurrentIndex(default_index)


        except Exception as e:
            self.set_controls_enabled(True)
            self.append_log(f"❌ Error creating speaker profile: {e}")
            self.update_status("Error creating speaker.")
            QMessageBox.critical(self, "Speaker Creation Error", f"Failed to create speaker profile:\n{e}")

    def save_speaker_profile(self, speaker_object, suggested_name="custom_speaker"):
        """Saves the speaker object to the designated profile directory."""
        profile_dir = epub_to_speech_oute.SPEAKER_PROFILE_DIR
        # Ensure directory exists (should have been created at startup)
        os.makedirs(profile_dir, exist_ok=True)

        # Clean suggested name for filename
        safe_suggested_name = re.sub(r'[^\w\-]+', '_', suggested_name)
        if not safe_suggested_name: safe_suggested_name = "custom_speaker"

        # Loop to find a unique filename
        counter = 0
        save_name_base = safe_suggested_name
        while True:
             save_filename = f"{safe_suggested_name}.json"
             save_path = os.path.join(profile_dir, save_filename)
             if not os.path.exists(save_path):
                 break
             counter += 1
             safe_suggested_name = f"{save_name_base}_{counter}"
             if counter > 100: # Safety break
                 self.append_log("Error: Could not find a unique filename to save speaker profile.")
                 QMessageBox.warning(self, "Save Error", "Could not determine a unique filename in the speaker_profiles directory.")
                 return


        # Ask user to confirm/edit filename (optional but good UX)
        confirmed_save_path, ok = QFileDialog.getSaveFileName(
            self,
            "Confirm Save Speaker Profile",
            save_path, # Pre-populate with suggested unique path
            "JSON Files (*.json)"
        )

        if not ok or not confirmed_save_path:
            self.append_log("Speaker profile saving cancelled by user.")
            # Keep the unsaved profile active temporarily?
            self._active_speaker_identifier = speaker_object
            self.append_log("Using newly created speaker (unsaved) for next conversion.")
            self.speaker_combo.setToolTip(f"Using unsaved speaker from temporary object")
            default_index = self.speaker_combo.findData(epub_to_speech_oute.DEFAULT_SPEAKER)
            if default_index != -1: self.speaker_combo.setCurrentIndex(default_index)
            return

        # Ensure final path ends with .json
        if not confirmed_save_path.lower().endswith(".json"):
            confirmed_save_path += ".json"

        try:
            interface = epub_to_speech_oute.get_outeTTS_interface()
            if not interface: raise RuntimeError("outeTTS Interface not available.")

            interface.save_speaker(speaker_object, confirmed_save_path)
            self.append_log(f"Speaker profile saved successfully to: {confirmed_save_path}")
            self.update_status("Speaker profile saved.")

            # Refresh the dropdown and select the newly saved profile
            self._active_speaker_identifier = confirmed_save_path # Update identifier to path
            self.populate_speaker_dropdown() # This will re-read the dir and select it

        except Exception as e:
             self.append_log(f"❌ Error saving speaker profile: {e}")
             self.update_status("Error saving speaker profile.")
             QMessageBox.critical(self, "Save Error", f"Failed to save speaker profile:\n{e}")


    def reset_speaker_to_default(self):
        """Resets the active speaker to the default by selecting it in the dropdown."""
        # Find the default item and set the dropdown's index
        default_name = epub_to_speech_oute.DEFAULT_SPEAKER
        default_index = self.speaker_combo.findData(default_name)
        if default_index != -1:
            self.speaker_combo.setCurrentIndex(default_index)
            # speaker_selection_changed will be triggered automatically if index changed
        else:
            self.append_log("Warning: Could not find default speaker in dropdown to reset.")
        # Explicitly log and update status just in case index didn't change
        self.append_log(f"Reset speaker to default: {default_name}")
        self.update_status("Speaker reset to default.")
        self._active_speaker_identifier = default_name # Ensure internal state matches


    # --- Chapter Handling ---
    # ... (load_chapters, toggle_check_all, check_highlighted, uncheck_highlighted - no changes) ...
    def load_chapters(self, epub_path):
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
        state = Qt.Checked if check else Qt.Unchecked
        for i in range(self.chapter_list.count()):
            self.chapter_list.item(i).setCheckState(state)

    def check_highlighted(self):
        selected_items = self.chapter_list.selectedItems()
        if not selected_items:
            self.update_status("Select chapters in the list first to check them.")
            return
        for item in selected_items: item.setCheckState(Qt.Checked)
        self.update_status(f"Checked {len(selected_items)} highlighted chapters.")

    def uncheck_highlighted(self):
        selected_items = self.chapter_list.selectedItems()
        if not selected_items:
            self.update_status("Select chapters in the list first to uncheck them.")
            return
        for item in selected_items: item.setCheckState(Qt.Unchecked)
        self.update_status(f"Unchecked {len(selected_items)} highlighted chapters.")


    # --- Conversion Process ---
    def start_conversion(self):
        # ... (checks remain the same) ...
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

        # Use the currently selected speaker identifier from the dropdown/internal state
        params = {
            'epub_path': self.current_epub_path,
            'output_dir': self.current_output_dir,
            'temperature': self.temp_spin.value(),
            'selected_chapter_indices': selected_chapter_indices,
            'speaker_profile': self._active_speaker_identifier # Pass the active identifier (str name/path or object)
        }

        # ... (rest of start_conversion: logging, disable controls, create worker/thread, connect signals, start thread) ...
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

    # --- Other Methods ---
    # ... (stop_conversion, conversion_finished, reset_ui_after_conversion, thread_cleanup) ...
    # ... (update_progress, highlight_current_chapter, reset_chapter_highlight) ...
    # ... (handle_overwrite_request_dialog, closeEvent) ...
    # All these should remain largely unchanged

    def stop_conversion(self):
        if self.worker and self.thread and self.thread.isRunning():
            self.update_status("Stopping conversion...")
            self.append_log("Attempting to stop the conversion...")
            self.stop_btn.setEnabled(False)
            self.worker.stop()

    def conversion_finished(self, success, message):
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
         self.set_controls_enabled(True)
         self.reset_chapter_highlight()

    def thread_cleanup(self):
         if self.worker: self.worker.deleteLater()
         if self.thread: self.thread.deleteLater()
         self.worker = None
         self.thread = None

    def update_progress(self, current_chap_num, total_chapters, chapter_title):
        self.progress_bar.setMaximum(total_chapters)
        self.progress_bar.setValue(current_chap_num)
        if total_chapters > 0:
            progress_percent = (current_chap_num / total_chapters) * 100
            self.progress_bar.setFormat(f"Chapter {current_chap_num}/{total_chapters} ({progress_percent:.0f}%)")
        else:
             self.progress_bar.setFormat(f"Chapter {current_chap_num}/{total_chapters}")
        self.update_status(f"Processing chapter {current_chap_num}/{total_chapters}: {chapter_title}")

    def highlight_current_chapter(self, index):
        self.reset_chapter_highlight()
        if 0 <= index < self.chapter_list.count():
            item = self.chapter_list.item(index)
            if item:
                item.setSelected(True)
                self.chapter_list.scrollToItem(item, QListWidget.ScrollHint.PositionAtCenter)
                self.highlighted_chapter_item = item

    def reset_chapter_highlight(self):
         if self.highlighted_chapter_item:
            self.highlighted_chapter_item.setSelected(False)
            self.highlighted_chapter_item = None

    def handle_overwrite_request_dialog(self, output_wav, output_m4b):
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
        if self.thread and self.thread.isRunning():
            reply = QMessageBox.question(
                self, 'Confirm Exit', "A conversion is in progress. Stop and exit?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No
            )
            if reply == QMessageBox.StandardButton.Yes:
                self.append_log("Exiting application - stopping active conversion.")
                self.stop_conversion()
                if self.thread:
                    if not self.thread.wait(3000): self.append_log("Warning: Worker thread did not finish stopping gracefully.")
                event.accept()
            else:
                event.ignore()
        else:
            event.accept()


# --- Main Execution ---
if __name__ == "__main__":
    # ... (keep optional High DPI and Theme) ...
    app = QApplication(sys.argv)

    app.setStyle("Fusion")
    # ... (dark theme palette code) ...
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
         sys.exit(1) # Error message shown during import failure

# --- END OF FILE epub_to_speech_oute_ui.py ---