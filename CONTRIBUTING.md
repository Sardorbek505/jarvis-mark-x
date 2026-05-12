# Contributing to JARVIS

Thank you for your interest in contributing to JARVIS - Russian voice AI assistant!

## Project Structure

```
jarvis-ru/
├── main.py                 # Main engine (Gemini Live API integration)
├── ui.py                   # PyQt6 HUD interface
├── requirements.txt        # Python dependencies
├── config/                 # Configuration files
│   ├── api_keys.json     # API keys (Gemini, Spotify)
│   ├── modes.json        # Lifestyle modes configuration
│   └── user_profile.json # User preferences and memory
├── core/                   # Core functionality
│   ├── prompt.txt        # System prompt for AI
│   └── emotion_analyzer.py # Emotional intelligence
├── tools/                  # External integrations
│   └── spotify/          # Spotify Web API integration
│       ├── auth.py       # OAuth 2.0 authentication
│       ├── search.py     # Track search with fuzzy matching
│       ├── devices.py    # Device management
│       └── controller.py # Main Spotify controller
├── memory/                 # Long-term memory system
│   ├── memory_manager.py # Memory CRUD operations
│   └── data.json        # Stored memories
└── actions/                # Voice command handlers
    ├── open_app.py       # Application launcher
    ├── weather.py        # Weather information
    ├── web_search.py     # Web search
    ├── computer_settings.py # System controls
    ├── browser_control.py # Browser automation
    ├── file_controller.py # File operations
    ├── calendar.py       # Calendar management
    ├── movie_player.py   # Movie player (kinogo.mu)
    └── vision_review.py  # Screen analysis
```

## Development Setup

1. Clone the repository
2. Install dependencies: `pip install -r requirements.txt`
3. Configure API keys in `config/api_keys.json`
4. Run: `python main.py`

## Key Technologies

- **Google Gemini Live API** - Real-time voice AI
- **PyQt6** - Desktop UI
- **Spotify Web API** - Music integration
- **Selenium** - Browser automation (kinogo.mu)
- **SoundDevice** - Audio I/O

## Adding New Features

1. Create new action in `actions/` directory
2. Add tool description in `main.py` TOOLS list
3. Add handler in `_execute_tool()` method
4. Test with voice and text input

## Code Style

- Follow existing patterns in the codebase
- Use Russian for user-facing strings
- Add error handling with try/except
- Log important events with logger

## Testing

Test voice commands in Russian:
- Application control
- Music playback
- Weather queries
- Web search
- File operations

## Issues

Report bugs with:
- Python version
- OS version
- Error messages
- Steps to reproduce