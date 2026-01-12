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
