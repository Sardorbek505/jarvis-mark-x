# JARVIS - Russian Voice AI Assistant

## Quick Summary
JARVIS is a Russian-speaking voice AI assistant powered by Google Gemini Live API. It provides voice control for Windows applications, Spotify music, web browsing, file management, and more.

## Core Technology Stack
- **AI Engine**: Google Gemini Live API (real-time audio)
- **UI Framework**: PyQt6 (HUD-style interface)
- **Language**: Python 3.8+
- **Audio**: SoundDevice (NumPy)
- **Integrations**: Spotify Web API, Selenium (browser automation)

## Main Features
1. Voice control in Russian language
2. Application launcher (open any app by voice)
3. Spotify music control (play, pause, search, volume)
4. Weather information
5. Web search
6. File management
7. Windows system controls (volume, brightness, screenshots)
8. Browser automation
9. Long-term memory system
10. Vision mode (screen analysis for design/UX)
11. Lifestyle modes (study/work/movie/music)
12. Calendar management
13. Smart reminders with emotional intelligence
14. Real-time translation (multiple languages)

## Project Structure
- `main.py` - Main application logic and Gemini Live integration
- `ui.py` - PyQt6 interface
- `actions/` - Voice command handlers
- `tools/spotify/` - Spotify Web API integration
- `memory/` - Long-term memory system
- `core/` - Core functionality (emotion analyzer, prompts)
- `config/` - Configuration files (API keys, user preferences)

## Setup Requirements
1. Google Gemini API key (free from aistudio.google.com)
2. Spotify API credentials (for music features - optional)
3. Python dependencies (see requirements.txt)

## Key Files for Understanding the Codebase
- `main.py` - Entry point, main loop, tool execution
- `core/prompt.txt` - System prompt defining AI behavior
- `tools/spotify/controller.py` - Spotify integration example
- `actions/` - Examples of voice command implementations

## Recent Improvements (Mark X)
- Fixed critical stability bugs (exception handling, loop errors)
- Improved speech recognition (debounce, normalization, anti-debounce)
- Enhanced intent parsing with text normalization
- Added critical action warnings
- Implemented structured logging
- Added application state management (idle/listening/processing/reconnecting)

## License
MIT License - Open source, free to use and modify