from abc import ABC, abstractmethod
from PIL import Image

class DisplayDriver(ABC):
    @abstractmethod
    def init(self):
        """Initialize the display hardware."""
        pass

    @abstractmethod
    def clear(self):
        """Clear the display."""
        pass

    @abstractmethod
    def display(self, image: Image.Image):
        """Display the given image."""
        pass

    @abstractmethod
    def width(self) -> int:
        """Return the display width."""
        pass

    @abstractmethod
    def height(self) -> int:
        """Return the display height."""
        pass

    @abstractmethod
    def cleanup(self):
        """Release resources."""
        pass

    @abstractmethod
    def render_loading(self, percent: int, label: str):
        """Render and display a loading screen."""
        pass

    @abstractmethod
    def render_message(self, text: str, is_error: bool = False):
        """Render and display a message screen."""
        pass

    @abstractmethod
    def render_standard(self, snapshot: dict, structure: dict, server_online: bool):
        """Render and display the standard dashboard."""
        pass
