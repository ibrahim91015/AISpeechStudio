class SpeechLimitError(RuntimeError):
    """The model exhausted its output-token budget before the speech was complete."""
