import json
import os
import sys
import threading
import time
import urllib.request
import zipfile
from pathlib import Path
from typing import Optional
from platform import system

import numpy as np
import sounddevice as sd
import onnxruntime as ort
from vosk import Model as VoskModel, KaldiRecognizer
import openwakeword
from openwakeword.model import Model as WakeWordModel
from PySide6.QtCore import Qt, QStandardPaths, QTranslator, QThread, Signal
from PySide6.QtGui import QAction, QCloseEvent, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QProgressBar,
    QProgressDialog,
    QPushButton,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
)

if system() == "Windows":
    import win32api
    import win32gui
    import win32con

WAKE_WORD_FILE = "alexa_v0.1.onnx"
VOSK_MODEL_URL = "https://alphacephei.com/vosk/models/vosk-model-en-us-0.22.zip"
VOSK_MODEL_NAME = "vosk-model-en-us-0.22"


class DownloadThread(QThread):
    """Thread for downloading files without blocking the UI"""

    progress_updated = Signal(int)
    download_finished = Signal(str)
    download_error = Signal(str)

    def __init__(self, url: str, target_path: Path, parent=None):
        super().__init__(parent)
        self.url = url
        self.target_path = target_path
        self.target_path.parent.mkdir(parents=True, exist_ok=True)

    def run(self):
        """Download file in background thread"""
        try:

            def progress_hook(block_num, block_size, total_size):
                if total_size > 0:
                    downloaded = block_num * block_size
                    percentage = min(int(downloaded * 100 / total_size), 100)
                    self.progress_updated.emit(percentage)

            urllib.request.urlretrieve(self.url, self.target_path, progress_hook)
            self.download_finished.emit(str(self.target_path))

        except Exception as e:
            self.download_error.emit(str(e))


class ModelDownloadDialog(QProgressDialog):
    """Dialog for downloading and extracting models"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(self.tr("Download Model"))
        self.setLabelText(self.tr("Downloading Vosk model..."))
        self.setRange(0, 100)
        self.setModal(True)
        self.setAutoClose(False)
        self.setAutoReset(False)

        # Thread for downloading
        self.download_thread = None
        self.models_dir = None
        self.target_file = None

    def start_download(self, models_dir: Path):
        """Start the download process"""
        self.models_dir = models_dir
        self.target_file = models_dir / "vosk-model-en-us-0.22.zip"

        # Create download thread
        self.download_thread = DownloadThread(VOSK_MODEL_URL, self.target_file, self)
        self.download_thread.progress_updated.connect(self.setValue)
        self.download_thread.download_finished.connect(self.on_download_finished)
        self.download_thread.download_error.connect(self.on_download_error)

        # Start download
        self.download_thread.start()
        self.show()

    def on_download_finished(self, file_path: str):
        """Handle successful download completion"""
        self.setLabelText(self.tr("Extracting model..."))
        self.setValue(100)

        try:
            # Extract the zip file
            with zipfile.ZipFile(file_path, "r") as zip_ref:
                zip_ref.extractall(self.models_dir)

            # Clean up zip file
            os.remove(file_path)

            self.setLabelText(self.tr("Model downloaded successfully!"))
            QMessageBox.information(
                self, self.tr("Success"), self.tr("Vosk model downloaded and extracted successfully!")
            )
            self.accept()

        except Exception as e:
            self.on_download_error(f"Failed to extract: {e}")

    def on_download_error(self, error_message: str):
        """Handle download error"""
        self.hide()
        QMessageBox.critical(self, self.tr("Download Error"), self.tr(f"Failed to download model: {error_message}"))
        self.reject()

    def closeEvent(self, event):
        """Handle dialog close event"""
        if self.download_thread and self.download_thread.isRunning():
            self.download_thread.terminate()
            self.download_thread.wait()
        event.accept()


class ApiUrlDialog(QDialog):
    """Dialog for setting API URL"""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(self.tr("Set API URL"))
        self.setModal(True)
        self.resize(400, 150)

        # Setup layout
        layout = QVBoxLayout(self)
        layout.setSpacing(15)
        layout.setContentsMargins(20, 20, 20, 20)

        # URL input
        self.url_label = QLabel(self.tr("Enter API URL:"))
        self.url_input = QLineEdit()
        self.url_input.setPlaceholderText(self.tr("https://api.example.com"))

        # Buttons
        button_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)

        # Set button text
        button_box.button(QDialogButtonBox.Ok).setText(self.tr("Set"))
        button_box.button(QDialogButtonBox.Cancel).setText(self.tr("Cancel"))

        layout.addWidget(self.url_label)
        layout.addWidget(self.url_input)
        layout.addWidget(button_box)

        # Focus on input
        self.url_input.setFocus()

    def get_url(self) -> str:
        """Get the entered URL"""
        return self.url_input.text().strip()


class MainWindow(QMainWindow):
    def __init__(self, app) -> None:
        super().__init__()
        self.app = app
        self.setWindowTitle(self.tr("Aleva - Audio Language Assistant"))
        self.setGeometry(200, 200, 400, 300)

        # Flag to track if we're actually quitting vs just hiding
        self.is_quitting = False

        # Translator for internationalization
        self.translator = QTranslator()

        # Language mapping
        self.language_codes = {"English": "en", "中文": "zh", "日本語": "ja"}

        self.current_language = "en"

        # Configuration
        self.config_dir = Path(QStandardPaths.writableLocation(QStandardPaths.AppDataLocation))
        self.config_file = self.config_dir / "config.json"
        self.config = {}

        # Initialize configuration
        self.init_config()

        # Audio processing variables
        self.is_listening = False
        self.audio_thread: Optional[threading.Thread] = None
        self.oww_model: Optional[WakeWordModel] = None
        self.vosk_model: Optional[VoskModel] = None
        self.vosk_recognizer: Optional[KaldiRecognizer] = None
        self.sample_rate = 16000
        self.chunk_size = 1024

        # Initialize wake word model
        self.init_wake_word_model()

        # Setup UI
        self.setup_ui()

        # Check model status
        has_vosk_model = self.check_and_update_model_status()
        if has_vosk_model:
            self.vosk_model = VoskModel(str(self.config_dir / "models" / VOSK_MODEL_NAME))
            self.vosk_recognizer = KaldiRecognizer(self.vosk_model, self.sample_rate)

        # Setup system tray
        self.setup_system_tray()

        # Refresh microphones on startup
        self.refresh_microphones()

        # Load default language (English)
        self.load_language("en")

    def setup_ui(self) -> None:
        """Setup the main UI components"""
        central_widget = QWidget()
        self.setCentralWidget(central_widget)

        layout = QVBoxLayout(central_widget)
        layout.setSpacing(20)
        layout.setContentsMargins(20, 20, 20, 20)

        # Language selector
        language_layout = QHBoxLayout()
        self.language_label = QLabel(self.tr("Language:"))
        self.language_combo = QComboBox()
        self.language_combo.addItems(["English", "中文", "日本語"])
        self.language_combo.currentTextChanged.connect(self.on_language_changed)

        language_layout.addWidget(self.language_label)
        language_layout.addWidget(self.language_combo)
        language_layout.addStretch()

        # Microphone selector
        microphone_layout = QHBoxLayout()
        self.microphone_label = QLabel(self.tr("Microphone:"))
        self.microphone_combo = QComboBox()
        self.microphone_combo.currentTextChanged.connect(self.on_microphone_changed)
        self.refresh_button = QPushButton(self.tr("Refresh"))
        self.refresh_button.clicked.connect(self.refresh_microphones)

        microphone_layout.addWidget(self.microphone_label)
        microphone_layout.addWidget(self.microphone_combo)
        microphone_layout.addWidget(self.refresh_button)

        # Model section
        model_layout = QHBoxLayout()
        self.model_label = QLabel(self.tr("Model:"))
        self.vosk_model_label = QLabel(self.tr("Not loaded"))
        self.vosk_model_label.setStyleSheet("color: gray; font-style: italic;")
        self.load_model_button = QPushButton(self.tr("Load"))
        self.load_model_button.clicked.connect(self.show_model_download_dialog)

        model_layout.addWidget(self.model_label)
        model_layout.addWidget(self.vosk_model_label)
        model_layout.addStretch()
        model_layout.addWidget(self.load_model_button)

        # API URL section
        api_layout = QHBoxLayout()
        self.api_label = QLabel(self.tr("API URL:"))
        self.api_url = QLabel(self.tr("Not set"))
        self.api_url.setStyleSheet("color: gray; font-style: italic;")
        self.set_api_button = QPushButton(self.tr("Set"))
        self.set_api_button.clicked.connect(self.show_api_dialog)

        api_layout.addWidget(self.api_label)
        api_layout.addWidget(self.api_url)
        api_layout.addStretch()
        api_layout.addWidget(self.set_api_button)

        # Listen button section
        listen_layout = QHBoxLayout()
        self.listen_button = QPushButton(self.tr("Listen"))
        self.listen_button.setCheckable(True)
        self.listen_button.clicked.connect(self.toggle_listening)
        self.status_label = QLabel(self.tr("Ready"))
        self.status_label.setStyleSheet("color: gray; font-style: italic;")

        listen_layout.addWidget(self.listen_button)
        listen_layout.addWidget(self.status_label)
        listen_layout.addStretch()

        layout.addLayout(language_layout)
        layout.addLayout(microphone_layout)
        layout.addLayout(model_layout)
        layout.addLayout(api_layout)
        layout.addLayout(listen_layout)
        layout.addStretch()

    def setup_system_tray(self) -> None:
        """Setup system tray icon and menu"""
        # Create a simple icon programmatically
        pixmap = QPixmap(16, 16)
        pixmap.fill(Qt.blue)
        painter = QPainter(pixmap)
        painter.setPen(Qt.white)
        painter.drawText(pixmap.rect(), Qt.AlignCenter, "A")
        painter.end()

        icon = QIcon(pixmap)

        # Create system tray icon
        self.tray_icon = QSystemTrayIcon(icon, self)

        # Create tray menu
        self.tray_menu = QMenu()

        # Show/Hide action
        self.show_hide_action = QAction(self.tr("Show"), self)
        self.show_hide_action.triggered.connect(self.toggle_visibility)

        # Quit action
        self.quit_action = QAction(self.tr("Quit"), self)
        self.quit_action.triggered.connect(self.quit_application)

        self.tray_menu.addAction(self.show_hide_action)
        self.tray_menu.addSeparator()
        self.tray_menu.addAction(self.quit_action)

        self.tray_icon.setContextMenu(self.tray_menu)

        # Connect tray icon activation (left click)
        self.tray_icon.activated.connect(self.on_tray_activated)

        # Show tray icon
        self.tray_icon.show()

        # Set initial tooltip
        self.tray_icon.setToolTip(self.tr("Aleva - Click to show/hide"))

    def show_api_dialog(self) -> None:
        """Show API URL input dialog"""
        dialog = ApiUrlDialog(self)
        if dialog.exec() == QDialog.Accepted:
            api_url = dialog.get_url()
            if api_url.strip():
                self.api_url.setText(api_url)
                self.api_url.setStyleSheet("color: black; font-style: normal;")
            else:
                self.api_url.setText(self.tr("Not set"))
                self.api_url.setStyleSheet("color: gray; font-style: italic;")

            # Save configuration after API URL change
            self.save_config()

    def show_model_download_dialog(self) -> None:
        """Show model download dialog"""
        models_dir = self.config_dir / "models"

        # Check if model already exists
        vosk_model_dir = models_dir / VOSK_MODEL_NAME
        if vosk_model_dir.exists():
            reply = QMessageBox.question(
                self,
                self.tr("Model Exists"),
                self.tr("Vosk model already exists. Do you want to redownload it?"),
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if reply != QMessageBox.Yes:
                return

        # Start download
        dialog = ModelDownloadDialog(self)
        dialog.start_download(models_dir)

        # Update model status after successful download
        if dialog.exec() == QProgressDialog.Accepted:
            has_model = self.check_and_update_model_status()
            if has_model:
                try:
                    # Initialize Vosk model and recognizer
                    self.vosk_model = VoskModel(str(models_dir / VOSK_MODEL_NAME))
                    self.vosk_recognizer = KaldiRecognizer(self.vosk_model, self.sample_rate)
                    print("Vosk model and recognizer initialized successfully")
                except Exception as e:
                    print(f"Error initializing Vosk model: {e}")
                    self.vosk_model = None
                    self.vosk_recognizer = None

    def check_and_update_model_status(self) -> bool:
        """Check if Vosk model exists and update UI accordingly"""
        models_dir = self.config_dir / "models"
        vosk_model_dir = models_dir / VOSK_MODEL_NAME

        if vosk_model_dir.exists() and vosk_model_dir.is_dir():
            self.vosk_model_label.setText(VOSK_MODEL_NAME)
            self.vosk_model_label.setStyleSheet("color: green; font-weight: bold;")
            self.load_model_button.setText(self.tr("Reload"))
            return True

        self.vosk_model_label.setText(self.tr("Not loaded"))
        self.vosk_model_label.setStyleSheet("color: gray; font-style: italic;")
        self.load_model_button.setText(self.tr("Load"))
        return False

    def init_wake_word_model(self) -> None:
        """Initialize the OpenWakeWord model"""
        try:
            # Initialize with default models first
            model_file = self.config_dir / "models" / WAKE_WORD_FILE
            print(f"Using wake word model: {model_file}")
            self.oww_model = WakeWordModel(
                # TODO: use Aleva model
                # wakeword_models=["aleva"],
                wakeword_models=[str(model_file)],
                # inference_framework="tflite",
                inference_framework="onnx",
                vad_threshold=0.2,
            )
            print("Wake word model initialized successfully")

            # Note: For a custom "aleva" wake word, you would need to train a custom model
            # For now, we'll use the general model and implement simple text matching
            print("Using general wake word detection model")

        except Exception as e:
            print(f"Failed to initialize wake word model: {e}")
            self.oww_model = None

    def init_config(self) -> None:
        """Initialize configuration file"""
        try:
            # Create config directory if it doesn't exist
            self.config_dir.mkdir(parents=True, exist_ok=True)
            print(f"Config directory: {self.config_dir}")

            if self.config_file.exists():
                # Load existing config
                self.load_config()
                print(f"Loaded existing config from: {self.config_file}")
            else:
                # Create default config
                self.create_default_config()
                print(f"Created default config at: {self.config_file}")

        except Exception as e:
            print(f"Error initializing config: {e}")
            # Fallback to default config in memory
            self.config = self.get_default_config()

    def get_default_config(self) -> dict:
        """Get default configuration"""
        return {
            "version": "0.1.0",
            "ui": {"language": "en", "window_geometry": {"x": 200, "y": 200, "width": 400, "height": 300}},
            "audio": {
                "sample_rate": 16000,
                "chunk_size": 1024,
                "selected_microphone": None,
                "wake_word_threshold": 0.5,
            },
            "api": {"url": None},
            "models": {"vosk_model_path": None},
            "system": {"minimize_to_tray": True, "show_tray_notifications": True},
        }

    def create_default_config(self) -> None:
        """Create default configuration file"""
        self.config = self.get_default_config()
        # TODO: use Aleva model
        models_dir = self.config_dir / "models"
        openwakeword.utils.download_models(target_directory=str(models_dir))
        self.save_config()

    def load_config(self) -> None:
        """Load configuration from file"""
        try:
            with open(self.config_file, "r", encoding="utf-8") as f:
                loaded_config = json.load(f)

            # Merge with default config to ensure all keys exist
            default_config = self.get_default_config()
            self.config = self.merge_configs(default_config, loaded_config)

            # Apply loaded configuration
            self.apply_config()

        except Exception as e:
            print(f"Error loading config: {e}")
            self.config = self.get_default_config()

    def save_config(self) -> None:
        """Save configuration to file"""
        try:
            # Update config with current settings before saving
            self.update_config_from_ui()

            with open(self.config_file, "w", encoding="utf-8") as f:
                json.dump(self.config, f, indent=4, ensure_ascii=False)
            print("Configuration saved successfully")

        except Exception as e:
            print(f"Error saving config: {e}")

    def merge_configs(self, default: dict, loaded: dict) -> dict:
        """Recursively merge loaded config with default config"""
        result = default.copy()

        for key, value in loaded.items():
            if key in result and isinstance(result[key], dict) and isinstance(value, dict):
                result[key] = self.merge_configs(result[key], value)
            else:
                result[key] = value

        return result

    def apply_config(self) -> None:
        """Apply configuration to UI and settings"""
        try:
            # Apply language setting
            language = self.config.get("ui", {}).get("language", "en")
            if language != self.current_language:
                self.load_language(language)

                # Update language combo box
                language_map = {"en": "English", "zh": "中文", "ja": "日本語"}
                if language in language_map:
                    index = self.language_combo.findText(language_map[language])
                    if index >= 0:
                        self.language_combo.setCurrentIndex(index)

            # Apply window geometry
            geometry = self.config.get("ui", {}).get("window_geometry", {})
            if all(key in geometry for key in ["x", "y", "width", "height"]):
                self.setGeometry(geometry["x"], geometry["y"], geometry["width"], geometry["height"])

            # Apply audio settings
            audio_config = self.config.get("audio", {})
            self.sample_rate = audio_config.get("sample_rate", 16000)
            self.chunk_size = audio_config.get("chunk_size", 1024)

            # Apply API URL
            api_url = self.config.get("api", {}).get("url")
            if api_url:
                self.api_url.setText(api_url)
                self.api_url.setStyleSheet("color: black; font-style: normal;")

        except Exception as e:
            print(f"Error applying config: {e}")

    def update_config_from_ui(self) -> None:
        """Update configuration with current UI settings"""
        try:
            # Update language
            self.config["ui"]["language"] = self.current_language

            # Update window geometry
            geometry = self.geometry()
            self.config["ui"]["window_geometry"] = {
                "x": geometry.x(),
                "y": geometry.y(),
                "width": geometry.width(),
                "height": geometry.height(),
            }

            # Update API URL
            api_url = self.api_url.text()
            if api_url and api_url != self.tr("Not set"):
                self.config["api"]["url"] = api_url
            else:
                self.config["api"]["url"] = None

            # Update selected microphone
            if self.microphone_combo.count() > 0:
                selected_mic = self.microphone_combo.currentText()
                if selected_mic != self.tr("No microphones found"):
                    self.config["audio"]["selected_microphone"] = selected_mic

        except Exception as e:
            print(f"Error updating config from UI: {e}")

    def toggle_listening(self) -> None:
        """Toggle audio listening on/off"""
        if self.is_listening:
            self.stop_listening()
        else:
            self.start_listening()

    def start_listening(self) -> None:
        """Start audio capture and processing"""
        if self.microphone_combo.count() == 0 or self.microphone_combo.currentText() == self.tr("No microphones found"):
            self.status_label.setText(self.tr("No microphone selected"))
            self.status_label.setStyleSheet("color: red;")
            self.listen_button.setChecked(False)
            return

        if self.oww_model is None:
            self.status_label.setText(self.tr("Wake word model not available"))
            self.status_label.setStyleSheet("color: red;")
            self.listen_button.setChecked(False)
            return

        if self.vosk_model is None:
            self.status_label.setText(self.tr("Speech model not available"))
            self.status_label.setStyleSheet("color: red;")
            self.listen_button.setChecked(False)
            return

        self.is_listening = True
        self.listen_button.setText(self.tr("Stop"))
        self.status_label.setText(self.tr("Listening..."))
        self.status_label.setStyleSheet("color: green;")

        # Get selected microphone device index
        mic_text = self.microphone_combo.currentText()
        device_id = None
        if "(" in mic_text and ")" in mic_text:
            try:
                device_id = int(mic_text.split("(")[-1].split(")")[0])
            except (ValueError, IndexError):
                device_id = None

        # Start audio processing thread
        self.audio_thread = threading.Thread(target=self.audio_processing_loop, args=(device_id,), daemon=True)
        self.audio_thread.start()

    def stop_listening(self) -> None:
        """Stop audio capture and processing"""
        self.is_listening = False
        if self.audio_thread and self.audio_thread.is_alive():
            self.audio_thread.join(timeout=1.0)

        self.listen_button.setText(self.tr("Listen"))
        self.status_label.setText(self.tr("Ready"))
        self.status_label.setStyleSheet("color: gray; font-style: italic;")

    def audio_processing_loop(self, device_id: Optional[int]) -> None:
        """Main audio processing loop"""
        try:
            # Setup audio stream
            def audio_callback(indata, frames, time, status):
                if status:
                    print(f"Audio callback status: {status}")

                # Convert to the format expected by OpenWakeWord and Vosk
                audio_data = indata[:, 0] if len(indata.shape) > 1 else indata
                audio_data_int16 = (audio_data * 32767).astype(np.int16)

                # Process with wake word detection
                if self.oww_model is not None:
                    try:
                        # Get prediction scores
                        prediction = self.oww_model.predict(audio_data_int16)

                        # Check for wake word detection (adjust threshold as needed)
                        for wake_word, score in prediction.items():
                            if score > 0.5:  # Threshold for detection
                                print("device_id", device_id)
                                print(f"Wake word '{wake_word}' detected with score: {score}")
                                self.wake_word_detected()
                                break
                    except Exception as e:
                        print(f"Error in wake word detection: {e}")

                # Process with Vosk speech recognition
                if self.vosk_recognizer is not None:
                    try:
                        # Convert audio data to bytes for Vosk
                        audio_bytes = audio_data_int16.tobytes()

                        # Feed audio to Vosk recognizer
                        if self.vosk_recognizer.AcceptWaveform(audio_bytes):
                            # End of utterance detected (silence after speech)
                            result = self.vosk_recognizer.Result()
                            result_dict = json.loads(result)
                            text = result_dict.get("text", "").strip()

                            if text:
                                print(f"Recognized speech: {text}")

                        # Optionally, you can also get partial results during speech
                        # partial_result = self.vosk_recognizer.PartialResult()
                        # partial_dict = json.loads(partial_result)
                        # partial_text = partial_dict.get('partial', '').strip()
                        # if partial_text:
                        #     print(f"Partial: {partial_text}")

                    except Exception as e:
                        print(f"Error in speech recognition: {e}")

            # Start recording
            with sd.InputStream(
                device=device_id,
                channels=1,
                samplerate=self.sample_rate,
                blocksize=self.chunk_size,
                callback=audio_callback,
                dtype=np.float32,
            ):
                print(f"Started listening on device {device_id}")
                while self.is_listening:
                    time.sleep(0.1)

        except Exception as e:
            print(f"Error in audio processing: {e}")
            self.is_listening = False

    def wake_word_detected(self) -> None:
        """Handle wake word detection"""
        print("Aleva wake word detected!")
        # Update status to show detection
        self.status_label.setText(self.tr("Wake word detected!"))
        self.status_label.setStyleSheet("color: blue; font-weight: bold;")

        # Reset status after 2 seconds
        threading.Timer(2.0, self.reset_listening_status).start()

    def reset_listening_status(self) -> None:
        """Reset listening status after wake word detection"""
        if self.is_listening:
            self.status_label.setText(self.tr("Listening..."))
            self.status_label.setStyleSheet("color: green;")

    def on_tray_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        """Handle tray icon activation"""
        if reason == QSystemTrayIcon.Trigger:  # Left click
            self.toggle_visibility()

    def toggle_visibility(self) -> None:
        """Toggle window visibility"""
        if self.isVisible():
            self.hide()
            self.show_hide_action.setText(self.tr("Show"))
        else:
            self.show()
            self.raise_()
            self.activateWindow()
            self.show_hide_action.setText(self.tr("Hide"))

    def on_language_changed(self, language_text: str) -> None:
        """Handle language selection change"""
        language_code = self.language_codes.get(language_text, "en")
        self.load_language(language_code)

        # Save configuration after language change
        self.save_config()

    def on_microphone_changed(self, microphone_text: str) -> None:
        """Handle microphone selection change"""
        if microphone_text and microphone_text != self.tr("No microphones found"):
            # Save configuration after microphone change
            self.save_config()

    def load_language(self, language_code: str) -> None:
        """Load a language using QTranslator"""
        self.current_language = language_code

        # Remove previous translator if exists
        if hasattr(self, "translator") and self.translator:
            self.app.removeTranslator(self.translator)

        # Skip loading for English (source language)
        if language_code == "en":
            self.translator = None
            self.retranslate_ui()
            return

        # Load translation file
        self.translator = QTranslator()

        # Find the languages directory
        current_dir = os.path.dirname(os.path.abspath(__file__))
        languages_dir = os.path.join(current_dir, "languages")

        translation_file = os.path.join(languages_dir, f"aleva_{language_code}.qm")

        if os.path.exists(translation_file):
            if self.translator.load(translation_file):
                self.app.installTranslator(self.translator)
                print(f"Loaded translation: {translation_file}")
            else:
                print(f"Failed to load translation: {translation_file}")
        else:
            print(f"Translation file not found: {translation_file}")

        self.retranslate_ui()

    def retranslate_ui(self) -> None:
        """Retranslate all UI elements"""
        self.setWindowTitle(self.tr("Aleva - Audio Language Assistant"))
        self.language_label.setText(self.tr("Language:"))
        self.microphone_label.setText(self.tr("Microphone:"))
        self.refresh_button.setText(self.tr("Refresh"))
        self.model_label.setText(self.tr("Model:"))
        self.api_label.setText(self.tr("API URL:"))
        self.set_api_button.setText(self.tr("Set"))

        # Update Listen button text based on current state
        if self.is_listening:
            self.listen_button.setText(self.tr("Stop"))
        else:
            self.listen_button.setText(self.tr("Listen"))

        # Update API URL label if it's "Not set"
        if self.api_url.text() == "Not set":
            self.api_url.setText(self.tr("Not set"))

        # Update status label if it's in default state
        if self.status_label.text() == "Ready":
            self.status_label.setText(self.tr("Ready"))

        # Update model status
        self.check_and_update_model_status()

        # Update tray menu
        if self.isVisible():
            self.show_hide_action.setText(self.tr("Hide"))
        else:
            self.show_hide_action.setText(self.tr("Show"))

        self.quit_action.setText(self.tr("Quit"))
        self.tray_icon.setToolTip(self.tr("Aleva - Click to show/hide"))

        # Refresh microphones to update "No microphones found" text if needed
        self.refresh_microphones()

    def refresh_microphones(self) -> None:
        """Refresh the list of available microphones"""
        self.microphone_combo.clear()

        try:
            # Get list of audio devices
            devices = sd.query_devices()
            microphones = []
            device_names = set()

            # Virtual device keywords to filter out
            virtual_keywords = [
                "virtual",
                "loopback",
                "cable",
                "mix",
                "mixer",
                "voicemeeter",
                "obs",
                "stream",
                "capture",
                "monitor",
                "what u hear",
                "stereo mix",
                "wave",
                "software",
                "digital",
                "system",
                "aggregate",
                "multi-output",
            ]

            for i, device in enumerate(devices):
                # Check if device has input channels (microphone)
                if device["max_input_channels"] > 0:
                    device_name = device["name"].lower()
                    if device_name in device_names:
                        continue
                    device_names.add(device_name)

                    # Skip virtual devices by checking for virtual keywords
                    is_virtual = any(keyword in device_name for keyword in virtual_keywords)

                    # Additional checks for virtual devices
                    # Skip devices with certain host API types that are typically virtual
                    host_api = device.get("hostapi", -1)
                    if host_api >= 0:
                        try:
                            host_api_info = sd.query_hostapis(host_api)
                            host_api_name = host_api_info["name"].lower()
                            # Skip certain host APIs that typically contain virtual devices
                            if "wasapi" in host_api_name and "loopback" in device_name:
                                is_virtual = True
                        except Exception:
                            pass

                    # Only add non-virtual devices
                    if not is_virtual:
                        microphones.append(f"{device['name']} ({i})")

            if microphones:
                self.microphone_combo.addItems(microphones)

                # Restore previously selected microphone if available
                selected_mic = self.config.get("audio", {}).get("selected_microphone")
                if selected_mic:
                    index = self.microphone_combo.findText(selected_mic)
                    if index >= 0:
                        self.microphone_combo.setCurrentIndex(index)
            else:
                self.microphone_combo.addItem(self.tr("No microphones found"))

        except Exception as e:
            print(f"Error querying audio devices: {e}")
            self.microphone_combo.addItem(self.tr("No microphones found"))

    def closeEvent(self, event: QCloseEvent) -> None:
        """Override close event to hide instead of close"""
        if self.is_quitting:
            # Actually quit the application
            self.cleanup_and_quit()
            event.accept()
        elif self.tray_icon.isVisible():
            # Hide to system tray instead of closing
            self.hide()
            event.ignore()

            # Show a message the first time (optional)
            if not hasattr(self, "_tray_message_shown"):
                self.tray_icon.showMessage(
                    "Aleva", self.tr("Application was minimized to tray"), QSystemTrayIcon.Information, 2000
                )
                self._tray_message_shown = True
        else:
            # No system tray available, quit properly
            self.cleanup_and_quit()
            event.accept()

    def cleanup_and_quit(self) -> None:
        """Clean up resources and quit the application"""
        # Prevent multiple calls to cleanup
        if hasattr(self, "_cleanup_called") and self._cleanup_called:
            return
        self._cleanup_called = True

        try:
            # Save configuration before quitting
            self.save_config()

            # Stop listening if active
            if self.is_listening:
                self.stop_listening()

            # Hide and clean up tray icon
            if hasattr(self, "tray_icon") and self.tray_icon:
                self.tray_icon.hide()
                self.tray_icon.setParent(None)
                self.tray_icon = None

        except Exception as e:
            print(f"Error during cleanup: {e}")

        # Force application to quit
        instance = QApplication.instance()
        if instance:
            instance.quit()

    def quit_application(self) -> None:
        """Properly quit the application"""
        self.is_quitting = True
        self.close()


def main():
    """Main entry point"""
    app = QApplication(sys.argv)

    # Check if system tray is available
    if not QSystemTrayIcon.isSystemTrayAvailable():
        QMessageBox.critical(None, "Systray", "System tray is not available on this system.")
        sys.exit(1)

    # Prevent application from exiting when main window closes
    app.setQuitOnLastWindowClosed(False)

    # Create and show main window
    window = MainWindow(app)
    window.show()

    try:
        return app.exec()
    except KeyboardInterrupt:
        # Handle Ctrl+C gracefully
        window.cleanup_and_quit()
        return 0


if __name__ == "__main__":
    sys.exit(main())
