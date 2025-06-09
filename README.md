# Aleva - Audio Language Assistant

A PySide6 application with system tray functionality, language selector, and microphone detection.

## Features

- **System Tray Integration**: The application minimizes to system tray instead of closing when you click the 'X' button
- **Language Support**: Choose between English, Chinese (中文), and Japanese (日本語)
- **Microphone Detection**: Automatically detects and lists available microphones on your system
- **Tray Icon Control**: Left-click the system tray icon to show/hide the window

## Installation

1. Install the required dependencies:
   ```bash
   pip install -r requirements.txt
   ```

   Or using the project dependencies:
   ```bash
   pip install PySide6>=6.5.0 sounddevice>=0.4.6
   ```

## Usage

### Method 1: Using the run script
```bash
python run.py
```

### Method 2: Using the module
```bash
python -m aleva
```

### Method 3: Direct execution
```bash
python src/aleva/main_window.py
```

## How to Use

1. **Language Selection**: Use the dropdown to select your preferred language (English, Chinese, Japanese)
2. **Microphone Selection**: The application will automatically detect available microphones. Use the "Refresh" button to update the list
3. **System Tray**: 
   - Click the 'X' button to minimize to system tray (won't close the application)
   - Left-click the tray icon to show/hide the window
   - Right-click the tray icon to access the context menu
   - Use "Quit" from the context menu to properly exit the application

## System Requirements

- Python 3.11+
- Windows/Linux/macOS with system tray support
- Audio devices (for microphone detection)

## Dependencies

- **PySide6**: GUI framework
- **sounddevice**: Audio device detection and management

## Notes

- The application will show a notification when first minimized to tray
- The system tray icon displays a blue square with the letter "A"
- All UI text updates when changing languages
- If no microphones are detected, a "No microphones found" message will be displayed
