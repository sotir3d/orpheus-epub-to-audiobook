# --- START OF FILE epub_to_speech_oute_ui.py ---

import os
import sys
import time
import re # Import re for speaker saving filename cleaning
from datetime import datetime
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
                               QListWidget, QListWidgetItem, QPushButton, QLabel, QComboBox,
                               QProgressBar, QFileDialog, QMessageBox, QCheckBox, QDoubleSpinBox,
                               QTextEdit, QGroupBox, QFormLayout, QSizePolicy, QSpinBox, # Added QSpinBox
                               QStatusBar)
from PySide6.QtCore import Qt, QThread, Signal, QObject, QTimer
from PySide6.QtGui import QPalette, QColor, QIcon

# Import backend and outetts
try:
    import epub_to_speech_oute
    import outetts
except ImportError as e:
    print(f"Error importing backend or outetts: {e}")
    app = QApplication([])
    QMessageBox.critical(None, "Import Error", "Failed to import backend script or outetts library.\n"
                         f"Make sure they are installed and accessible.\n\nError: {e}")
    sys.exit(1)

# --- ConversionWorker ---
class ConversionWorker(QObject):
    progress = Signal(int, int, str)
    processing_chapter_index = Signal(int)
    log_message = Signal(str)
    finished = Signal(bool, str)
    overwrite_required = Signal(str, str)

    # Accept sampler_options dictionary
    def __init__(self, epub_path, output_dir, selected_chapter_indices, speaker_profile, sampler_options):
        super().__init__()
        self.epub_path = epub_path
        self.output_dir = output_dir
        self.selected_chapter_indices = selected_chapter_indices
        self.speaker_profile = speaker_profile
        self.sampler_options = sampler_options # Store the dictionary
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
                epub_path=self.epub_path,
                output_dir=self.output_dir,
                selected_chapter_indices=self.selected_chapter_indices,
                speaker_profile=self.speaker_profile,
                sampler_options=self.sampler_options, # Pass the dictionary
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

# --- MainWindow ---
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("EPUB to Audiobook Converter (outeTTS)")
        self.setGeometry(100, 100, 1000, 700) # Example: 1000 width, 700 height

        self.worker = None
        self.thread = None
        self.current_epub_path = None
        self.current_output_dir = None
        self.book_title = None
        self.all_chapters_data = []
        self.highlighted_chapter_item = None
        self.normal_palette = self.palette()

        self._active_speaker_identifier = epub_to_speech_oute.DEFAULT_SPEAKER

        # Get default sampler values from backend (or define defaults here matching backend)
        self.default_sampler_options = {
            "temperature": epub_to_speech_oute.DEFAULT_TEMPERATURE,
            "repetition_penalty": epub_to_speech_oute.DEFAULT_REPETITION_PENALTY,
            "top_k": epub_to_speech_oute.DEFAULT_TOP_K,
            "top_p": epub_to_speech_oute.DEFAULT_TOP_P,
            "min_p": epub_to_speech_oute.DEFAULT_MIN_P,
            "mirostat": epub_to_speech_oute.DEFAULT_MIROSTAT,
            "mirostat_tau": epub_to_speech_oute.DEFAULT_MIROSTAT_TAU,
            "mirostat_eta": epub_to_speech_oute.DEFAULT_MIROSTAT_ETA,
        }

        self.init_ui()
        self.update_status("Ready")
        self.check_backend_initialization()

    def check_backend_initialization(self):
        self.update_status("Initializing outeTTS backend...")
        QApplication.processEvents()
        try:
            epub_to_speech_oute.get_outeTTS_interface()
            self.append_log("outeTTS backend initialized successfully.")
            self.update_status("Ready (outeTTS backend loaded)")
            self.populate_speaker_dropdown()
            self.set_controls_enabled(True)
        except Exception as e:
             self.append_log(f"❌ ERROR: Failed to initialize outeTTS backend: {e}")
             self.update_status("ERROR: outeTTS backend failed to load!")
             QMessageBox.critical(self, "Backend Error",
                                  f"Failed to initialize the outeTTS backend.\n"
                                  f"Please check console logs and ensure models are accessible.\n\nError: {e}")
             self.set_controls_enabled(False)

    def init_ui(self):
        main_widget = QWidget()
        # Overall vertical layout for the main widget's content + control buttons
        main_layout = QVBoxLayout(main_widget)
        main_layout.setContentsMargins(10, 10, 10, 10) # Add some padding
        main_layout.setSpacing(10) # Add spacing between major elements

        # --- Top Horizontal Splitter ---
        top_h_layout = QHBoxLayout()
        top_h_layout.setSpacing(10)

        # === Left Vertical Column ===
        left_v_layout = QVBoxLayout()
        left_v_layout.setSpacing(10)

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
        left_v_layout.addWidget(file_group)

        # --- Chapters Group ---
        chapter_group = QGroupBox("Chapters")
        chapter_layout = QVBoxLayout()
        self.chapter_list = QListWidget()
        self.chapter_list.setSelectionMode(QListWidget.ExtendedSelection)
        self.chapter_list.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding) # Allow chapter list to expand
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
        left_v_layout.addWidget(chapter_group) # Add chapter group to left column

        # Add Left Layout to Top Horizontal Layout
        top_h_layout.addLayout(left_v_layout, stretch=3) # Give chapters more horizontal space initially

        # === Right Vertical Column ===
        right_v_layout = QVBoxLayout()
        right_v_layout.setSpacing(10)

        # --- Parameters Group (with internal split) ---
        params_group = QGroupBox("Conversion Parameters")
        params_outer_v_layout = QVBoxLayout(params_group) # Use QVBoxLayout for group

        # Speaker Row (stays at top of parameters)
        speaker_row_widget = QWidget()
        speaker_layout = QHBoxLayout(speaker_row_widget)
        speaker_layout.setContentsMargins(0,0,0,0)
        self.speaker_combo = QComboBox()
        self.speaker_combo.setToolTip("Select a speaker profile (default or saved .json).")
        self.speaker_combo.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.speaker_combo.activated.connect(self.speaker_selection_changed)
        self.create_speaker_btn = QPushButton("Create...")
        self.create_speaker_btn.setToolTip("Create a new speaker profile from a WAV/MP3/FLAC file.")
        self.create_speaker_btn.clicked.connect(self.create_speaker_from_audio)
        speaker_layout.addWidget(QLabel("Speaker Profile:"), 0, Qt.AlignLeft) # Add label here
        speaker_layout.addWidget(self.speaker_combo, 1)
        speaker_layout.addWidget(self.create_speaker_btn)
        params_outer_v_layout.addWidget(speaker_row_widget)

        # Horizontal layout for the two columns of samplers
        sampler_h_layout = QHBoxLayout()
        sampler_h_layout.setSpacing(15) # Spacing between sampler columns

        # Sampler Column 1 (Form Layout)
        sampler_form_left = QFormLayout()
        sampler_form_left.setSpacing(8)
        self.temp_spin = QDoubleSpinBox()
        self.temp_spin.setRange(0.0, 2.0)
        self.temp_spin.setValue(self.default_sampler_options["temperature"])
        self.temp_spin.setSingleStep(0.05)
        self.temp_spin.setToolTip("Controls randomness. Higher values increase diversity (0.0-2.0).")
        sampler_form_left.addRow(QLabel("Temperature:"), self.temp_spin)

        self.rep_penalty_spin = QDoubleSpinBox()
        self.rep_penalty_spin.setRange(0.0, 5.0)
        self.rep_penalty_spin.setValue(self.default_sampler_options["repetition_penalty"])
        self.rep_penalty_spin.setSingleStep(0.05)
        self.rep_penalty_spin.setToolTip("Penalizes repeating sequences. Higher values reduce repetition (e.g., 1.0-1.5).")
        sampler_form_left.addRow(QLabel("Repetition Penalty:"), self.rep_penalty_spin)

        self.top_k_spin = QSpinBox()
        self.top_k_spin.setRange(0, 200)
        self.top_k_spin.setValue(self.default_sampler_options["top_k"])
        self.top_k_spin.setToolTip("Consider only the top K most likely tokens (0 = disabled).")
        sampler_form_left.addRow(QLabel("Top-K:"), self.top_k_spin)

        self.top_p_spin = QDoubleSpinBox()
        self.top_p_spin.setRange(0.0, 1.0)
        self.top_p_spin.setValue(self.default_sampler_options["top_p"])
        self.top_p_spin.setSingleStep(0.01)
        self.top_p_spin.setToolTip("Consider tokens comprising the top P probability mass (0.0-1.0).")
        sampler_form_left.addRow(QLabel("Top-P:"), self.top_p_spin)

        sampler_h_layout.addLayout(sampler_form_left)

        # Sampler Column 2 (Form Layout)
        sampler_form_right = QFormLayout()
        sampler_form_right.setSpacing(8)
        self.min_p_spin = QDoubleSpinBox()
        self.min_p_spin.setRange(0.0, 1.0)
        self.min_p_spin.setValue(self.default_sampler_options["min_p"])
        self.min_p_spin.setSingleStep(0.01)
        self.min_p_spin.setToolTip("Minimum probability for a token to be considered (0.0-1.0).")
        sampler_form_right.addRow(QLabel("Min-P:"), self.min_p_spin)

        self.mirostat_check = QCheckBox()
        self.mirostat_check.setChecked(self.default_sampler_options["mirostat"])
        self.mirostat_check.setToolTip("Enable Mirostat sampling algorithm.")
        self.mirostat_check.stateChanged.connect(self.update_mirostat_controls)
        sampler_form_right.addRow(QLabel("Use Mirostat:"), self.mirostat_check)

        self.mirostat_tau_spin = QDoubleSpinBox()
        self.mirostat_tau_spin.setRange(0.0, 10.0)
        self.mirostat_tau_spin.setValue(self.default_sampler_options["mirostat_tau"])
        self.mirostat_tau_spin.setSingleStep(0.1)
        self.mirostat_tau_spin.setToolTip("Mirostat learning target.")
        sampler_form_right.addRow(QLabel("Mirostat Tau:"), self.mirostat_tau_spin)

        self.mirostat_eta_spin = QDoubleSpinBox()
        self.mirostat_eta_spin.setRange(0.0, 1.0)
        self.mirostat_eta_spin.setValue(self.default_sampler_options["mirostat_eta"])
        self.mirostat_eta_spin.setSingleStep(0.01)
        self.mirostat_eta_spin.setToolTip("Mirostat learning rate.")
        sampler_form_right.addRow(QLabel("Mirostat Eta:"), self.mirostat_eta_spin)

        sampler_h_layout.addLayout(sampler_form_right)

        # Add the sampler horizontal layout to the parameter group's outer layout
        params_outer_v_layout.addLayout(sampler_h_layout)
        self.update_mirostat_controls() # Initial update for Mirostat controls

        right_v_layout.addWidget(params_group) # Add parameters group to right column

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
        right_v_layout.addWidget(output_group) # Add output group to right column

        # --- Progress and Log Group ---
        progress_log_group = QGroupBox("Progress & Log")
        progress_log_layout = QVBoxLayout()
        self.progress_bar = QProgressBar()
        self.progress_bar.setTextVisible(True)
        self.progress_bar.setValue(0)
        self.log_area = QTextEdit()
        self.log_area.setReadOnly(True)
        self.log_area.setLineWrapMode(QTextEdit.WidgetWidth) # Changed line wrap mode
        self.log_area.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding) # Allow log to expand
        progress_log_layout.addWidget(self.progress_bar)
        progress_log_layout.addWidget(QLabel("Log:"))
        progress_log_layout.addWidget(self.log_area)
        progress_log_group.setLayout(progress_log_layout)
        right_v_layout.addWidget(progress_log_group) # Add progress/log group to right column

        # Add Right Layout to Top Horizontal Layout
        top_h_layout.addLayout(right_v_layout, stretch=4) # Give right side slightly more space

        # Add the top horizontal layout (containing left/right columns) to the main vertical layout
        main_layout.addLayout(top_h_layout)

        # --- Control Buttons ---
        control_layout = QHBoxLayout()
        self.start_btn = QPushButton("Start Conversion")
        self.start_btn.setStyleSheet("background-color: darkseagreen; color: black; padding: 5px;") # Added padding
        self.start_btn.clicked.connect(self.start_conversion)
        self.stop_btn = QPushButton("Stop Conversion")
        self.stop_btn.setStyleSheet("background-color: indianred; color: black; padding: 5px;") # Added padding
        self.stop_btn.clicked.connect(self.stop_conversion)
        self.stop_btn.setEnabled(False)
        control_layout.addStretch()
        control_layout.addWidget(self.start_btn)
        control_layout.addWidget(self.stop_btn)
        control_layout.addStretch()

        # Add control buttons layout to the main vertical layout (below the top split)
        main_layout.addLayout(control_layout)

        # --- Status Bar ---
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        self.status_label = QLabel("Ready")
        self.status_bar.addWidget(self.status_label)

        # Set the main widget and initial state
        self.setCentralWidget(main_widget)
        self.set_controls_enabled(False) # Start disabled

        # Set a more reasonable default size
        self.setGeometry(100, 100, 1000, 700) # Wider, less tall default

    # --- Speaker Dropdown Logic ---
    # ... (populate_speaker_dropdown, speaker_selection_changed - no changes needed) ...
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

        found_profiles.sort(key=lambda x: x['display'])

        for profile in found_profiles:
            self.speaker_combo.addItem(profile['display'], userData=profile['path'])

        # 3. Reselect previous or default
        found_index = self.speaker_combo.findData(current_selection_identifier)
        if found_index != -1:
            self.speaker_combo.setCurrentIndex(found_index)
        else:
            self.speaker_combo.setCurrentIndex(0)
            self._active_speaker_identifier = self.speaker_combo.currentData()

        self.speaker_combo.blockSignals(False) # Re-enable signals
        self.speaker_selection_changed() # Manually trigger update


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
             self.append_log("Warning: No data associated with selected speaker. Reverting to default.")
             self.reset_speaker_to_default()

    # --- UI Control and Logging ---
    # ... (update_status, append_log - no changes) ...
    def update_status(self, message):
        self.status_label.setText(message)

    def append_log(self, message):
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.log_area.append(f"[{timestamp}] {message}")
        self.log_area.verticalScrollBar().setValue(self.log_area.verticalScrollBar().maximum())

    def update_mirostat_controls(self):
        """Enable/disable Mirostat Tau and Eta based on checkbox."""
        enabled = self.mirostat_check.isChecked()
        self.mirostat_tau_spin.setEnabled(enabled)
        self.mirostat_eta_spin.setEnabled(enabled)


    def set_controls_enabled(self, enabled, force_not_converting=False):
        """Enable or disable input controls, considering backend status and conversion state."""
        timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        is_converting = False
        if not force_not_converting:
             is_converting = self.thread is not None and self.thread.isRunning()
        elif force_not_converting:
             is_converting = False

        backend_ok = self.status_label.text() != "ERROR: outeTTS backend failed to load!"
        # print(f"[{timestamp}] set_controls_enabled(enabled={enabled}, force_not_converting={force_not_converting}), is_converting={is_converting}, backend_ok={backend_ok}")

        effective_enabled_for_inputs = enabled and backend_ok and not is_converting

        self.select_epub_btn.setEnabled(effective_enabled_for_inputs)
        self.chapter_list.setEnabled(effective_enabled_for_inputs)
        self.speaker_combo.setEnabled(effective_enabled_for_inputs)
        self.create_speaker_btn.setEnabled(effective_enabled_for_inputs)
        # Chapter list buttons
        buttons_layout_item = self.chapter_list.parent().layout().itemAt(1)
        if buttons_layout_item and buttons_layout_item.layout():
             buttons_layout = buttons_layout_item.layout()
             for i in range(buttons_layout.count()):
                  widget_item = buttons_layout.itemAt(i)
                  if widget_item and widget_item.widget():
                       widget_item.widget().setEnabled(effective_enabled_for_inputs)

        # Sampler controls
        self.temp_spin.setEnabled(effective_enabled_for_inputs)
        self.rep_penalty_spin.setEnabled(effective_enabled_for_inputs)
        self.top_k_spin.setEnabled(effective_enabled_for_inputs)
        self.top_p_spin.setEnabled(effective_enabled_for_inputs)
        self.min_p_spin.setEnabled(effective_enabled_for_inputs)
        self.mirostat_check.setEnabled(effective_enabled_for_inputs)
        # Mirostat Tau/Eta enabled state depends on both global enable AND checkbox state
        mirostat_sub_enabled = effective_enabled_for_inputs and self.mirostat_check.isChecked()
        self.mirostat_tau_spin.setEnabled(mirostat_sub_enabled)
        self.mirostat_eta_spin.setEnabled(mirostat_sub_enabled)

        self.select_output_btn.setEnabled(effective_enabled_for_inputs)

        # Start/Stop buttons
        start_enabled = backend_ok and not is_converting
        stop_enabled = backend_ok and is_converting
        self.start_btn.setEnabled(start_enabled)
        self.stop_btn.setEnabled(stop_enabled)
        # print(f"[{timestamp}]   -> Start Button Enabled: {start_enabled}, Stop Button Enabled: {stop_enabled}")

        start_text = ""
        if not backend_ok: start_text = "Backend Error"
        elif is_converting: start_text = "Converting..."
        else: start_text = "Start Conversion"
        self.start_btn.setText(start_text)
        # print(f"[{timestamp}]   -> Start Button Text: '{start_text}'")

    # --- File/Directory Selection ---
    # ... (select_epub, select_output - no changes) ...
    def select_epub(self):
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
        start_dir = os.path.dirname(self.current_epub_path) if self.current_epub_path else ""
        path = QFileDialog.getExistingDirectory(self, "Select Output Directory", start_dir)
        if path:
            self.current_output_dir = path
            self.output_label.setText(f"Output to: {path}")
            self.output_label.setToolTip(path)
            self.append_log(f"Set output directory: {path}")


    # --- Speaker Creation/Saving ---
    # ... (create_speaker_from_audio, save_speaker_profile, reset_speaker_to_default - no changes needed) ...
    def create_speaker_from_audio(self):
        audio_filter = "Audio Files (*.wav *.mp3 *.flac *.ogg);;All Files (*)"
        path, _ = QFileDialog.getOpenFileName(self, "Select Audio File for Speaker Profile", "", audio_filter)
        if not path: return

        self.update_status(f"Creating speaker from {os.path.basename(path)}...")
        self.append_log(f"Attempting to create speaker profile from: {path}")
        QApplication.processEvents()

        temp_speaker_object = None

        try:
            interface = epub_to_speech_oute.get_outeTTS_interface()
            if not interface: raise RuntimeError("outeTTS Interface not available.")

            self.set_controls_enabled(False)
            QApplication.processEvents()
            temp_speaker_object = interface.create_speaker(path) # Store the object
            self.set_controls_enabled(True)

            self.append_log(f"Successfully created speaker profile object from {os.path.basename(path)}.")
            self.update_status("Custom speaker created (unsaved).")

            reply = QMessageBox.question(self, "Save Speaker Profile?",
                                         "Speaker profile created successfully.\n\n"
                                         "Do you want to save this profile as a .json file to the list?",
                                         QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                         QMessageBox.StandardButton.Yes)

            if reply == QMessageBox.StandardButton.Yes:
                self.save_speaker_profile(temp_speaker_object, os.path.splitext(os.path.basename(path))[0])
            else:
                self._active_speaker_identifier = temp_speaker_object
                self.append_log("Using newly created speaker (unsaved) for next conversion.")
                self.speaker_combo.setToolTip(f"Using unsaved speaker from {os.path.basename(path)}")
                default_index = self.speaker_combo.findData(epub_to_speech_oute.DEFAULT_SPEAKER)
                if default_index != -1:
                    self.speaker_combo.setCurrentIndex(default_index)

        except Exception as e:
            self.set_controls_enabled(True)
            self.append_log(f"❌ Error creating speaker profile: {e}")
            self.update_status("Error creating speaker.")
            QMessageBox.critical(self, "Speaker Creation Error", f"Failed to create speaker profile:\n{e}")

    def save_speaker_profile(self, speaker_object, suggested_name="custom_speaker"):
        profile_dir = epub_to_speech_oute.SPEAKER_PROFILE_DIR
        os.makedirs(profile_dir, exist_ok=True)

        safe_suggested_name = re.sub(r'[^\w\-]+', '_', suggested_name)
        if not safe_suggested_name: safe_suggested_name = "custom_speaker"

        counter = 0
        save_name_base = safe_suggested_name
        while True:
             save_filename = f"{safe_suggested_name}.json"
             save_path = os.path.join(profile_dir, save_filename)
             if not os.path.exists(save_path):
                 break
             counter += 1
             safe_suggested_name = f"{save_name_base}_{counter}"
             if counter > 100:
                 self.append_log("Error: Could not find a unique filename to save speaker profile.")
                 QMessageBox.warning(self, "Save Error", "Could not determine a unique filename in the speaker_profiles directory.")
                 return

        confirmed_save_path, ok = QFileDialog.getSaveFileName(
            self, "Confirm Save Speaker Profile", save_path, "JSON Files (*.json)"
        )

        if not ok or not confirmed_save_path:
            self.append_log("Speaker profile saving cancelled by user.")
            self._active_speaker_identifier = speaker_object
            self.append_log("Using newly created speaker (unsaved) for next conversion.")
            self.speaker_combo.setToolTip(f"Using unsaved speaker from temporary object")
            default_index = self.speaker_combo.findData(epub_to_speech_oute.DEFAULT_SPEAKER)
            if default_index != -1: self.speaker_combo.setCurrentIndex(default_index)
            return

        if not confirmed_save_path.lower().endswith(".json"):
            confirmed_save_path += ".json"

        try:
            interface = epub_to_speech_oute.get_outeTTS_interface()
            if not interface: raise RuntimeError("outeTTS Interface not available.")

            interface.save_speaker(speaker_object, confirmed_save_path)
            self.append_log(f"Speaker profile saved successfully to: {confirmed_save_path}")
            self.update_status("Speaker profile saved.")

            self._active_speaker_identifier = confirmed_save_path
            self.populate_speaker_dropdown()

        except Exception as e:
             self.append_log(f"❌ Error saving speaker profile: {e}")
             self.update_status("Error saving speaker profile.")
             QMessageBox.critical(self, "Save Error", f"Failed to save speaker profile:\n{e}")

    def reset_speaker_to_default(self):
        default_name = epub_to_speech_oute.DEFAULT_SPEAKER
        default_index = self.speaker_combo.findData(default_name)
        if default_index != -1:
            self.speaker_combo.setCurrentIndex(default_index)
        else:
            self.append_log("Warning: Could not find default speaker in dropdown to reset.")
        self.append_log(f"Reset speaker to default: {default_name}")
        self.update_status("Speaker reset to default.")
        self._active_speaker_identifier = default_name


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
        # Basic checks
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

        # --- Collect Sampler Options ---
        sampler_options = {
            "temperature": self.temp_spin.value(),
            "repetition_penalty": self.rep_penalty_spin.value(),
            "top_k": self.top_k_spin.value(),
            "top_p": self.top_p_spin.value(),
            "min_p": self.min_p_spin.value(),
            "mirostat": self.mirostat_check.isChecked(),
            "mirostat_tau": self.mirostat_tau_spin.value(),
            "mirostat_eta": self.mirostat_eta_spin.value(),
        }

        # Create worker parameters dictionary
        worker_params = {
            'epub_path': self.current_epub_path,
            'output_dir': self.current_output_dir,
            'selected_chapter_indices': selected_chapter_indices,
            'speaker_profile': self._active_speaker_identifier,
            'sampler_options': sampler_options # Pass the collected options
        }

        # Log parameters being used
        self.append_log("="*30 + " Starting Conversion " + "="*30)
        self.append_log(f"  EPUB: {os.path.basename(self.current_epub_path)}")
        self.append_log(f"  Output Dir: {self.current_output_dir or 'Default'}")
        self.append_log(f"  Speaker: {self.speaker_combo.currentText()} ({'Path/Obj' if isinstance(self._active_speaker_identifier, str) else 'Object'})")
        self.append_log(f"  Chapters: {len(selected_chapter_indices)} selected")
        self.append_log(f"  Sampler Options:")
        for key, value in sampler_options.items():
            self.append_log(f"    {key}: {value}")
        self.append_log("-" * 70)


        self.update_status("Starting conversion...")
        self.progress_bar.setValue(0)
        self.progress_bar.setFormat("Starting...")
        self.progress_bar.setStyleSheet("")

        self.set_controls_enabled(False)

        # Create and start worker thread
        self.thread = QThread(self)
        self.worker = ConversionWorker(**worker_params) # Unpack the params dict
        self.worker.moveToThread(self.thread)

        # Connect signals
        self.worker.progress.connect(self.update_progress)
        self.worker.processing_chapter_index.connect(self.highlight_current_chapter)
        self.worker.log_message.connect(self.append_log)
        self.worker.finished.connect(self.conversion_finished)
        self.worker.overwrite_required.connect(self.handle_overwrite_request_dialog)

        self.thread.started.connect(self.worker.run)
        self.thread.finished.connect(self.thread_cleanup) # Ensure cleanup connection

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
            self.stop_btn.setEnabled(False) # Disable immediately
            self.worker.stop()

    def conversion_finished(self, success, message):
        # This runs in the main thread
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

        # Crucially, reset UI state AFTER thread/worker cleanup has been *scheduled*
        # The actual cleanup happens when the event loop processes deleteLater
        self.reset_ui_after_conversion()


    def reset_ui_after_conversion(self):
         """Resets UI elements after conversion finishes, stops, or errors."""
         # print(f"[{datetime.now().strftime('%H:%M:%S.%f')[:-3]}] reset_ui_after_conversion called")
         self.reset_chapter_highlight()
         # Force UI update assuming conversion is definitely over
         # print(f"[{datetime.now().strftime('%H:%M:%S.%f')[:-3]}]   Calling set_controls_enabled(True, force_not_converting=True)")
         self.set_controls_enabled(True, force_not_converting=True)


    def thread_cleanup(self):
         """Clean up thread and worker objects."""
         # print(f"[{datetime.now().strftime('%H:%M:%S.%f')[:-3]}] thread_cleanup called")
         # deleteLater is handled by finished signal connection or event loop
         self.worker = None
         self.thread = None
         # print(f"[{datetime.now().strftime('%H:%M:%S.%f')[:-3]}]   Worker and Thread references set to None")


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
            try:
                self.highlighted_chapter_item.setSelected(False)
            except RuntimeError: # Item might have been deleted if EPUB reloaded
                pass
            self.highlighted_chapter_item = None


    def handle_overwrite_request_dialog(self, output_wav, output_m4b):
        # This slot runs in the main thread, called by signal from worker
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
        # Send response back to worker (worker is waiting in its handle_overwrite_request)
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
                # Give thread time to potentially finish stopping
                if self.thread:
                     # Wait briefly, but don't block excessively on exit
                    if not self.thread.wait(1500):
                        self.append_log("Warning: Worker thread did not finish stopping quickly on exit.")
                event.accept()
            else:
                event.ignore()
        else:
            event.accept()


# --- Main Execution ---
if __name__ == "__main__":
    # Optional High DPI scaling
    # QApplication.setAttribute(Qt.AA_EnableHighDpiScaling)
    # QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps)

    app = QApplication(sys.argv)

    # --- Dark Theme ---
    app.setStyle("Fusion")
    dark_palette = QPalette()
    # ... (palette colors - unchanged) ...
    dark_palette.setColor(QPalette.Window, QColor(53, 53, 53))
    dark_palette.setColor(QPalette.WindowText, Qt.white)
    dark_palette.setColor(QPalette.Base, QColor(35, 35, 35))
    dark_palette.setColor(QPalette.AlternateBase, QColor(53, 53, 53))
    dark_palette.setColor(QPalette.ToolTipBase, QColor(35, 35, 35)) # Darker tooltips
    dark_palette.setColor(QPalette.ToolTipText, Qt.white)
    dark_palette.setColor(QPalette.Text, Qt.white)
    dark_palette.setColor(QPalette.Button, QColor(53, 53, 53))
    dark_palette.setColor(QPalette.ButtonText, Qt.white)
    dark_palette.setColor(QPalette.BrightText, Qt.red)
    dark_palette.setColor(QPalette.Link, QColor(42, 130, 218))
    dark_palette.setColor(QPalette.Highlight, QColor(42, 130, 218))
    dark_palette.setColor(QPalette.HighlightedText, QColor(230, 230, 230)) # Lighter highlighted text
    text_disabled_color = QColor(127, 127, 127)
    button_disabled_color = QColor(80, 80, 80) # Darker disabled button
    dark_palette.setColor(QPalette.Disabled, QPalette.Text, text_disabled_color)
    dark_palette.setColor(QPalette.Disabled, QPalette.WindowText, text_disabled_color)
    dark_palette.setColor(QPalette.Disabled, QPalette.ButtonText, text_disabled_color)
    dark_palette.setColor(QPalette.Disabled, QPalette.Base, button_disabled_color)
    dark_palette.setColor(QPalette.Disabled, QPalette.Button, button_disabled_color)
    dark_palette.setColor(QPalette.Disabled, QPalette.Highlight, QColor(80, 80, 80))

    app.setPalette(dark_palette)
    app.setStyleSheet("QToolTip { color: #ffffff; background-color: #2a82da; border: 1px solid white; }")
    # --- End Dark Theme ---

    if 'epub_to_speech_oute' in sys.modules:
         window = MainWindow()
         window.show()
         sys.exit(app.exec())
    else:
         # Error message already shown during import failure
         sys.exit(1)

# --- END OF FILE epub_to_speech_oute_ui.py ---